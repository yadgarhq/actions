#!/usr/bin/env python3
"""A chart change that Argo CD cannot apply to a live Service is refused here.

LEDGER 735, and it has already cost an outage. Ledger 615 changed `iam`'s
Service to `clusterIP: None`. `spec.clusterIP` is IMMUTABLE, and
`argocd/applicationsets/modules.yaml` carries `syncOptions: [CreateNamespace=true]`
and nothing else -- no `Replace`, no `ServerSideApply` -- so the sync failed and
kept failing. The Service had to be deleted by hand before Argo could recreate
it, and roughly ninety seconds of logins failed while it did not exist. Nothing
warned before the merge, so the next such change repeats it.

THE IMMUTABILITY IS CITED, NOT REMEMBERED, and the citation comes out of the
apiserver this estate actually deploys to. `kubectl explain service.spec.clusterIP`
against kind-yadgar, Kubernetes v1.36.1, on 2026-09-06:

    This field may not be changed through updates unless the type field is also
    being changed to ExternalName (which requires this field to be blank) or the
    type field is being changed from ExternalName

`spec.clusterIPs` carries the same sentence, and no chart here renders
`spec.type` at all -- so neither escape clause is reachable from a chart edit,
and EVERY diff on these two fields against a live Service is one the apiserver
refuses. That is a first-party reference read, not a probe: nothing was applied
to the cluster to establish it.

WHY BOTH DIRECTIONS AND A REMOVAL ALL COUNT. The refusal above is what happens
when the chart sets a different value. When the chart DELETES the field instead,
Argo's apply leaves the apiserver holding what it already had, so the live
Service silently keeps its old identity while the chart says otherwise. Failing
loudly and doing nothing are different outcomes with the same consequence: the
chart's intent does not reach the cluster. So any difference in these fields is
reported.

WHAT THIS COMPARES, AND IT IS EXACTLY TWO FIELDS. The class was enumerated
against every chart in the estate on 2026-09-06 rather than assumed, and the
enumeration is what justifies the narrowness:

  * `Service.spec.clusterIP` / `spec.clusterIPs` -- rendered by six charts, and
    the field ledger 615 moved. IN SCOPE.
  * `Service.spec.type` -- rendered by NO chart. A transition it could refuse
    cannot arise from a chart edit today, and the transitions that are refused
    depend on the live Service's shape rather than on the field alone. Left out
    deliberately. If a chart ever renders `spec.type`, measure which transitions
    the apiserver refuses and extend `ALWAYS` then; do not guess.
  * `Deployment.spec.selector`, `PodDisruptionBudget.spec.selector` and
    `ScaledObject.spec.scaleTargetRef` -- rendered by all six, and every one of
    them renders `{{ .Chart.Name }}`. So each moves only when the CHART is
    renamed, which renames the object too and makes the sync a create rather
    than an update. That is the whole reason they are out of scope: none of the
    three was established to be immutable here, and none needs to be, because
    the gate would have nothing to fire on either way.
  * `StatefulSet.spec.serviceName` and `.volumeClaimTemplates`,
    `PersistentVolumeClaim.spec.resources`, `Job.spec.template` -- NO chart in
    this estate renders any of these kinds. Nothing renders them, so there is
    nothing to gate. Stating that is the result, not a gap.

WHAT IS NOT COVERED, said plainly rather than left to be assumed:

  * `spec.ipFamilies` and `spec.ipFamilyPolicy`. This cluster is single-stack
    and no chart renders either field. A dual-stack operator should establish
    their behaviour and add them here.
  * `nodePort` values and `loadBalancerClass`. No Service in this estate is
    either kind, and no chart renders either field.

WHAT THIS COMPARES, AND IT IS NOT THE CLUSTER. CI cannot reach the cluster, so
this renders the chart at the pull request's HEAD and the chart at its BASE and
compares those two. That is a real limitation and not a detail:

  * It CATCHES the pull request that introduces the change -- which is the
    moment ledger 615 could have been stopped.
  * It DOES NOT catch a change already merged and not yet synced, which is how
    ledger 615 actually became an outage, nor drift that arrived by hand. A
    check that compared the chart against ITSELF would prove nothing at all,
    and this is the strongest thing available on the CI side of the wall.

So this is a flag for human review, not a proof of safety. What it buys is that
the diff is named, with the procedure attached, before the merge rather than
after the failed sync.

WHY NOT FIX IT ON THE ARGO SIDE INSTEAD, and the popular answer is the wrong
one. `Replace=true` alone does NOT apply this change. Argo CD's sync-options
documentation says `Replace=true` makes it "use `kubectl replace` or `kubectl
create` command to apply changes" -- and `kubectl replace` is an UPDATE, which is
the exact verb the apiserver sentence quoted above refuses. The option that
works is `Force=true`, documented as synchronising "using the `kubectl
delete/create` command": it applies the change by DELETING the Service and
recreating it. So the only Argo-side fix is the one that reintroduces ledger
615's outage on purpose, and estate-wide it would do so on every Service change
rather than on the few that need it. A loud refusal before the merge is the
better trade. The per-Service annotation
`argocd.argoproj.io/sync-options: Force=true,Replace=true` stays available to
whoever wants that specific delete-and-recreate, and this gate's own message
says so.

WHY A CI GATE AND NOT A PRE-COMMIT HOOK. Three inputs decide it. This needs
PyYAML, which a published `language: script` hook cannot install -- every hook
in `.pre-commit-hooks.yaml` is stdlib-only for that reason. It needs `helm`,
which the shared workflow installs and a developer's machine may not carry. And
it needs a BASE REF, which exists on a pull request and does not exist for
somebody committing on a branch with no pull request yet. A hook would have to
guess the base or skip, and a gate that skips is the defect this estate has
measured four times in a week.

WHY IT LIVES HERE. `yadgarhq/actions` is where CI is defined once (D62). Six
repositories render a Service -- gateway, iam, iam-db, project-db, task and
task-db -- and a check copied into six repositories is not a shared check, which
is the lesson ledger 648 recorded when the ADR-0569 gate turned out to be five
byte-identical copies.

ONE FAITHFULNESS CAVEAT, MEASURED. The ApplicationSet injects `values:` this
render does not see -- `autoscaling`, `sourceAddress`, `tls` and the four
per-upstream dial blocks. None of them reaches a Service template today: the six
`chart/templates/service.yaml` files read `.Chart.Name` and, in `gateway` alone,
`.Values.service.port`, and the ApplicationSet sets no `service.port`. So the
render is faithful for Services as of 2026-09-06. That is a measurement rather
than a guarantee, and a value that one day reaches a Service would break it.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

import yaml

CHART = Path("chart")

# helm precedes every rendered document with `# Source: <chart>/templates/x.yaml`.
# It is the only way to get from a rendered Service back to the file somebody has
# to edit, and PyYAML drops comments, so the line is read off the raw output
# before it is parsed.
SOURCE = re.compile(r"^#\s*Source:\s*(\S+)\s*$")

# THE FLOOR, in two parts, because a constant one is wrong here.
#
# The precedent is `MINIMUM_SITES = 2` in `no_build_cache.py`, and copying the
# shape without the reasoning would have been a defect: MEASURED across the
# estate, five chart repositories render exactly one Service, `gateway` renders
# one, and `yadgarhq/config` renders a chart with NO Service at all. So any
# constant floor above zero reddens `config` for ever, and a floor of zero is not
# a floor. A gate people have to exempt a repository from is a gate they learn to
# work around.
#
# PART ONE IS ABSOLUTE. `helm template` producing NO DOCUMENT is not a chart
# without a Service, it is a render that said nothing -- and that is the state in
# which this gate reports agreement having read nothing.
MINIMUM_DOCUMENTS = 1

# PART TWO IS DERIVED FROM HISTORY, the same trick `versions_pinned.py` uses:
# history answers the question the working tree cannot. The floor for THIS
# repository is the number of Services the chart rendered at the BASE. A chart
# that rendered one Service and now renders none has had a template renamed, a
# condition flipped, or a Service deliberately deleted -- and all three need a
# human, because Argo prunes. A chart that rendered none and still renders none
# is `config`, and that is a fact about the repository rather than a check
# declining to run. Nothing has to be listed anywhere for this to work, which is
# the property D54 is about.
#
# THE FLOOR IS NEVER "FOUND A DIFFERENCE". A run that finds no difference is the
# normal, correct outcome of almost every pull request, so it can never be the
# thing that proves the gate looked.

# THE WHOLE OF WHAT IS COMPARED, and the docstring's enumeration is why it is
# these two and nothing else. Both carry the apiserver's own "may not be changed
# through updates" sentence, and the literal string `None` -- which is what makes
# a Service headless, and what ledger 615 introduced -- is one of their values
# rather than a special case.
ALWAYS = ("clusterIP", "clusterIPs")


def one_line(text: object) -> str:
    """`::error::` annotates the FIRST line of its payload and drops the rest."""
    return " ".join(str(text).split())


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )


def base_ref() -> tuple[str | None, str | None]:
    """The revision to compare against, or the reason there is none.

    REFUSES RATHER THAN SKIPS when it cannot find one, and names `fetch-depth: 0`
    in the refusal the way `versions_pinned.py` does. A gate whose base ref is
    missing has nothing to compare and must not report that as agreement.

    `SERVICE_IMMUTABLE_BASE` is checked first so the suite -- and anybody proving
    this gate red by hand -- can point it at a known revision.
    """
    override = os.environ.get("SERVICE_IMMUTABLE_BASE", "").strip()
    if override:
        if git("rev-parse", "--verify", f"{override}^{{commit}}").returncode == 0:
            return override, None
        return None, (
            f"`SERVICE_IMMUTABLE_BASE` names `{override}`, which this checkout "
            f"cannot resolve to a commit."
        )

    candidates = []
    target = os.environ.get("GITHUB_BASE_REF", "").strip()
    if target:
        candidates = [f"origin/{target}", target]
    else:
        head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if head.returncode == 0 and head.stdout.strip():
            candidates = [head.stdout.strip()]

    for candidate in candidates:
        if git("rev-parse", "--verify", f"{candidate}^{{commit}}").returncode == 0:
            return candidate, None

    tried = ", ".join(f"`{c}`" for c in candidates) or "nothing"
    return None, (
        f"No base revision resolves in this checkout (tried {tried}), so this "
        f"gate has nothing to compare the chart against and would report "
        f"agreement having read one side. Set `fetch-depth: 0` on the checkout "
        f"so the base branch is present, or pass `SERVICE_IMMUTABLE_BASE`."
    )


def chart_at(revision: str, destination: Path) -> bool:
    """Write `chart/` as of `revision` under `destination`. False when absent.

    `git archive` rather than a second worktree: it needs no clean index, no
    lock and no cleanup beyond the temporary directory, and it reads the same
    object store a worktree would.
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision, str(CHART)],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        return False
    with tempfile.NamedTemporaryFile(suffix=".tar") as handle:
        handle.write(archive.stdout)
        handle.flush()
        with tarfile.open(handle.name) as tar:
            # `filter="data"` refuses absolute paths, `..` and device nodes. The
            # archive comes from this repository's own history, but the default
            # changes across Python versions and an explicit filter is one line.
            tar.extractall(destination, filter="data")
    return (destination / CHART).is_dir()


