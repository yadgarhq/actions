"""What `chart_dependencies_resolved.py` REFUSES, pinned so it cannot quietly
stop refusing.

ADR-0725. `ci-release.yaml`'s `chart` job (#83) and, until today, all three
helm-installing jobs in `ci-pr.yaml` installed helm and resolved nothing — a
chart with a `dependencies:` key refuses outright wherever that lands.

MOST OF THIS FILE IS RED CASES, for the reason `test_no_build_cache.py` gives:
a suite that only feeds a gate conforming input certifies the fixture rather
than the gate.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "chart_dependencies_resolved.py"
REPO = Path(__file__).resolve().parents[2]

SETUP_HELM = "azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310"


def workflow(*jobs, name="ci"):
    body = f"name: {name}\non: push\njobs:\n"
    return body + "".join(jobs)


def job(job_id, *steps):
    text = f"  {job_id}:\n    runs-on: ubuntu-latest\n    steps:\n"
    return text + "".join(steps)


def step(uses=None, run=None):
    if uses:
        return f"      - uses: {uses}\n"
    return f"      - run: {run}\n"


def write(tmp_path: Path, **files):
    """Lay out a `.github/workflows/` tree and return the root to run in."""
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    for filename, text in files.items():
        (directory / filename.replace("__", ".")).write_text(text)
    return tmp_path


def run(cwd: Path):
    return subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# ------------------------------------------------------- the gate must REFUSE


def test_a_job_installing_helm_and_resolving_nothing_is_refused(tmp_path):
    """THE DEFECT ITSELF — `ci-pr.yaml`'s shape until today."""
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "precommit",
                step(uses=SETUP_HELM),
                step(uses="pre-commit/action@v3.0.1"),
            ),
            # A CONTROL, present in every fixture below the floor: without a
            # second helm-installing job, `precommit` alone is one site, and
            # one site is refused for being below the floor rather than for
            # the defect this test names.
            job(
                "portability",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
            job("other", step(uses="actions/checkout@v5")),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "job `precommit`" in result.stdout
    assert "resolves a chart's dependencies" in result.stdout
    assert "job `portability`" not in result.stdout
    assert "job `other`" not in result.stdout


def test_a_dependency_update_step_clears_it(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "precommit",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
                step(uses="pre-commit/action@v3.0.1"),
            ),
            job(
                "portability",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
            job("other", step(uses="actions/checkout@v5")),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_package_dash_u_on_the_same_line_clears_it(tmp_path):
    """`ci-release.yaml`'s shape: resolution and packaging are one command."""
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "chart",
                step(uses=SETUP_HELM),
                step(run="helm package chart -u --version \"$VERSION\""),
            ),
            job(
                "portability",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
            job("other", step(uses="actions/checkout@v5")),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_package_dependency_update_long_flag_clears_it(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "chart",
                step(uses=SETUP_HELM),
                step(run="helm package chart --dependency-update"),
            ),
            job(
                "portability",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
            job("other", step(uses="actions/checkout@v5")),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_a_bare_package_with_no_flag_is_refused(tmp_path):
    """`ci-release.yaml`'s ORIGINAL defect, reproduced as a fixture."""
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "chart",
                step(uses=SETUP_HELM),
                step(run="helm package chart --version \"$VERSION\""),
            ),
            job(
                "portability",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
            job("other", step(uses="actions/checkout@v5")),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "resolves a chart's dependencies" in result.stdout


def test_a_comment_naming_the_command_is_not_a_resolution(tmp_path):
    """THE ANTI-GREP CASE, for the reason `helm_pin_agrees.py` gives at length:
    `helm dependency update` appears in PROSE throughout this repository's own
    workflows, including the comment above the step this gate's own change
    adds. A real YAML `#` comment naming the command, sitting where every real
    comment in this repository's workflows sits (directly above a step), must
    not clear a job that never actually runs it -- `yaml.safe_load` drops the
    comment before this gate ever sees it, which is the property under test.
    """
    root = write(
        tmp_path,
        a__yaml=(
            "name: ci\non: push\njobs:\n"
            "  precommit:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            f"      - uses: {SETUP_HELM}\n"
            "      # helm dependency update chart -- see ADR-0725\n"
            "      - uses: pre-commit/action@v3.0.1\n"
            "  portability:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            f"      - uses: {SETUP_HELM}\n"
            "      - run: helm dependency update chart\n"
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "resolves a chart's dependencies" in result.stdout


def test_a_job_with_no_setup_helm_is_not_inspected(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job("build", step(uses="actions/checkout@v5")),
            job("test", step(run="cargo test")),
        ),
    )
    result = run(root)
    assert result.returncode == 1  # zero sites, below the floor
    assert "fewest this can inspect" in result.stdout


def test_a_single_site_is_refused_rather_than_passed(tmp_path):
    """A GATE WITH NOTHING TO COMPARE REPORTS SUCCESS — the estate's own
    recurring defect. One site is below the floor even if it resolves."""
    root = write(
        tmp_path,
        a__yaml=workflow(
            job(
                "precommit",
                step(uses=SETUP_HELM),
                step(run="helm dependency update chart"),
            ),
        ),
    )
    result = run(root)
    assert result.returncode == 1
    assert "fewest this can inspect" in result.stdout


def test_a_missing_workflows_directory_is_refused(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_an_unparseable_workflow_is_refused_rather_than_skipped(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job("j1", step(uses=SETUP_HELM), step(run="helm dependency update chart")),
            job("j2", step(uses=SETUP_HELM), step(run="helm dependency update chart")),
        ),
        b__yaml="name: ci\non: push\njobs:\n  a-job:\n   steps: [ unclosed\n",
    )
    result = run(root)
    assert result.returncode == 1
    assert "not a YAML document" in result.stdout


def test_a_yml_workflow_is_read_too(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job("j1", step(uses=SETUP_HELM), step(run="helm dependency update chart"))
        ),
        b__yml=workflow(job("j2", step(uses=SETUP_HELM))),
    )
    result = run(root)
    assert result.returncode == 1
    assert "b.yml" in result.stdout


# -------------------------------------------------------------- and it PASSES


def test_the_finding_names_every_site(tmp_path):
    root = write(
        tmp_path,
        a__yaml=workflow(
            job("j1", step(uses=SETUP_HELM), step(run="helm dependency update chart")),
            job("j2", step(uses=SETUP_HELM), step(run="helm package chart -u")),
        ),
    )
    result = run(root)
    assert result.returncode == 0, result.stdout
    assert "job `j1`" in result.stdout and "job `j2`" in result.stdout


def test_this_repository_resolves_before_every_helm_install(tmp_path):
    """LEDGER 725's OWN QUESTION, asked of the real tree rather than a fixture.

    All four real sites are named, so this fails if a helm-installing job
    moves somewhere this gate does not read, or if its resolution step is
    ever deleted without another taking its place.
    """
    result = run(REPO)
    assert result.returncode == 0, result.stdout
    assert "resolving a chart's dependencies first" in result.stdout
    for job_id in ("precommit", "portability", "service_immutable", "chart"):
        assert f"job `{job_id}`" in result.stdout
