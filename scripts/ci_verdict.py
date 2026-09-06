#!/usr/bin/env python3
"""The merge gate's verdict, in one place, derived from the conditions themselves.

`ci / passed` is the ONE required check on `main` in every repository in this
estate, with `bypass_actors: []`. Whatever this file decides is what "may merge"
means, so the interesting question is not which results it accepts but what a
`skipped` job is allowed to prove.

WHAT WAS THERE BEFORE, and why it was not enough. The `passed` job read every
upstream result through one shell loop:

    case "$r" in
      success|skipped) ;;
      *) echo "upstream job result: $r"; exit 1 ;;
    esac

That is already better than the `!= 'failure'` shape this class of defect usually
takes — `cancelled` reddens rather than passing — and its `skipped` arm was
deliberate, because `test` genuinely has nothing to do in a repository with no
`Cargo.toml` and `proto` genuinely has nothing to do in one that vendors no
contract. The hole is that the arm is UNCONDITIONAL. It accepts `skipped` from
any job, on any event, for any reason, and GitHub reports `skipped` for two
situations that mean opposite things:

    the job's own `if:` was false      this job had nothing to do
    a job in its `needs` did not run   this job did not run

The loop cannot tell them apart, because a result is not a reason. So any
condition that skips a job — one added on purpose, one that becomes true by
accident, one an author can make true from inside the pull request — turns the
required check green having checked less.

THE FIX IS NOT A LONGER ALLOW-LIST. A hand-written table of which jobs may skip
is a second copy of the job conditions, kept in agreement with `ci-pr.yaml` by
hand, and this repository has already paid for that mistake twice: `pr_body.py`'s
docstring is headed "WHY IT IS A FILE RATHER THAN A THIRD INLINE COPY", and the
`detect` job exists because one detection lived in two files.

So this reads the CONDITIONS THEMSELVES out of the workflow file and evaluates
them:

    the job's `if:` is true   ->  it had something to do  ->  require `success`
    the job's `if:` is false  ->  it had nothing to do    ->  require `skipped`

Both halves are asserted, which is the part an allow-list cannot do at all: a job
that RUNS when its condition says it should not is as much a defect as one that
skips when it should not, and nothing anywhere reported it.

Drift is then structurally impossible rather than guarded. Editing a job's `if:`
edits this gate's expectation in the same keystroke, and adding a job to
`passed`'s `needs:` puts it under the gate with no second edit to forget.

FAIL-CLOSED ON ANYTHING IT DOES NOT UNDERSTAND, which is the property that makes
the whole approach safe rather than clever. The evaluator accepts a small, closed
subset of GitHub's expression language over a WHITELIST of context references.
An expression it cannot parse, a function it does not know, or a reference it has
not been taught refuses the run with a message naming what it saw. A gate that
guessed would be worse than the loop it replaces: it would report a verdict about
a condition nobody had read.

That refusal is also the interlock this file was written for. Pull request 52
(ledger 685) short-circuits a text-only pull request edit by giving six jobs a
`needs.detect.outputs.text_edit != 'true'` conjunct, and it is HELD because the
loop above maps the resulting skips to success — a red `ci / passed` superseded
by a green one on the same head sha, with no code change and no human. Against
this file that change does not silently pass: `text_edit` is not a whitelisted
reference, so the gate refuses until somebody teaches it the reference AND the
reason a `text_edit` skip is legitimate, which is the assertion 52 names — a
PRIOR COMPLETED `ci / passed` on this sha concluded success, read through
`GET /repos/{owner}/{repo}/commits/{sha}/check-runs`, which needs `checks: read`.
`register_deferred_reference` below is where that lands.

WHY A `needs.<job>.result` REFERENCE IS REFUSED RATHER THAN RESOLVED, since the
JSON handed to this script contains every one of them. A condition that reads
another job's result makes "should this job have run" depend on whether an
upstream job passed, and then a skip caused by an upstream FAILURE reads as a job
that legitimately had nothing to do. That is the same defect one level up, and
`ci-release.yaml` carried a live instance of it until ledger 729: `chart` ran on
`always() && ... && needs.image.result != 'failure'`, so a CANCELLED image job
published a chart with no digest pinned. Its skip arm now asks
`needs.detect.outputs.image` — the absent `Containerfile` that made the image job
skip — rather than the skip itself. A result is a consequence; only a FACT is a
reason.

`chart`'s SUCCESS arm still reads `needs.image.result`, and that is not the same
claim wearing a disguise. This gate's rule governs what may justify a job
CHECKING LESS under a required merge check; `ci-release.yaml` publishes rather
than merges, is not gated by this script, and its success arm has no fact to
stand on, because "the digest exists" is not a property of the tree. The skip
arm — the one that decides whether less work is acceptable — is on the fact.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import json
import os
import re
import sys

import yaml


class Refused(Exception):
    """The gate will not report a verdict, because it did not understand its input.

    Raised rather than returned so that no caller can mistake it for a verdict.
    Every path that raises it exits non-zero.
    """


# ---------------------------------------------------------------------------
# The expression subset
# ---------------------------------------------------------------------------

# WHAT A CONTEXT REFERENCE MAY BE. Dotted, no indexing, no wildcards — the
# shapes `ci-pr.yaml` actually uses. `[ ]`, `*` and `format()` are all real
# GitHub syntax and all deliberately outside the subset; see the docstring.
_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<and>&&)
      | (?P<or>\|\|)
      | (?P<eq>==)
      | (?P<ne>!=)
      | (?P<string>'(?:[^']|'')*')
      | (?P<call>[A-Za-z_][A-Za-z0-9_]*\(\))
      | (?P<ref>[A-Za-z_][A-Za-z0-9_.\-]*)
    )
    """,
    re.VERBOSE,
)