def sources(output: str) -> dict[str, str]:
    """Service name -> the `# Source:` path helm rendered it from.

    BEST-EFFORT, AND DELIBERATELY SEPARATE FROM THE COMPARISON. The chunks are
    cut on `---`, which is not quite how YAML delimits documents -- a `---`
    inside a block scalar would split one document in two. So this never decides
    what is compared: `safe_load_all` over the whole output does that. This only
    supplies the file path in the message, and a chunk that will not parse
    contributes nothing rather than shifting the others.

    An earlier revision aligned two lists BY INDEX and named
    `chart/templates/serviceaccount.yaml` for a defect in
    `chart/templates/service.yaml` -- helm opens its output with a leading `---`,
    so the split yielded one more chunk than there were documents. Keying on the
    name cannot drift that way.
    """
    found: dict[str, str] = {}
    for chunk in re.split(r"^---\s*$", output, flags=re.MULTILINE):
        path = None
        for line in chunk.splitlines():
            match = SOURCE.match(line.strip())
            if match:
                path = match.group(1)
                break
        if not path:
            continue
        try:
            document = yaml.safe_load(chunk)
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict) or document.get("kind") != "Service":
            continue
        name = (document.get("metadata") or {}).get("name")
        if isinstance(name, str):
            found[name] = path
    return found


