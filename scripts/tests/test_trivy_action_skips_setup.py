"""yaadgaar 565. `aquasecurity/trivy-action`'s own `setup-trivy` step checks out
trivy `main` and runs its `install.sh`, which exits 6 on a cache miss. Every
trivy-action step therefore sets `skip-setup-trivy: true`, and a verified
install step of our own (sha256 from `versions.yaml`, checked before anything
is unpacked as root) sits before it in the same job.

This suite fails when a trivy-action step lacks `skip-setup-trivy: true`, when
no verified install step precedes it in its job, or when the scan finds no
trivy-action step at all (a rename would otherwise pass over zero).

yaadgaar 569 adds: each install step's `TRIVY_VERSION` and `TRIVY_SHA256`
trace to an output `tool_versions.py` really emits, and its download retries.

Run: python3 -m pytest scripts/tests/ -q
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ACTION = "aquasecurity/trivy-action@"

# (workflow, number of trivy-action steps) today.
EXPECTED = {
    "ci-pr.yaml": 1,
    "ci-release.yaml": 1,
    "base-images.yaml": 1,
    "estate-runner-image.yaml": 2,
}


def trivy_steps():
    """(workflow, job, index, steps of the job) for every trivy-action step."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        for job_id, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            for index, step in enumerate(steps):
                if str(step.get("uses", "")).startswith(ACTION):
                    found.append((path.name, job_id, index, steps))
    return found


FOUND = trivy_steps()
IDS = [f"{name}:{job}:{index}" for name, job, index, _ in FOUND]


def is_verified_install(step):
    env = step.get("env") or {}
    run = str(step.get("run", ""))
    return "TRIVY_VERSION" in env and "TRIVY_SHA256" in env and "sha256sum -c" in run


def test_every_known_trivy_action_step_is_scanned():
    counts = {}
    for name, _job, _index, _steps in FOUND:
        counts[name] = counts.get(name, 0) + 1
    assert counts == EXPECTED


@pytest.mark.parametrize("name,job,index,steps", FOUND, ids=IDS)
def test_the_step_skips_the_actions_own_setup_trivy(name, job, index, steps):
    value = (steps[index].get("with") or {}).get("skip-setup-trivy")
    assert str(value).lower() == "true", f"{name}:{job}: skip-setup-trivy is {value!r}, not true"


@pytest.mark.parametrize("name,job,index,steps", FOUND, ids=IDS)
def test_a_verified_install_precedes_the_step(name, job, index, steps):
    assert any(is_verified_install(step) for step in steps[:index]), (
        f"{name}:{job}: no step before the trivy-action step installs trivy with a sha256 check"
    )


# ---------------------------------------------------------------------------
# yaadgaar 569: the install step's inputs are real, and its download retries
# ---------------------------------------------------------------------------

TOOL_VERSIONS = subprocess.run(
    [sys.executable, str(REPO / "scripts" / "tool_versions.py"), str(REPO / "versions.yaml")],
    capture_output=True,
    text=True,
    check=True,
).stdout
EMITTED = {line.split("=", 1)[0] for line in TOOL_VERSIONS.splitlines() if "=" in line}

_NEEDS = re.compile(r"^\$\{\{\s*needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$")
_STEPS = re.compile(r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$")


def install_steps():
    """(workflow, job id, jobs, index, steps) for every verified install step."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        jobs = doc.get("jobs") or {}
        for job_id, job in jobs.items():
            steps = job.get("steps") or []
            for index, step in enumerate(steps):
                if is_verified_install(step):
                    found.append((path.name, job_id, jobs, index, steps))
    return found


INSTALLS = install_steps()
INSTALL_IDS = [f"{name}:{job}:{index}" for name, job, _jobs, index, _ in INSTALLS]


def resolves_to_tool_versions(expr, job_id, jobs, steps, before):
    """True when `expr` is a `tool_versions.py` output that the run really emits.

    `steps.<id>.outputs.<key>` must name a step before `before` in `steps` that
    runs `tool_versions.py`. `needs.<job>.outputs.<key>` must name a job this one
    needs, whose `outputs.<key>` is itself such a step output in that job.
    """
    expr = str(expr).strip()
    m = _STEPS.match(expr)
    if m:
        sid, key = m.groups()
        producers = [s for s in steps[:before] if s.get("id") == sid]
        return (
            len(producers) == 1
            and "scripts/tool_versions.py" in str(producers[0].get("run", ""))
            and key in EMITTED
        )
    m = _NEEDS.match(expr)
    if m:
        dep, key = m.groups()
        needs = jobs[job_id].get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        if dep not in needs or dep not in jobs:
            return False
        out = (jobs[dep].get("outputs") or {}).get(key)
        if out is None:
            return False
        dep_steps = jobs[dep].get("steps") or []
        return resolves_to_tool_versions(out, dep, jobs, dep_steps, len(dep_steps))
    return False


def test_every_known_install_step_is_scanned():
    """One install per trivy-action job; a rename would otherwise pass over zero."""
    assert len(INSTALLS) == len({(n, j) for n, j, _i, _s in FOUND}) == 4


@pytest.mark.parametrize("name,job,jobs,index,steps", INSTALLS, ids=INSTALL_IDS)
@pytest.mark.parametrize("var", ["TRIVY_VERSION", "TRIVY_SHA256"])
def test_the_install_reads_an_output_that_exists(name, job, jobs, index, steps, var):
    """An install input must trace to a `versions.yaml` value, not to an empty string.

    Deleting `trivy_sha256` from a `detect` job's `outputs` renders the input
    empty; only the runtime hex guard would catch that, on the runner.
    """
    expr = (steps[index].get("env") or {}).get(var)
    assert resolves_to_tool_versions(expr, job, jobs, steps, index), (
        f"{name}:{job}: {var}={expr!r} does not resolve to a tool_versions.py output"
    )


@pytest.mark.parametrize("name,job,jobs,index,steps", INSTALLS, ids=INSTALL_IDS)
def test_the_install_download_retries(name, job, jobs, index, steps):
    """A transient fetch failure retries. Safe, because the sha256 check runs after it."""
    run = str(steps[index].get("run", ""))
    curls = [line for line in run.splitlines() if re.match(r"\s*curl\s", line)]
    assert curls, f"{name}:{job}: the install step has no curl"
    for line in curls:
        assert re.search(r"\s--retry\s+3(\s|$)", line), f"{name}:{job}: {line.strip()}"
        assert re.search(r"\s--retry-all-errors(\s|$)", line), f"{name}:{job}: {line.strip()}"
    assert run.index("curl") < run.index("sha256sum -c")