# FUNCTIONS THIS GATE KNOWS. `always()` is the only one any condition under
# `passed` uses, and it is true by definition. Anything else — `success()`,
# `failure()`, `cancelled()`, `contains()`, `startsWith()` — is refused rather
# than approximated, because each of the first three is a statement about
# upstream RESULTS and would reintroduce exactly what this file refuses above.
_FUNCTIONS = {"always": True}


def tokenize(expr):
    out = []
    i = 0
    while i < len(expr):
        m = _TOKEN.match(expr, i)
        if not m or m.end() == m.start():
            rest = expr[i:].strip()
            if not rest:
                break
            raise Refused(f"cannot tokenize {rest!r} in condition {expr!r}")
        i = m.end()
        kind = m.lastgroup
        if kind is None:
            break
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    """Recursive descent over `||` then `&&` then comparison then primary.

    GitHub gives `&&` higher precedence than `||`, which is what this nesting
    encodes. Parentheses are supported because `ci-release.yaml` would need them
    the moment its `chart` condition is written correctly.
    """

    def __init__(self, tokens, expr, resolve):
        self.t = tokens
        self.i = 0
        self.expr = expr
        self.resolve = resolve

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        v = self.or_expr()
        if self.i != len(self.t):
            raise Refused(f"trailing {self.peek()[1]!r} in condition {self.expr!r}")
        return as_bool(v, self.expr)

    def or_expr(self):
        v = self.and_expr()
        while self.peek()[0] == "or":
            self.take()
            r = self.and_expr()
            v = as_bool(v, self.expr) or as_bool(r, self.expr)
        return v

    def and_expr(self):
        v = self.cmp_expr()
        while self.peek()[0] == "and":
            self.take()
            r = self.cmp_expr()
            v = as_bool(v, self.expr) and as_bool(r, self.expr)
        return v

    def cmp_expr(self):
        left = self.primary()
        kind = self.peek()[0]
        if kind in ("eq", "ne"):
            self.take()
            right = self.primary()
            # COMPARED AS THE STRINGS THEY ARE. Every value in the whitelist is
            # a string and every literal beside it is a string, so there is no
            # coercion to get wrong. A comparison against a function result is
            # refused instead of guessed.
            if isinstance(left, bool) or isinstance(right, bool):
                raise Refused(f"cannot compare a function result in {self.expr!r}")
            return (left == right) if kind == "eq" else (left != right)
        return left

    def primary(self):
        kind, text = self.take()
        if kind == "lparen":
            v = self.or_expr()
            if self.peek()[0] != "rparen":
                raise Refused(f"unbalanced parenthesis in condition {self.expr!r}")
            self.take()
            return v
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "call":
            name = text[:-2]
            if name not in _FUNCTIONS:
                raise Refused(
                    f"condition {self.expr!r} calls {name}(), which this gate does "
                    f"not know. Teach it deliberately or rewrite the condition; a "
                    f"guessed verdict is worse than none."
                )
            return _FUNCTIONS[name]
        if kind == "ref":
            return self.resolve(text, self.expr)
        raise Refused(f"unexpected {text!r} in condition {self.expr!r}")


def as_bool(value, expr):
    if isinstance(value, bool):
        return value
    # A BARE STRING IS NOT A CONDITION HERE, though GitHub would take it as one.
    # Every `if:` under `passed` compares explicitly, and refusing the implicit
    # form keeps a typo'd reference from reading as "true, therefore this job
    # should have run" or its opposite.
    raise Refused(
        f"condition {expr!r} uses a bare value where a boolean is needed; "
        f"compare it explicitly."
    )


def evaluate(expr, resolve):
    """True/False for `expr`, or raise `Refused`. `resolve(ref, expr) -> str`."""
    if expr is None:
        # No `if:` at all. The job always runs.
        return True
    expr = str(expr).strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    if not expr:
        raise Refused("empty condition")
    return _Parser(tokenize(expr), expr, resolve).parse()