def services(
    chart_directory: Path,
) -> tuple[dict[str, tuple[dict, str | None]], int, str | None]:
    """Services by name -> (spec, source file), and how many documents rendered."""
    render = subprocess.run(
        ["helm", "template", "immutability-check", str(chart_directory)],
        capture_output=True,
        text=True,
        check=False,
    )
    if render.returncode != 0:
        return {}, 0, one_line(render.stderr) or f"helm exited {render.returncode}"

    try:
        documents = [d for d in yaml.safe_load_all(render.stdout) if d is not None]
    except yaml.YAMLError as error:
        return {}, 0, f"helm rendered something that is not YAML: {one_line(error)}"

    where = sources(render.stdout)
    found: dict[str, tuple[dict, str | None]] = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        if document.get("kind") != "Service":
            continue
        name = (document.get("metadata") or {}).get("name", "<unnamed>")
        # helm reports `<chart name>/templates/service.yaml`; on disk that is
        # `<chart directory>/templates/service.yaml`.
        rendered = where.get(name)
        on_disk = None
        if rendered and "/" in rendered:
            on_disk = str(chart_directory / rendered.split("/", 1)[1])
        found[name] = (document.get("spec") or {}, on_disk)
    return found, len(documents), None


def at_line(path: str | None, field: str) -> str:
    """`file:line` for the field somebody has to edit, or the file, or nothing."""
    if not path:
        return ""
    source = Path(path)
    if not source.is_file():
        return f"{path}: "
    pattern = re.compile(rf"^\s*{re.escape(field)}\s*:")
    for number, line in enumerate(source.read_text().splitlines(), start=1):
        if pattern.match(line):
            return f"{path}:{number}: "
    return f"{path}: "


