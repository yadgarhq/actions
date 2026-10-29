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

That refusal is also the interlock this file was written for, and ledger 685 is
what came through it. Pull request 52 short-circuits a text-only pull request
edit by giving seven jobs a `needs.detect.outputs.text_edit != 'true'` conjunct.
Under the shell loop above the resulting skips read as success — a red
`ci / passed` superseded by a green one on the same head sha, with no code change
and no human. Under this file they do not, because `text_edit` resolves through
`make_text_edit_resolver` rather than through the whitelist, and that resolver
hands back `true` ONLY after establishing the fact that makes the skips a reason:
the STANDING `ci / passed` on this same commit — the most recent completed one,
this run's own excluded — concluded success. Anything else raises `Refused`, so
absence, a red predecessor and an unreadable API all redden the merge gate.

WHAT THAT COSTS, stated here because it is the trade rather than a detail. The
read is `GET /repos/{owner}/{repo}/commits/{sha}/check-runs`, which needs
`checks: read`. `ci-pr.yaml` declares it at the workflow block, so every job in
the shared workflow carries it in all eighteen repositories that call the file.
A called workflow's token can only be NARROWED by its caller, never widened, so
each caller must grant it on its own `uses:` line as well — which is why the
assertion is inert, and refuses loudly rather than passing quietly, in any
repository that has not.

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

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

import config_only


class Refused(Exception):
    """The gate will not report a verdict, because it did not understand its input.

    Raised rather than returned so that no caller can mistake it for a verdict.
    Every path that raises it exits non-zero.
    """


class NoStandingVerdict(Refused):
    """No COMPLETED `ci / passed` of another run is on this commit yet.

    A `Refused` like any other, so every caller that catches `Refused` still
    reddens on it. It is a separate class for ONE reader only:
    `await_standing_verdict`, which waits on this refusal and on no other.
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

    LEDGER 685 LANDED HERE, and the shape it landed in is the one this hook was
    written for: the resolver does not report what `text_edit` says, it reports
    what `text_edit` has EARNED. `make_text_edit_resolver` below returns the
    value only after establishing that the standing `ci / passed` on this commit
    concluded success, and raises `Refused` otherwise — so the seven skips a
    text-only edit produces are accepted only on a run that has a green verdict
    to carry forward.

    The registration happens in `main()` rather than at import, so that a
    resolver is always built against ONE run's facts and no test can leave a
    stale one behind for the next.
    """
    _DEFERRED[ref] = resolver


# ---------------------------------------------------------------------------
# Ledger 685: the fact that earns a text-only edit's skips
# ---------------------------------------------------------------------------

# THE REFERENCE THE SEVEN CONDITIONS READ. Written once here rather than spelled
# out at each use, because the string is the join between `ci-pr.yaml`'s job
# conditions and this gate's whitelist and a typo in either is a silent refusal.
TEXT_EDIT_REF = "needs.detect.outputs.text_edit"

# THE ONE CONTEXT THE RULESET REQUIRES, in this repository and in all eighteen
# consumers: `<caller job id> / <callee job id>`, where the caller job is named
# `ci` precisely so that one ruleset serves every repository. A repository that
# renamed it would not be gated by this name at all, so its pull requests block
# on a check that never reports rather than merge on one this gate misread.
DEFAULT_REQUIRED_CHECK = "ci / passed"