# ---------------------------------------------------------------------------
# The context whitelist
# ---------------------------------------------------------------------------

# THE FACTS A SKIP MAY BE JUSTIFIED BY, NAMED ONE AT A TIME — and the whole
# interlock rests on this being a list of full reference names rather than a
# pattern. A rule of the shape "any `needs.detect.outputs.*`" reads whatever
# `detect` happens to emit, so the day a new output is added the gate starts
# honouring a condition nobody decided it should honour. That is precisely
# ledger 685's `text_edit`, and it is how this file was written the first time.
#
# Each entry is a fact about the CHANGE or the EVENT that the gate can check for
# itself, which is what distinguishes it from a job result. Adding one is a
# decision about what the merge gate accepts as a reason to check less, so it is
# made here, in one place, rather than by writing a conjunct into a job's `if:`.
_FACTS = {
    # `Cargo.toml` is absent, so there is no Rust to test.
    "needs.detect.outputs.rust",
    # `PROTO_VERSION` is absent — and `detect` refuses to report that while
    # `proto/` is still in the tree, which is what keeps the "no" true.
    "needs.detect.outputs.proto",
}

_DEFERRED = {}


def register_deferred_reference(ref, resolver):
    """Teach the gate one further reference, with the assertion that earns it.

    THIS IS LEDGER 685'S LANDING SPOT. Pull request 52 adds
    `needs.detect.outputs.text_edit` to six job conditions; until it is
    registered here the gate refuses the run rather than accepting six skips.
    Registering it means writing the resolver that also asserts the prior
    `ci / passed` on this sha concluded success — the fact that makes a
    text-only edit's skip a reason rather than a bypass.
    """
    _DEFERRED[ref] = resolver


