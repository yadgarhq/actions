#!/usr/bin/env python3
"""Trivy renders what it scans (ledger 1150), and this is the gate that proves it.

THE BUG THIS REPLACES. The `vulnerabilities` job ran `aquasecurity/trivy-action`
over `scan-type: fs`, whose misconfig scanner also walks a `chart/` directory as
helm. That action never resolves a chart's dependencies first, and trivy's own
behaviour on an unresolved chart is not an error -- it is a WARN
(`[helm scanner] Skipping chart ... missing in charts/ directory`) and zero
findings for that chart. A plain `exit-code: 1` with zero findings reads as
"clean", so the CI step was silently scanning nothing while reporting success
either way. The action also has no `--helm-values` / `--helm-api-versions`
input, so even a resolved chart renders at `chart/values.yaml`'s own defaults
rather than the estate's CI values -- a different, narrower render than every
other shared gate (`helm-lint`, `d80_portability.py`, `service_immutable.py`)
already uses.

THE FIX RUNS THE INSTALLED trivy BINARY DIRECTLY (not the action), after
`helm dependency update chart`, against the SAME scan values every other shared
gate reads: `chart_values_override.values_override` for `chart/ci/values.yaml`
and `api_versions.declared_api_versions` for `chart/ci/api-versions.txt`. One
reader each, not a second copy of either parse (ADR-0806, C-A2).

ONE PASS PER CI VALUES FILE (yaadgaar #179, ruled 2026-11-01, option (a)).
`chart/ci/values.yaml` is the primary pass. Every `chart/ci/*-values.yaml` is
one more pass, rendered ALONE with the same `--helm-api-versions`, never
layered over the primary file: `platform`'s mixed-release guard (ADR-0787)
refuses operators and estate objects in one render, so its estate templates
can only be scanned under a second, estate-shaped values file. Every refusal
below runs PER PASS and names the values file it came from -- a healthy
primary pass must never mask an extra file that rendered nothing. Findings
from every pass are merged before `evaluate()` sees them, so an ignore entry
is judged against the union. Duplicates are removed ACROSS passes only, never
inside one: for each (target, id, resource) key the merge keeps the largest
count any single pass reported, so a one-pass run lists exactly what it
listed before extra passes existed (`merge_pass_results`).
Only this gate reads the extra files; `helm-lint`, `d80_portability.py` and
`service_immutable.py` keep the primary file alone.

WHY THE SCAN ITSELF STAYS UNFILTERED, AND WHY `--config` AND `--ignorefile`
BOTH POINT AT `/dev/null`. `.trivyignore.yaml` is not auto-detected, but trivy
auto-loads TWO OTHER FILES from the working directory whether asked to or
not: a plain `.trivyignore` (bare IDs, one per line) and `trivy.yaml` (a
`scan: skip-dirs:` entry there silently removes a whole directory from the
scan). Measured: either file, dropped into a chart-bearing repository with no
flag naming it at all, turned a real finding on a privileged pod green.
Neither is what this gate's own review record is -- an ID with no path, no
reason and no expiry -- so both are switched off entirely (`--config
/dev/null --ignorefile /dev/null`) and the scan reports EVERY HIGH/CRITICAL
misconfiguration with nothing trivy itself could have pre-filtered.
`evaluate()` is what decides, finding by finding and entry by entry, which
ones a `.trivyignore.yaml` legitimately covers.

THIS REPOSITORY'S OWN `.trivyignore` STAYS HONOURED, DELIBERATELY, for
exactly the findings it already covers and no others. `DS-0002` on
`containers/rust-build/Containerfile` is a real, reviewed, pre-existing
suppression (see that file) that predates this gate and has nothing to do
with a rendered chart. `apply_legacy_ignore` re-implements just enough of
trivy's own plain-`.trivyignore` reading to keep that working -- an ID listed
there drops a finding ONLY when the finding's `Target` does not start with
`chart/`. A chart finding can never be suppressed this way: the plain format
carries no path, so an entry meant for one Containerfile line would silently
cover a same-numbered check anywhere in the repository, chart included, if it
were honoured there too.

`--quiet` IS FORBIDDEN, not merely unused. It suppresses the `WARN [helm
scanner] Skipping chart` line, the only human-readable trace of WHY a chart did
not render (the gate itself refuses on a zero `helm` result count; measured, trivy 0.74.0: the WARN is on stderr at the default log
level and is the only trace a chart failed to render at all).

`--skip-check-update` AND `--skip-version-check` are set for the same reason
every other install in this repository is pinned by exact version rather
than "latest": the alternative is `trivy-checks`, a bundle this binary would
otherwise fetch over the network on every run, floating independently of the
`trivy` binary's own pin. A network hiccup or an upstream check rename is
then a false CI failure that has nothing to do with the change under review.
The checks embedded in the pinned 0.74.0 binary are what this gate's own
tests and its real-repository proof were measured against (`run_trivy`
refuses outright if the binary on `PATH` is not that exact version).

THE UPSTREAM BOUNDARY IS DERIVED, NEVER A PER-REPOSITORY CONSTANT. A finding at
`chart/charts/<X>/...` is this repository's own if `<X>` is a dependency THIS
chart's `Chart.lock` names with an `oci://ghcr.io/yadgarhq/charts` repository --
an estate-published module or the `platform` layer -- and upstream otherwise.
When `<X>` is estate-published, the same question recurses one level deeper
against `<X>`'s OWN `Chart.lock` -- NOT a `chart/charts/<X>/Chart.lock` file on
disk, because `helm dependency update` never extracts a dependency: it writes
`chart/charts/<X>-<version>.tgz` and nothing else (measured against
`yadgarhq/chart`'s own parent: nine `.tgz` files, zero subdirectories). So the
walk opens that archive and reads member `<X>/Chart.lock` out of it, the same
thing `helm` and `trivy` themselves do internally -- an estate chart may itself
vendor a genuinely third-party dependency (`platform` vendors `nats`, five
operators and `prometheus`; no module chart vendors anything of its own).
Measured against the two repositories that have findings today: walking
`yadgarhq/platform`'s own `chart/Chart.lock` for `nats` answers "not ours" in
one step, giving the upstream boundary `chart/charts/**`; walking
`yadgarhq/chart`'s `chart/Chart.lock` for `platform` answers "ours", so the walk
opens `chart/charts/platform-<version>.tgz` and reads `platform/Chart.lock`
from inside it for `nats`, answering "not ours" there, giving
`chart/charts/platform/charts/**`. Neither string is written down anywhere in
this file -- both are computed from the `Chart.lock`s `helm dependency update`
already produced, so a repository that starts vendoring a new upstream chart,
or a module that starts vendoring one of its own, needs no edit here to be
covered correctly on its next scan.

WHY NOT `fnmatch`. A `.trivyignore.yaml` entry's `paths` are a tiny glob
language where a single `*` matches one path segment (never crosses `/`) and
`**` matches any number of them, INCLUDING zero -- the same distinction
trivy's own path matching draws. Python's `fnmatch.fnmatch` draws no such
distinction: its `*` already matches `/` exactly as `**` would, so
`chart/charts/*` would silently also cover
`chart/charts/platform/charts/nats/...` -- the one thing an upstream-scoped
glob must NOT do by accident. `_glob_regex` below compiles the two forms
differently on purpose.

`expired_at` LOADS AS `datetime.date`, NOT A STRING, the moment it is written
unquoted in YAML -- which is how every entry in this estate's own
`.trivyignore.yaml` files is written (measured: `yaml.safe_load` on
`yadgarhq/chart#33`'s merged file returns `datetime.date(2027, 1, 8)` for
`expired_at`, never `"2027-01-08"`). `_as_date` accepts both shapes so a
quoted date -- legal YAML, just not how any file here is written -- is not a
second code path that silently never runs.

WHAT "STALE" MEANS, and it is four distinct reasons, every one reported by
name rather than folded into one message: an entry with no `paths` (would
otherwise ignore its `id` EVERYWHERE, including an estate template with the
same check); an entry whose `paths` reach outside the upstream boundary above
(the parent-repository mistake this gate's own tests construct: `paths:
["chart/charts/**"]` on the `yadgarhq/chart` side also covers
`chart/charts/iam/...`, an estate module); an entry that matches NO finding in
THIS scan, or one of whose `paths` matches none (checked per path; the review
it records no longer describes anything real); and an
entry whose `expired_at` is today or in the past (the review lapsed). A finding
is "unignored" when it sits inside the upstream boundary and no entry currently
covers it. A finding OUTSIDE the upstream boundary is never ignorable at all --
estate-authored code, by construction -- and fails regardless of what any entry
claims.

Run: python3 -m pytest scripts/tests/ -q
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_versions import DeclarationError, declared_api_versions  # noqa: E402
from chart_values_override import extra_values_files, values_override  # noqa: E402

SEVERITY = "HIGH,CRITICAL"
TRIVYIGNORE = Path(".trivyignore.yaml")
LEGACY_TRIVYIGNORE = Path(".trivyignore")
CHART = Path("chart")
SKIPPING_CHART = "[helm scanner] Skipping chart"
# The one version this gate's own tests, and its real-repository proof, were
# measured against. `trivy-action` installs its OWN default version (0.70.0,
# measured) and puts it on `PATH` ahead of anything installed before it ran
# -- pinning `version:` on that step is the actual fix; this is the belt that
# catches the day someone removes the pin instead of the suspenders.
TRIVY_VERSION = "0.74.0"


class Refused(Exception):
    """A malformed input the gate will not guess its way past."""


# --------------------------------------------------------------- the glob


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile one `.trivyignore.yaml` `paths` entry. `*` never crosses `/`;
    `**` always may, including matching zero characters. Everything else in
    the pattern is a literal, escaped before compiling.
    """
    parts: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                parts.append(".*")
                i += 2
            else:
                parts.append("[^/]*")
                i += 1
        elif c == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


