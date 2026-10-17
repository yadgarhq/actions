"""LEDGER 1532. The range `ci-pr.yaml`'s precommit job hands gitleaks.

The published `gitleaks` hook runs `gitleaks git --pre-commit --staged`, which
scans the INDEX. On a CI runner nothing is staged, so every pull request in the
estate reported "no leaks found" having scanned zero bytes. `secret_scan.py`
replaces that with a history scan over the commits the pull request adds, and
these tests pin the three properties that make it a gate rather than a second
decoration: it scans what the pull request adds, it never passes having scanned
nothing, and it reddens on a real-shaped token.

NO FAKE TOKEN IS WRITTEN IN THIS FILE. Each one is assembled at run time, so the
range scan of the pull request that adds this file does not find its own
fixtures.
"""

from __future__ import annotations

import os
import pathlib
import random
import string
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import secret_scan  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "secret_scan.py"


def _git_env():
    """`GIT_*` stripped: `git commit` exports `GIT_DIR` and `GIT_INDEX_FILE`
    to the `pytest-scripts` hook, and a nested `git` would obey the OUTER
    repository. Same helper as `test_next_version.py`."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(cwd, *args):
    out = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null",
         "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_git_env(),
    )
    return out.stdout.strip()


def fake_token(seed: int = 1532) -> str:
    """A GitHub-PAT-shaped string that has never been a credential."""
    alphabet = string.ascii_letters + string.digits
    rng = random.Random(seed)
    body = "".join(rng.choice(alphabet) for _ in range(36))
    return "gh" + "p_" + body


def commit(repo, name, content, message):
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "README.md", "hello\n", "root")
    return tmp_path


# ---------------------------------------------------------------------------
# Range resolution.
# ---------------------------------------------------------------------------


def test_a_pull_request_scans_base_to_head(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "a.txt", "a\n", "one")
    head = commit(repo, "b.txt", "b\n", "two")

    plan = secret_scan.resolve(base, head, repo)

    assert plan.log_opts == f"{base}..{head}"
    assert plan.commits == 2


def test_a_moved_base_still_scans_only_what_head_adds(repo):
    """Two dots, not a merge-base: commits main gained after the branch point
    are not on head and so are not scanned."""
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "a.txt", "a\n", "feature")
    git(repo, "checkout", "-q", "main")
    base = commit(repo, "m.txt", "m\n", "main moved")

    plan = secret_scan.resolve(base, head, repo)

    assert plan.commits == 1


def test_a_force_push_scans_the_new_head_only(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "a.txt", "a\n", "first push")
    git(repo, "reset", "-q", "--hard", base)
    head = commit(repo, "b.txt", "b\n", "force-pushed")

    plan = secret_scan.resolve(base, head, repo)

    assert plan.log_opts == f"{base}..{head}"
    assert plan.commits == 1


@pytest.mark.parametrize("base", ["", "0" * 40])
def test_no_base_falls_back_to_the_whole_history_of_head(repo, base):
    """A first commit or an event with no base: scan everything head reaches,
    never nothing."""
    head = commit(repo, "a.txt", "a\n", "second")

    plan = secret_scan.resolve(base, head, repo)

    assert plan.log_opts == head
    assert plan.commits == 2
    assert "whole history" in plan.note


def test_a_base_absent_from_the_clone_falls_back_to_the_whole_history(repo):
    head = git(repo, "rev-parse", "HEAD")

    plan = secret_scan.resolve("1" * 40, head, repo)

    assert plan.log_opts == head
    assert plan.commits == 1


@pytest.mark.parametrize("head", ["", "2" * 40])
def test_a_missing_or_unknown_head_is_refused(repo, head):
    base = git(repo, "rev-parse", "HEAD")

    with pytest.raises(secret_scan.Refused):
        secret_scan.resolve(base, head, repo)


def test_an_empty_range_is_refused_rather_than_passed(repo):
    """THE CLASS THIS LEDGER IS ABOUT. Zero commits scanned is the exact
    false green the staged hook produced; it must not come back as a range."""
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(secret_scan.Refused, match="0 commits"):
        secret_scan.resolve(head, head, repo)


# ---------------------------------------------------------------------------
# The real gitleaks, end to end. CI installs gitleaks before pre-commit runs.
# ---------------------------------------------------------------------------


def run_script(repo, base, head, path=None):
    env = _git_env()
    if path is not None:
        env["PATH"] = path
    env["BASE_SHA"] = base
    env["HEAD_SHA"] = head
    env.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo, capture_output=True, text=True, env=env,
    )


def test_a_committed_token_reddens_the_scan(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "config.env", f"TOKEN={fake_token()}\n", "leak")

    result = run_script(repo, base, head)

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    # Exit 1 alone is not proof: an uncaught exception exits 1 too, which is
    # how this test once passed on a runner with no gitleaks at all.
    assert "RuleID:" in output and "github-pat" in output, output
    assert fake_token() not in output  # redacted


def test_a_clean_range_passes(repo):
    """THE CONTROL: without it the red above proves nothing."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "a.txt", "nothing secret\n", "clean")

    result = run_script(repo, base, head)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_token_already_on_base_is_outside_the_range(repo):
    """Historical fixtures on main do not red every later pull request."""
    commit(repo, "old.env", f"TOKEN={fake_token()}\n", "historical")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "a.txt", "clean\n", "clean")

    result = run_script(repo, base, head)

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_callers_gitleaksignore_is_respected(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    leak = commit(repo, "config.env", f"TOKEN={fake_token()}\n", "leak")
    (repo / ".gitleaksignore").write_text(
        f"{leak}:config.env:github-pat:1\n", encoding="utf-8")
    git(repo, "add", ".gitleaksignore")
    git(repo, "commit", "-q", "-m", "allowlist the fixture")
    head = git(repo, "rev-parse", "HEAD")

    result = run_script(repo, base, head)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_refusal_exits_2(repo):
    head = git(repo, "rev-parse", "HEAD")

    result = run_script(repo, head, head)

    assert result.returncode == 2
    assert "0 commits" in result.stderr


def test_a_missing_gitleaks_is_refused_not_reported_as_a_leak(repo, tmp_path_factory):
    """Exit 1 means "leaks found". A runner without the binary must not
    produce that code, nor any green: it is refused with exit 2."""
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "a.txt", "clean\n", "clean")
    bindir = tmp_path_factory.mktemp("bin")
    (bindir / "git").symlink_to(subprocess.run(
        ["which", "git"], capture_output=True, text=True, check=True).stdout.strip())

    result = run_script(repo, base, head, path=str(bindir))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "gitleaks" in result.stderr
