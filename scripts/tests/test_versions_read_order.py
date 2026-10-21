"""LEDGER 1756 S2b review. A job never runs with an empty tool version.

THE FAILURE THIS HOLDS SHUT. `detect` sets its facts (`chart=true`, ...) in
one step and reads `versions.yaml` in another. If the versions read came
LAST and failed, `detect` would fail with `chart=true` already set. A
downstream job whose `if:` starts with `always()` (`ci-release.yaml`'s `chart`)
does not skip on a failed need, so it would run `azure/setup-helm` with
`version: v` and an empty input, and the action would pick its own helm.

Two rules, both enforced, either one sufficient on its own:

1. In each `detect` job, the versions read runs BEFORE every other step whose
   outputs the job exposes. A failed read then leaves no fact set.
2. A job whose `if:` uses `always()`, `!cancelled()` or `failure()` and reads
   a versions output from `needs.detect` must also require
   `needs.detect.result == 'success'`.

Each rule is paired with a constructed red case.

Run: python3 -m pytest scripts/tests/ -q
"""

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
sys.path.insert(0, str(REPO / "scripts"))

import tool_versions  # noqa: E402

DETECT_WORKFLOWS = ("ci-pr.yaml", "ci-release.yaml")
VERSIONS_ID = "versions"
STATUS_FNS = ("always()", "!cancelled()", "failure()")
SUCCESS = "needs.detect.result == 'success'"
OUTPUTS = set(tool_versions.read(REPO / "versions.yaml"))
STEP_REF = re.compile(r"steps\.([\w-]+)\.outputs\.")
DETECT_READ = re.compile(r"needs\.detect\.outputs\.([\w-]+)")


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def order_violations(doc, name):
    """Rule 1 for one workflow's `detect` job."""
    detect = doc["jobs"]["detect"]
    exposed = set()
    for value in (detect.get("outputs") or {}).values():
        exposed |= set(STEP_REF.findall(str(value)))
    ids = [step.get("id") for step in detect["steps"]]
    if VERSIONS_ID not in ids:
        return [f"{name}: detect has no `id: {VERSIONS_ID}` step"]
    first = ids.index(VERSIONS_ID)
    return [
        f"{name}: detect step `{step_id}` sets exposed outputs before the versions read"
        for step_id in ids[:first]
        if step_id in exposed and step_id != "own_sha"
    ]


def guard_violations(doc, name):
    """Rule 2 for one workflow."""
    found = []
    for job_id, job in (doc.get("jobs") or {}).items():
        cond = str(job.get("if", "")).replace(" ", "")
        if not any(fn in cond for fn in STATUS_FNS):
            continue
        reads = set(DETECT_READ.findall(yaml.safe_dump(job.get("steps") or [])))
        if not reads & OUTPUTS:
            continue
        if SUCCESS.replace(" ", "") not in cond:
            found.append(
                f"{name}: job `{job_id}` runs under a status function and reads "
                f"{sorted(reads & OUTPUTS)} from detect without `{SUCCESS}`"
            )
    return found


def test_the_real_detect_jobs_read_versions_first():
    for name in DETECT_WORKFLOWS:
        assert order_violations(load(name), name) == []


def test_the_real_always_jobs_require_a_successful_detect():
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        assert guard_violations(load(path.name), path.name) == []


def test_ci_release_chart_is_guarded():
    """The concrete job the review named; holds the rule from going vacuous."""
    doc = load("ci-release.yaml")
    assert "always()" in doc["jobs"]["chart"]["if"]
    assert SUCCESS in doc["jobs"]["chart"]["if"]


def test_red_versions_read_after_the_facts():
    doc = load("ci-release.yaml")
    steps = doc["jobs"]["detect"]["steps"]
    index = next(i for i, s in enumerate(steps) if s.get("id") == VERSIONS_ID)
    steps.append(steps.pop(index))
    problems = order_violations(doc, "ci-release.yaml")
    assert any("`d`" in p for p in problems), problems


def test_red_always_job_without_the_success_guard():
    doc = load("ci-release.yaml")
    doc["jobs"]["chart"]["if"] = doc["jobs"]["chart"]["if"].replace(SUCCESS, "true")
    problems = guard_violations(doc, "ci-release.yaml")
    assert any("`chart`" in p and "helm" in p for p in problems), problems


def test_red_new_always_job_reading_a_version():
    doc = load("ci-pr.yaml")
    doc["jobs"]["extra"] = {
        "needs": "detect",
        "if": "always()",
        "steps": [{"uses": "x/y@z", "with": {"version": "${{ needs.detect.outputs.trivy }}"}}],
    }
    problems = guard_violations(doc, "ci-pr.yaml")
    assert any("`extra`" in p for p in problems), problems
