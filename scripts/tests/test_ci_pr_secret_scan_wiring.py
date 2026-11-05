"""YAADGAAR 648. How `ci-pr.yaml`'s precommit job wires `secret_scan.py`.

`test_secret_scan.py` tests the script. Nothing tested the step that calls it,
so a pull request could delete the step, swap the two SHAs, interpolate a SHA
into `run:`, or make the checkout shallow, and the suite stayed green while CI
stopped scanning for secrets. These tests pin that wiring as it reads today:

- the precommit job has exactly one step that runs `scripts/secret_scan.py`,
  and that script exists;
- `BASE_SHA` and `HEAD_SHA` reach it through `env:` from the pull request's
  base and head, in that order;
- its `run:` interpolates nothing (`${{` never appears in it);
- the job's own checkout is `fetch-depth: 0`, since a shallow clone has no
  `base..head` range to scan.

No workflow is edited by the change that adds this file.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-pr.yaml"
STEP_NAME = "scan the commits this pull request adds for secrets"
SCRIPT = "scripts/secret_scan.py"


def job():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["precommit"]


def scan_steps():
    return [s for s in job()["steps"] if SCRIPT in (s.get("run") or "")]


def scan_step():
    steps = scan_steps()
    assert len(steps) == 1, f"precommit must run {SCRIPT} in exactly one step, found {len(steps)}"
    return steps[0]


def test_the_precommit_job_runs_the_secret_scan():
    step = scan_step()
    assert step["name"] == STEP_NAME
    assert (ROOT / SCRIPT).is_file()
    run = step["run"]
    assert f"src={SCRIPT}" in run
    assert f"src=.ci-actions/{SCRIPT}" in run
    assert 'python3 "$RUNNER_TEMP/secret_scan.py"' in run


def test_the_scan_step_is_never_skipped_or_softened():
    step = scan_step()
    assert "if" not in step
    assert step.get("continue-on-error", False) is False


def test_base_and_head_arrive_through_env_and_are_not_swapped():
    env = scan_step()["env"]
    assert env["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["UNDER_REVIEW"] == "${{ github.repository }}"


def test_the_scan_run_interpolates_nothing():
    assert "${{" not in scan_step()["run"]


def test_the_job_checkout_has_full_history():
    steps = job()["steps"]
    own = [
        s for s in steps
        if str(s.get("uses", "")).startswith("actions/checkout@")
        and "path" not in s.get("with", {})
        and "repository" not in s.get("with", {})
    ]
    assert len(own) == 1, f"precommit must have one checkout of the tree under review, found {len(own)}"
    assert own[0]["with"].get("fetch-depth") == 0
    assert steps.index(own[0]) < steps.index(scan_step())