def make_resolver(event, author_type, repository, needs):
    """The whitelist, closed over this run's facts."""

    def resolve(ref, expr):
        if ref == "github.event_name":
            return event
        if ref == "github.repository":
            return repository
        if ref == "github.event.pull_request.user.type":
            # Empty on any event that carries no pull request, which is what
            # GitHub itself renders. The conditions that read it are guarded by
            # an event test, so the empty value is never load-bearing.
            return author_type
        if ref in _DEFERRED:
            return _DEFERRED[ref](expr)
        if ref.startswith("needs.") and ref.endswith(".result"):
            raise Refused(
                f"condition {expr!r} reads {ref}. A job's result is a "
                f"consequence, not a reason: a skip caused by an upstream "
                f"failure would read as a job that had nothing to do. Gate on "
                f"the fact that made the upstream job skip instead."
            )
        m = re.fullmatch(r"needs\.([A-Za-z0-9_\-]+)\.outputs\.([A-Za-z0-9_\-]+)", ref)
        if m and ref in _FACTS:
            job, key = m.group(1), m.group(2)
            if job not in needs:
                raise Refused(
                    f"condition {expr!r} reads outputs of {job!r}, which is not "
                    f"among the jobs this gate was given."
                )
            outputs = needs[job].get("outputs") or {}
            # A JOB THAT DID NOT RUN PRODUCED NOTHING, and GitHub renders that
            # as the empty string rather than as an error. Reproduced exactly,
            # because it is what makes `proto` skip on a push to `main`.
            return str(outputs.get(key, ""))
        raise Refused(
            f"condition {expr!r} reads {ref}, which this gate has not been "
            f"taught. A skip justified by a reference nobody registered is a "
            f"skip justified by nothing. Add it through "
            f"register_deferred_reference() together with the assertion that "
            f"makes it a reason."
        )

    return resolve


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def load_conditions(workflow_path, gate_job):
    """The gated job names, in order, each with the `if:` written for it."""
    with open(workflow_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    jobs = (doc or {}).get("jobs") or {}
    if gate_job not in jobs:
        raise Refused(f"{workflow_path} has no job named {gate_job!r}")
    gated = jobs[gate_job].get("needs") or []
    if isinstance(gated, str):
        gated = [gated]
    if not gated:
        raise Refused(
            f"{gate_job!r} declares no `needs:`, so it gates nothing. A required "
            f"check that depends on no job reports success unconditionally."
        )
    missing = [j for j in gated if j not in jobs]
    if missing:
        raise Refused(f"{gate_job!r} needs jobs that do not exist: {missing}")
    # `if` is a YAML boolean-ish key; PyYAML gives back the string 'if' here
    # because these are plain mappings, but the `on:` -> True quirk is real in
    # this format so the lookup is written defensively.
    return [(j, jobs[j].get("if", jobs[j].get(True))) for j in gated]


def verdict(conditions, results, resolve):
    """`(ok, rows)`. Each row is (job, should_run, required, actual, ok)."""
    # THE TWO SETS MUST BE THE SAME SET, and only one of the two asymmetries is
    # obvious. This function iterates the CONDITIONS, so a job that reported a
    # result the workflow file does not list is never looked at: no row records
    # it, nothing requires anything of it, and it can report `failure` while the
    # verdict comes back green. `passed` runs under `if: always()`, so that run
    # exists. It is this file's own defect one level up — a gate reporting on a
    # matrix smaller than the one it was handed, and saying so about the part it
    # read rather than about the run.
    #
    # HOW THE SETS COME APART, since they are produced by two different reads of
    # the same file. `toJSON(needs)` is built by the workflow version the CALLER
    # resolved when the run started; the conditions are read from the checkout
    # the gate step takes minutes later. Today every consumer pins
    # `ci-pr.yaml@main`, so the two agree except across a merge to `main` inside
    # that window — and the moment any consumer pins a tag instead, they diverge
    # for as long as the pin lags. Both directions refuse here, rather than one
    # refusing and the other passing quietly.
    gated = [j for j, _ in conditions]
    unreported = [j for j in gated if j not in results]
    unread = [j for j in results if j not in gated]
    if unreported or unread:
        parts = []
        if unreported:
            parts.append(
                "no result was reported for "
                + ", ".join(repr(j) for j in unreported)
            )
        if unread:
            parts.append(
                "a result was reported for "
                + ", ".join(repr(j) for j in unread)
                + ", which is not among the jobs this gate reads and so would "
                "not have been checked at all"
            )
        raise Refused(
            "the `needs` context and the workflow do not describe the same run: "
            + "; ".join(parts)
            + ". The gate reads the conditions from its own checkout; a caller "
            "running a different version of this file produces exactly this."
        )
    rows = []
    for job, condition in conditions:
        should_run = evaluate(condition, resolve)
        required = "success" if should_run else "skipped"
        actual = (results.get(job) or {}).get("result")
        if actual is None:
            # The job IS in the context — the set check above passed — but its
            # entry carries no `result`. GitHub always writes one, so this is a
            # malformed payload rather than a version mismatch, and it is
            # refused separately so the two are not reported as one thing.
            raise Refused(
                f"the entry for {job!r} in the `needs` context carries no "
                f"`result`, so there is nothing to check it against."
            )
        rows.append((job, should_run, required, actual, actual == required))
    return all(r[4] for r in rows), rows


def render(rows):
    width = max(len(r[0]) for r in rows)
    out = []
    for job, should_run, required, actual, ok in rows:
        why = "had work to do" if should_run else "its condition was false"
        mark = "ok " if ok else "BAD"
        out.append(
            f"{mark} {job.ljust(width)}  {why:<23}  "
            f"required {required:<8} got {actual}"
        )
    return "\n".join(out)


def main(argv=None):
    env = os.environ
    workflow = env.get("WORKFLOW") or ".github/workflows/ci-pr.yaml"
    gate_job = env.get("GATE_JOB") or "passed"
    raw = env.get("NEEDS")
    if not raw:
        raise Refused("NEEDS is empty; the gate was given no results to read.")
    try:
        needs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(f"NEEDS is not JSON: {exc}") from exc
    if not isinstance(needs, dict):
        raise Refused("NEEDS is not an object.")
    event = env.get("EVENT") or ""
    if not event:
        raise Refused("EVENT is empty; every condition here keys off the event.")

    resolve = make_resolver(
        event=event,
        author_type=env.get("AUTHOR_TYPE") or "",
        repository=env.get("REPO") or "",
        needs=needs,
    )
    conditions = load_conditions(workflow, gate_job)
    ok, rows = verdict(conditions, needs, resolve)
    print(render(rows))
    if ok:
        # THE COUNT IS PART OF THE VERDICT, not decoration. "Everything passed"
        # is what a gate that inspected nothing also says; "all 8 of them" is
        # not, and it is the number a reader can compare against `needs:`.
        print(
            f"\nAll {len(rows)} jobs under `{gate_job}` did what their "
            f"conditions asked of them."
        )
        return 0
    bad = [r for r in rows if not r[4]]
    for job, should_run, required, actual, _ in bad:
        if should_run:
            print(
                f"::error::`{job}` reported '{actual}' and its condition was "
                f"TRUE, so it had work to do and did not do it. This run proved "
                f"nothing about the change.",
                file=sys.stderr,
            )
        else:
            print(
                f"::error::`{job}` reported '{actual}' and its condition was "
                f"FALSE, so it ran when the workflow says it should not have.",
                file=sys.stderr,
            )
    return 1


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Refused as exc:
        print(f"::error::the merge gate refused to report a verdict: {exc}", file=sys.stderr)
        sys.exit(1)