def glob_match(target: str, pattern: str) -> bool:
    """Whether `target` -- a trivy finding's `Target`, repository-relative --
    matches one `.trivyignore.yaml` `paths` glob."""
    return _glob_regex(pattern).match(target) is not None


# -------------------------------------------------- the derived upstream line


def _lock_text(directory: Path | None, archive: Path | None, chart_name: str) -> str | None:
    """The text of one `Chart.lock`, from wherever `helm dependency update`
    actually leaves it.

    THE REPOSITORY'S OWN `chart/` IS A REAL DIRECTORY -- it is the chart under
    review, never packaged by this step -- so `directory` is set and
    `Chart.lock` is a loose file beside `Chart.yaml`.

    EVERY VENDORED DEPENDENCY IS NOT. `helm dependency update` writes each one
    as `<name>-<version>.tgz` and does not extract it (measured: after that
    command against `yadgarhq/chart`'s parent, `chart/charts/` holds nine
    `.tgz` files and no subdirectories at all) -- so a dependency's own
    `Chart.lock` has to be read out of its archive, member `<name>/Chart.lock`,
    the same thing `helm` and `trivy` themselves do internally. `archive` and
    `chart_name` are set for this case; `directory` is not.
    """
    if directory is not None:
        path = directory / "Chart.lock"
        return path.read_text() if path.is_file() else None
    if archive is None or not archive.is_file():
        return None
    try:
        with tarfile.open(archive) as tar:
            member = tar.extractfile(f"{chart_name}/Chart.lock")
            return member.read().decode("utf-8") if member is not None else None
    except (tarfile.TarError, UnicodeDecodeError):
        return None