def check_runs_url(repo, sha, check_name):
    """The check runs named `check_name` on `sha` — ALL of them, not the latest.

    `filter=all` IS A SUPERSET RATHER THAN THE THING THAT CARRIES THE LOAD, and
    saying so corrects what this docstring claimed before. It claimed the
    endpoint's default returns exactly one check run per NAME, so that a live
    run would see only its own. That is false. Re-measured on `yadgarhq/actions`
    on 2026-09-09, `check_name=ci / passed`, `total_count` under the default,
    `&filter=all` and `&filter=latest` in turn:

        7c3afde   5   5   5
        b893dc9   4   4   4
        ed8b334   2   2   2

    `filter=latest` dedups per check SUITE, not per name, and every workflow run
    opens its own suite — the five entries on `7c3afde` carry five DISTINCT
    `check_suite.id` values. So prior completed verdicts come back either way.

    THE PARAMETER STAYS, because a superset cannot lose the entry this gate
    needs and it costs one query string. What actually stops this run reading
    its own answer is `standing_verdict`'s self-exclusion by workflow RUN ID
    below, and the tests that pin the property say so.

    THE CAVEAT, said here rather than left for the next reader to fall into:
    those three measurements were taken after the fact, on commits whose every
    run had completed. What the endpoint returns DURING a live in-progress run
    was not observed, so this records what was measured and claims nothing past
    it.

    `per_page=100` with the truncation check in `standing_verdict` below rather
    than pagination: a hundred `ci / passed` runs on one commit is a situation to
    refuse, not to page through.
    """
    return (
        "https://api.github.com/repos/"
        f"{repo}/commits/{sha}/check-runs"
        f"?check_name={urllib.parse.quote(check_name, safe='')}"
        "&filter=all&per_page=100"
    )


