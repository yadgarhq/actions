"""What `trivy_gate.py` asserts about a scan, pinned so it cannot quietly stop
asserting it (ledger 1150, D-A1).

EVERY RED CASE HERE IS ONE THE PLAN ITSELF NAMED before this file existed: the
glob that must not behave like `fnmatch`, the date that loads as a
`datetime.date` rather than a string, the entry with no `paths`, the entry
that outlives its own review, the entry that matches nothing in the scan, and
the one glob mistake a reviewer actually made while writing
`yadgarhq/chart#33` (`chart/charts/**`, which also covers every module chart).
A gate whose behaviour lives only in a pull request description is a gate
nobody re-runs.

Run: python3 -m pytest scripts/tests/ -q
"""

from __future__ import annotations

import datetime
import io
import pathlib
import subprocess
import sys
import tarfile

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import trivy_gate  # noqa: E402

TODAY = datetime.date(2026, 10, 8)


# ------------------------------------------------------------------- globs


def test_single_star_does_not_cross_a_slash():
    """THE fnmatch MISTAKE THIS FUNCTION EXISTS TO AVOID. `fnmatch.fnmatch`
    draws no distinction at all between `*` and `**` -- both already cross
    `/` -- so a naive port would make `chart/charts/*` cover
    `chart/charts/platform/charts/nats/...`, the one thing an upstream-scoped
    single-segment glob must not do."""
    import fnmatch

    assert fnmatch.fnmatch("chart/charts/platform/charts/nats/x.yaml", "chart/charts/*")
    assert not trivy_gate.glob_match("chart/charts/platform/charts/nats/x.yaml", "chart/charts/*")
    assert trivy_gate.glob_match("chart/charts/nats/x.yaml", "chart/charts/*/x.yaml")


def test_doublestar_crosses_any_number_of_slashes_including_zero():
    assert trivy_gate.glob_match(
        "chart/charts/platform/charts/nats/templates/stateful-set.yaml",
        "chart/charts/platform/charts/**",
    )
    assert trivy_gate.glob_match("chart/charts/platform/charts", "chart/charts/**")
    assert trivy_gate.glob_match(
        "chart/charts/platform/charts/nats/templates/stateful-set.yaml",
        "chart/charts/platform/charts/nats/templates/stateful-set.yaml",
    )


# ------------------------------------------------------- the derived boundary