def _dependency(lock_text: str | None, name: str) -> dict | None:
    """`name`'s entry in a `Chart.lock` already read as text; `None` if
    `lock_text` is absent or `name` is not declared in it."""
    if lock_text is None:
        return None
    try:
        doc = yaml.safe_load(lock_text) or {}
    except yaml.YAMLError:
        return None
    for dep in doc.get("dependencies") or []:
        if isinstance(dep, dict) and dep.get("name") == name:
            return dep
    return None


def _is_estate_published(repository: object) -> bool:
    return isinstance(repository, str) and repository.startswith("oci://ghcr.io/yadgarhq/charts")


def is_upstream(target: str, root: Path = CHART) -> bool:
    """Whether `target` -- repository-relative, as trivy reports it -- sits in
    a vendored THIRD-PARTY chart rather than this estate's own templates.

    Walks `chart/charts/<a>/charts/<b>/...` one named dependency at a time,
    asking each level's OWN `Chart.lock` whether the next name is published by
    this estate. The walk stops and answers "upstream" the first time a name
    IS a declared dependency and is NOT estate-published. A name that is not a
    declared dependency AT ALL defaults to "not upstream" -- the conservative,
    fail-closed reading, and one a `Target` trivy itself produced should never
    reach, since trivy only names paths it found on disk under a chart
    `helm dependency update` actually resolved. Bottoming out at a real file
    with every step along the way estate-published also answers "not
    upstream": the file is this estate's own template.
    """
    if not target.startswith("chart/charts/"):
        return False
    remainder = target[len("chart/charts/") :]
    lock_text = _lock_text(directory=root, archive=None, chart_name="")
    charts_dir = root / "charts"
    while True:
        name, _, rest = remainder.partition("/")
        dep = _dependency(lock_text, name)
        if dep is None:
            return False
        if not _is_estate_published(dep.get("repository")):
            return True
        if not rest.startswith("charts/"):
            # Estate-published, and there is no further `charts/` nesting left
            # in the path: this file belongs to `name`'s own templates.
            return False
        archive = charts_dir / f"{name}-{dep.get('version')}.tgz" if charts_dir else None
        lock_text = _lock_text(directory=None, archive=archive, chart_name=name)
        # A deeper level (an estate chart vendoring an estate chart that
        # itself vendors something) would need archive-of-an-archive reading
        # this does not attempt -- unreached today (measured: no chart in
        # this estate vendors one of its own siblings) and safe if it ever
        # happens, since the next `_dependency` call on a `None` lock answers
        # "not upstream" rather than guessing.
        charts_dir = None
        remainder = rest[len("charts/") :]


