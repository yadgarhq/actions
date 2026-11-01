"""YAADGAAR 260. The `sizes` job in `ci-pr.yaml`: report only.

It prints what `hooks/complexity.py --report` measures into the job summary. The
properties that matter are structural: it is NOT part of the verdict (it must
not become a second gate), it reaches `complexity.py` through the same
own-SHA second checkout as the other jobs, and the shell block that selects the
changed files runs against a real merge commit.
"""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-pr.yaml"


def job():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["sizes"]


def test_the_job_is_not_part_of_the_verdict():
    assert "sizes" not in yaml.safe_load(WORKFLOW.read_text())["jobs"]["passed"]["needs"]


def test_the_job_runs_on_rust_pull_requests_only():
    cond = " ".join(job()["if"].split())
    for part in (
        "github.event_name != 'push'",
        "needs.detect.outputs.rust == 'true'",
        "needs.detect.outputs.text_edit != 'true'",
    ):
        assert part in cond


def test_the_job_cannot_red_the_run():
    """`sweep.py` V2 needs the run conclusion to be success, so a failed `sizes`
    job (checkout flake, own-SHA refusal, timeout) must not fail the run."""
    assert job()["continue-on-error"] is True


def test_the_job_has_no_secret_no_extra_permission_and_a_timeout():
    j = job()
    assert "secrets." not in str(j)
    assert "permissions" not in j
    assert j["timeout-minutes"] == 10
    assert j["steps"][0]["with"]["persist-credentials"] is False
    assert j["steps"][0]["with"]["fetch-depth"] == 0


def test_the_script_comes_from_the_own_sha_checkout_for_a_consumer():
    steps = job()["steps"]
    second = next(s for s in steps if s.get("with", {}).get("path") == ".ci-actions")
    assert second["if"] == "github.repository != 'yadgarhq/actions'"
    assert second["with"]["ref"] == "${{ steps.own_sha.outputs.sha }}"
    run = next(s for s in steps if s.get("name") == "sizes the gate measured")["run"]
    assert ".ci-actions/hooks/complexity.py" in run
    assert "${{" not in run  # inputs arrive through env, never interpolated


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def _step_run():
    steps = job()["steps"]
    return next(s for s in steps if s.get("name") == "sizes the gate measured")["run"]


def _run_block(tmp_path, base, repo="someone/else"):
    summary = tmp_path / "summary.md"
    summary.write_text("")
    runner_temp = tmp_path / "rt"
    runner_temp.mkdir()
    (tmp_path / ".ci-actions" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ci-actions" / "hooks" / "complexity.py").write_text(
        (ROOT / "hooks" / "complexity.py").read_text()
    )
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "UNDER_REVIEW": repo,
        "BASE_SHA": base,
        "HEAD_SHA": "feedface",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(
        ["bash", "-c", _step_run()], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    return result, summary.read_text()


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.rs").write_text("// padding\n" * 600)
    (tmp_path / "src" / "keep.rs").write_text("// padding\n" * 3)
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return _git(tmp_path, "rev-parse", "HEAD")


def test_the_block_reports_only_changed_rust_files_and_never_fails(tmp_path):
    base = _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "src" / "new.rs").write_text("// padding\n" * 7)
    (tmp_path / "src" / "notes.md").write_text("x\n")
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-q", "-m", "change")
    result, summary = _run_block(tmp_path, base)
    assert result.returncode == 0, result.stderr
    assert "src/new.rs: 7 lines (ceiling 500)" in summary
    assert "old.rs" not in summary and "keep.rs" not in summary and "notes.md" not in summary
    assert "## sizes the gate measured" in summary
    assert "Gate exit status on these files: 0" in summary
    assert not (tmp_path / ".ci-actions").exists()


def test_a_file_over_the_ceiling_is_printed_and_the_step_still_passes(tmp_path):
    base = _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "src" / "old.rs").write_text("// padding\n" * 601)
    _git(tmp_path, "commit", "-q", "-am", "grow")
    result, summary = _run_block(tmp_path, base)
    assert result.returncode == 0, result.stderr
    assert "src/old.rs: 601 lines (ceiling 500)" in summary
    assert "Gate exit status on these files: 1" in summary


def test_no_changed_rust_file_is_said_not_silently_skipped(tmp_path):
    base = _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "docs")
    result, summary = _run_block(tmp_path, base)
    assert result.returncode == 0, result.stderr
    assert "No Rust file changed." in summary


def test_an_event_without_a_base_is_said_not_silently_skipped(tmp_path):
    _repo(tmp_path)
    result, summary = _run_block(tmp_path, "")
    assert result.returncode == 0, result.stderr
    assert "No pull request base on this event" in summary


def test_files_changed_only_on_the_base_branch_are_not_reported(tmp_path):
    """Three dots: the merge base, so a base that moved on does not leak in.

    `inputs.ref` may be a plain head rather than the merge commit, and then a
    two-dot diff would list every file the base changed since the branch point
    (a MODIFIED one: a deletion is filtered out whatever the dots).
    """
    first = _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "src" / "mine.rs").write_text("// padding\n" * 4)
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-q", "-m", "mine")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "src" / "keep.rs").write_text("// padding\n" * 9)
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-q", "-m", "theirs")
    moved_base = _git(tmp_path, "rev-parse", "HEAD")
    assert moved_base != first
    _git(tmp_path, "checkout", "-q", "pr")
    result, summary = _run_block(tmp_path, moved_base)
    assert result.returncode == 0, result.stderr
    assert "src/mine.rs: 4 lines" in summary
    assert "keep.rs" not in summary
