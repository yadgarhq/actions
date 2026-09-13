#!/usr/bin/env python3
"""Ledger 511 — every module chart carries the four baseline fields, with the rule rather than the number.

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

THE FOUR FIELDS ARE THE PLAN'S, AND NOTHING ELSE IS HERE. The plan adopted
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

THE GUARD CHECK ITSELF SKIPS THIS MODEL (ledger 897, not fixed here). It reports
a guard as "left off" for anything not truthy, and an anchor, an alias, a
sequence, a template expression or a deleted key under the guard are ALL not
truthy — `values.raw()` returns `None` for a construct this scan does not model,
the same `None` it returns for a key that is genuinely absent. So
`terminationGracePeriodSeconds: &grace 35` prints the identical "values.yaml
leaves it off" message a truly-absent key would print, even though the anchor
sets it. The exit code is still red either way; the message is not accurate for
the modelled-but-not-a-plain-scalar case.

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

WHAT IS KEYED ON A FACT RATHER THAN A FILENAME. The ServiceAccount and
NetworkPolicy checks ask whether any file under `templates/` DECLARES
`kind: ServiceAccount` / `kind: NetworkPolicy`, not whether a file of a
particular name exists. A rename is then not an exemption, which is the property
`certificate_usages.py` bought by deleting its issuer allowlist.

`gateway` HAS NO NetworkPolicy IN ITS CHART, and this gate reports it. That is a
dated observation — 2026-09-13, `origin/main` of the seven module repositories —
and NOT this file's standing claim about anybody's tree (ledger 847). The reason
it is unresolved rather than simply missing: `gateway`'s ingress source is the
Envoy Gateway data plane in `envoy-gateway-system`, selected by
`gateway.envoyproxy.io/*` labels, and `chart-hardening.md` places that policy in
`yadgarhq/deploy`'s `infra/network-policies/` precisely because putting it in the
chart would be D80's first bullet — "a service chart that reads, requires, or is
validated against a resource owned by one ingress implementation". A hook cannot
see another repository, so there is no in-tree fact to key an exemption on, and
there is deliberately NO comment marker either: `no_test_skips.py` states the
rule this file follows — the exemption is a fact, never a reason string. The
finding therefore stands until the adoption sweep resolves it, and ADR-0584 is
satisfied because publication is not adoption.

ADOPTION IS PER REPOSITORY AND IS NOT AUTOMATIC (ADR-0577, ADR-0607), and
ADR-0584 forbids adopting a gate in a repository it hard-fails. Two consequences
worth stating. A library repository with no chart — `dial`, `lifecycle` — does not
reference this hook: the gate REFUSES a tree carrying no `Chart.yaml` rather than
passing it, the way `boot_reads_watched.py` refuses a repository with no
`src/rotate.rs`, because a tree with nothing to judge must not report a green
tick. And `yadgarhq/actions` does not reference it either — this repository holds
no chart — so what gates this file on every commit is `pytest-scripts`, not the
hook itself.

THREE WAYS TO GO BLIND, and no one check sees all of them (ledger 715's
`MINIMUM_SITES=2`, and `certificate_usages.py`'s pair, extended by one). `MINIMUM_CHARTS`
catches the walk finding nothing at all — a `chart/` to `charts/` rename, a
restructured repository. `MINIMUM_ASSERTIONS` catches every chart turning into
something this gate does not judge AT ONCE, which is what a broken
`kind: Deployment` detection used to look like before this file's own regex
carried that defect: the chart count stays 7 while the number of assertions
evaluated drops to 0. Neither catches a THIRD way: some charts judged, others not,
in the same tree — the total assertions stay well above the floor because the
judged charts carry it, and the tree reports "N charts, N-1 judged" with exit 0.
So a chart's `judged` flag is checked against the chart count directly: every
discovered chart must be judged, or the run is red. All three are floors rather
than the current counts, because adding a module is a legitimate change and must
not redden. All three counts are in the success line.

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
    python3 hooks/chart_baseline.py --root <tree> --report
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

# Floors, not current counts. See the module docstring.
MINIMUM_CHARTS = 1
MINIMUM_ASSERTIONS = 1

# Directories a chart never lives in. `charts/` is helm's vendored-subchart
# directory: a dependency's copy is not this repository's chart to judge.
PRUNED_DIRECTORIES = frozenset(
    {".git", "charts", "node_modules", "target", ".venv", "vendor"}
)

# A scalar this scanner will not claim to have read. Distinct from absence, so a
# lookup can tell "no such key" from "a key whose value I do not model".
NOT_MODELLED = object()

TRUE_WORDS = frozenset({"true", "yes", "on"})
FALSE_WORDS = frozenset({"false", "no", "off", "null", "~", "0", '""', "''"})

KEY_LINE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[A-Za-z0-9_.\-]+):(?P<rest>.*)$")
# `certificate_usages.py`'s `CERTIFICATE_KIND` carries the comment recording what
# anchoring this on `$` alone cost, three times, one level further down each time
# (ledger 720, actions#63). This gate shipped the same defect on its own first
# publication: `kind: NetworkPolicy  # one ingress policy` and
# `kind: "NetworkPolicy"` both went unrecognised (a false refusal), and worse,
# `kind: Deployment  # the workload` went unrecognised too — which is a chart this
# gate never judges, silently, since `MINIMUM_ASSERTIONS` cannot see one chart in
# a many-chart tree going dark while the rest still pass. Tolerating leading
# space, an optional quote and a trailing comment closes all three.
KIND_LINE = re.compile(
    r"^\s*kind:\s*[\"']?(?P<kind>[A-Za-z][A-Za-z0-9]*)[\"']?\s*(?:#.*)?$"
)
TEMPLATE_ACTION = re.compile(r"\{\{-?\s*(?P<word>[a-z]+)\b(?P<arg>[^}]*?)-?\}\}")
VALUES_REFERENCE = re.compile(r"^\.Values\.(?P<path>[A-Za-z0-9_.]+)$")
ANY_VALUES_REFERENCE = re.compile(r"\.Values\.([A-Za-z0-9_.]+)")
SEQUENCE_ITEM = re.compile(r"^(?P<indent>[ ]*)-[ ]")
VALUES_ACTION = re.compile(r"^\{\{-?\s*\.Values\.(?P<path>[A-Za-z0-9_.]+)\s*-?\}\}$")
DOT_ACTION = re.compile(r"^\{\{-?\s*\.\s*-?\}\}$")
BLOCK_OPENERS = frozenset({"if", "with", "range", "define", "block"})


# ---------------------------------------------------------------- values.yaml


def strip_inline_comment(raw: str) -> str:
    """Drop a YAML inline comment, respecting a quoted scalar that contains `#`."""
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "\"'":
        quote = text[0]
        end = text.find(quote, 1)
        while end != -1 and text[end - 1] == "\\":
            end = text.find(quote, end + 1)
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
    """

    scalars: dict
    modelled: bool
    reason: str = ""

    def raw(self, path: str):
        """The raw scalar at `path`, or None when it is absent or not modelled."""
        if not self.modelled:
            return None
        found = self.scalars.get(path, None)
        return None if found is NOT_MODELLED else found

    def present(self, path: str) -> bool:
        return self.modelled and path in self.scalars