def fetch_check_runs(url, token, permission="checks"):
    """`GET` the URL as JSON, or refuse. Every failure path refuses.

    A GATE THAT CANNOT READ THE ANSWER HAS NOT GOT A GREEN ONE. The 403 a
    missing `checks: read` produces is the failure most likely to happen in
    practice — a consumer that subscribes to `edited` without granting the
    permission on its `uses:` line gets exactly it — and it must redden the
    merge gate rather than wave the run through. `permission` only names the
    grant in that message; yaadgaar 442's reads need `contents: read`.
    """
    if not token:
        raise Refused(
            "no token was given to read this commit's check runs with, so the "
            "standing verdict cannot be established. Absence is not success."
        )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "yadgarhq-actions/ci_verdict",
        },
    )
    # Unredirected, so urllib never copies the token onto a redirect hop.
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (403, 404):
            hint = (
                f" This is what a missing `{permission}: read` looks like: the "
                "permission must be granted on the CALLER's `uses:` line as "
                "well as declared here, because a called workflow's token can "
                "only be narrowed by the caller, never widened."
            )
        raise Refused(
            f"reading this commit's check runs returned HTTP {exc.code}.{hint}"
        ) from exc
    except urllib.error.URLError as exc:
        raise Refused(
            f"this commit's check runs could not be read: {exc.reason}"
        ) from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise Refused(f"the check runs response is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise Refused("the check runs response is not an object.")
    return payload


def standing_verdict(payload, run_id, check_name):
    """The most recent COMPLETED `check_name` on this commit, THIS RUN'S OWN EXCLUDED.

    THE SELF-EXCLUSION IS THE SUBTLE PART, and without it the assertion is not
    merely weak — it reads its own answer. This job's check run for
    `check_name` already exists while this step runs, because GitHub creates it
    when the job starts. It carries the same name, it sits on the same commit,
    and it is the newest one there. So a query that does not exclude it hands
    back the entry belonging to the very run whose legitimacy is in question.

    EXCLUDED TWICE, BY STATE AND BY IDENTITY, because the two fail differently.
    `status != "completed"` drops it structurally — a running job's check run
    cannot be completed — and a `details_url` carrying `/runs/<this run id>/`
    drops it by name. If GitHub ever completed a check run before its job's last
    step, the first test would stop working and the second would not.

    THE MOST RECENT RATHER THAN ANY, and this is a tightening on what pull
    request 52 asked for. "Some prior run concluded success" would let a commit
    whose LATEST full run went red — a newly published advisory that `trivy`
    now finds, a test that is not deterministic — be waved through by a body
    edit, because an older green would still be sitting there. A text-only edit
    may CARRY FORWARD the verdict that stands, never improve on it.
    """
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise Refused("the check runs response carries no `check_runs` array.")
    total = payload.get("total_count")
    if isinstance(total, int) and total > len(runs):
        raise Refused(
            f"this commit carries {total} `{check_name}` check runs and the "
            f"response holds {len(runs)}, so the most recent one may not be "
            f"among them."
        )
    mine = f"/runs/{run_id}/"
    if not run_id:
        raise Refused(
            "this run's own id is unknown, so its own check run cannot be told "
            "apart from the prior one it would be read as."
        )
    prior = [
        r
        for r in runs
        if mine not in str(r.get("details_url") or "")
        and r.get("status") == "completed"
    ]
    if not prior:
        raise NoStandingVerdict(
            f"no COMPLETED `{check_name}` on this commit other than this run's "
            f"own, so "
            f"there is no standing verdict for a text-only edit to carry "
            f"forward. Absence is not success."
        )
    prior.sort(
        key=lambda r: (str(r.get("completed_at") or ""), int(r.get("id") or 0)),
        reverse=True,
    )
    return prior[0]


# ---------------------------------------------------------------------------
# Yaadgaar 440: a text-only edit racing the full run on the same commit
# ---------------------------------------------------------------------------
#
# THE RACE, measured on yadgarhq/yadgar#94 (head `c28c9e3`, 2026-10-26).
# `fan_out.py` moves an open bot pull request with a file write
# (`synchronize`) and then a body update (`edited`) seconds apart, so a full
# run and a text-only run start on ONE head sha. The text-only run reaches
# this gate in about ten seconds. The full run's `ci / passed` does not EXIST
# yet at that moment: `passed` starts only after every job it needs has
# finished, which was 80 seconds later on #94 and can be twenty minutes in a
# repository with a database-backed `test`. So `standing_verdict` found no
# prior verdict, refused, and that red check run kept the commit rollup at
# FAILURE after the full run went green.
#
# THE FIX WAITS, BOUNDED, AND DECIDES BY THE SAME RULE. On the absence
# refusal ONLY, the gate looks for a SIBLING run on this commit that is still
# checking it: another run of the same caller job (`ci / *`), on the same
# `head_sha`, that has no completed `ci / passed` and is not a text-only run
# itself. If one exists, it sleeps and asks `standing_verdict` again. Each
# answer it acts on is `standing_verdict`'s, unchanged, over the response as
# it stood at that poll — so for any one response the gate passes only where
# the shipped gate passes. What changes is WHEN it reads, which is what the
# documented workaround (re-run the red run after the full run finishes)
# already did by hand.
#
# STILL FAIL-CLOSED. No sibling: refuse at once, as before. A sibling that
# does not finish inside the bound: refuse. A sibling that finishes red:
# refuse, because `standing_verdict` then returns its red. Any other refusal
# (a truncated response, a 403, no run id) is not waited on at all.
#
# A TEXT-ONLY SIBLING IS NOT WAITED FOR, because it checked nothing and has no
# verdict of its own to bring, and because two quick body edits would
# otherwise hold each other until the bound. It is recognised by its
# SENTINELS — the jobs whose `if:` carries the text-edit conjunct, read from
# the workflow — all of which it reports `skipped`. A full run executes at
# least `precommit`, which no other fact skips on a pull request. A sibling
# with no sentinel check run yet (its `detect` still running) cannot be told
# apart, so it is waited for; for a text-only one that costs seconds.
#
# ONE WINDOW READS THE WRONG WAY, and it fails closed. In a repository with no
# Rust or no vendored contract, a full run's `test` and `proto` are listed as
# completed-`skipped` the moment `detect` finishes, about a second before
# `precommit` is listed. A poll inside that second sees only skipped sentinels,
# reads the sibling as text-only, and refuses at once — the shipped behaviour.
#
# THE PRODUCER EDITS FIRST (ADR-1033). `fan_out.py` now updates the pull
# request's title and body BEFORE it moves the branch, so the `edited` run
# lands on the OLD head, which already carries a verdict, and the new head
# gets only the full `synchronize` run. The wait is left for a human editing
# a body while a push is still being checked.

# THE BOUND, ten minutes (ADR-1033). The longest successful pull request runs
# measured on 2026-10-27 were `store` 1128 s and `iam` 1011 s, so a text-only
# edit racing one of those still refuses at the bound. That is accepted: with
# the producer editing first, the wait serves only a human edit race, and a
# waiting `passed` holds one of the organisation's runner slots for as long
# as it waits. `ci-pr.yaml` gives `passed` a `timeout-minutes` above this
# bound. `CARRY_FORWARD_WAIT_SECONDS` overrides it; no value of it turns a
# red commit green, because the bound only decides how long to wait before a
# refusal.
DEFAULT_WAIT_SECONDS = 600
POLL_SECONDS = 30

# The unfiltered listing pages at 100. Ten pages is far past any real commit;
# more than that refuses rather than reads a partial set.
MAX_PAGES = 10

_RUN_ID = re.compile(r"/runs/(\d+)/")


def wait_budget():
    """`CARRY_FORWARD_WAIT_SECONDS` as a whole number of seconds, or refuse."""
    raw = os.environ.get("CARRY_FORWARD_WAIT_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_WAIT_SECONDS
    if not raw.isdigit():
        raise Refused(
            f"CARRY_FORWARD_WAIT_SECONDS is {raw!r}, not a whole number of seconds."
        )
    return int(raw)


def text_edit_sentinels(conditions, check_name):
    """The check-run names of the jobs a text-only edit skips, read off the workflow."""
    prefix = check_name.rpartition(" / ")[0]
    if not prefix:
        return ()
    return tuple(
        f"{prefix} / {job}"
        for job, cond in conditions
        if cond is not None and TEXT_EDIT_REF in str(cond)
    )


def all_check_runs_url(repo, sha, page):
    """Every check run on `sha`, any name, one page of 100."""
    return (
        "https://api.github.com/repos/"
        f"{repo}/commits/{sha}/check-runs"
        f"?filter=all&per_page=100&page={page}"
    )


def fetch_all_check_runs(fetch, repo, sha):
    """Every check run on `sha`, paged to `total_count`, or refuse."""
    runs = []
    for page in range(1, MAX_PAGES + 1):
        body = fetch(all_check_runs_url(repo, sha, page))
        batch = body.get("check_runs")
        total = body.get("total_count")
        if not isinstance(batch, list) or not isinstance(total, int):
            raise Refused("the check runs listing carries no `check_runs` or `total_count`.")
        runs.extend(batch)
        if len(runs) >= total or not batch:
            return runs
    raise Refused(
        f"this commit carries more than {MAX_PAGES * 100} check runs, so the "
        f"runs still checking it cannot all be read."
    )


def pending_siblings(runs, run_id, sha, check_name, sentinels):
    """Other runs of this caller job on `sha` that may yet bring a verdict."""
    prefix = check_name.rpartition(" / ")[0] + " / "
    by_run = {}
    for r in runs:
        if not str(r.get("name") or "").startswith(prefix):
            continue
        if r.get("head_sha") != sha:
            continue
        m = _RUN_ID.search(str(r.get("details_url") or ""))
        if not m or m.group(1) == str(run_id):
            continue
        by_run.setdefault(m.group(1), []).append(r)
    pending = []
    # A sibling with a COMPLETED `check_name` needs no test here: the
    # `standing_verdict` call that precedes this one would have returned it.
    for rid, entries in sorted(by_run.items()):
        marks = [r for r in entries if r.get("name") in sentinels]
        text_only = marks and all(
            r.get("status") == "completed" and r.get("conclusion") == "skipped"
            for r in marks
        )
        if not text_only:
            pending.append(rid)
    return pending


def await_standing_verdict(fetch, repo, sha, run_id, check_name, sentinels, budget, sleep, clock, log):
    """`standing_verdict`, asked again while a sibling run is still checking `sha`."""
    deadline = clock() + budget
    while True:
        try:
            return standing_verdict(
                fetch(check_runs_url(repo, sha, check_name)), run_id, check_name
            )
        except NoStandingVerdict as absent:
            if not sentinels:
                raise
            pending = pending_siblings(
                fetch_all_check_runs(fetch, repo, sha), run_id, sha, check_name, sentinels
            )
            if not pending:
                raise Refused(
                    f"{absent} No other run on {sha} is still checking it either."
                ) from None
            remaining = deadline - clock()
            if remaining <= 0:
                raise Refused(
                    f"{absent} Waited {budget}s for run(s) {', '.join(pending)} "
                    f"on {sha}, and none concluded `{check_name}` inside the bound."
                ) from None
            log(
                f"no standing `{check_name}` on {sha} yet; run(s) "
                f"{', '.join(pending)} still checking it. Waiting "
                f"{min(POLL_SECONDS, remaining):.0f}s ({remaining:.0f}s of the bound left)."
            )
            sleep(min(POLL_SECONDS, remaining))


def make_text_edit_resolver(
    needs,
    repo,
    sha,
    run_id,
    check_name,
    token,
    fetch=None,
    sentinels=(),
    budget=0,
    sleep=time.sleep,
    clock=time.monotonic,
    log=None,
):
    """Resolve `text_edit`, and on `true` assert the verdict that makes it a reason.

    THE VALUE IS RETURNED ONLY WHEN IT HAS BEEN EARNED. `text_edit` is `true`
    exactly when this run is a pull request edit that changed the title or the
    body and not the base, and the seven conditions that read it then evaluate
    FALSE — which tells this gate that seven `skipped` results are what the
    workflow asked for. That is the merge-gate bypass pull request 52 was held
    on, and it stops being one here: the value is handed back only after the
    standing `ci / passed` on this same commit is shown to have concluded
    success.

    ON `false` NOTHING IS READ AT ALL. An ordinary pull request does not touch
    the API, so the permission is exercised only on the path that needs it and
    an outage cannot redden a run that is checking everything anyway.
    """
    if fetch is None:

        def fetch(url):
            return fetch_check_runs(url, token)

    if log is None:

        def log(message):
            print(message, file=sys.stderr, flush=True)

    cache = {}

    def resolve(expr):
        outputs = (needs.get("detect") or {}).get("outputs") or {}
        value = str(outputs.get("text_edit", ""))
        if value != "true":
            # Not a text-only edit, so this reference is justifying no skip and
            # there is nothing for it to earn. `detect` skipped on a push to
            # `main` renders as the empty string, exactly as `_FACTS` does.
            return value
        if not repo or not sha:
            raise Refused(
                "a text-only edit was reported, but the commit whose standing "
                "verdict it would carry forward was not named."
            )
        if "run" not in cache:
            cache["run"] = await_standing_verdict(
                fetch, repo, sha, run_id, check_name, sentinels, budget, sleep, clock, log
            )
        latest = cache["run"]
        if latest.get("head_sha") != sha:
            # The endpoint is per commit, so this cannot happen against GitHub.
            # Checked anyway: a verdict on other bytes is no verdict on these.
            raise Refused(
                f"the standing `{check_name}` ({latest.get('details_url')}) is on "
                f"another commit, {latest.get('head_sha')!r}, not {sha}."
            )
        conclusion = str(latest.get("conclusion") or "")
        if conclusion != "success":
            raise Refused(
                f"this run is a text-only edit, so it checked the body and "
                f"nothing else. The standing `{check_name}` on {sha} concluded "
                f"'{conclusion}' ({latest.get('details_url')}), so there is no "
                f"green verdict to carry forward and these skips prove nothing. "
                f"Fix the commit, or push it again to re-run the checks — "
                f"editing the description cannot make a red run green."
            )
        return value

    return resolve


# ---------------------------------------------------------------------------
# Yaadgaar 442: the fact that earns a hook rev bump's skips
# ---------------------------------------------------------------------------
#
# `detect` reports `config_only=true` when the whole pull request is one
# forward move of the `yadgarhq/actions` hook rev (`config_only.py` says what
# that means exactly). Seven jobs then skip, and `precommit` runs the hooks at
# the new rev. This gate does not take `detect`'s word for it. It reads the
# change again from the API — `passed` has no checkout of the consumer — and
# runs the SAME `config_only.classify` over both versions of the file. Any
# failure to read, any doubt, and any disagreement with `detect` refuses.
#
# FOUR READS, all `contents: read`, and only when the value is `true`:
#
#   GET /repos/{o}/{r}/git/ref/heads/{base}       the base tip, which must be the
#                                                 one `detect` merged onto
#   GET /repos/{o}/{r}/compare/{base}...{head}   the files the pull request changes
#   GET /repos/{o}/{r}/git/trees/{commit}         the file's mode and blob, per side
#   GET /repos/{o}/{r}/git/blobs/{sha}            the file itself, per side
#
# The compare response carries no file modes, so the trees are read for them.
#
# THE TEXT CHECK IS NOT THE WHOLE CHECK. `classify` reads lines, and YAML is not
# lines: a duplicate `repo:` key or a multi-line quoted scalar can put a line
# that looks like the actions block where no actions entry is. So both sides
# are also parsed, refusing duplicate keys, and must be equal once the one
# actions entry's `rev` is moved from `before` to `after`.

CONFIG_ONLY_REF = "needs.detect.outputs.config_only"


def compare_url(repo, base, head):
    return (
        f"https://api.github.com/repos/{repo}/compare/"
        f"{urllib.parse.quote(base, safe='')}...{head}"
    )


def tree_url(repo, commit):
    return f"https://api.github.com/repos/{repo}/git/trees/{commit}"


def blob_url(repo, sha):
    return f"https://api.github.com/repos/{repo}/git/blobs/{sha}"


def compared_file(compare):
    """The one changed file of a compare response, and the merge base, or refuse."""
    files = compare.get("files")
    if not isinstance(files, list) or len(files) != 1:
        count = len(files) if isinstance(files, list) else "no"
        raise Refused(f"the pull request changes {count} files, not exactly one.")
    f = files[0]
    if f.get("filename") != config_only.CONFIG or f.get("status") != "modified":
        raise Refused(f"the one changed file is {f.get('filename')!r} ({f.get('status')!r}).")
    if f.get("previous_filename"):
        raise Refused(f"{config_only.CONFIG} was renamed from {f.get('previous_filename')!r}.")
    if not isinstance(f.get("patch"), str) or not f["patch"]:
        raise Refused("the compare response carries no patch, so it was truncated.")
    if f.get("additions") != 1 or f.get("deletions") != 1:
        raise Refused(
            f"the patch adds {f.get('additions')} and deletes {f.get('deletions')} "
            f"lines, not one of each."
        )
    base = (compare.get("merge_base_commit") or {}).get("sha")
    if not config_only.SHA.fullmatch(str(base or "")):
        raise Refused("the compare response names no merge base commit.")
    return f, base


def config_at(fetch, repo, commit):
    """`.pre-commit-config.yaml` at `commit`, as text, after checking its mode."""
    tree = fetch(tree_url(repo, commit))
    entries = tree.get("tree")
    if tree.get("truncated") or not isinstance(entries, list):
        raise Refused(f"the tree of {commit} could not be read in full.")
    matching = [e for e in entries if e.get("path") == config_only.CONFIG]
    if len(matching) != 1 or matching[0].get("type") != "blob":
        raise Refused(f"{commit} has no single {config_only.CONFIG} blob.")
    if matching[0].get("mode") != config_only.REGULAR_FILE:
        raise Refused(
            f"{config_only.CONFIG} at {commit} has mode {matching[0].get('mode')!r}, "
            f"not {config_only.REGULAR_FILE}."
        )
    blob = fetch(blob_url(repo, matching[0].get("sha")))
    if blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
        raise Refused(f"the {config_only.CONFIG} blob at {commit} is not base64 content.")
    try:
        return base64.b64decode(blob["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused(f"the {config_only.CONFIG} blob at {commit} did not decode: {exc}") from exc


def patch_lines(patch):
    """The `-` and `+` lines of a compare patch, hunk headers excluded."""
    removed = [line[1:] for line in patch.split("\n") if line.startswith("-")]
    added = [line[1:] for line in patch.split("\n") if line.startswith("+")]
    return removed, added


class _UniqueKeyLoader(yaml.SafeLoader):
    """`SafeLoader` that refuses a mapping with the same key twice."""


def _unique_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
            seen.add(key)
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None, None, f"an unhashable key {key!r}", key_node.start_mark
            ) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None, None, f"the key {key!r} appears twice", key_node.start_mark
            )
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)

ACTIONS_URL = "https://github.com/yadgarhq/actions"


def parsed_config(text, commit):
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise Refused(f"{config_only.CONFIG} at {commit} is not YAML this gate accepts: {exc}") from None


def parsed_agrees(old_text, new_text, before, after):
    """Refuse unless the two files parse equal apart from the actions entry's `rev`."""
    old = parsed_config(old_text, "the merge base")
    new = parsed_config(new_text, "the head")
    repos = old.get("repos") if isinstance(old, dict) else None
    if not isinstance(repos, list):
        raise Refused(f"{config_only.CONFIG} at the merge base has no `repos` list.")
    hits = [e for e in repos if isinstance(e, dict) and e.get("repo") == ACTIONS_URL]
    if len(hits) != 1:
        raise Refused(f"{config_only.CONFIG} has {len(hits)} yadgarhq/actions entries, not one.")
    if hits[0].get("rev") != before:
        raise Refused(
            f"the yadgarhq/actions entry's parsed rev is {hits[0].get('rev')!r}, "
            f"not the {before} the changed line shows."
        )
    hits[0]["rev"] = after
    if old != new:
        raise Refused(
            f"{config_only.CONFIG} parses differently at the head in more than the "
            f"yadgarhq/actions entry's rev."
        )


def branch_url(repo, base_ref):
    return (
        f"https://api.github.com/repos/{repo}/git/ref/heads/"
        f"{urllib.parse.quote(base_ref, safe='/')}"
    )


def earn_config_only(fetch, repo, base_ref, sha, outputs):
    """Re-derive `config_only` from the API, or refuse. Returns nothing."""
    if not repo or not base_ref or not config_only.SHA.fullmatch(sha or ""):
        raise Refused(
            "a config-only change was reported, but the repository, the base "
            "branch or the head commit was not named."
        )
    # THE BASE HAS NOT MOVED since `detect` merged onto it, so both read one base.
    tip = (fetch(branch_url(repo, base_ref)).get("object") or {}).get("sha")
    if not config_only.SHA.fullmatch(str(tip or "")) or tip != outputs.get("config_only_base"):
        raise Refused(
            f"`{base_ref}` is at {tip!r} and `detect` merged onto "
            f"{outputs.get('config_only_base')!r}, so the two did not read the same "
            f"base. Re-run the checks."
        )
    changed, merge_base = compared_file(fetch(compare_url(repo, base_ref, sha)))
    old_text, new_text = config_at(fetch, repo, merge_base), config_at(fetch, repo, sha)
    try:
        before, after = config_only.classify(old_text, new_text)
    except config_only.NotConfigOnly as exc:
        raise Refused(f"`detect` reported a config-only change and the API says otherwise: {exc}.") from None
    parsed_agrees(old_text, new_text, before, after)
    if patch_lines(changed["patch"]) != ([f"    rev: {before}"], [f"    rev: {after}"]):
        raise Refused("the compare patch does not show exactly the rev line that changed.")
    claimed = (outputs.get("config_only_from"), outputs.get("config_only_to"))
    if claimed != (before, after):
        raise Refused(
            f"`detect` reported {claimed[0]} -> {claimed[1]}, and the API shows "
            f"{before} -> {after}."
        )


def make_config_only_resolver(needs, repo, base_ref, sha, token, fetch=None):
    """Resolve `config_only`, and on `true` re-derive it before handing it back.

    ON ANY OTHER VALUE NOTHING IS READ. `false` and the empty string (a push,
    where `detect` is skipped) justify no skip, so they are returned as they are.
    """
    if fetch is None:

        def fetch(url):
            return fetch_check_runs(url, token, permission="contents")

    cache = {}

    def resolve(expr):
        outputs = (needs.get("detect") or {}).get("outputs") or {}
        value = str(outputs.get("config_only", ""))
        if value != "true":
            return value
        if "earned" not in cache:
            earn_config_only(fetch, repo, base_ref, sha, outputs)
            cache["earned"] = True
        return value

    return resolve


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
    # HOW THE SETS COULD COME APART, since they are produced by two different
    # reads of the same file. `toJSON(needs)` is built by the workflow version
    # the CALLER resolved when the run started; the conditions are read from the
    # checkout the gate step takes minutes later. Until ledger 1756 that
    # checkout was `ref: main`, so the two disagreed across a merge to `main`
    # inside that window, and would have diverged for any consumer pinning a
    # tag. Since ledger 1756 (ADR-0981) the checkout reads the run's own commit,
    # `job.workflow_sha`, so for every run dispatched after that change both
    # sets come from one commit, whether the caller pins `@main` or a tag. Both
    # directions still refuse here, as defence in depth: a gate that reads two
    # inputs must not trust that they agree, and a disagreement must refuse
    # rather than one direction passing quietly.
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

    # LEDGER 685, REGISTERED PER RUN. The resolver closes over this run's
    # commit, this run's id and this run's token, so there is no way for one
    # run's answer to be read by another — and `_DEFERRED` never holds a
    # resolver built from facts that have gone stale.
    #
    # `REQUIRED_CHECK` IS AN OVERRIDE RATHER THAN A SETTING, and it is SAFE BY
    # DIRECTION rather than by who can write it. In the eighteen consumers the
    # value comes from `ci-pr.yaml@main`, which a pull request under review
    # cannot edit for the run that gates it. In THIS repository it can: the
    # caller uses a local path, exactly so a change to a shared workflow is
    # gated by the version in the pull request. What that buys an author is
    # nothing, because every wrong value fails CLOSED — a check name with no
    # check runs on this commit produces no standing verdict, and no standing
    # verdict refuses. There is no value of this variable that turns a red
    # commit green.
    #
    # YAADGAAR 440: the sentinels come from the same conditions the verdict
    # reads, so a job added to the short-circuit is a sentinel with no second
    # edit, and the wait bound is the only new input.
    conditions = load_conditions(workflow, gate_job)
    check_name = env.get("REQUIRED_CHECK") or DEFAULT_REQUIRED_CHECK
    register_deferred_reference(
        TEXT_EDIT_REF,
        make_text_edit_resolver(
            needs=needs,
            repo=env.get("REPO") or "",
            sha=env.get("HEAD_SHA") or "",
            run_id=env.get("RUN_ID") or "",
            check_name=check_name,
            token=env.get("GH_TOKEN") or "",
            sentinels=text_edit_sentinels(conditions, check_name),
            budget=wait_budget(),
        ),
    )

    # YAADGAAR 442. Earned from the API rather than read off `detect`; see
    # `make_config_only_resolver`.
    register_deferred_reference(
        CONFIG_ONLY_REF,
        make_config_only_resolver(
            needs=needs,
            repo=env.get("REPO") or "",
            base_ref=env.get("BASE_REF") or "",
            sha=env.get("HEAD_SHA") or "",
            token=env.get("GH_TOKEN") or "",
        ),
    )

    resolve = make_resolver(
        event=event,
        author_type=env.get("AUTHOR_TYPE") or "",
        repository=env.get("REPO") or "",
        needs=needs,
    )
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