# ------------------------------------------------------- the ignore entries


@dataclass(frozen=True)
class Entry:
    id: str
    paths: list[str]
    statement: str
    expired_at: datetime.date | None
    index: int  # 1-based position in the file, for messages only


def _as_date(value: object) -> datetime.date | None:
    """`expired_at` as written: a bare `datetime.date` (unquoted YAML, the
    shape every real file here uses) or an ISO string (quoted YAML, legal but
    unused). Anything else -- including absent -- is `None`, and `None` is
    treated as "already expired": an entry with no stated expiry reviews
    nothing.
    """
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def load_entries(path: Path) -> list[Entry]:
    """Every `misconfigurations:` entry in `path`; `[]` if `path` is absent.

    A PRESENT-BUT-UNPARSEABLE FILE REFUSES rather than running the gate as if
    it were empty -- an empty ignore list and a broken one both start red, but
    only one of them should.
    """
    if not path.is_file():
        return []
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise Refused(f"{path} is not valid YAML: {exc}") from exc
    raw = doc.get("misconfigurations") or []
    if not isinstance(raw, list):
        raise Refused(f"{path}: `misconfigurations` is not a list.")
    entries = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not item.get("id"):
            raise Refused(f"{path}: entry {i} has no `id`.")
        paths = item.get("paths")
        entries.append(
            Entry(
                id=str(item["id"]),
                paths=[str(p) for p in paths] if isinstance(paths, list) else [],
                statement=str(item.get("statement") or ""),
                expired_at=_as_date(item.get("expired_at")),
                index=i,
            )
        )
    return entries


# --------------------------------------------------------------- the verdict


@dataclass(frozen=True)
class Finding:
    target: str
    id: str
    # The key part that tells two findings of one check in one file apart,
    # used only to merge passes (`merge_pass_results`): `CauseMetadata.Resource`
    # when trivy sets it, else `Message`. Measured, trivy 0.74.0 sets NO
    # `Resource` on a helm finding (platform, 2026-11-01), so `Message` is the
    # best key trivy gives -- and it is NOT unique per object: the nats
    # StatefulSet yields KSV-0118 "container nats in default namespace is
    # using the default security context" twice, for two different blocks
    # (likewise KSV-0106, KSV-0030). That is why the merge keeps the largest
    # per-pass count instead of collapsing a key to one. `StartLine` is no
    # key: the same object sits on different lines under different values.
    # The verdict is unaffected either way -- `evaluate()` keys on (target, id).
    resource: str = ""