def shown(value: object) -> str:
    """A field's rendered value, or the fact that the chart does not render it."""
    return "absent" if value is None else repr(value)


def procedure(
    name: str, field: str, before: object, after: object, path: str | None
) -> str:
    return (
        f"{at_line(path, field)}"
        f"`{name}`: `spec.{field}` changes from {shown(before)} to {shown(after)}. "
        f"`kubectl explain service.spec.{field}` says this field \"may not be "
        f'changed through updates unless the type field is also being changed to '
        f'ExternalName" -- and no chart here renders `spec.type`, so that escape '
        f"clause is not available. `argocd/applicationsets/modules.yaml` syncs "
        f"with `CreateNamespace=true` alone -- no `Replace`, no "
        f"`ServerSideApply` -- so Argo CD either retries the rejection forever, "
        f"or, where the chart drops the field, leaves the live Service holding "
        f"its old value. Either way this diff does not reach the cluster, and "
        f"nothing reports it as an operator would notice. Ledger 615 is this "
        f"exact change and it cost about ninety seconds of failed logins. "
        f"TO SHIP IT: merge, then delete the Service by hand "
        f"(`kubectl -n yadgar delete svc {name}`) and let the next reconciliation "
        f"recreate it -- the Service does not exist while that happens, so do it "
        f"deliberately rather than discovering it. Per-Service "
        f"`argocd.argoproj.io/sync-options: Force=true,Replace=true` automates "
        f"the same delete-and-recreate; it is not enabled estate-wide because it "
        f"would make every Service change a silent brief outage. If this diff is "
        f"NOT meant to reach a live Service, say so in the pull request body."
    )


