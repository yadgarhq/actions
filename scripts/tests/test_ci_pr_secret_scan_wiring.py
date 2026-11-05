"""YAADGAAR 648. How `ci-pr.yaml`'s precommit job wires `secret_scan.py`.

`test_secret_scan.py` tests the script. Nothing tested the step that calls it,
so a pull request could delete the step, swap the two SHAs, interpolate a SHA
into `run:`, or make the checkout shallow, and the suite stayed green while CI
stopped scanning for secrets. These tests pin that wiring as it reads today:

- the precommit job has exactly one step that runs `scripts/secret_scan.py`,
  and that script exists;
- `BASE_SHA` and `HEAD_SHA` reach it through `env:` from the pull request's
  base and head, in that order;
- its `run:` interpolates nothing (`${{` never appears in it) and equals the
  reviewed script text exactly, so `|| true` or an early `exit 0` fails;
- no `shell:` override (step, job `defaults`, workflow `defaults`) and no
  `continue-on-error` (step or job) can turn the scan into a pass;
- its env holds exactly `BASE_SHA`, `HEAD_SHA` and `UNDER_REVIEW`, no job or
  workflow `env` exists, and it has no `working-directory`;
- the job's own checkout is `fetch-depth: 0`, since a shallow clone has no
  `base..head` range to scan, and the only other checkout is the own-SHA copy
  of yadgarhq/actions into `.ci-actions`.

No workflow is edited by the change that adds this file.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-pr.yaml"
STEP_NAME = "scan the commits this pull request adds for secrets"
SCRIPT = "scripts/secret_scan.py"
EXPECTED_RUN = """\
set -euo pipefail
src=scripts/secret_scan.py
if [ "$UNDER_REVIEW" != "yadgarhq/actions" ]; then
  src=.ci-actions/scripts/secret_scan.py
fi
cp "$src" "$RUNNER_TEMP/secret_scan.py"
rm -rf .ci-actions
python3 -E -s "$RUNNER_TEMP/secret_scan.py"
"""


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
    assert 'python3 -E -s "$RUNNER_TEMP/secret_scan.py"' in run


def test_the_scan_run_is_exactly_the_reviewed_script():
    """Whole-text equality, not a substring: `|| true` on the python line or an
    `exit 0` before it disables the scan while every substring still matches."""
    assert scan_step()["run"].strip() == EXPECTED_RUN.strip()


def test_the_scan_step_is_never_skipped_or_softened():
    step = scan_step()
    assert "if" not in step
    assert "continue-on-error" not in step
    assert "continue-on-error" not in job()


def test_no_shell_override_reaches_the_scan_step():
    """`shell: "true {0}"` at step, job or workflow level runs `true` instead
    of the script and the step passes."""
    assert "shell" not in scan_step()
    assert "defaults" not in job()
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "shell" not in (workflow.get("defaults") or {}).get("run", {})


def test_base_and_head_arrive_through_env_and_are_not_swapped():
    env = scan_step()["env"]
    assert env["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert env["UNDER_REVIEW"] == "${{ github.repository }}"


def test_nothing_else_reaches_the_scan_environment():
    """`BASH_ENV` runs a file before the script. The scan runs with `-E -s`
    (#653), so `PYTHONPATH` cannot shadow a module it imports. The step's env
    still holds exactly the three inputs, and no job or workflow `env` exists to
    add more."""
    assert set(scan_step()["env"]) == {"BASE_SHA", "HEAD_SHA", "UNDER_REVIEW"}
    assert "env" not in job()
    assert "env" not in yaml.safe_load(WORKFLOW.read_text())


def test_the_scan_runs_from_the_workspace_root():
    """`working-directory:` would resolve `scripts/secret_scan.py` in another
    directory, where a pull request to yadgarhq/actions can plant its own."""
    assert "working-directory" not in scan_step()


def test_the_scan_run_interpolates_nothing():
    assert "${{" not in scan_step()["run"]


def checkouts():
    """Split the job's checkouts: the tree under review (no `path`, no
    `repository`) and every other one."""
    own, others = [], []
    for s in job()["steps"]:
        if str(s.get("uses", "")).lower().startswith("actions/checkout@"):
            w = s.get("with", {})
            (others if "path" in w or "repository" in w else own).append(s)
    return own, others


def test_the_job_checkout_has_full_history():
    steps = job()["steps"]
    own, _ = checkouts()
    assert len(own) == 1, f"precommit must have one checkout of the tree under review, found {len(own)}"
    assert own[0]["with"].get("fetch-depth") == 0
    assert steps.index(own[0]) < steps.index(scan_step())


def test_no_other_checkout_can_replace_the_tree_under_review():
    """The only other checkout is the own-SHA copy of yadgarhq/actions into
    `.ci-actions`. A second checkout of the pull request's repository (a
    shallow one, say) would replace the full-history tree the scan reads."""
    _, others = checkouts()
    assert len(others) == 1, f"precommit must have exactly one extra checkout, found {len(others)}"
    w = others[0]["with"]
    assert w.get("repository") == "yadgarhq/actions"
    assert w.get("path") == ".ci-actions"
    assert w.get("ref") == "${{ steps.own_sha.outputs.sha }}"
    assert others[0].get("if") == "github.repository != 'yadgarhq/actions'"
