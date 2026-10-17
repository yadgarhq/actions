#!/usr/bin/env python3
"""Scan the commits a pull request adds for secrets. Ledger 1532.

WHY THIS EXISTS. The published `gitleaks` hook runs `gitleaks git --pre-commit
--staged`, which scans the INDEX. That is right on a developer's machine and
vacuous on a CI runner, where `pre-commit run --all-files` stages nothing:
measured, a repository with a committed fake GitHub token reported "0 commits
scanned ... no leaks found" in staged mode, while a history scan found it. Every
pull request in the estate merged with that green.

WHAT IT SCANS: `BASE_SHA..HEAD_SHA`, two dots — every commit reachable from the
pull request's head and not from its base. No merge-base step is needed, a
force-push is covered because both SHAs come fresh from each event, and a token
already on the base branch is outside the range, so a historical fixture does not
red every later pull request.

IT NEVER PASSES HAVING SCANNED NOTHING, which is the defect being fixed:
  - no head, or a head this clone does not hold  -> refused, exit 2
  - no base, an all-zero base, or a base this clone does not hold -> the whole
    history of head is scanned instead, and the summary says so
  - a range of zero commits -> refused, exit 2

THE CALLER'S CONFIGURATION APPLIES: gitleaks is pointed at `.`, so it reads the
repository's own `.gitleaks.toml` and `.gitleaksignore` exactly as it does
locally. Output is always redacted.

Environment: BASE_SHA, HEAD_SHA; GITHUB_STEP_SUMMARY when present.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ZERO = "0" * 40


class Refused(Exception):
    """The scan cannot be made meaningful; exit 2 rather than pass."""


@dataclass
class Plan:
    log_opts: str
    commits: int
    note: str


def _env() -> dict[str, str]:
    # `GIT_*` stripped so a `GIT_DIR` inherited from an outer `git commit` (the
    # pytest-scripts hook) cannot point git at the wrong repository.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=_env())


def _is_commit(cwd: Path, sha: str) -> bool:
    return bool(sha) and _git(cwd, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _count(cwd: Path, rev_range: str) -> int:
    out = _git(cwd, "rev-list", "--count", rev_range)
    if out.returncode != 0:
        raise Refused(f"git rev-list {rev_range} failed: {out.stderr.strip()}")
    return int(out.stdout.strip())


def resolve(base: str, head: str, cwd: Path) -> Plan:
    if not _is_commit(cwd, head):
        raise Refused(f"head {head or '(empty)'} is not a commit in this clone")

    if not base or base == ZERO or not _is_commit(cwd, base):
        commits = _count(cwd, head)
        note = (f"base {base or '(empty)'} unavailable: scanning the whole history "
                f"of {head[:12]} ({commits} commits)")
        log_opts = head
    else:
        log_opts = f"{base}..{head}"
        commits = _count(cwd, log_opts)
        note = f"scanning {base[:12]}..{head[:12]} ({commits} commits)"

    if commits == 0:
        raise Refused(f"0 commits in {log_opts}: refusing to report a scan of nothing")
    return Plan(log_opts=log_opts, commits=commits, note=note)


def main() -> int:
    cwd = Path.cwd()
    try:
        plan = resolve(os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", ""), cwd)
    except Refused as exc:
        print(f"::error::secret scan refused: {exc}", file=sys.stderr)
        return 2

    print(plan.note, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"gitleaks: {plan.note}.\n")

    try:
        result = subprocess.run(
            ["gitleaks", "git", "--redact", "--no-banner", "--verbose",
             "--exit-code", "1", f"--log-opts={plan.log_opts}", "."],
            cwd=cwd, env=_env(),
        )
    except FileNotFoundError:
        # Not exit 1: that code means "leaks found", and a traceback exits 1.
        print("::error::secret scan refused: gitleaks is not on PATH", file=sys.stderr)
        return 2
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