def _entry_within_upstream(entry: Entry, root: Path = CHART) -> bool:
    """Whether every one of `entry.paths` names only upstream territory.

    A literal path (no wildcard) is checked exactly like a finding would be:
    `is_upstream` on the string itself, minus its last segment (the probe is
    a directory, not a file). A glob is checked the same way on its literal
    prefix -- the text before its first `*`, rounded down to the last
    complete segment.

    BOTH SHAPES THIS GATE HAS EVER SEEN ARE REFUSED, not accepted, and that
    is FAIL-SAFE rather than a gap: `chart/charts/**`'s prefix rounds down to
    `chart/charts`, which names no dependency at all and refuses; even
    `chart/charts/platform/charts/**` -- the textually correct, narrower
    boundary for the parent repository -- rounds its prefix down to
    `chart/charts/platform/charts`, a bare `charts` segment with no name
    after it for `is_upstream` to look up, so IT refuses too. This function
    only accepts a glob whose literal prefix survives all the way down to a
    real file (a `paths` entry this estate's own reviewed files always
    write, per `chart#33`) -- a glob that stops at a directory boundary is
    refused rather than guessed at, which is the conservative direction: an
    entry too narrow to exercise is loud and harmless, never a silent extra
    ignore.
    """
    for pattern in entry.paths:
        literal_prefix = pattern.split("*", 1)[0]
        if not literal_prefix or not literal_prefix.startswith("chart/charts/"):
            return False
        # A prefix ending mid-segment (no wildcard was anchored right after a
        # `/`) can only be judged by rounding it down to the last complete
        # segment -- rounding up would accept a pattern that happens to share
        # a prefix with an upstream name without actually naming it.
        if literal_prefix.endswith("/"):
            probe = literal_prefix.rstrip("/")
        else:
            probe = literal_prefix.rsplit("/", 1)[0]
        if not is_upstream(probe, root):
            return False
    return True


@dataclass
class Verdict:
    estate_findings: list[Finding]
    unignored_findings: list[Finding]
    stale_entries: list[tuple[Entry, str]]

    @property
    def ok(self) -> bool:
        return not (self.estate_findings or self.unignored_findings or self.stale_entries)


def evaluate(
    findings: list[Finding],
    entries: list[Entry],
    today: datetime.date,
    root: Path = CHART,
) -> Verdict:
    """The whole gate, as pure data in and data out -- no trivy, no files.

    EVERY ENTRY IS JUDGED ONCE, independent of whether any finding happens to
    need it: a stale entry is a problem in the REVIEW RECORD, not only in
    today's scan, so an entry with no `paths` is reported even if no current
    finding shares its `id`.
    """
    estate: list[Finding] = []
    upstream: list[Finding] = []
    for f in findings:
        (upstream if is_upstream(f.target, root) else estate).append(f)

    stale: list[tuple[Entry, str]] = []
    valid_entries: list[Entry] = []
    for entry in entries:
        if not entry.paths:
            stale.append((entry, "has no `paths`, so it would ignore its `id` everywhere"))
            continue
        if not _entry_within_upstream(entry, root):
            stale.append((entry, "names `paths` outside this repository's upstream boundary"))
            continue
        # STALENESS IS PER PATH, not per entry: a path that matches no
        # finding of this `id` is a dead line in the review record that would
        # silently pre-approve a future finding there. A glob path is live
        # if it matches at least one finding. When EVERY path is dead the
        # one entry-level reason stays; otherwise each dead path is reported
        # by name. A partially stale entry still reaches `valid_entries`
        # below -- harmless, `Verdict.ok` is already false once anything is stale.
        dead = [
            p
            for p in entry.paths
            if not any(entry.id == f.id and glob_match(f.target, p) for f in findings)
        ]
        if len(dead) == len(entry.paths):
            stale.append((entry, "matches no finding in this scan"))
            continue
        for p in dead:
            stale.append((entry, f"path `{p}` matches no `{entry.id}` finding in this scan"))
        if entry.expired_at is None or entry.expired_at <= today:
            stale.append((entry, f"expired ({entry.expired_at})" if entry.expired_at else "has no `expired_at`"))
            continue
        valid_entries.append(entry)

    unignored = [
        f
        for f in upstream
        if not any(
            e.id == f.id and any(glob_match(f.target, p) for p in e.paths)
            for e in valid_entries
        )
    ]

    return Verdict(estate_findings=estate, unignored_findings=unignored, stale_entries=stale)


# ------------------------------------------------------------- trivy itself


def _resource_key(misconfiguration: dict) -> str:
    cause = misconfiguration.get("CauseMetadata") or {}
    return str(cause.get("Resource") or misconfiguration.get("Message") or "")


