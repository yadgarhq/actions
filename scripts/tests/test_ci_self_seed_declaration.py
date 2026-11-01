"""Yaadgaar 473. `seed-declaration.yaml` had zero runs anywhere: no consumer calls
it and `ci-self.yaml` did not exercise it, so its own-SHA checkout (ADR-0981)
and its two gates were unproven. `ci-self.yaml` carries a `seed-declaration`
job that calls it BY LOCAL PATH over a fixture content root. The job going green
proves the `own_sha` guard accepted a real 40-hex `job.workflow_sha` and both
gates ran over a conforming tree.

This suite holds that wiring, and holds that the fixture really conforms to the
real gate. Each check is paired with a mutation that must go red.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_SELF = ROOT / ".github" / "workflows" / "ci-self.yaml"
GATE = ROOT / "hooks" / "seed_repository.py"
CALLEE = "./.github/workflows/seed-declaration.yaml"
JOB = "seed-declaration"


def problems(text=None):
    text = CI_SELF.read_text(encoding="utf-8") if text is None else text
    job = yaml.safe_load(text)["jobs"].get(JOB)
    if job is None:
        return [f"no `{JOB}` job"]
    found = []
    if job.get("uses") != CALLEE:
        found.append(f"does not call {CALLEE} by local path")
    root = (job.get("with") or {}).get("content-root")
    if not root:
        found.append("no content-root")
    elif not (ROOT / root).is_dir():
        found.append(f"content-root {root!r} is not a directory")
    if "pull_request" not in str(job.get("if", "")):
        found.append("not limited to pull_request")
    if (job.get("permissions") or {}).get("contents") != "read":
        found.append("permissions.contents is not read")
    return found


def test_ci_self_exercises_seed_declaration():
    assert problems() == []


def test_mutation_no_job_is_red():
    text = CI_SELF.read_text(encoding="utf-8").replace(f"  {JOB}:", "  other:")
    assert problems(text)


def test_mutation_missing_fixture_is_red():
    text = CI_SELF.read_text(encoding="utf-8").replace(
        "fixtures/seed_declaration", "fixtures/nowhere")
    assert problems(text)


def test_the_fixture_passes_both_real_gates():
    root = yaml.safe_load(CI_SELF.read_text(encoding="utf-8"))["jobs"][JOB]["with"]["content-root"]
    for part in ("seed-no-ask-prompt", "seed-front-matter"):
        done = subprocess.run(
            [sys.executable, str(GATE), "--part", part, "--root", str(ROOT / root)],
            capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stdout