def main() -> int:
    if not CHART.is_dir():
        print(
            "No `chart/` directory in this repository, so it renders no Service "
            "and there is nothing here to compare. Nothing was inspected, and "
            "that is a fact about the repository rather than a check declining "
            "to run."
        )
        return 0

    revision, refusal = base_ref()
    if refusal:
        print(f"::error::{refusal}")
        return 1

    head, rendered, failure = services(CHART)
    if failure:
        print(
            f"::error::`helm template` failed on the chart in this working tree, "
            f"so no Service could be read and this gate cannot speak: {failure}"
        )
        return 1

    if rendered < MINIMUM_DOCUMENTS:
        print(
            f"::error::`helm template` succeeded on `{CHART}` and produced "
            f"{rendered} document(s), and {MINIMUM_DOCUMENTS} is the fewest this "
            f"gate can read. A chart that renders NOTHING is not a chart without "
            f"a Service, it is a render that said nothing -- and in that state "
            f"this gate reports agreement having read one empty side. Check what "
            f"`helm template {CHART}` prints."
        )
        return 1

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        if not chart_at(revision, root):
            print(
                f"`{CHART}` does not exist at `{revision}`, so this chart is new "
                f"on this branch and there is no earlier Service to compare "
                f"against. {len(head)} Service(s) rendered here."
            )
            return 0
        base, _, failure = services(root / CHART)

    if failure:
        print(
            f"::error::`helm template` failed on the chart as of `{revision}`, so "
            f"the comparison has only one side: {failure}"
        )
        return 1

    # THE DERIVED FLOOR. See `MINIMUM_DOCUMENTS` above for why it is not a
    # constant. A Service the chart used to render and no longer does is exactly
    # the state in which the comparison below has nothing to compare, and Argo
    # prunes what a chart stops rendering -- so this is a deletion, not a silence.
    missing = sorted(set(base) - set(head))
    if missing:
        print(
            f"::error::this chart rendered {len(base)} Service(s) at `{revision}` "
            f"and {len(head)} here; "
            + ", ".join(f"`{name}`" for name in missing)
            + " is gone. Argo CD prunes what a chart stops rendering, so this "
            "DELETES the live Service and everything dialling it stops. If a "
            "template was renamed or a condition flipped, that is the defect. If "
            "the deletion is intended, say so in the pull request body -- this "
            "gate refuses rather than guessing, because a Service that vanishes "
            "from the render is also how it would report agreement having "
            "compared nothing."
        )
        return 1

    problems: list[str] = []
    inspected = 0
    fields_compared = 0

    for name in sorted(head):
        if name not in base:
            # A Service this branch ADDS has no deployed counterpart to conflict
            # with -- the apiserver creates it. Not a diff to an immutable field.
            continue
        inspected += 1
        before, _ = base[name]
        after, path = head[name]
        for field in ALWAYS:
            fields_compared += 1
            if before.get(field) != after.get(field):
                problems.append(
                    procedure(name, field, before.get(field), after.get(field), path)
                )

    # THE COUNT IS PRINTED WHETHER OR NOT ANYTHING IS WRONG, so a reader can tell
    # "compared six fields across one Service and they agree" from "compared
    # nothing". The two look identical without it, and only one of them is a pass.
    summary = (
        f"Compared {fields_compared} immutable field(s) across {inspected} "
        f"Service(s) present in both this tree and `{revision}` "
        f"({len(head)} rendered here, {len(base)} there)."
    )

    if not problems:
        print(f"{summary} No immutable field changes.")
        return 0

    print(f"::error::{summary}")
    for problem in problems:
        print(f"::error::{problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
