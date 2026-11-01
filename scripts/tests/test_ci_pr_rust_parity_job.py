"""ADR-1120 rollout step 5 (yaadgaar 527): the `rust_parity` job BLOCKS.

The job started warn-only: `--mode warn`, `continue-on-error: true`, and absent
from `passed.needs`, because every consumer still carried `rust-version = "1.90"`.
All fourteen Rust consumers now carry the org version, so the check fails the
pull request that drifts. These tests pin the blocking shape and the revert body
ADR-0583 requires.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-pr.yaml"
REVERT = ROOT / ".github" / "revert-bodies" / "rust-parity-fail.md"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def job():
    return workflow()["rust_parity"]


def check_run():
    return next(s for s in job()["steps"] if s.get("name", "").startswith("every Rust version field"))["run"]


def test_the_check_runs_in_fail_mode():
    run = check_run()
    assert "--mode fail" in run
    assert "--mode warn" not in run


def test_the_job_can_red_the_run():
    assert "continue-on-error" not in job()


def test_the_job_is_part_of_the_verdict():
    assert "rust_parity" in workflow()["passed"]["needs"]


def test_the_job_keeps_its_skip_conjuncts():
    """A repo without Cargo.toml (chart, config) skips; so do text-only and config-only edits."""
    cond = " ".join(job()["if"].split())
    for part in (
        "github.event_name != 'push'",
        "needs.detect.outputs.rust == 'true'",
        "needs.detect.outputs.text_edit != 'true'",
        "needs.detect.outputs.config_only != 'true'",
    ):
        assert part in cond


def test_the_revert_body_follows_the_template():
    text = REVERT.read_text()
    for header in ("## What", "## Why", "## Changelog", "## Verification", "## Risk"):
        assert header in text
    assert "ADR-0583" in text
    assert "--mode warn" in text  # the revert restores the warn-only shape