def scan_values(text: str) -> Values:
    """Read a plain block-mapping `values.yaml` into dotted-path scalars."""
    scalars: dict = {}
    stack: list = []
    suppress_from = None
    seen_content = False

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
                scalars[dotted(stack)] = NOT_MODELLED
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
            continue
        if value[0] in "{[|>&*!":
            # A flow collection, a block scalar, an anchor, an alias or a tag.
            scalars[path] = NOT_MODELLED
            suppress_from = indent + 1
            continue
        scalars[path] = value

    return Values(scalars, True)


def dotted(stack) -> str:
    return ".".join(key for _, key in stack)


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
class KeyOccurrence:
    lineno: int
    raw_value: str
    guards: tuple
    dot: str


@dataclass(frozen=True)
class Template:
    declares: frozenset
    keys: dict
    balanced: bool
    references: frozenset

    def first(self, key: str):
        found = self.keys.get(key)
        return found[0] if found else None


def scan_template(text: str) -> Template:
    """Read a Go-templated manifest: which kinds it declares, and where keys sit.

    Template actions are tracked on EVERY line, comments included, because Go's
    text/template executes a `{{- if }}` inside a YAML comment exactly as it
    executes one outside it. Keys are read from non-comment lines only.
    """
    declares: set = set()
    keys: dict = {}
    stack: list = []
    balanced = True
    references = set(ANY_VALUES_REFERENCE.findall(text))

    for lineno, line in enumerate(text.splitlines(), 1):
        for action in TEMPLATE_ACTION.finditer(line):
            word = action.group("word")
            if word in BLOCK_OPENERS:
                reference = VALUES_REFERENCE.match(action.group("arg").strip())
                stack.append((word, reference.group("path") if reference else ""))
            elif word == "end":
                if stack:
                    stack.pop()
                else:
                    balanced = False

        body = line.strip()
        if body.startswith("#"):
            continue
        kind = KIND_LINE.match(body)
        if kind is not None:
            declares.add(kind.group("kind"))

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
        for word, path in reversed(stack):
            if word == "with":
                dot = path
                break
        guards = tuple(path for _, path in stack if path)
        keys.setdefault(match.group("key"), []).append(
            KeyOccurrence(lineno, rest.strip(), guards, dot)
        )

    return Template(
        frozenset(declares), keys, balanced and not stack, frozenset(references)
    )


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
    path = ""
    if DOT_ACTION.match(text):
        if not occurrence.dot:
            return "unresolvable", "`{{ . }}` outside a `with .Values...` block"
        path = occurrence.dot
    else:
        action = VALUES_ACTION.match(text)
        if action is not None:
            path = action.group("path")
        elif "{{" in text:
            return "unresolvable", f"an expression this gate does not model: {text}"
        else:
            return "ok", text
    if not values.modelled:
        return "unresolvable", f"values.yaml carries {values.reason}"
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
    """Which `kind:` each template under `templates/` declares, by kind."""
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
                by_kind.setdefault(kind.group("kind"), []).append(path.name)
    return by_kind


