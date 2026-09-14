#!/usr/bin/env python3
"""Ledger 511 — every module chart carries the four baseline fields, with the rule rather than the number.

TWO PUBLISHED HOOKS LIVE IN THIS FILE, SPLIT BY ADR-0685 (ledger 911). `--part
chart-baseline` asserts the three fields that are properties of the chart's own pod
spec; `--part chart-network-policy` asserts the fourth, that a template declares
`kind: NetworkPolicy`. The field set did not change and neither did any assertion —
what changed is that a repository adopts each half separately. `Part` carries the
reasoning, the floors and the sentence each half prints about its own scope; read it
before changing either.

`docs/plans/chart-hardening.md` asked whether ledger 511 is one shared chart
decision or N local fixes, priced a shared chart library, and refused it: a new
repository, a publish pipeline, a `dependencies:` stanza and a version bump per
chart per change, against roughly twelve duplicated lines in six charts. Its
answer was "a shared pre-commit gate, NOT a shared chart library", and this file
is that gate. The library would also add a THIRD clock to a delivery path that
already lands a chart from the module repo at HEAD and an image by digest from
`argocd/versions/` — the plan's sharpest argument and the one a reader is most
likely to skip.

WHY PUBLISHED RATHER THAN COPIED (ledger 648). The ADR-0569 gate lived as five
md5-identical `repo: local` copies, and those five copies only ever checked the
five repositories that already passed; publishing it let one sweep reach the
seven that never had it, and that sweep found real violations. A published hook
is one artefact. A copied one is drift waiting to happen.

THE FOUR FIELDS ARE THE PLAN'S, AND NOTHING ELSE IS HERE — across both halves. The plan adopted
`terminationGracePeriodSeconds`, `topologySpreadConstraints`, a dedicated
ServiceAccount and a NetworkPolicy; it DECLINED `livenessProbe`, `startupProbe`,
`preStop`, `strategy`, `minReadySeconds` and `affinity`, each with its own
argument. A gate that checked one of the six would enforce a decision the plan
refused, so the field set is closed and adding to it needs the plan to change
first.

THE GATE READS TWO FILES PER CHART, and that is forced rather than chosen. No
module template's NON-COMMENT line sets `35` or `ScheduleAnyway` as a literal:
every value arrives through `.Values.*`, so the template carries the KEY and
`values.yaml` carries the NUMBER, and an assertion against either alone is
fail-open. A template setting
`terminationGracePeriodSeconds: {{ . }}` under `{{- with .Values.terminationGracePeriodSeconds }}`
renders NOTHING when the value is absent, `0` or `false` — the template's own
comment says so — and a chart can keep every `topologySpreadConstraints` line and
flip `topologySpread.enabled: false` to render no constraint at all. So each
assertion is a pair, and every resolution has THREE outcomes rather than two:
resolved-and-good, resolved-and-bad, and UNRESOLVABLE, which is red. A gate whose
parse failure is spelled the same way as a pass is the second failure mode this
estate keeps shipping.

LEDGER 897 CLOSED NINE FALSE REFUSALS AND TWO FAIL-OPENS in this file, and the
detail of each sits beside the code that fixes it — `GUARD_ON` and the four guard
states, `Guard`, `unwrap_action`, `prestop_sleep`, and `declared_kinds`. What is
worth stating here is the SHAPE they shared, because it is one defect wearing nine
faces: this gate modelled a narrower Helm than the estate writes, and spelled
"I cannot read this" the same way it spelled "the chart is wrong".

`values.raw()` collapsed the `NOT_MODELLED` sentinel to the same `None` absence
returns, so a guard check could not tell a mapping, an anchor, an alias, a
sequence or a template expression from a deleted key, and printed "values.yaml
leaves it off" about keys `values.yaml` plainly sets. `{{- with .Values.X }}` over
a MAPPING — the commonest Helm guard idiom — was invisible for the same reason,
since `scan_values` recorded only scalars. `resolve` accepted `{{ .Values.x }}`
verbatim and refused the estate's own endorsed `required "…" .Values.x` (50 uses)
and `| quote` (113). `{{- else }}` was neither an opener nor an `end`, so the
if-condition's guard propagated into the arm that actually renders, invisibly to
`template.balanced`. And a guard whose condition this gate could not read was
DROPPED rather than refused, which reads every key inside it as unconditional.

A FALSE REFUSAL IS THE WORST FAILURE A GATE HAS, and not as a matter of taste:
it reddens honest work and gets the gate switched off, which bypasses the whole
gate rather than the one case (ADR-0679's `alternatives` rejects the opposite
view by name). This file's own first revision reported "sets no `maxSkew`" against
all seven correct charts. So every fix here is proved by a PAIR on one tree — the
legitimate shape passes, and a broken variant of that same shape still refuses —
and `scripts/tests/test_chart_baseline.py` carries both halves of each.

THE GRACE PERIOD IS ASSERTED AS A RULE, NEVER AS THE LITERAL 35 (ADR-0601,
ADR-0602). All seven module charts ship 35 today, and `chart-hardening.md` still
recommends 30 — the plan is stale on this point and the ADR is not. The rule is
`terminationGracePeriodSeconds >= DRAIN_BUDGET + EXIT_MARGIN + the preStop sleep`:
25 + 5 + 5 = 35, which is what those charts ship, derived rather than copied.
ADR-0602 names the only direction that breaks the bound — a chart going UNDER —
so a chart raising the grace period is never a finding, and a chart raising its
preStop sleep without raising the grace period is. A flat `>= 30` would pass a
chart with a 5s preStop and a 32s grace, leaving 27s for a 25s drain plus a 5s
exit: under the bound, and green. Pinning 35 would redden the first chart that
legitimately lengthens its sleep.

THE TWO NUMBERS ARE LITERALS HERE, deliberately (ADR-0573, and ADR-0601's
`alternatives` rejects the alternative by name). `DRAIN_BUDGET` is
`Duration::from_secs(25)` in `yadgarhq/lifecycle`'s `src/drain.rs`, and the 5
seconds are the slack that constant's docstring reserves to log and exit. This
gate does NOT read that repository: the estate has exactly one mechanism for
asserting a chart against a constant, `include_str!` in the owning repository's
own `tests/assembly.rs`, and it is repo-local by construction. A pre-commit hook
performing a network read to check a bound would be worse than the bound being
written twice.

`ScheduleAnyway` IS REQUIRED, AND `DoNotSchedule` IS THE FINDING. Measured on the
reference cluster: three kind nodes, the control plane tainted
`node-role.kubernetes.io/control-plane:NoSchedule`, so two schedulable nodes for
two replicas. `maxSurge: 1` puts three pods on those two nodes for the length of
a roll, a skew of 2 against `maxSkew: 1`; a required constraint leaves the surge
pod Pending and, with `maxUnavailable: 0`, the rollout neither proceeds nor
fails. It hangs, with no error — D68's failure mode wearing another field's name.
A hard spread constraint on a two-node cluster causes the outage it exists to
prevent, so the strength is part of the baseline and not a preference.

THE TWO `enabled` KEYS ARE NOT ONE RULE, and a reader will otherwise read them as
one. `topologySpread.enabled` MUST be true: a spread constraint needs no CRD,
installs on a bare cluster, and has nothing to fail, so D80's fail-closed arm
does not reach it. `networkPolicy.enabled` may be — and in all six charts that
have one, is — FALSE: a module chart must stay installable on a bare cluster, so
whether the policy is switched ON is the reference deployment's business and not
the chart's. This gate therefore demands that the TEMPLATE and the KEYS exist,
never that the policy is enabled. Demanding `true` would refuse the design D80
chose.

WHAT IS KEYED ON A FACT RATHER THAN A FILENAME — now including the pod spec. The
ServiceAccount and NetworkPolicy checks ask whether any file under `templates/`
DECLARES `kind: ServiceAccount` / `kind: NetworkPolicy`, not whether a file of a
particular name exists. A rename is then not an exemption, which is the property
`certificate_usages.py` bought by deleting its issuer allowlist. The DEPLOYMENT
was the exception and is no longer (ledger 897): it was read from
`templates/deployment.yaml` while the kind scan covered all of `templates/`, so a
chart whose workload sat elsewhere was judged against whatever held that path.
Two templates declaring `kind: Deployment` is the case that is refused now, with
one accurate message rather than four false ones.

`gateway` HAS NO NetworkPolicy IN ITS CHART, AND THAT IS WHY THIS GATE IS TWO
(ADR-0685, ledger 911). Its ingress source is the Envoy Gateway data plane in
`envoy-gateway-system`, selected by `gateway.envoyproxy.io/*` labels, and a module
chart validated against one ingress implementation is D80's first bullet — "a service
chart that reads, requires, or is validated against a resource owned by one ingress
implementation". So that policy ships in `yadgarhq/deploy` as
`infra/network-policies/gateway-ingress.yaml`, and `gateway`'s chart is CORRECT in not
carrying one. ADR-0584 forbids adopting a gate in a repository it hard-fails, so the
single four-field gate cost `gateway` the three assertions it does satisfy in order to
report the one it never will. Splitting the gate is what returns them, with no
exemption anywhere — see `Part`.

MEASURED 2026-09-14 against `origin/main` of the seven module repositories:
`chart-baseline` passes all SEVEN at 29 assertions each, `chart-network-policy` passes
six at 1 and refuses `gateway`. 29 + 1 = 30 is what the single gate evaluated per chart
before the split, on every one of the seven, which is the measurement that says the
fourth assertion MOVED rather than being dropped (ADR-0645). `project` declared no
ServiceAccount when this gate was published and now does (ledger 894). Dated
observations, not this file's standing claim about anybody's tree (ledger 847).

ADOPTION IS PER REPOSITORY AND IS NOT AUTOMATIC (ADR-0577, ADR-0607), and
ADR-0584 forbids adopting a gate in a repository it hard-fails. Two consequences
worth stating. A library repository with no chart — `dial`, `lifecycle` — does not
reference this hook: the gate REFUSES a tree carrying no `Chart.yaml` rather than
passing it, the way `boot_reads_watched.py` refuses a repository with no
`src/rotate.rs`, because a tree with nothing to judge must not report a green
tick. And `yadgarhq/actions` does not reference it either — this repository holds
no chart — so what gates this file on every commit is `pytest-scripts`, not the
hook itself.

THREE WAYS TO GO BLIND, EACH CHECKED IN EACH HALF, and no one check sees all of them.
Splitting a gate multiplies this risk rather than moving it: a half able to report
success having examined nothing reintroduces ledger 715's class on whichever side is
weaker, so all three are evaluated per part against that part's own numbers and the
suite fires each of the three in BOTH halves. (Ledger 715's
`MINIMUM_SITES=2`, and `certificate_usages.py`'s pair, extended by one). `MINIMUM_CHARTS`
catches the walk finding nothing at all — a `chart/` to `charts/` rename, a
restructured repository. The assertions floor catches every chart turning into
something this gate does not judge AT ONCE, which is what a broken
`kind: Deployment` detection used to look like before this file's own regex
carried that defect: the chart count stays 7 while the number of assertions
evaluated drops to 0. Neither catches a THIRD way: some charts judged, others not,
in the same tree — the total assertions stay well above the floor because the
judged charts carry it, and the tree reports "N charts, N-1 judged" with exit 0.
So a chart's `judged` flag is checked against the chart count directly: every
discovered chart must be judged, or the run is red. All three are floors rather
than the current counts, because adding a module is a legitimate change and must
not redden. All three counts are in the running half's own success line.

`language: script` AND STDLIB ONLY, the reason eight hooks in
`.pre-commit-hooks.yaml` already carry: `language: python` makes pre-commit
pip-install this repository, which is not a package, and the failure surfaces in
the CONSUMING repository where nothing explains it. So the YAML scan is
hand-written, and `scripts/tests/test_chart_baseline.py` asserts it against
PyYAML over a corpus carrying the traps the real files contain — the differential
test `certificate_usages.py` uses for the same reason. A hand scan that quietly
disagrees with YAML is a gate that judges something other than the chart.

WHAT THIS DOES NOT CHECK, stated so nobody mistakes it for covered. Whether the
keys sit on the right object: the templates are Go templates, not YAML, so this
scan requires the file to declare `kind: Deployment` and then matches keys at any
indentation within it. `helm lint --strict` and `helm template` — the `helm-lint`
hook beside this one — are what judge the rendered shape. It also does not check
that the NetworkPolicy admits the right callers; that is per-module by
construction and there is no shared shape to extract.

RUN IT AGAINST A TREE THIS REPOSITORY DOES NOT CONTAIN with `--root`, the way
`no_test_skips.py` is swept across the estate:

    git clone --depth 1 git@github.com:yadgarhq/<repo>.git <tree>
    python3 hooks/chart_baseline.py --root <tree> --part chart-baseline --report
    python3 hooks/chart_baseline.py --root <tree> --part chart-network-policy --report

Each half is its own run, because `--part` has no default.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# `yadgarhq/lifecycle`'s `src/drain.rs`: `DRAIN_BUDGET` is
# `Duration::from_secs(25)` and its docstring reserves five seconds to log and
# exit. Literals, not a cross-repository read — see the module docstring.
DRAIN_BUDGET_SECONDS = 25
EXIT_MARGIN_SECONDS = 5

REQUIRED_WHEN_UNSATISFIABLE = "ScheduleAnyway"

# Floors, not current counts, and EACH HALF CARRIES ITS OWN (ledger 911). They are
# both 1 in both halves today and that is a coincidence of arithmetic rather than a
# shared constant: `chart-baseline` evaluates 29 assertions per conforming chart and
# `chart-network-policy` exactly 1, so a shared number would have to be the smaller of
# the two and would stop bounding the larger. A floor is what a half must clear to be
# allowed to report success, so it belongs to the half. See `Part` and the module
# docstring.
MINIMUM_CHARTS = 1
MINIMUM_ASSERTIONS = 1
MINIMUM_POLICY_CHARTS = 1
MINIMUM_POLICY_ASSERTIONS = 1

# Directories a chart never lives in. `charts/` is helm's vendored-subchart
# directory: a dependency's copy is not this repository's chart to judge.
PRUNED_DIRECTORIES = frozenset(
    {".git", "charts", "node_modules", "target", ".venv", "vendor"}
)

# A scalar this scanner will not claim to have read. Distinct from absence, so a
# lookup can tell "no such key" from "a key whose value I do not model".
NOT_MODELLED = object()

# The absence sentinel. `None` cannot serve: it is also what `raw()` returns for a
# key it declines to model, and collapsing the two is the ledger 897 defect below.
ABSENT = object()

# THE FOUR STATES A GUARD CAN BE IN (ledger 897), and the reason they are four.
# The shipped gate asked `is_truthy(values.raw(guard))` and got False for all of
# them, so an anchor (`&grace 35`), an alias (`*grace`), a sequence, an unquoted
# template expression in `values.yaml` and a genuinely deleted key ALL printed
# "values.yaml leaves it off" — accurate for exactly one of the five. The exit code
# was right every time; the message was false four times out of five, which is the
# failure mode this file's own docstring warns about in the opposite direction.
#
# THE MECHANISM, because the sentinel above was added for precisely this and never
# reached it: `Values.raw()` returns None for a NOT_MODELLED entry, which is the
# same None it returns for a key that is not there. Every caller compared against
# None, so the distinction the sentinel exists to carry was discarded one line
# after it was read. `judge_grace`'s dedicated "cannot be resolved" branch was
# therefore unreachable for every real chart — the guard fails first and returns.
#
# A gate must distinguish "the path is absent" from "the path is not a scalar I
# model". Both are red; only one of them is a statement about the chart.
GUARD_ON = "on"
GUARD_OFF = "off"
GUARD_ABSENT = "absent"
GUARD_UNMODELLED = "unmodelled"

TRUE_WORDS = frozenset({"true", "yes", "on"})
FALSE_WORDS = frozenset({"false", "no", "off", "null", "~", "0", '""', "''"})

KEY_LINE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[A-Za-z0-9_.\-]+):(?P<rest>.*)$")
# `certificate_usages.py`'s `CERTIFICATE_KIND` carries the comment recording what
# anchoring this on `$` alone cost, three times, one level further down each time
# (ledger 720, actions#63). This gate shipped the same defect on its own first
# publication: `kind: NetworkPolicy  # one ingress policy` and
# `kind: "NetworkPolicy"` both went unrecognised (a false refusal), and worse,
# `kind: Deployment  # the workload` went unrecognised too — which is a chart this
# gate never judges, silently, since the assertions floor cannot see one chart in
# a many-chart tree going dark while the rest still pass. Tolerating leading
# space, an optional quote and a trailing comment closes all three.
KIND_LINE = re.compile(
    r"^\s*kind:\s*[\"']?(?P<kind>[A-Za-z][A-Za-z0-9]*)[\"']?\s*(?:#.*)?$"
)
TEMPLATE_ACTION = re.compile(r"\{\{-?\s*(?P<word>[a-z]+)\b(?P<arg>[^}]*?)-?\}\}")
VALUES_REFERENCE = re.compile(r"^\.Values\.(?P<path>[A-Za-z0-9_.]+)$")
SEQUENCE_ITEM = re.compile(r"^(?P<indent>[ ]*)-[ ]")
TEMPLATE_ACTION_BODY = re.compile(r"^\{\{-?\s*(?P<body>.*?)\s*-?\}\}$", re.DOTALL)
# An operand rooted at the values tree, with or without the `$` that reaches the
# root context from inside a `with` block.
ROOTED_OPERAND = re.compile(r"^\$?\.Values\.(?P<path>[A-Za-z0-9_.]+)$")
# An operand relative to the `.` a `with` block bound: `{{ .maxSkew }}`.
RELATIVE_OPERAND = re.compile(r"^\.(?P<path>[A-Za-z0-9_][A-Za-z0-9_.]*)$")
# Go template roots that are NOT the values tree, so a relative-looking operand
# beginning with one of them is never re-rooted onto a `with` path.
BUILT_IN_ROOTS = frozenset(
    {"Values", "Chart", "Release", "Files", "Capabilities", "Template", "Subcharts"}
)
BLOCK_OPENERS = frozenset({"if", "with", "range", "define", "block"})

# THE WRAPPERS THIS ESTATE ACTUALLY WRITES, and nothing wider (ledger 897 item 3).
# Counted on `origin/main` of the seven module repositories on 2026-09-14, over each
# `chart/templates/deployment.yaml`:
#
#     required "…"   iam 12, gateway 12, task 7, project 7, iam-db/task-db/project-db 4 each  = 50
#     | quote        iam 15, gateway 34, task 8, project 8, iam-db/task-db/project-db 11 each = 113
#     | default      0 — and iam's chart carries two COMMENTS arguing against it by name
#                    ("`required` rather than a bare lookup or a `| default 250`"),
#                    so modelling it would widen the gate past the estate's own
#                    decision. An unmodelled wrapper is REFUSED, which is red rather
#                    than green, so leaving it out cannot hide a bad chart.
#     | nindent      14, all of them on `toYaml` blocks over `resources`/
#                    `rollingUpdate` — never on a baseline key, and not a scalar.
#
# The shipped resolver accepted `{{ .Values.x }}` or `{{ . }}` VERBATIM, so every one
# of those 163 endorsed uses yielded "an expression this gate does not model". None
# of them sits on a baseline key today, which is the only reason nothing was red.
QUOTE_PIPE = re.compile(r"\|\s*quote\s*$")
REQUIRED_CALL = re.compile(r"^required\s+(?=[\"'])")


# ---------------------------------------------------------------- values.yaml


def end_of_quoted_scalar(text: str, start: int) -> int:
    """The index of the quote closing the string opened at `start`, or -1.

    THE CANONICAL FORM IN THIS FILE, and the only one (ADR-0679: a predicate over a
    shared syntax is copied from its hardened site and cited, never re-derived).
    It was the loop inside `strip_inline_comment`, which is now one of its two
    callers; the other is `unwrap_action`, which must skip the message argument of
    `required "…" .Values.x` without being fooled by a `.` or a `}}` inside it.
    A second expression for this job is a finding on its own.
    """
    quote = text[start]
    end = text.find(quote, start + 1)
    while end != -1 and text[end - 1] == "\\":
        end = text.find(quote, end + 1)
    return end


def strip_inline_comment(raw: str) -> str:
    """Drop a YAML inline comment, respecting a quoted scalar that contains `#`."""
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'":
        end = end_of_quoted_scalar(text, 0)
        return text if end == -1 else text[: end + 1]
    if text.startswith("#"):
        return ""
    cut = min(
        (i for i in (text.find(" #"), text.find("\t#")) if i != -1),
        default=-1,
    )
    return text.strip() if cut == -1 else text[:cut].strip()


@dataclass(frozen=True)
class Values:
    """Dotted-path scalars read out of a `values.yaml` by hand.

    `modelled` is False when the document carries a construct this scanner does
    not model, which makes EVERY lookup unresolvable rather than wrong.

    `mappings` and `nulls` carry what `scalars` structurally cannot: a key whose
    value is a nested block rather than a leaf. They exist for the guard check and
    for nothing else, so `scalars` stays exactly the set of scalar leaves the
    PyYAML differential in `scripts/tests/test_chart_baseline.py` compares against.
    """

    scalars: dict
    modelled: bool
    reason: str = ""
    # Paths whose value is a NON-EMPTY block mapping. `{{- with .Values.X }}` over
    # one of these is the commonest Helm guard idiom and the shipped gate could not
    # see it at all: `scan_values` recorded only scalars, so `topologySpread` had no
    # entry, `raw()` answered None, and a mapping `values.yaml` sets with four
    # sub-keys was reported as "left off". A non-empty map is TRUE in Go's template
    # truth, which is what makes `with` bind and the block render.
    mappings: frozenset = frozenset()
    # Paths written with a colon and nothing under them at all: YAML null, which Go
    # reads as false. Distinct from a mapping, and distinct from absence.
    nulls: frozenset = frozenset()

    def raw(self, path: str):
        """The raw scalar at `path`, or None when it is absent or not modelled.

        KEPT LOSSY ON PURPOSE, for the callers that want a scalar or nothing. A
        caller that must tell the two apart — every guard check — calls `truth()`.
        """
        if not self.modelled:
            return None
        found = self.scalars.get(path, None)
        return None if found is NOT_MODELLED else found

    def truth(self, path: str) -> str:
        """Which of the four guard states `path` is in.

        A scalar entry answers first, so a sequence or a flow collection recorded as
        NOT_MODELLED reads as unmodelled rather than as the empty mapping it is not.
        """
        if not self.modelled:
            return GUARD_UNMODELLED
        found = self.scalars.get(path, ABSENT)
        if found is NOT_MODELLED:
            return GUARD_UNMODELLED
        if found is not ABSENT:
            return GUARD_ON if is_truthy(found) else GUARD_OFF
        if path in self.mappings:
            return GUARD_ON
        if path in self.nulls:
            return GUARD_OFF
        return GUARD_ABSENT

    def present(self, path: str) -> bool:
        return self.modelled and path in self.scalars


def scan_values(text: str) -> Values:
    """Read a plain block-mapping `values.yaml` into dotted-path scalars."""
    scalars: dict = {}
    stack: list = []
    suppress_from = None
    seen_content = False
    # Every path written with a colon and no value on the line, and every path some
    # deeper entry proved to be a parent. A key in both is a non-empty mapping; a key
    # only in `opened` is YAML null.
    opened: set = set()
    parents: set = set()

    for line in text.splitlines():
        if not line.strip():
            continue
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        if body.startswith("#"):
            continue
        if body.startswith("---") or body.startswith("..."):
            if seen_content:
                return Values({}, False, "more than one YAML document")
            continue
        if body.startswith("%"):
            return Values({}, False, "a YAML directive")
        if suppress_from is not None:
            if indent >= suppress_from:
                continue
            suppress_from = None
        seen_content = True

        if body.startswith("-"):
            # A sequence item. Its parent is a sequence rather than a scalar, and
            # this scanner does not descend into one.
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                path = dotted(stack)
                scalars[path] = NOT_MODELLED
                parents.update(ancestors(path))
            suppress_from = indent
            continue

        match = KEY_LINE.match(line)
        if match is None:
            return Values({}, False, f"a line this scanner does not model: {line!r}")
        rest = match.group("rest")
        if rest and not rest.startswith((" ", "\t")):
            return Values({}, False, f"a key with no space after the colon: {line!r}")

        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = match.group("key")
        value = strip_inline_comment(rest)
        path = dotted(stack + [(indent, key)])

        if value == "":
            stack.append((indent, key))
            opened.add(path)
            continue
        if value[0] in "{[|>&*!":
            # A flow collection, a block scalar, an anchor, an alias or a tag.
            scalars[path] = NOT_MODELLED
            parents.update(ancestors(path))
            suppress_from = indent + 1
            continue
        scalars[path] = value
        parents.update(ancestors(path))

    # A PATH `scalars` ALREADY HOLDS BELONGS TO NEITHER SET. A block sequence is
    # written `clients:` and then `- gateway`, so `clients` is opened like a mapping
    # and the sequence branch above records it as NOT_MODELLED; it has no child entry,
    # so `opened - parents` would call it YAML null, which it is not. `truth()` reads
    # `scalars` first and so never asked, but a set holding a wrong member is a trap
    # for the next caller. Measured against PyYAML over the seven real `values.yaml`:
    # this line is the difference between 2 charts disagreeing on `nulls` and 0.
    reached = set(scalars)
    return Values(
        scalars,
        True,
        "",
        frozenset((opened & parents) - reached),
        frozenset((opened - parents) - reached),
    )


def dotted(stack) -> str:
    return ".".join(key for _, key in stack)


def ancestors(path: str):
    """Every proper prefix of a dotted path: `a.b.c` yields `a` and `a.b`."""
    parts = path.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def as_integer(raw):
    """`raw` as an int, or None. YAML's other integer spellings are not modelled."""
    if raw is None:
        return None
    text = raw.strip().strip("'\"")
    if re.fullmatch(r"[+-]?[0-9]+", text) is None:
        return None
    return int(text)


