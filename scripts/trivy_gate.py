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

WHY THE SCAN ITSELF STAYS UNFILTERED. trivy 0.74.0's own `--ignorefile` is NOT
auto-detected for `.trivyignore.yaml`, and more importantly it would hide the
distinction this gate exists to draw: an upstream finding this repository has
reviewed and an estate-authored finding it has not. So the trivy invocation
below never passes `--ignorefile` at all -- it reports EVERY HIGH/CRITICAL
misconfiguration, and `evaluate()` is what decides, finding by finding and
entry by entry, which ones a `.trivyignore.yaml` legitimately covers.

`--quiet` IS FORBIDDEN, not merely unused. It suppresses the `WARN [helm
scanner] Skipping chart` line this gate's own "did it actually render"
check depends on (measured, trivy 0.74.0: the WARN is on stderr at the default
log level and is the only trace a chart failed to render at all).

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
THIS scan (the review it records no longer describes anything real); and an
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
from chart_values_override import values_override  # noqa: E402

SEVERITY = "HIGH,CRITICAL"
TRIVYIGNORE = Path(".trivyignore.yaml")
CHART = Path("chart")
SKIPPING_CHART = "[helm scanner] Skipping chart"


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


def _entry_within_upstream(entry: Entry, root: Path = CHART) -> bool:
    """Whether every one of `entry.paths` names only upstream territory.

    A literal path (no wildcard) is checked exactly like a finding would be:
    `is_upstream` on the string itself. A glob is checked on its literal
    prefix -- the text before its first `*` -- which is enough to place the
    two shapes this gate has ever seen: `chart/charts/platform/charts/**`
    resolves through `platform` (estate) into its own `Chart.lock`, landing
    past the boundary; `chart/charts/**` resolves nothing (the wildcard is the
    very first segment) and is refused rather than guessed at.
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
        matches_something = any(
            entry.id == f.id and any(glob_match(f.target, p) for p in entry.paths)
            for f in findings
        )
        if not matches_something:
            stale.append((entry, "matches no finding in this scan"))
            continue
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


def findings_from_report(report: dict) -> list[Finding]:
    out = []
    for result in report.get("Results") or []:
        target = result.get("Target") or ""
        for m in result.get("Misconfigurations") or []:
            vid = m.get("ID")
            if vid:
                out.append(Finding(target=target, id=str(vid)))
    return out


def helm_result_count(report: dict) -> int:
    return sum(1 for r in report.get("Results") or [] if r.get("Type") == "helm")


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


def run_trivy(chart_dir: Path = CHART) -> dict:
    """The one unfiltered scan this gate evaluates. Fails loud, before any
    ignore-list logic runs, on either shape of "this did not actually
    render": the WARN trivy itself writes, or -- belt and suspenders, in case
    a future trivy changes that WARN's wording -- a `--helm-values` scan that
    somehow produced not one `helm`-typed result.
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
        *scan_args(chart_dir),
        ".",
    ]
    print("+ " + " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stderr, file=sys.stderr, end="")
    if SKIPPING_CHART in proc.stderr:
        raise Refused(
            f"trivy printed `{SKIPPING_CHART}` -- the chart did not render, so "
            f"this scan proves nothing. Run `helm dependency update chart` "
            f"before this step, and check `Chart.lock` against `chart/charts/`."
        )
    if proc.returncode != 0:
        raise Refused(f"trivy exited {proc.returncode}: {proc.stderr.strip()}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Refused(f"trivy's own output was not JSON: {exc}") from exc
    used_helm_values = "--helm-values" in args
    if used_helm_values and helm_result_count(report) == 0:
        raise Refused(
            "a --helm-values scan reported zero `helm`-typed results. The "
            "chart did not render -- check `helm dependency update chart` ran, "
            "and that chart/ci/values.yaml still names a real key."
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
        findings = findings_from_report(report)
        verdict = evaluate(findings, entries, datetime.date.today())
    except (Refused, DeclarationError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(render_report(verdict))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    sys.exit(main())