def judge_grace(judge: Judge, template: Template, values: Values, where: str) -> None:
    """`terminationGracePeriodSeconds >= DRAIN_BUDGET + EXIT_MARGIN + preStop sleep`."""
    occurrence = template.first("terminationGracePeriodSeconds")
    if not judge.assert_that(
        occurrence is not None,
        f"{where} sets no `terminationGracePeriodSeconds`. ADR-0601 bounds the "
        f"pod's grace period against `DRAIN_BUDGET`; a chart that does not set it "
        f"inherits kubelet's default, and the bound then holds by accident.",
    ):
        return

    for guard in occurrence.guards:
        if not judge.assert_that(
            is_truthy(values.raw(guard)),
            f"{where}:{occurrence.lineno} renders "
            f"`terminationGracePeriodSeconds` only when `{guard}` is set, and "
            f"values.yaml leaves it off — so the field renders nothing.",
        ):
            return

    state, text = resolve(occurrence, values)
    if not judge.assert_that(
        state == "ok",
        f"{where}:{occurrence.lineno} — `terminationGracePeriodSeconds` cannot be "
        f"resolved: {text}.",
    ):
        return
    grace = as_integer(text)
    if not judge.assert_that(
        grace is not None,
        f"{where}:{occurrence.lineno} — `terminationGracePeriodSeconds` resolves "
        f"to `{text}`, which is not an integer number of seconds.",
    ):
        return

    sleep = 0
    if "preStopSleepSeconds" in template.references:
        raw = values.raw("preStopSleepSeconds")
        if raw is not None:
            sleep = as_integer(raw)
            if not judge.assert_that(
                sleep is not None,
                f"values.yaml sets `preStopSleepSeconds: {raw}`, which is not an "
                f"integer number of seconds, so the grace-period floor cannot be "
                f"computed.",
            ):
                return

    floor = DRAIN_BUDGET_SECONDS + EXIT_MARGIN_SECONDS + sleep
    judge.assert_that(
        grace >= floor,
        f"{where}:{occurrence.lineno} — `terminationGracePeriodSeconds: {grace}` "
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
    named = template.first("serviceAccountName")
    judge.assert_that(
        named is not None,
        f"{where} sets no `serviceAccountName`, so the pod takes the namespace's "
        f"`default` account whatever the chart's own ServiceAccount says.",
    )
    mounted = template.first("automountServiceAccountToken")
    if judge.assert_that(
        mounted is not None,
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
    occurrence = template.first("topologySpreadConstraints")
    if not judge.assert_that(
        occurrence is not None,
        f"{where} sets no `topologySpreadConstraints`. With `replicaCount: 2` and "
        f"nothing spreading them, both replicas may land on one node and a node "
        f"reboot takes the whole Service; today's even split is a scheduler "
        f"coincidence.",
    ):
        return

    for guard in occurrence.guards:
        if not judge.assert_that(
            is_truthy(values.raw(guard)),
            f"{where}:{occurrence.lineno} renders "
            f"`topologySpreadConstraints` only when `{guard}` is on, and "
            f"values.yaml leaves it off — every template line is present and no "
            f"constraint renders. Unlike `networkPolicy.enabled`, this has no D80 "
            f"argument for being off: a spread constraint needs no CRD and "
            f"installs on a bare cluster.",
        ):
            return

    skew = template.first("maxSkew")
    if judge.assert_that(
        skew is not None,
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

    key = template.first("topologyKey")
    if judge.assert_that(
        key is not None,
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

    strength = template.first("whenUnsatisfiable")
    if judge.assert_that(
        strength is not None,
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


def judge_chart(chart: Path, root: Path) -> Verdict:
    relative = chart.relative_to(root).as_posix() or "."
    verdict = Verdict(chart=relative, judged=False)

    deployment = chart / "templates" / "deployment.yaml"
    kinds = declared_kinds(chart / "templates")
    if "Deployment" not in kinds:
        verdict.note = "renders no Deployment"
        return verdict

    if not deployment.is_file():
        verdict.judged = True
        verdict.findings.append(
            Finding(
                relative,
                "deployment",
                "a template declares `kind: Deployment` but there is no "
                "`templates/deployment.yaml`. This gate reads the pod spec from "
                "that path; a Deployment spelled elsewhere is unjudged, which is "
                "not a state this gate accepts.",
            )
        )
        verdict.assertions += 1
        return verdict

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
    structure.assert_that(
        "Deployment" in template.declares,
        f"{where} declares no `kind: Deployment`.",
    )
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
    judge_network_policy(Judge(verdict, "network-policy"), kinds)
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ledger 511 — every module chart carries the four baseline fields: a "
            "grace period above ADR-0601's floor, a dedicated ServiceAccount with "
            "no API token, a hostname spread that cannot hang a roll, and an "
            "ingress NetworkPolicy template."
        )
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

    root = Path(arguments.root)
    if not root.is_dir():
        print(f"chart-baseline: --root {arguments.root} is not a directory")
        return 1

    charts = find_charts(root)
    verdicts = [judge_chart(chart, root) for chart in charts]
    judged = [verdict for verdict in verdicts if verdict.judged]
    assertions = sum(verdict.assertions for verdict in verdicts)
    findings = [finding for verdict in verdicts for finding in verdict.findings]

    if arguments.report:
        print("chart-baseline: per chart")
        for verdict in verdicts:
            if not verdict.judged:
                print(f"  {verdict.chart}: not judged — {verdict.note}")
                continue
            failed = sorted({finding.field for finding in verdict.findings})
            state = "PASS" if not failed else "FAIL " + ",".join(failed)
            print(f"  {verdict.chart}: {state} ({verdict.assertions} assertions)")

    for finding in sorted(findings, key=lambda f: (f.chart, f.field)):
        print(f"{finding.chart} [{finding.field}] {finding.message}")

    # THE FLOORS. A gate that examined nothing must not report a pass: a renamed
    # chart directory or a `kind: Deployment` this scan stopped recognising would
    # otherwise turn it green by finding no input.
    if len(charts) < MINIMUM_CHARTS:
        print(
            f"chart-baseline: found {len(charts)} charts under {root}, below the "
            f"floor of {MINIMUM_CHARTS}. A tree with no `Chart.yaml` has nothing "
            f"for this gate to judge, and a library repository does not reference "
            f"it — see ADR-0577 on adoption."
        )
        return 1
    if assertions < MINIMUM_ASSERTIONS:
        print(
            f"chart-baseline: {len(charts)} charts found but {assertions} "
            f"assertions evaluated, below the floor of {MINIMUM_ASSERTIONS}. The "
            f"charts were found and none was judged, which is what a broken "
            f"`kind: Deployment` scan looks like."
        )
        return 1
    # A THIRD WAY TO GO BLIND, neither floor above catches: SOME charts judged,
    # others not. `MINIMUM_ASSERTIONS` only sees the total across every chart, so
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
            f"chart-baseline: {len(charts)} charts found, {len(judged)} judged. "
            f"{len(unjudged)} chart(s) were never examined: {', '.join(unjudged)}. "
            f"A verdict over some of the charts is not a verdict. Prune an "
            f"unjudgeable chart from this walk, or widen this gate to judge it."
        )
        return 1
    if findings:
        print(
            f"chart-baseline: {len(findings)} findings across {len(judged)} of "
            f"{len(charts)} charts ({assertions} assertions evaluated)."
        )
        return 1

    print(
        f"chart-baseline: OK — {len(charts)} charts, {len(judged)} judged, "
        f"{assertions} assertions evaluated, 0 findings."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