def as_boolean(raw):
    """`raw` as a bool, or None when it is neither a YAML true nor a YAML false."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def is_truthy(raw) -> bool:
    """Go template truth for a values scalar: absent, empty, `0` and false are off."""
    if raw is None:
        return False
    decided = as_boolean(raw)
    if decided is not None:
        return decided
    number = as_integer(raw)
    if number is not None:
        return number != 0
    return raw.strip() not in ("", "''", '""')


# -------------------------------------------------------- deployment template


@dataclass(frozen=True)
class Guard:
    """One enclosing `{{ if }}`/`{{ with }}`, and what this gate can say about it.

    `path` is the single `.Values` path the branch turns on. `negated` is True inside
    the `{{- else }}` arm, which renders when the condition is FALSE. `constant` is
    True or False for a condition with no values term at all (`{{- if true }}`).
    `modelled` is False for a condition this gate cannot evaluate.

    TWO LEDGER 897 DEFECTS LIVE IN THIS SHAPE, and both were invisible because the
    shipped gate reduced a frame to a bare path string.

    `{{- else }}` PROPAGATED THE IF-CONDITION'S GUARD TO THE ELSE ARM. `else` was
    neither a `BLOCK_OPENER` nor `end`, so it was ignored entirely: one push, one
    pop, balance holds, and `template.balanced` could not see the problem. A chart
    spelling `{{- if X }} …wrong… {{- else }} …correct… {{- end }}` was refused with
    the message for X being off, while the arm that actually renders is correct.
    All seven module charts already carry an inline `{{ if .Values.image.digest }}…
    {{ else }}…{{ end }}`, so the construct is live in the estate today.

    A CONDITION THE GATE COULD NOT READ WAS DROPPED RATHER THAN REFUSED, which is a
    FAIL-OPEN of item 5's class. `guards` was built as `tuple(path for _, path in
    stack if path)`, so a frame whose argument did not match `VALUES_REFERENCE`
    contributed nothing and the key inside it was judged as though it always
    rendered. `{{- if and A B }}`, `{{- if $clientCert }}` and `{{- range }}` all
    take that branch, and all three appear in the seven charts today (6, 8 and 0
    uses) — none over a baseline key, which is why nothing was red.
    """

    path: str = ""
    negated: bool = False
    modelled: bool = True
    constant: object = None


@dataclass(frozen=True)
class KeyOccurrence:
    """One `key:` line: where it is, what it carries, and what encloses it.

    `key` and `indent` were added by ledger 911 and are what make a key's POSITION
    legible. `indent` is the column the key sits at AFTER a leading sequence dash is
    rewritten to whitespace, so `- maxSkew: 1` reports the column `maxSkew` really
    occupies. Together with `Template.ordered` they are the whole of the positional
    model `prestop_sleep` needs — see `block_of`.
    """

    lineno: int
    raw_value: str
    guards: tuple
    dot: str
    key: str = ""
    indent: int = 0


@dataclass(frozen=True)
class Template:
    keys: dict
    balanced: bool
    # Every occurrence in LINE ORDER, which `keys` cannot give: it is grouped by key
    # name, and a question about what NESTS UNDER a key is a question about the lines
    # between that key and the next one at its own indentation or shallower. Ledger
    # 911; `block_of` is the only reader.
    ordered: tuple = ()

    # `first()` lived here and is DELETED: every caller went through it without
    # evaluating a guard, which is exactly how five of the seven baseline fields kept
    # both ledger 897 defects after the other two were fixed. `renders()` is the only
    # way in now, and it cannot be used without the guard check.


def read_condition(word: str, argument: str) -> Guard:
    """The guard a block opener imposes on every key inside it."""
    if word in ("define", "block", "range"):
        # A `define`/`block` body is a named template rather than this Deployment's
        # pod spec, and a `range` body renders zero-or-more times with `.` rebound
        # per item. Neither is a truth question, so neither is modelled.
        return Guard(modelled=False)
    text = argument.strip()
    negated = False
    if text.startswith("not "):
        negated = True
        text = text[4:].strip()
    if text in ("true", "false"):
        return Guard(negated=negated, constant=text == "true")
    reference = VALUES_REFERENCE.match(text)
    if reference is None:
        # `and`, `or`, `eq`, a `$variable`, a function call. A conjunction is not a
        # path, and this gate refuses rather than guessing which term is in force.
        return Guard(modelled=False)
    return Guard(path=reference.group("path"), negated=negated)


def guard_state(guard: Guard, values: Values) -> str:
    """Which of the four states a guard is in, honouring `not` and `{{- else }}`."""
    if not guard.modelled:
        return GUARD_UNMODELLED
    if guard.constant is not None:
        state = GUARD_ON if guard.constant else GUARD_OFF
    else:
        state = values.truth(guard.path)
    if not guard.negated:
        return state
    # Negation decides the two decided states and leaves the undecided one
    # undecided: `not` of "this gate cannot read the value" is still that. An ABSENT
    # key is falsey in Go, so its negation RENDERS.
    if state == GUARD_ON:
        return GUARD_OFF
    if state in (GUARD_OFF, GUARD_ABSENT):
        return GUARD_ON
    return GUARD_UNMODELLED


def scan_template(text: str) -> Template:
    """Read a Go-templated manifest: where each key sits, and under which guards.

    Template actions are tracked on EVERY line, comments included, because Go's
    text/template executes a `{{- if }}` inside a YAML comment exactly as it
    executes one outside it. Keys are read from non-comment lines only.

    `declares` and `references` used to be collected here and are GONE. `declares`
    lost its only consumer to item 6, which now selects the file BY its declaration
    and made the assertion over it unreachable; `references` was the name-matching
    the item 5 fail-open was built on, and `prestop_sleep` reads the handler's own
    structure instead. Neither is kept as an unread field.
    """
    keys: dict = {}
    ordered: list = []
    stack: list = []
    balanced = True

    for lineno, line in enumerate(text.splitlines(), 1):
        for action in TEMPLATE_ACTION.finditer(line):
            word = action.group("word")
            if word in BLOCK_OPENERS:
                stack.append((word, read_condition(word, action.group("arg"))))
            elif word == "else":
                # NEITHER A PUSH NOR A POP — an if/else/end is one opener and one
                # `end`, so balance must stay exactly as it was. This REPLACES the
                # top frame; pushing one here would unbalance every chart in the
                # estate, all seven of which carry an inline `{{ else }}` today.
                if not stack:
                    balanced = False
                    continue
                opener, guard = stack[-1]
                if action.group("arg").strip():
                    # `{{- else if X }}` renders when the first condition is false
                    # AND X is true. That is a conjunction, which is not a path.
                    stack[-1] = (opener, Guard(modelled=False))
                else:
                    # The else arm of a `{{ with }}` does NOT rebind `.`, so the
                    # frame stops supplying a dot as well as flipping polarity.
                    stack[-1] = (
                        "if",
                        Guard(
                            path=guard.path,
                            negated=not guard.negated,
                            modelled=guard.modelled,
                            constant=guard.constant,
                        ),
                    )
            elif word == "end":
                if stack:
                    stack.pop()
                else:
                    balanced = False

        body = line.strip()
        if body.startswith("#"):
            continue

        # `- maxSkew: 1` is a key on a sequence item, and the dash is the only
        # thing standing between it and the mapping-key pattern. Rewriting the
        # dash to whitespace keeps the column the key really sits at, so a key
        # inside a list is judged rather than silently skipped — which is what
        # this gate did to every chart's `maxSkew` in its first revision.
        match = KEY_LINE.match(SEQUENCE_ITEM.sub(r"\g<indent>  ", line))
        if match is None:
            continue
        rest = match.group("rest")
        if rest and not rest.startswith((" ", "\t")):
            continue
        dot = ""
        for word, guard in reversed(stack):
            if word == "with":
                dot = guard.path
                break
        # EVERY frame, including the ones this gate cannot read. Dropping those was
        # the fail-open recorded on `Guard` above.
        guards = tuple(guard for _, guard in stack)
        occurrence = KeyOccurrence(
            lineno,
            rest.strip(),
            guards,
            dot,
            match.group("key"),
            len(match.group("indent")),
        )
        keys.setdefault(match.group("key"), []).append(occurrence)
        ordered.append(occurrence)

    return Template(keys, balanced and not stack, tuple(ordered))


def unwrap_action(text: str):
    """The operand inside a `{{ … }}`, wrappers removed, or None when unmodelled.

    Only the wrappers the seven module charts actually write — see the counts beside
    `QUOTE_PIPE` above. `required "…" X` and a trailing `| quote` both render X
    itself, so the value a chart ships is legible through them; anything else returns
    None and the caller refuses.
    """
    action = TEMPLATE_ACTION_BODY.match(text)
    if action is None:
        return None
    body = action.group("body").strip()
    while True:
        stripped = QUOTE_PIPE.sub("", body).strip()
        if stripped == body:
            break
        body = stripped
    if REQUIRED_CALL.match(body):
        argument = body[len(REQUIRED_CALL.match(body).group(0)):]
        # Skip the message. It is prose holding dots, semicolons and the word
        # `.Values`, so it must be SKIPPED as a quoted scalar rather than pattern-
        # matched — `end_of_quoted_scalar` is the file's one expression for that.
        end = end_of_quoted_scalar(argument, 0)
        if end == -1:
            return None
        body = argument[end + 1:].strip()
    if not body or "|" in body or "(" in body or " " in body:
        # A pipeline or a call this gate has not been taught. Refused, not guessed.
        return None
    return body


def resolve(occurrence: KeyOccurrence, values: Values):
    """The value a key renders to: ('ok', text) or ('unresolvable', why).

    Three outcomes collapse to two names on purpose: a literal in the template
    and a scalar reached through `.Values` are both resolved, and everything else
    — an expression this scan does not model, a `.Values` path absent from
    `values.yaml`, a value that is not a plain scalar — is unresolvable and red.
    """
    text = strip_inline_comment(occurrence.raw_value)
    if text == "":
        return "unresolvable", "the key carries no value"
    if "{{" not in text:
        return "ok", text
    operand = unwrap_action(text)
    if operand is None:
        return "unresolvable", f"an expression this gate does not model: {text}"

    if operand == ".":
        if not occurrence.dot:
            return "unresolvable", "`{{ . }}` outside a `with .Values...` block"
        path = occurrence.dot
    else:
        rooted = ROOTED_OPERAND.match(operand)
        relative = RELATIVE_OPERAND.match(operand)
        if rooted is not None:
            path = rooted.group("path")
        elif relative is not None and relative.group("path").split(".")[0] not in (
            BUILT_IN_ROOTS
        ):
            # A reference relative to the `.` a `with` bound: inside
            # `{{- with .Values.topologySpread }}`, `{{ .maxSkew }}` IS
            # `.Values.topologySpread.maxSkew`, and it is the only spelling Helm
            # renders there — `.Values` is unreachable from that scope. Refusing it
            # made item 1's legitimate shape unprovable as well as unusable.
            if not occurrence.dot:
                return (
                    "unresolvable",
                    f"`{operand}` is relative to a `with` block and there is none",
                )
            path = f"{occurrence.dot}.{relative.group('path')}"
        else:
            return "unresolvable", f"an expression this gate does not model: {text}"

    if not values.modelled:
        return "unresolvable", f"values.yaml carries {values.reason}"
    if values.truth(path) == GUARD_UNMODELLED:
        return (
            "unresolvable",
            f"values.yaml sets `{path}` to a construct this gate does not model — an "
            f"anchor, an alias, a sequence, a flow collection or a template "
            f"expression. This is NOT a claim that `{path}` is unset",
        )
    raw = values.raw(path)
    if raw is None:
        return "unresolvable", f"values.yaml sets no plain scalar at `{path}`"
    return "ok", raw


# --------------------------------------------------------------- the verdicts


@dataclass(frozen=True)
class Finding:
    chart: str
    field: str
    message: str


@dataclass
class Verdict:
    chart: str
    judged: bool
    assertions: int = 0
    findings: list = None
    note: str = ""

    def __post_init__(self):
        if self.findings is None:
            self.findings = []


class Judge:
    """One chart's assertions. Every check increments the count it is judged by."""

    def __init__(self, verdict: Verdict, field: str):
        self.verdict = verdict
        self.field = field

    def assert_that(self, held: bool, message: str) -> bool:
        self.verdict.assertions += 1
        if not held:
            self.verdict.findings.append(
                Finding(self.verdict.chart, self.field, message)
            )
        return held


def guard_verdict(occurrences, values: Values):
    """Which occurrences of a key can render, and what blocks the others.

    Returns `(renderable, blocking)`. `renderable` holds the occurrences whose every
    enclosing guard is on; `blocking` is the `(occurrence, guard, state)` that
    decided against one of the rest, preferring an unmodelled guard because "this
    gate cannot tell" outranks "that branch is off".

    THIS IS WHAT MAKES `{{- else }}` LEGIBLE (ledger 897 item 4). An
    `{{- if X }}…{{- else }}…{{- end }}` pair is TWO occurrences of the same key with
    opposite polarity, so exactly one of them renders — and the shipped gate read
    only `template.first()`, judged the if-branch, found its guard false and reported
    that the field renders nothing while the else arm renders it correctly.
    """
    renderable = []
    blocking = None
    for occurrence in occurrences:
        blocked = None
        for guard in occurrence.guards:
            state = guard_state(guard, values)
            if state != GUARD_ON:
                blocked = (occurrence, guard, state)
                break
        if blocked is None:
            renderable.append(occurrence)
        elif blocking is None or blocked[2] == GUARD_UNMODELLED:
            blocking = blocked
    return renderable, blocking


def guard_message(blocking, field: str, where: str, tail: str = "") -> str:
    """Why a guard stops a field rendering. FOUR CAUSES, FOUR MESSAGES (ledger 897).

    The shipped gate printed "values.yaml leaves it off" for all four, which is a
    claim about the chart and was false for three of them. Every case is still red;
    what changes is that the gate now names the one it found.
    """
    if blocking is None:
        # Nothing blocked the field, so the caller's assertion holds and this string
        # is never printed. `assert_that` takes its message eagerly.
        return ""
    occurrence, guard, state = blocking
    named = f"`{guard.path}`" if guard.path else "its condition"
    prefix = f"{where}:{occurrence.lineno} renders `{field}` only when "
    if not guard.modelled:
        return (
            f"{where}:{occurrence.lineno} — `{field}` sits inside a "
            f"`{{{{ if }}}}`/`{{{{ with }}}}` condition this gate does not model, so "
            f"it cannot tell whether the field renders. A guard it cannot read is "
            f"REFUSED rather than ignored: treating it as always-true passes a chart "
            f"whose field renders nothing. Write the condition over a single "
            f"`.Values` path, or set the field unguarded.{tail}"
        )
    if state == GUARD_UNMODELLED:
        return (
            f"{prefix}{named} is on, and values.yaml sets {named} to a construct this "
            f"gate does not model — an anchor, an alias, a sequence, a flow "
            f"collection or a template expression. The chart is refused because this "
            f"gate cannot tell whether the field renders. It is NOT a claim that "
            f"{named} is unset.{tail}"
        )
    if state == GUARD_ABSENT:
        return (
            f"{prefix}{named} is on, and values.yaml sets no {named} at all — so the "
            f"field renders nothing.{tail}"
        )
    return (
        f"{prefix}{named} is on, and values.yaml sets {named} to a value Go reads as "
        f"false — so the field renders nothing.{tail}"
    )


def renders(
    judge: Judge,
    template: Template,
    values: Values,
    key: str,
    where: str,
    absent: str,
    tail: str = "",
):
    """The occurrences of `key` that RENDER, or `[]` when the chart is refused.

    Two assertions in the order they can fail: the key exists at all, and some
    occurrence of it survives its enclosing guards.

    EVERY BASELINE FIELD GOES THROUGH HERE, and that uniformity is the fix rather
    than an incidental tidy-up. The ledger 897 guard work first landed on
    `terminationGracePeriodSeconds` and the `topologySpreadConstraints` presence
    check only, because those two were the shapes the review named; the other five
    lookups kept `template.first()` and evaluated no guard at all. Both defects
    survived there, one in each direction, and both were measured on a scratch copy
    of `iam`'s chart:

      * FALSE REFUSAL — an `{{- if }}`/`{{- else }}` pair over
        `automountServiceAccountToken` reported "resolves to `true`, not `false`"
        about a chart that renders `false`, because `first()` returns the branch
        that does not render.
      * FAIL-OPEN, and the worse half — `automountServiceAccountToken`,
        `serviceAccountName` and `whenUnsatisfiable` behind a guard that is OFF all
        passed with exit 0 while rendering NOTHING. A `whenUnsatisfiable` that
        renders nothing takes the API default `DoNotSchedule`, which is the hang
        this gate exists to prevent, reported green.

    A per-field hand-rolled preamble is what allowed five of seven fields to differ
    from the two that were fixed. One function is what stops the next one.
    """
    occurrences = template.keys.get(key, [])
    if not judge.assert_that(bool(occurrences), absent):
        return []
    renderable, blocking = guard_verdict(occurrences, values)
    # An unmodelled guard ANYWHERE over this key means the gate cannot say whether
    # the field renders, even if another occurrence of it plainly does.
    undecided = blocking is not None and blocking[2] == GUARD_UNMODELLED
    if not judge.assert_that(
        bool(renderable) and not undecided,
        guard_message(blocking, key, where, tail),
    ):
        return []
    return renderable


def find_charts(root: Path):
    """Every chart directory under `root`, outermost first, vendored ones pruned."""
    found = []
    for path in sorted(root.rglob("Chart.yaml")):
        parts = set(path.relative_to(root).parts[:-1])
        if parts & PRUNED_DIRECTORIES:
            continue
        found.append(path.parent)
    return found


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def declared_kinds(templates: Path) -> dict:
    """Which template FILE under `templates/` declares each `kind:`, by kind.

    The paths are load-bearing rather than diagnostic (ledger 897 item 6): they are
    how `judge_chart` finds the pod spec. De-duplicated, because a file is one
    declarer however many times it spells the line.
    """
    by_kind: dict = {}
    if not templates.is_dir():
        return by_kind
    for path in sorted(templates.rglob("*")):
        if not path.is_file() or path.suffix not in (".yaml", ".yml"):
            continue
        for line in read(path).splitlines():
            body = line.strip()
            if body.startswith("#"):
                continue
            kind = KIND_LINE.match(body)
            if kind is not None:
                found = by_kind.setdefault(kind.group("kind"), [])
                if path not in found:
                    found.append(path)
    return by_kind


def block_of(template: Template, opener: KeyOccurrence):
    """The key occurrences NESTED UNDER `opener`, by YAML indentation.

    The block a mapping key opens runs from the line after it to the first later key
    at its own indentation or shallower. That is the whole positional model, and
    stating its size matters: the ledger 897 car filed the defect below rather than
    fixing it because it believed scoping a key to a block needed a positional model
    this scan did not have. It needed one `indent` field, one line-ordered tuple and
    this loop.

    WHAT IT DOES NOT MODEL, so nobody reads more into it. A flow mapping —
    `lifecycle: {preStop: {sleep: {seconds: 5}}}` — puts no inner key on a line of its
    own, so `preStop` never enters `template.keys` and `prestop_sleep` sees a chart
    with no handler, exactly as it did before ledger 911. No module chart writes one,
    and widening the hand scan to flow collections is a change to the scanner the
    PyYAML differential covers rather than a change to this function.
    """
    inside = []
    for occurrence in template.ordered:
        if occurrence.lineno <= opener.lineno:
            continue
        if occurrence.indent <= opener.indent:
            break
        inside.append(occurrence)
    return inside


def prestop_sleep(template: Template, values: Values):
    """The preStop sleep the grace floor must cover: ('ok', seconds) or ('no', why).

    THE ONE FAIL-OPEN THIS GATE SHIPPED (ledger 897 item 5), and the worst defect in
    it. The floor's sleep term was added only when the literal dotted path
    `preStopSleepSeconds` appeared in `template.references` AND in `values.yaml`, so
    a term DISCOVERED BY NAME silently became 0 whenever the name was anything else:
    a hardcoded `sleep: seconds: 5`, or a differently-named value. Measured against
    `iam`'s real chart with the value inlined as a literal 5 and the grace period cut
    to 32 — 25 assertions, 0 findings, exit 0, and a pod that gets 27s for a 25s
    drain plus a 5s exit. The floor dropped from 35 to 30 with nothing said.

    A DERIVED BOUND WHOSE TERMS ARE DISCOVERED BY NAME MUST REFUSE WHEN IT CANNOT
    FIND A TERM IT EXPECTS, never quietly compute a weaker bound. So the sleep is
    read from the TEMPLATE's own preStop handler — the structure that renders it —
    rather than from a values key this gate hopes is spelled a particular way:

      * no `preStop` key at all      -> 0, and the floor is ADR-0601's 25 + 5 = 30.
      * a `preStop` whose block does not render -> 0, for the same reason.
      * a `sleep: seconds:` that resolves      -> that number, whatever it is named.
      * anything else — an `exec` handler, a `seconds:` this gate cannot resolve —
        -> REFUSED. An `exec` handler's duration is not knowable from the chart, and
        assuming 0 is exactly the fail-open above.

    THE SLEEP IS FOUND BY POSITION, NOT BY KEY NAME (ledger 911), and that residual is
    the same class as the fail-open above rather than a tidy-up. Reading the handler's
    structure fixed WHICH FILE the term came from and left the term itself discovered
    by an unqualified `seconds:` anywhere in the template. `SleepAction` is equally
    valid under `postStart`, and a `postStart` sleep is not time kubelet spends
    draining — it runs at startup — so pricing it into the grace-period floor is a
    FALSE REFUSAL of a legitimate chart. Measured on `yadgarhq/iam`'s real chart at
    `origin/main` with a `postStart: sleep: seconds: 20` added beside its own 5s
    `preStop`: "`terminationGracePeriodSeconds: 35` is below the floor of 50s (25
    DRAIN_BUDGET + 5 exit margin + 20 preStop sleep)" — a number no `preStop` in that
    chart sets, attributed to `preStop` by name. Latent at the time only because every
    module chart holds exactly one `seconds:` key and it is the right one.

    IT POINTED BOTH WAYS, which the filing did not record. The same unqualified lookup
    also let a `postStart` sleep SATISFY the `exec`-handler refusal three paragraphs
    up: a chart whose `preStop` is an `exec` — a duration not knowable from the chart —
    found `seconds:` under `postStart`, priced THAT as the drain sleep and passed.
    Measured on the same `iam` tree with `preStop: exec: command: [sh, -c, sleep 5]`, a
    `postStart` sleep of 7 and the grace period at 40: v1.21.1 reports "OK — 1 charts,
    1 judged, 30 assertions evaluated, 0 findings", exit 0. So the residual was a FALSE
    REFUSAL and a FAIL-OPEN wearing one lookup, and only the first half had been filed.

    So `seconds` is taken from `block_of` the `preStop` key rather than from
    `template.keys`. Every `preStop` occurrence contributes its own block, because an
    `{{- if }}`/`{{- else }}` pair is two of them and `guard_verdict` below decides
    which renders.
    """
    openers = template.keys.get("preStop", [])
    if not openers:
        return "ok", 0
    occurrences = [
        occurrence
        for opener in openers
        for occurrence in block_of(template, opener)
        if occurrence.key == "seconds"
    ]
    if not occurrences:
        return (
            "no",
            "the template sets a `preStop` handler with no `sleep: seconds:` under "
            "it. An `exec` handler's duration is not knowable from the chart, so the "
            "grace-period floor cannot be computed — and assuming no sleep is the "
            "fail-open ADR-0601's bound exists to close",
        )
    renderable, blocking = guard_verdict(occurrences, values)
    if not renderable:
        if blocking is not None and blocking[2] == GUARD_UNMODELLED:
            return (
                "no",
                "this gate cannot tell whether the `preStop` sleep renders, because "
                "its guard is a construct it does not model, so the grace-period "
                "floor cannot be computed",
            )
        # The handler is guarded off, so no sleep renders and the floor is the bare
        # drain plus exit margin.
        return "ok", 0
    longest = 0
    for occurrence in renderable:
        state, text = resolve(occurrence, values)
        if state != "ok":
            return (
                "no",
                f"the `preStop` sleep at line {occurrence.lineno} cannot be "
                f"resolved ({text}), so the grace-period floor cannot be computed",
            )
        seconds = as_integer(text)
        if seconds is None:
            return (
                "no",
                f"the `preStop` sleep at line {occurrence.lineno} resolves to "
                f"`{text}`, which is not an integer number of seconds, so the "
                f"grace-period floor cannot be computed",
            )
        longest = max(longest, seconds)
    return "ok", longest


def judge_grace(judge: Judge, template: Template, values: Values, where: str) -> None:
    """`terminationGracePeriodSeconds >= DRAIN_BUDGET + EXIT_MARGIN + preStop sleep`."""
    field = "terminationGracePeriodSeconds"
    renderable = renders(
        judge,
        template,
        values,
        field,
        where,
        f"{where} sets no `{field}`. ADR-0601 bounds the "
        f"pod's grace period against `DRAIN_BUDGET`; a chart that does not set it "
        f"inherits kubelet's default, and the bound then holds by accident.",
    )
    if not renderable:
        return

    state, sleep = prestop_sleep(template, values)
    if not judge.assert_that(
        state == "ok",
        f"{where} — `{field}` cannot be judged: {sleep}. ADR-0601's floor is "
        f"{DRAIN_BUDGET_SECONDS} DRAIN_BUDGET + {EXIT_MARGIN_SECONDS} exit margin "
        f"PLUS the preStop sleep, so a sleep this gate cannot price makes the bound "
        f"unknowable rather than smaller.",
    ):
        return
    floor = DRAIN_BUDGET_SECONDS + EXIT_MARGIN_SECONDS + sleep

    for occurrence in renderable:
        state, text = resolve(occurrence, values)
        if not judge.assert_that(
            state == "ok",
            f"{where}:{occurrence.lineno} — `{field}` cannot be resolved: {text}.",
        ):
            continue
        grace = as_integer(text)
        if not judge.assert_that(
            grace is not None,
            f"{where}:{occurrence.lineno} — `{field}` resolves "
            f"to `{text}`, which is not an integer number of seconds.",
        ):
            continue
        judge.assert_that(
            grace >= floor,
            f"{where}:{occurrence.lineno} — `{field}: {grace}` "
            f"is below the floor of {floor}s "
            f"({DRAIN_BUDGET_SECONDS} DRAIN_BUDGET + {EXIT_MARGIN_SECONDS} exit "
            f"margin + {sleep} preStop sleep). ADR-0601: the grace period covers the "
            f"preStop sleep PLUS the drain after it, and going UNDER is the only "
            f"direction that breaks the bound. Raise the grace period or lower the "
            f"sleep; the 35 the estate ships is this arithmetic, not a magic number.",
        )


def judge_service_account(
    judge: Judge, template: Template, values: Values, kinds: dict, where: str
) -> None:
    """A ServiceAccount the workload owns, named by the pod, with no API token."""
    judge.assert_that(
        "ServiceAccount" in kinds,
        "no template under `chart/templates/` declares `kind: ServiceAccount`. "
        "Without one the pod runs as the namespace's `default` account, shared "
        "with every other unnamed workload, and a permission cannot be granted to "
        "this workload without granting it to all of them.",
    )
    renders(
        judge,
        template,
        values,
        "serviceAccountName",
        where,
        f"{where} sets no `serviceAccountName`, so the pod takes the namespace's "
        f"`default` account whatever the chart's own ServiceAccount says.",
    )
    for mounted in renders(
        judge,
        template,
        values,
        "automountServiceAccountToken",
        where,
        f"{where} sets no `automountServiceAccountToken`. The default is to mount "
        f"a Kubernetes API token into a pod that calls no API.",
    ):
        state, text = resolve(mounted, values)
        if judge.assert_that(
            state == "ok",
            f"{where}:{mounted.lineno} — `automountServiceAccountToken` cannot be "
            f"resolved: {text}.",
        ):
            judge.assert_that(
                as_boolean(text) is False,
                f"{where}:{mounted.lineno} — `automountServiceAccountToken` "
                f"resolves to `{text}`, not `false`. Set on the pod spec because "
                f"the pod spec wins, and because it is what a reader of this file "
                f"sees.",
            )


def judge_topology_spread(
    judge: Judge, template: Template, values: Values, where: str
) -> None:
    """A hostname spread at `maxSkew: 1`, and `ScheduleAnyway` rather than a hang."""
    if not renders(
        judge,
        template,
        values,
        "topologySpreadConstraints",
        where,
        f"{where} sets no `topologySpreadConstraints`. With `replicaCount: 2` and "
        f"nothing spreading them, both replicas may land on one node and a node "
        f"reboot takes the whole Service; today's even split is a scheduler "
        f"coincidence.",
        tail=(
            " Unlike `networkPolicy.enabled`, this has no D80 argument for being "
            "off: a spread constraint needs no CRD and installs on a bare cluster."
        ),
    ):
        return

    # THE THREE SPREAD SCALARS GO THROUGH `renders` TOO, and the middle one is why
    # it matters most here: a `whenUnsatisfiable` guarded off renders NOTHING, and
    # the constraint then takes the API default `DoNotSchedule` — the hang the
    # docstring above describes. Judging the key without its guard reported that
    # green.
    for skew in renders(
        judge,
        template,
        values,
        "maxSkew",
        where,
        f"{where} — the `topologySpreadConstraints` block sets no `maxSkew`.",
    ):
        state, text = resolve(skew, values)
        if judge.assert_that(
            state == "ok",
            f"{where}:{skew.lineno} — `maxSkew` cannot be resolved: {text}.",
        ):
            number = as_integer(text)
            judge.assert_that(
                number is not None and number >= 1,
                f"{where}:{skew.lineno} — `maxSkew` resolves to `{text}`, which is "
                f"not a positive integer.",
            )

    for key in renders(
        judge,
        template,
        values,
        "topologyKey",
        where,
        f"{where} — the `topologySpreadConstraints` block sets no `topologyKey`.",
    ):
        state, text = resolve(key, values)
        if judge.assert_that(
            state == "ok",
            f"{where}:{key.lineno} — `topologyKey` cannot be resolved: {text}.",
        ):
            judge.assert_that(
                text.strip().strip("'\"") != "",
                f"{where}:{key.lineno} — `topologyKey` resolves to an empty value.",
            )

    for strength in renders(
        judge,
        template,
        values,
        "whenUnsatisfiable",
        where,
        f"{where} — the `topologySpreadConstraints` block sets no "
        f"`whenUnsatisfiable`, so it takes the API default `DoNotSchedule`.",
    ):
        state, text = resolve(strength, values)
        if judge.assert_that(
            state == "ok",
            f"{where}:{strength.lineno} — `whenUnsatisfiable` cannot be resolved: "
            f"{text}.",
        ):
            judge.assert_that(
                text.strip().strip("'\"") == REQUIRED_WHEN_UNSATISFIABLE,
                f"{where}:{strength.lineno} — `whenUnsatisfiable` resolves to "
                f"`{text}`, not `{REQUIRED_WHEN_UNSATISFIABLE}`. On two "
                f"schedulable nodes, `maxSurge: 1` puts three pods on two nodes "
                f"for the length of a roll — a skew of 2 against `maxSkew: 1`. A "
                f"required constraint leaves the surge pod Pending and, with "
                f"`maxUnavailable: 0`, the rollout neither proceeds nor fails. It "
                f"hangs, with no error.",
            )


def judge_network_policy(judge: Judge, kinds: dict) -> None:
    """The chart ships a policy template. Whether it is ON is not this gate's business."""
    judge.assert_that(
        "NetworkPolicy" in kinds,
        "no template under `chart/templates/` declares `kind: NetworkPolicy`. "
        "Ledger 511 puts one ingress policy in each module's own chart, selecting "
        "that module's `app:` label. This gate asks only that the template exist: "
        "`enabled: false` is the correct default under D80, because a module chart "
        "must stay installable on a bare cluster, and switching the policy on is "
        "the reference deployment's business.",
    )


BASELINE_HOOK = "chart-baseline"
POLICY_HOOK = "chart-network-policy"


@dataclass(frozen=True)
class Part:
    """One published half of this gate: its floors, and the sentence it prints.

    LEDGER 511 PUT FOUR FIELDS IN ONE GATE AND ADR-0685 SPLIT IT ALONG THE BOUNDARY OF
    WHERE ITS SUBJECTS LIVE. Three of the four are properties of the chart's own pod
    spec. The fourth asks whether a template declares `kind: NetworkPolicy`, and for
    `yadgarhq/gateway` that policy legitimately does NOT live in the chart: its ingress
    source is the Envoy Gateway data plane, and putting a policy validated against one
    ingress implementation in a module chart is D80's first bullet, so the policy lives
    in `yadgarhq/deploy` as `infra/network-policies/gateway-ingress.yaml`. One correct
    assertion was therefore costing that repository the other three — ADR-0584 forbids
    adopting a gate in a repository it hard-fails, so `gateway` could adopt none of it.

    THE SPLIT NEEDS NO EXEMPTION, AND THAT IS THE POINT. An exemption keyed on an
    in-tree fact was considered and is rejected by ADR-0685 by name: `kind: HTTPRoute`
    discriminates `gateway` from the other six today, but it encodes "ships an
    HTTPRoute" while the reason is "is edge-facing", so the next edge-facing module
    inherits the exemption with nothing red. A fact that merely correlates with the
    reason is not the reason. Splitting the gate and adopting each half per repository
    leaves nothing to exempt.

    ONE FILE, TWO `entry:` LINES, and not two scripts. The floors, the chart walk and
    the success line are one mechanism, and `.pre-commit-hooks.yaml` already passes
    arguments to a script hook (`no_test_skips.py --run-pytest`). Two files would be two
    copies of the three floors — the five md5-identical copies ADR-0569's gate had, and
    the drift this file's own header argues against publishing to avoid.

    `--part` HAS NO DEFAULT. A default would make a bare invocation mean one half
    without saying so, which is the "inferred from which hooks a repository happens to
    reference" reading ADR-0685 forbids. Every invocation names its half.
    """

    hook: str
    minimum_charts: int
    minimum_assertions: int
    examined: str
    declined: str
    sibling: str

    def scope(self) -> str:
        """What this half examined, what it did NOT, and what it cannot know.

        ADR-0685: "a module can be partially covered, and partial coverage must be
        legible in the success line rather than inferred from which hooks a repository
        happens to reference." So the omission is stated POSITIVELY, on the pass as
        well as on the failure, and it names the hook that covers it.

        THE LAST CLAUSE IS THE LOAD-BEARING ONE. This process cannot see the consumer's
        `.pre-commit-config.yaml` — it is handed a tree and a part — so it must not
        imply the other half ran. Before the split a green `chart-baseline` meant four
        fields; after it, an unqualified green line would silently mean three, and a
        reader carrying the old meaning would be wrong with nothing to correct them.
        This sentence is what corrects them.
        """
        return (
            f"{self.hook}: examined {self.examined}. It did NOT examine "
            f"{self.declined} — the `{self.sibling}` hook is what does, and this run "
            f"cannot say whether this repository references it (ADR-0685 split the "
            f"gate; ADR-0577 makes adoption per repository). A green {self.hook} is a "
            f"verdict over part of a chart's baseline, never over the whole of it."
        )


PARTS = {
    BASELINE_HOOK: Part(
        hook=BASELINE_HOOK,
        minimum_charts=MINIMUM_CHARTS,
        minimum_assertions=MINIMUM_ASSERTIONS,
        examined=(
            "the grace period against ADR-0601's floor, a dedicated ServiceAccount "
            "with no API token, and a hostname topology spread that cannot hang a roll"
        ),
        declined="whether a template declares `kind: NetworkPolicy`",
        sibling=POLICY_HOOK,
    ),
    POLICY_HOOK: Part(
        hook=POLICY_HOOK,
        minimum_charts=MINIMUM_POLICY_CHARTS,
        minimum_assertions=MINIMUM_POLICY_ASSERTIONS,
        examined="whether a template declares `kind: NetworkPolicy`",
        declined=(
            "the grace period, the ServiceAccount or the topology spread — this half "
            "reads `kind:` lines and neither the pod spec nor `values.yaml`"
        ),
        sibling=BASELINE_HOOK,
    ),
}


def judge_chart(chart: Path, root: Path, part: Part) -> Verdict:
    """One chart, judged by ONE HALF of the gate (ledger 911).

    SUBJECTHOOD IS THE SAME QUESTION FOR BOTH HALVES, and keeping it so is what makes
    the `judged == charts` floor mean the same thing in each: a chart that renders a
    Deployment is a module workload, and a module workload is what ledger 511's four
    fields are about. A chart rendering none — a library chart, a chart of CRDs — is
    not judged by either half, so the two halves always see the same chart set and
    neither can be silently narrower than the other.

    WHAT DIFFERS IS EVERYTHING AFTER THAT. `chart-network-policy` reads `kind:` lines
    and NOTHING else — see `judge_policy_template`.
    """
    relative = chart.relative_to(root).as_posix() or "."
    verdict = Verdict(chart=relative, judged=False)

    kinds = declared_kinds(chart / "templates")
    declarers = kinds.get("Deployment", [])
    if not declarers:
        verdict.note = "renders no Deployment"
        return verdict

    if part.hook == POLICY_HOOK:
        return judge_policy_template(verdict, kinds)
    return judge_deployment_fields(verdict, chart, root, kinds, declarers)


def judge_policy_template(verdict: Verdict, kinds: dict) -> Verdict:
    """`chart-network-policy`: the policy-template assertion, and nothing else.

    THIS PATH DELIBERATELY DOES NOT TOUCH THE DEPLOYMENT TEMPLATE OR `values.yaml`,
    and that is a fail-open this split would otherwise have shipped. The pre-split
    `judge_chart` asserts `template.balanced` first and RETURNS on failure, so a chart
    whose Go template does not balance never reaches the policy check. Had this half
    reused that path, an unbalanced deployment template would have produced
    `judged=True` with no assertion about the policy at all — a chart declaring no
    `kind: NetworkPolicy` passing this half because a DIFFERENT file is malformed. The
    policy assertion needs `declared_kinds` and nothing more, so it is given nothing
    more, and `balanced` / `values.modelled` / two-Deployment are now structurally
    irrelevant here rather than relevant-and-handled.

    TWO BEHAVIOUR DELTAS FOLLOW, both deliberate, neither visible in the seven-tree
    sweep because no module chart has either shape. A chart with two `kind: Deployment`
    templates is refused by `chart-baseline` — it has no rule for choosing a pod spec —
    and PASSES here, because a policy template is not a pod spec. A chart whose
    deployment template is unbalanced is likewise refused there and judged here.
    """
    verdict.judged = True
    judge_network_policy(Judge(verdict, "network-policy"), kinds)
    return verdict


def judge_deployment_fields(
    verdict: Verdict, chart: Path, root: Path, kinds: dict, declarers: list
) -> Verdict:
    """`chart-baseline`: the three deployment-shaped fields, over one pod spec."""
    relative = verdict.chart
    # THE POD SPEC IS FOUND BY THE FACT, NOT BY THE FILENAME (ledger 897 item 6).
    # The shipped gate read `templates/deployment.yaml` while `declared_kinds` scanned
    # all of `templates/`, so a chart whose Deployment lives at another filename was
    # judged against whatever that path happened to hold. Measured on a scratch copy
    # of `iam`'s real chart with the workload moved to `workload.yaml` and a ConfigMap
    # left at `deployment.yaml`: FIVE findings, of which FOUR were false — the real
    # template sets every field they said were missing. Keying on the declaration is
    # the same property the ServiceAccount and NetworkPolicy checks already have, and
    # it makes the strictness argument stronger rather than weaker: the Deployment is
    # now judged wherever it is, instead of being unjudged whenever it moves.
    if len(declarers) > 1:
        verdict.judged = True
        verdict.findings.append(
            Finding(
                relative,
                "deployment",
                f"{len(declarers)} templates declare `kind: Deployment`: "
                f"{', '.join(sorted(path.name for path in declarers))}. This gate "
                f"judges ONE pod spec per chart and has no rule for choosing among "
                f"them, so it refuses rather than judging an arbitrary one and "
                f"reporting a verdict over part of the chart.",
            )
        )
        verdict.assertions += 1
        return verdict

    deployment = declarers[0]
    verdict.judged = True
    template = scan_template(read(deployment))
    values = scan_values(read(chart / "values.yaml"))
    where = (deployment.relative_to(root)).as_posix()

    structure = Judge(verdict, "template")
    if not structure.assert_that(
        template.balanced,
        f"{where} — the Go template's `{{{{ if }}}}`/`{{{{ with }}}}`/`{{{{ end }}}}` "
        f"actions do not balance, so this gate cannot tell which values guard which "
        f"field.",
    ):
        return verdict
    # A `declares no kind: Deployment` assertion used to sit here. It is DELETED
    # rather than kept, because item 6 made it tautological: the file being scanned is
    # now the one `declared_kinds` picked BY that declaration, using the same
    # `KIND_LINE` over the same text, so the check could no longer fail. A check whose
    # failure branch is unreachable reads as coverage and is worse than its absence —
    # this file's own docstring makes that argument about parse failures. It costs one
    # assertion on every chart, which is why the per-chart count moves 26 -> 25.
    structure.assert_that(
        values.modelled,
        f"{chart.relative_to(root).as_posix()}/values.yaml cannot be read by this "
        f"gate: {values.reason}. Every value lookup would be unresolvable, so the "
        f"chart is refused rather than passed.",
    )

    judge_grace(Judge(verdict, "grace-period"), template, values, where)
    judge_service_account(
        Judge(verdict, "service-account"), template, values, kinds, where
    )
    judge_topology_spread(
        Judge(verdict, "topology-spread"), template, values, where
    )
    # `judge_network_policy` USED TO BE CALLED HERE and is now the other half's only
    # assertion (ledger 911, ADR-0685). Nothing else moved: the per-chart count goes
    # 30 -> 29 here and 1 there, and the two halves run over the same chart set, so
    # the sum over a tree is what the one gate evaluated before the split.
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ledger 511 — every module chart carries the four baseline fields: a "
            "grace period above ADR-0601's floor, a dedicated ServiceAccount with "
            "no API token, a hostname spread that cannot hang a roll, and an "
            "ingress NetworkPolicy template. ADR-0685 SPLIT those four across two "
            "published hooks, adopted per repository, so every run names the half "
            "it is: `--part chart-baseline` for the first three, "
            "`--part chart-network-policy` for the fourth."
        )
    )
    parser.add_argument(
        "--part",
        required=True,
        choices=sorted(PARTS),
        help=(
            "which published half to run. REQUIRED and with no default: a default "
            "would let a bare invocation mean one half without saying so, which is "
            "the inference ADR-0685 forbids."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="the tree to judge (default: the working directory)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print a per-chart, per-field table as well as the findings",
    )
    parser.add_argument(
        "filenames",
        nargs="*",
        help=(
            "ignored. The subject is every chart in the tree, so this hook runs "
            "with `pass_filenames: false`."
        ),
    )
    arguments = parser.parse_args(argv)
    part = PARTS[arguments.part]

    root = Path(arguments.root)
    if not root.is_dir():
        print(f"{part.hook}: --root {arguments.root} is not a directory")
        return 1

    charts = find_charts(root)
    verdicts = [judge_chart(chart, root, part) for chart in charts]
    judged = [verdict for verdict in verdicts if verdict.judged]
    assertions = sum(verdict.assertions for verdict in verdicts)
    findings = [finding for verdict in verdicts for finding in verdict.findings]

    if arguments.report:
        print(f"{part.hook}: per chart")
        for verdict in verdicts:
            if not verdict.judged:
                print(f"  {verdict.chart}: not judged — {verdict.note}")
                continue
            failed = sorted({finding.field for finding in verdict.findings})
            state = "PASS" if not failed else "FAIL " + ",".join(failed)
            print(f"  {verdict.chart}: {state} ({verdict.assertions} assertions)")

    for finding in sorted(findings, key=lambda f: (f.chart, f.field)):
        print(f"{finding.chart} [{finding.field}] {finding.message}")

    # THE FLOORS, AND EACH HALF CLEARS ITS OWN (ledger 911). A gate that examined
    # nothing must not report a pass: a renamed chart directory or a
    # `kind: Deployment` this scan stopped recognising would otherwise turn it green
    # by finding no input. Splitting a gate multiplies that risk rather than moving
    # it — a half able to report success having examined nothing reintroduces ledger
    # 715's class on whichever side is weaker — so all three are evaluated per part,
    # against that part's own numbers, and every number is in that part's own line.
    if len(charts) < part.minimum_charts:
        print(
            f"{part.hook}: found {len(charts)} charts under {root}, below the "
            f"floor of {part.minimum_charts}. A tree with no `Chart.yaml` has "
            f"nothing for this gate to judge, and a library repository does not "
            f"reference it — see ADR-0577 on adoption."
        )
        return 1
    if assertions < part.minimum_assertions:
        print(
            f"{part.hook}: {len(charts)} charts found but {assertions} "
            f"assertions evaluated, below the floor of {part.minimum_assertions}. "
            f"The charts were found and none was judged, which is what a broken "
            f"`kind: Deployment` scan looks like."
        )
        return 1
    # A THIRD WAY TO GO BLIND, neither floor above catches: SOME charts judged,
    # others not. The assertions floor only sees the total across every chart, so
    # one broken chart sitting beside N good ones keeps the total well above the
    # floor while that one chart is never examined — "N charts, N-1 judged", green.
    # Ledger 715's class, and independent of both `kind: Deployment` regex fixes:
    # a legitimately different workload kind in the same tree looks identical to
    # this gate. So every discovered chart is required to be judged; a chart this
    # gate cannot judge must be pruned from the walk (moved out of `--root`)
    # rather than left in it and outvoted by its neighbours.
    if len(judged) < len(charts):
        unjudged = sorted(verdict.chart for verdict in verdicts if not verdict.judged)
        print(
            f"{part.hook}: {len(charts)} charts found, {len(judged)} judged. "
            f"{len(unjudged)} chart(s) were never examined: {', '.join(unjudged)}. "
            f"A verdict over some of the charts is not a verdict. Prune an "
            f"unjudgeable chart from this walk, or widen this gate to judge it."
        )
        return 1
    if findings:
        print(
            f"{part.hook}: {len(findings)} findings across {len(judged)} of "
            f"{len(charts)} charts ({assertions} assertions evaluated)."
        )
        print(part.scope())
        return 1

    print(
        f"{part.hook}: OK — {len(charts)} charts, {len(judged)} judged, "
        f"{assertions} assertions evaluated, 0 findings."
    )
    print(part.scope())
    return 0


if __name__ == "__main__":
    sys.exit(main())