def findings_from_report(report: dict) -> list[Finding]:
    out = []
    for result in report.get("Results") or []:
        target = result.get("Target") or ""
        for m in result.get("Misconfigurations") or []:
            vid = m.get("ID")
            if vid:
                out.append(Finding(target=target, id=str(vid), resource=_resource_key(m)))
    return out


def merge_pass_results(passes: list[list[dict]]) -> list[dict]:
    """One `Results` list out of every pass's, duplicates removed ACROSS
    passes only. The first pass is kept whole. A later pass contributes, per
    (target, id, resource) key, only the occurrences beyond the largest count
    already kept -- so the merged count of a key is the largest count any
    single pass reported. Two same-key findings inside ONE pass both survive
    (trivy's `Message` is not unique per object, see `Finding.resource`); a
    template that renders the same object under two values files is one.
    A later pass's extra occurrences ride in a copy of its result, which keeps
    that result's `Target` and `Type`.
    """
    merged: list[dict] = []
    kept: dict[tuple[str, str, str], int] = {}
    for results in passes:
        counts: dict[tuple[str, str, str], int] = {}
        additions: list[dict] = []
        for result in results:
            target = result.get("Target") or ""
            extra = []
            for m in result.get("Misconfigurations") or []:
                key = (target, str(m.get("ID")), _resource_key(m))
                counts[key] = counts.get(key, 0) + 1
                if counts[key] > kept.get(key, 0):
                    extra.append(m)
            if not merged:
                additions.append(result)
            elif extra:
                additions.append({**result, "Misconfigurations": extra})
            elif not result.get("Misconfigurations"):
                additions.append(result)
        for key, n in counts.items():
            kept[key] = max(kept.get(key, 0), n)
        merged += additions
    return merged


def helm_result_count(report: dict, under: str = "") -> int:
    """`helm`-typed results, only those whose Target starts with `under`."""
    return sum(
        1
        for r in report.get("Results") or []
        if r.get("Type") == "helm" and (r.get("Target") or "").startswith(under)
    )


def finding_count(report: dict) -> int:
    return sum(len(r.get("Misconfigurations") or []) for r in report.get("Results") or [])


def scan_args(chart_dir: Path = CHART) -> list[str]:
    """`--helm-values`/`--helm-api-versions` for `chart_dir`, read off the same
    two declarations every other shared gate reads (ADR-0806, C-A2) -- never a
    third parse of either file.
    """
    args: list[str] = []
    override = values_override(chart_dir)
    if override is not None:
        args += ["--helm-values", str(override)]
    for version in declared_api_versions(chart_dir):
        args += ["--helm-api-versions", version]
    return args


def scan_passes(chart_dir: Path = CHART) -> list[tuple[str, list[str]]]:
    """`(label, flags)` per pass: the primary pass first (exactly
    `scan_args`, unchanged), then one per `ci/*-values.yaml`, each with ONLY
    its own `--helm-values` plus the same `--helm-api-versions`. `label` is
    the values file, for log lines and refusals."""
    override = values_override(chart_dir)
    primary = str(override) if override is not None else f"chart defaults (no {chart_dir}/ci/values.yaml)"
    passes = [(primary, scan_args(chart_dir))]
    api: list[str] = []
    for version in declared_api_versions(chart_dir):
        api += ["--helm-api-versions", version]
    for extra in extra_values_files(chart_dir):
        passes.append((str(extra), ["--helm-values", str(extra), *api]))
    return passes


