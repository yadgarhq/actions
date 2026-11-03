"""yaadgaar 565. `aquasecurity/trivy-action`'s own `setup-trivy` step checks out
trivy `main` and runs its `install.sh`, which exits 6 on a cache miss. Every
trivy-action step therefore sets `skip-setup-trivy: true`, and a verified
install step of our own (sha256 from `versions.yaml`, checked before anything
is unpacked as root) sits before it in the same job.

This suite fails when a trivy-action step lacks `skip-setup-trivy: true`, when
no verified install step precedes it in its job, or when the scan finds no
trivy-action step at all (a rename would otherwise pass over zero).

Run: python3 -m pytest scripts/tests/ -q
"""

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
