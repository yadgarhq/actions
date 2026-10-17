#!/usr/bin/env python3
"""Scan the commits a pull request adds for secrets. Ledgers 1532 and 1536.

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

TWO `git log` OPTIONS CLOSE WAYS A RANGE SCAN IS BLIND (ledger 1536):
  - `--diff-merges=remerge`: a merge commit shows only what it adds beyond the
    automatic merge, so a token typed into a conflict resolution or an evil
    merge is scanned. NOT `--first-parent` or `-m`: both re-show the base
    branch's own content through a "merge main" commit and red a clean branch.
  - `--text`: a `.gitattributes` line such as `* -diff` or `*.txt binary`,
    added by the pull request itself, otherwise turns its diff into "Binary
    files differ" and the scan sees nothing.

THE ALLOWLIST IS THE BASE BRANCH'S, NEVER THE PULL REQUEST'S (ledger 1536).
Main's ruleset requires zero approvals, so "a reviewer would see the allowlist
in the diff" is not a control: an agent that hits red can add an allowlist and
merge. So `.gitleaks.toml` and `.gitleaksignore` are read from the BASE commit
(`git show BASE:<path>`) into a temporary directory and passed explicitly;
`--ignore-gitleaks-allow` drops inline `gitleaks:allow` comments, which live in
the pull request's own lines. With no config on base the gitleaks defaults
apply, through an explicit `useDefault` file so the pull request's own
`.gitleaks.toml` in `.` is never picked up implicitly. The cost: a genuine false
positive takes two pull requests — the ignore entry lands on main first, then
the original re-runs. When there is no usable base (the whole-history fallback
below) there is no trusted tree at all, so defaults apply and no ignore file is
read: fail closed.

IT NEVER PASSES HAVING SCANNED NOTHING, which is the defect being fixed:
  - no head, or a head this clone does not hold  -> refused, exit 2
  - no base, an all-zero base, or a base this clone does not hold -> the whole
    history of head is scanned instead, and the summary says so
  - a range of zero commits -> refused, exit 2
  - no gitleaks binary -> refused, exit 2 (exit 1 means "leaks found")

Output is always redacted. Environment: BASE_SHA, HEAD_SHA; GITHUB_STEP_SUMMARY
when present.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ZERO = "0" * 40
LOG_FLAGS = "--diff-merges=remerge --text"
DEFAULT_CONFIG = "[extend]\nuseDefault = true\n"


class Refused(Exception):
    """The scan cannot be made meaningful; exit 2 rather than pass."""


@dataclass
class Plan:
    rev_range: str
    commits: int
    note: str
    trusted: str  # the commit whose config applies; "" means defaults only

    @property
    def log_opts(self) -> str:
        return f"{LOG_FLAGS} {self.rev_range}"


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
                f"of {head[:12]} ({commits} commits), gitleaks defaults, no ignore file")
        rev_range, trusted = head, ""
    else:
        rev_range, trusted = f"{base}..{head}", base
        commits = _count(cwd, rev_range)
        note = f"scanning {base[:12]}..{head[:12]} ({commits} commits), config from base"

    if commits == 0:
        raise Refused(f"0 commits in {rev_range}: refusing to report a scan of nothing")
    return Plan(rev_range=rev_range, commits=commits, note=note, trusted=trusted)


def stage_config(cwd: Path, trusted: str, into: Path) -> tuple[Path, Path]:
    """Write the trusted commit's config and ignore file into `into`.

    Missing on the trusted commit -> gitleaks defaults and an empty ignore file.
    Never read from the working tree, which is the pull request's.
    """
    config, ignore = into / "gitleaks.toml", into / ".gitleaksignore"
    config.write_text(DEFAULT_CONFIG, encoding="utf-8")
    ignore.write_text("", encoding="utf-8")
    if trusted:
        for name, target in ((".gitleaks.toml", config), (".gitleaksignore", ignore)):
            shown = _git(cwd, "show", f"{trusted}:{name}")
            if shown.returncode == 0:
                target.write_text(shown.stdout, encoding="utf-8")
    return config, ignore


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

    # THE SOURCE IS THE GIT DIRECTORY, NOT `.`: gitleaks also loads
    # `<source>/.gitleaksignore` on top of `--gitleaks-ignore-path` (measured on
    # 8.30.1: a pull request's own ignore entry silenced its own leak). Nothing
    # a pull request commits can land inside the git directory.
    gitdir = _git(cwd, "rev-parse", "--absolute-git-dir").stdout.strip()
    if not gitdir:
        print("::error::secret scan refused: no git directory here", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        config, ignore = stage_config(cwd, plan.trusted, Path(tmp))
        try:
            result = subprocess.run(
                ["gitleaks", "git", "--redact", "--no-banner", "--verbose",
                 "--exit-code", "1", "--ignore-gitleaks-allow",
                 f"--config={config}", f"--gitleaks-ignore-path={ignore}",
                 f"--log-opts={plan.log_opts}", gitdir],
                cwd=cwd, env=_env(),
            )
        except FileNotFoundError:
            # Not exit 1: that code means "leaks found", and a traceback exits 1.
            print("::error::secret scan refused: gitleaks is not on PATH", file=sys.stderr)
            return 2
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