def write_lock(path: pathlib.Path, deps: list[tuple[str, str, str]]) -> None:
    """`deps` is `(name, repository, version)`. This is always THE
    REPOSITORY'S OWN `chart/Chart.lock` in these tests -- a loose file,
    never packaged -- matching how `yadgarhq/chart` and `yadgarhq/platform`
    actually carry theirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"dependencies": [{"name": n, "repository": r, "version": v} for n, r, v in deps]}
    path.write_text(yaml.safe_dump(doc))


def write_dep_archive(charts_dir: pathlib.Path, name: str, version: str, deps: list[tuple[str, str, str]]) -> None:
    """A vendored dependency's `Chart.lock`, packaged exactly the way `helm
    dependency update` actually leaves it: `<name>-<version>.tgz` holding
    `<name>/Chart.lock` and nothing extracted to disk (measured against
    `yadgarhq/chart`'s own parent on 2026-10-08: `chart/charts/` holds nine
    `.tgz` files, zero subdirectories). An `is_upstream` that assumed a real
    `chart/charts/<name>/Chart.lock` directory -- the first shape this gate
    shipped with -- passed every synthetic fixture and was wrong against
    every real repository; this helper is what makes that mistake fail here
    too."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    doc = {"dependencies": [{"name": n, "repository": r, "version": v} for n, r, v in deps]}
    lock_bytes = yaml.safe_dump(doc).encode("utf-8")
    archive = charts_dir / f"{name}-{version}.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name=f"{name}/Chart.lock")
        info.size = len(lock_bytes)
        tar.addfile(info, io.BytesIO(lock_bytes))


YADGAR = "oci://ghcr.io/yadgarhq/charts"


def test_platform_repo_nats_is_upstream_in_one_step(tmp_path):
    """`yadgarhq/platform` scanning itself: `nats` is platform's OWN direct
    dependency, from NATS' own upstream, not this estate."""
    root = tmp_path / "chart"
    write_lock(root / "Chart.lock", [("nats", "https://nats-io.github.io/k8s/helm/charts", "2.14.6")])
    assert trivy_gate.is_upstream("chart/charts/nats/templates/stateful-set.yaml", root)


def test_parent_repo_nats_is_upstream_two_steps_down(tmp_path):
    """`yadgarhq/chart` scanning itself: `platform` is OURS, so the walk
    recurses into platform's OWN `Chart.lock` -- read out of the `.tgz`
    `helm dependency update` actually left, never a `charts/platform/`
    directory -- where `nats` is not."""
    root = tmp_path / "chart"
    write_lock(root / "Chart.lock", [("platform", YADGAR, "0.1.34")])
    write_dep_archive(
        root / "charts",
        "platform",
        "0.1.34",
        [("nats", "https://nats-io.github.io/k8s/helm/charts", "2.14.6")],
    )
    assert trivy_gate.is_upstream(
        "chart/charts/platform/charts/nats/templates/stateful-set.yaml", root
    )


def test_an_estate_modules_own_template_is_not_upstream(tmp_path):
    """`iam` is published by this estate and vendors nothing of its own: its
    templates bottom out estate-owned, never ignorable."""
    root = tmp_path / "chart"
    write_lock(root / "Chart.lock", [("iam", YADGAR, "0.9.1")])
    assert not trivy_gate.is_upstream("chart/charts/iam/templates/deployment.yaml", root)


def test_a_path_outside_chart_charts_is_not_upstream(tmp_path):
    assert not trivy_gate.is_upstream("example/kind/application.yaml", tmp_path / "chart")


def test_an_undeclared_dependency_name_is_not_upstream(tmp_path):
    """A name trivy never actually reports -- not in any `Chart.lock` at this
    level -- defaults to estate-owned rather than upstream. Unreachable for a
    real `Target` trivy produced, since trivy only names paths it found on
    disk; reachable for a mangled `.trivyignore.yaml` path, which is exactly
    where a conservative default belongs."""
    root = tmp_path / "chart"
    write_lock(root / "Chart.lock", [("platform", YADGAR, "0.1.34")])
    assert not trivy_gate.is_upstream("chart/charts/ghost/templates/x.yaml", root)


# --------------------------------------------------------------- expired_at


def test_expired_at_loads_as_a_date_not_a_string(tmp_path):
    """THE RED CASE NAMED BY THE REVIEW: exactly `yadgarhq/chart#33`'s file
    shape -- an unquoted date -- loads through `yaml.safe_load` as
    `datetime.date`, never `str`. A reader that assumed a string (`.split`,
    `.startswith`, `fromisoformat` on something already a `date`) would raise
    on every real file in this estate rather than on a crafted one."""
    path = tmp_path / ".trivyignore.yaml"
    path.write_text(
        "misconfigurations:\n"
        "  - id: KSV-0014\n"
        "    paths:\n"
        "      - \"chart/charts/platform/charts/nats/templates/stateful-set.yaml\"\n"
        "    statement: test\n"
        "    expired_at: 2027-01-08\n"
    )
    raw = yaml.safe_load(path.read_text())
    assert isinstance(raw["misconfigurations"][0]["expired_at"], datetime.date)
    assert not isinstance(raw["misconfigurations"][0]["expired_at"], str)
    entries = trivy_gate.load_entries(path)
    assert entries[0].expired_at == datetime.date(2027, 1, 8)


def test_expired_at_as_a_quoted_string_also_loads(tmp_path):
    """Legal YAML, unused by any real file here -- not a second path that
    silently never runs."""
    path = tmp_path / ".trivyignore.yaml"
    path.write_text(
        "misconfigurations:\n"
        "  - id: KSV-0014\n"
        "    paths: [\"chart/charts/platform/charts/nats/templates/stateful-set.yaml\"]\n"
        "    statement: test\n"
        "    expired_at: \"2027-01-08\"\n"
    )
    entries = trivy_gate.load_entries(path)
    assert entries[0].expired_at == datetime.date(2027, 1, 8)


def test_an_absent_ignorefile_is_an_empty_list(tmp_path):
    assert trivy_gate.load_entries(tmp_path / "nope.yaml") == []


def test_unparseable_ignorefile_refuses_rather_than_running_empty(tmp_path):
    path = tmp_path / ".trivyignore.yaml"
    path.write_text("misconfigurations: \"not a list\"\n")
    with pytest.raises(trivy_gate.Refused):
        trivy_gate.load_entries(path)


# --------------------------------------------------------------- the verdict

NATS_LOCKS = [("platform", YADGAR, "0.1.34")]


def chart_root(tmp_path) -> pathlib.Path:
    """The parent chart's own `Chart.lock` plus `platform`'s, exactly as
    `helm dependency update chart` leaves them on a real checkout: the
    parent's is a loose file, `platform`'s is read out of its `.tgz`."""
    root = tmp_path / "chart"
    write_lock(root / "Chart.lock", NATS_LOCKS)
    write_dep_archive(
        root / "charts",
        "platform",
        "0.1.34",
        [("nats", "https://nats-io.github.io/k8s/helm/charts", "2.14.6")],
    )
    return root


NATS_TARGET = "chart/charts/platform/charts/nats/templates/stateful-set.yaml"


def entry(id_="KSV-0014", paths=None, expired_at=TODAY + datetime.timedelta(days=90)):
    return trivy_gate.Entry(
        id=id_,
        paths=paths if paths is not None else [NATS_TARGET],
        statement="test",
        expired_at=expired_at,
        index=1,
    )


def test_a_valid_entry_covers_its_matching_upstream_finding(tmp_path):
    findings = [trivy_gate.Finding(target=NATS_TARGET, id="KSV-0014")]
    v = trivy_gate.evaluate(findings, [entry()], TODAY, chart_root(tmp_path))
    assert v.ok
    assert v.unignored_findings == []
    assert v.stale_entries == []


def test_an_estate_finding_fails_even_if_some_entry_would_otherwise_match(tmp_path):
    """OUTSIDE THE BOUNDARY ALWAYS FAILS. Constructed so an entry exists that
    matches on id+glob -- the entry alone is not what refuses it."""
    root = chart_root(tmp_path)
    estate_target = "chart/charts/platform/templates/certificates.yaml"
    findings = [trivy_gate.Finding(target=estate_target, id="KSV-0014")]
    broad = entry(paths=["chart/charts/platform/**"])
    v = trivy_gate.evaluate(findings, [broad], TODAY, root)
    assert not v.ok
    assert [f.target for f in v.estate_findings] == [estate_target]


def test_an_unignored_upstream_finding_fails(tmp_path):
    findings = [trivy_gate.Finding(target=NATS_TARGET, id="KSV-0014")]
    v = trivy_gate.evaluate(findings, [], TODAY, chart_root(tmp_path))
    assert not v.ok
    assert v.unignored_findings == findings


def test_an_entry_with_no_paths_is_stale(tmp_path):
    v = trivy_gate.evaluate([], [entry(paths=[])], TODAY, chart_root(tmp_path))
    assert not v.ok
    assert len(v.stale_entries) == 1
    assert "no `paths`" in v.stale_entries[0][1]


def test_an_entry_matching_no_finding_is_stale(tmp_path):
    v = trivy_gate.evaluate([], [entry()], TODAY, chart_root(tmp_path))
    assert not v.ok
    assert "matches no finding" in v.stale_entries[0][1]


def test_an_expired_entry_no_longer_applies_and_is_itself_stale(tmp_path):
    findings = [trivy_gate.Finding(target=NATS_TARGET, id="KSV-0014")]
    expired = entry(expired_at=TODAY - datetime.timedelta(days=1))
    v = trivy_gate.evaluate(findings, [expired], TODAY, chart_root(tmp_path))
    assert not v.ok
    # The finding is unignored (the expired entry no longer covers it) AND the
    # entry is separately reported stale -- two failures, not one swallowing
    # the other.
    assert v.unignored_findings == findings
    assert any("expired" in reason for _, reason in v.stale_entries)


def test_an_entry_expiring_exactly_today_no_longer_applies(tmp_path):
    """`<= today`, not `< today`: a review due today is due, not tomorrow."""
    findings = [trivy_gate.Finding(target=NATS_TARGET, id="KSV-0014")]
    v = trivy_gate.evaluate(findings, [entry(expired_at=TODAY)], TODAY, chart_root(tmp_path))
    assert any("expired" in reason for _, reason in v.stale_entries)
    assert v.unignored_findings == findings


def test_the_too_broad_parent_glob_is_stale_not_valid(tmp_path):
    """THE MISTAKE A REVIEWER ACTUALLY MADE drafting `yadgarhq/chart#33`:
    `chart/charts/**` also covers every module chart's own templates
    (`chart/charts/iam/...`), so it is refused as reaching outside the
    upstream boundary -- regardless of whether it happens to also match a
    real upstream finding."""
    root = chart_root(tmp_path)
    findings = [trivy_gate.Finding(target=NATS_TARGET, id="KSV-0014")]
    too_broad = entry(paths=["chart/charts/**"])
    v = trivy_gate.evaluate(findings, [too_broad], TODAY, root)
    assert not v.ok
    assert any("upstream boundary" in reason for _, reason in v.stale_entries)
    # And the finding it would otherwise have covered is unignored, not
    # silently let through by the same bad entry.
    assert v.unignored_findings == findings


def test_mutation_the_stale_match_check_is_load_bearing(tmp_path):
    """MUTATION-CHECKED: an entry whose `id` matches but whose glob does not
    must be stale ("matches no finding"), never silently treated as valid.
    Breaking the glob check in `evaluate` (matching on `id` alone) would turn
    this green."""
    root = chart_root(tmp_path)
    wrong_path = entry(paths=["chart/charts/platform/charts/nats/templates/other.yaml"])
    v = trivy_gate.evaluate([], [wrong_path], TODAY, root)
    assert any("matches no finding" in reason for _, reason in v.stale_entries)


# --------------------------------------------------------- trivy invocation


def test_run_trivy_refuses_on_the_skipping_chart_warn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chart").mkdir()

    def fake_run(args, capture_output, text):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"Results": []}',
            stderr='WARN\t[helm scanner] Skipping chart file_path="chart" err="..."\n',
        )

    monkeypatch.setattr(trivy_gate.subprocess, "run", fake_run)
    with pytest.raises(trivy_gate.Refused, match="Skipping chart"):
        trivy_gate.run_trivy(tmp_path / "chart")


def test_run_trivy_refuses_on_zero_helm_results_with_helm_values(tmp_path, monkeypatch):
    """THE BELT-AND-SUSPENDERS CHECK: even with no WARN at all (a future
    trivy that changes its wording, or any other silent-render path), a scan
    that passed `--helm-values` and still reports zero `helm`-typed results
    refuses -- a chart cannot render under override values AND render
    nothing, any more than `scan_delta.py`'s base/image comparison can."""
    monkeypatch.chdir(tmp_path)
    chart_dir = tmp_path / "chart"
    (chart_dir / "ci").mkdir(parents=True)
    (chart_dir / "ci" / "values.yaml").write_text("{}\n")

    def fake_run(args, capture_output, text):
        assert "--quiet" not in args
        assert "--ignorefile" not in args
        return subprocess.CompletedProcess(args, 0, stdout='{"Results": []}', stderr="")

    monkeypatch.setattr(trivy_gate.subprocess, "run", fake_run)
    with pytest.raises(trivy_gate.Refused, match="zero"):
        trivy_gate.run_trivy(chart_dir)


def test_run_trivy_never_passes_quiet_or_ignorefile(tmp_path, monkeypatch):
    """FORBIDDEN BY CONSTRUCTION. `--quiet` hides the one WARN this gate
    depends on; `--ignorefile` would have trivy itself apply the list this
    gate exists to apply instead, with none of the stale-entry or
    upstream-boundary checks above."""
    monkeypatch.chdir(tmp_path)
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    seen = {}

    def fake_run(args, capture_output, text):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout='{"Results": []}', stderr="")

    monkeypatch.setattr(trivy_gate.subprocess, "run", fake_run)
    trivy_gate.run_trivy(chart_dir)
    assert "--quiet" not in seen["args"]
    assert "--ignorefile" not in seen["args"]
    assert "--exit-code" in seen["args"] and "0" in seen["args"]