def installed_trivy_version() -> str:
    proc = subprocess.run(["trivy", "version", "--format", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Refused(f"`trivy version` exited {proc.returncode}: {proc.stderr.strip()}")
    try:
        version = json.loads(proc.stdout)["Version"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise Refused(f"`trivy version --format json` did not report a Version: {exc}") from exc
    if not isinstance(version, str):
        raise Refused(f"`trivy version --format json` reported a non-string Version: {version!r}")
    return version


def require_trivy_version(actual: str, expected: str = TRIVY_VERSION) -> None:
    """Refuse before scanning anything if `actual` is not the exact pinned
    version. NEEDED BECAUSE THE PIN CAN SILENTLY LOSE: measured,
    `aquasecurity/trivy-action` with no `version:` input installs its OWN
    default (0.70.0) into its own bin directory and PREPENDS that onto
    `$GITHUB_PATH` for every later step in the job -- so a separate install of
    the right version, run AFTER that step, is shadowed rather than used. The
    version matters beyond cosmetics: 0.70.0 has no `Skipping chart` WARN at
    all (it writes `ERROR Failed to render Chart files` instead), so this
    gate's own render-check would silently stop checking what it claims to.
    """
    if actual != expected:
        raise Refused(
            f"trivy on PATH reports version {actual!r}, not the pinned "
            f"{expected!r}. If this is CI: `aquasecurity/trivy-action` "
            f"installs its own default version and puts it on PATH ahead of "
            f"anything installed before it ran -- pin `version:` on that "
            f"step itself, do not install a second copy after it."
        )


def load_legacy_ids(path: Path = LEGACY_TRIVYIGNORE) -> set[str]:
    """Every bare ID in a plain, pre-existing `.trivyignore` -- the format
    trivy itself auto-loads from the working directory, which this gate now
    refuses with `--ignorefile /dev/null` (see the module docstring for why).
    `#` starts a comment anywhere on a line; a blank line is skipped. `set()`
    if `path` is absent. A line carrying trivy's `ID exp:DATE` suffix is
    REFUSED: read whole it would become the bogus ID `"ID exp:DATE"`, match no
    finding, and the entry would silently stop working with its expiry never
    enforced. The reviewed format with expiry is `.trivyignore.yaml`.
    """
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if re.search(r"(^|\s)exp:", entry):
            raise Refused(
                f"{path}: `{entry}` carries an `exp:` expiry, which this gate does "
                f"not read from the plain format. Move the entry to "
                f"`.trivyignore.yaml` (paths + expired_at)."
            )
        if entry:
            ids.add(entry)
    return ids


def apply_legacy_ignore(findings: list[Finding], legacy_ids: set[str]) -> list[Finding]:
    """Drop a finding whose `id` is in `legacy_ids` -- ONLY when its `Target`
    does not start with `chart/`. The plain format carries no path at all,
    so letting it reach a chart finding would make a review written for one
    Containerfile line silently cover a same-numbered check anywhere a chart
    renders, estate-authored templates included -- the exact hole closing
    `--ignorefile` opened in the first place.
    """
    return [f for f in findings if not (f.id in legacy_ids and not f.target.startswith("chart/"))]


def run_trivy(chart_dir: Path = CHART) -> dict:
    """The unfiltered scans this gate evaluates, one per CI values file
    (`scan_passes`), their `Results` concatenated into one report. Refuses
    before scanning anything if the installed trivy is not the pinned version.
    """
    version = installed_trivy_version()
    require_trivy_version(version)
    passes = [
        _run_pass(version, label, flags, chart_dir, extra=i > 0).get("Results") or []
        for i, (label, flags) in enumerate(scan_passes(chart_dir))
    ]
    return {"Results": merge_pass_results(passes)}


def _run_pass(version: str, label: str, flags: list[str], chart_dir: Path, extra: bool = False) -> dict:
    """One trivy scan under one values file. Fails loud, before any
    ignore-list logic runs, on either shape of "this did not actually
    render": the WARN trivy itself writes (any chart), a `--helm-values` scan
    with no `helm`-typed result, or a `chart/Chart.yaml` with no `helm`-typed
    result under `chart/`. Every refusal names `label`, the pass's values file.
    `extra` marks a `ci/*-values.yaml` pass: the primary pass already rendered
    with the same dependencies, so the WARN there most likely means the values
    file itself does not render, and the refusal says so first.
    """
    args = [
        "trivy",
        "config",
        "--format",
        "json",
        "--exit-code",
        "0",
        "--severity",
        SEVERITY,
        # NEITHER trivy's OWN config NOR its OWN ignore file is let anywhere
        # near this scan -- both are auto-loaded from the working directory
        # with NO flag asking for them, and either one can silently drop a
        # real finding (measured: a `trivy.yaml` with `scan: skip-dirs:` and
        # a plain `.trivyignore` each did, independently). `/dev/null` is a
        # valid empty value for both flags, present or absent in this
        # directory makes no difference.
        "--config",
        "/dev/null",
        "--ignorefile",
        "/dev/null",
        # EXPECTED STDERR: with `--skip-check-update` trivy logs an ERROR-level
        # "Falling back to embedded checks" line (it uses the pinned binary's
        # embedded rego checks). That ERROR is expected and is NOT a failure --
        # this gate decides from the JSON report, and echoes stderr unfiltered
        # on purpose (hiding tool output is worse than this comment).
        # The rego check bundle `trivy-checks` floats independently of the
        # `trivy` binary's own pin and is fetched over the network by
        # default; skip both the fetch and its own update notice so this
        # scan is reproducible from the binary alone.
        "--skip-check-update",
        "--skip-version-check",
        *flags,
        ".",
    ]
    print("+ " + " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stderr, file=sys.stderr, end="")
    # KEPT (ADR-0645): trivy skips a chart it cannot render with this WARN, for
    # ANY chart in the tree -- not only `chart/`. The count checks below are
    # added on top of this refusal, never in place of it.
    if SKIPPING_CHART in proc.stderr:
        likely = (
            f"The likely cause is {label} itself: check it with `helm template "
            f"chart -f {label}` and the `chart/ci/api-versions.txt` flags. Else: "
            if extra
            else ""
        )
        raise Refused(
            f"trivy printed `{SKIPPING_CHART}` -- the chart did not render, so "
            f"this scan proves nothing. {likely}Run `helm dependency update chart` "
            f"before this step, and check `Chart.lock` against `chart/charts/`. "
            f"(pass: {label})"
        )
    if proc.returncode != 0:
        raise Refused(f"trivy exited {proc.returncode} (pass: {label}): {proc.stderr.strip()}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Refused(f"trivy's own output was not JSON (pass: {label}): {exc}") from exc
    # Belt and suspenders on top of the WARN match: a `--helm-values` scan
    # with no helm result anywhere refuses (the old check, unchanged), and so
    # does an existing `chart/Chart.yaml` with no helm result UNDER `chart/`
    # (yadgarhq/config has no `chart/ci`, so the old check never ran there).
    # A healthy chart elsewhere in the tree must not mask a dead `chart/`.
    used_helm_values = "--helm-values" in args
    if used_helm_values and helm_result_count(report) == 0:
        raise Refused(
            "a --helm-values scan reported zero `helm`-typed results. The "
            "chart did not render -- check `helm dependency update chart` ran, "
            f"and that {label} still names a real key. (pass: {label})"
        )
    if (chart_dir / "Chart.yaml").is_file() and helm_result_count(report, f"{CHART.as_posix()}/") == 0:
        raise Refused(
            f"a scan of an existing {CHART}/Chart.yaml reported zero `helm`-typed "
            f"results under `{CHART}/`. The chart did not render -- check "
            f"`helm dependency update chart` ran, `Chart.lock` matches "
            f"`chart/charts/`, and the values still name a real key. "
            f"(pass: {label})"
        )
    print(
        f"trivy {version}, {label}: {helm_result_count(report)} helm-typed result(s), "
        f"{finding_count(report)} HIGH/CRITICAL misconfiguration(s)."
    )
    return report


def render_report(v: Verdict) -> str:
    lines: list[str] = []
    if v.estate_findings:
        lines.append(f"{len(v.estate_findings)} finding(s) in this repository's OWN templates:")
        for f in v.estate_findings:
            lines.append(f"  {f.id}  {f.target}")
    if v.unignored_findings:
        lines.append(f"{len(v.unignored_findings)} upstream finding(s) with no covering entry:")
        for f in v.unignored_findings:
            lines.append(f"  {f.id}  {f.target}")
    if v.stale_entries:
        lines.append(f"{len(v.stale_entries)} stale `.trivyignore.yaml` entry/entries:")
        for entry, reason in v.stale_entries:
            lines.append(f"  entry {entry.index} ({entry.id}): {reason}")
    if v.ok:
        lines.append("clean: every upstream finding is covered, no stale entries.")
    return "\n".join(lines)


def main() -> int:
    try:
        report = run_trivy()
        entries = load_entries(TRIVYIGNORE)
        findings = apply_legacy_ignore(findings_from_report(report), load_legacy_ids())
        verdict = evaluate(findings, entries, datetime.date.today())
    except (Refused, DeclarationError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(render_report(verdict))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
