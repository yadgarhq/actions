#!/usr/bin/env python3
"""Whether a pull request only moves the `yadgarhq/actions` hook rev forward.

YAADGAAR 442, PART 1. The fan-out of an actions release opens one pull request
per consumer, and each changes ONE line: the `rev:` of the
`https://github.com/yadgarhq/actions` block in `.pre-commit-config.yaml`. Every
one of them ran the full matrix. On the v1.37.0 fan-out that was 187 `ci` jobs
queued at once on the free plan, and on `yadgarhq/store#40` nine `ci /` jobs
executed to check a version string.

WHAT SUCH A DIFF CAN CHANGE is which hooks `precommit` runs, and `precommit`
still runs them, at the new rev, over every file. Nothing else reads the line:
no workflow, no dependency manifest, no source file. So `detect` reports
`config_only=true` for that diff and seven jobs skip. `ci / passed` does not
take the value on trust: `ci_verdict.py` re-derives it from the GitHub API with
the same `classify` below before it accepts one skip (ADR-0638's rule, that a
skip is earned by a fact the gate checks for itself).

THE CLASS IS NARROW ON PURPOSE, and every test refuses rather than guesses:

    one path, `.pre-commit-config.yaml`, status M, mode 100644 on both sides
    the same number of lines on both sides, and exactly one line differs
    both versions of that line match `    rev: vX.Y.Z` in full, X.Y.Z numeric
    the nearest `  - repo:` line above it is exactly the actions URL
    the new version is greater than the old one, as numbers (ADR-0690
    counted 19 downgrades that a text comparison would have called upgrades)

Anything else, and any error, is `false`, and `false` runs everything.

STANDARD LIBRARY ONLY. `detect` runs this on the runner's own `python3`,
before any `setup-python`.

Run: python3 -m pytest scripts/tests/ -q
"""

import os
import re
import subprocess
import sys

CONFIG = ".pre-commit-config.yaml"
ACTIONS_BLOCK = "  - repo: https://github.com/yadgarhq/actions"
REPO_KEY = "  - repo:"
REV = re.compile(r"    rev: v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
REGULAR_FILE = "100644"
SHA = re.compile(r"[0-9a-f]{40}")


class NotConfigOnly(Exception):
    """The diff is outside the class. The message says which test it failed."""


def version(line):
    """`(X, Y, Z)` for a `    rev: vX.Y.Z` line, or raise."""
    m = REV.fullmatch(line)
    if not m:
        raise NotConfigOnly(f"{line!r} is not a `    rev: vX.Y.Z` line")
    return tuple(int(part) for part in m.groups())


def block_of(lines, index):
    """The `  - repo:` line whose block holds `lines[index]`, or None.

    Walks up. A blank line or a comment is skipped. A line indented less than
    four spaces that is not a `  - repo:` line means the walk left the block,
    so the answer is None.
    """
    for i in range(index - 1, -1, -1):
        line = lines[i]
        if line.startswith(REPO_KEY):
            return line
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) < 4:
            return None
    return None


def classify(old_text, new_text):
    """`(old_rev, new_rev)` as `vX.Y.Z` strings when the change is in the class.

    Raises `NotConfigOnly` otherwise. Split on `\\n` and nothing else, so a
    `\\r` stays in the line and fails the full match.
    """
    old = old_text.split("\n")
    new = new_text.split("\n")
    if len(old) != len(new):
        raise NotConfigOnly("the file gained or lost lines")
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    if len(changed) != 1:
        raise NotConfigOnly(f"{len(changed)} lines differ, not exactly one")
    i = changed[0]
    before, after = version(old[i]), version(new[i])
    # ONE FILE IS ENOUGH: `block_of` reads only the lines above `i`, and those
    # are the same in both files.
    if block_of(new, i) != ACTIONS_BLOCK:
        raise NotConfigOnly("the changed line is not in the yadgarhq/actions block")
    if after <= before:
        raise NotConfigOnly(
            f"{old[i].strip()} -> {new[i].strip()} does not move the version forward"
        )
    return "v%d.%d.%d" % before, "v%d.%d.%d" % after


def git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True
    ).stdout


def from_merge_ref(head_sha, run_git=git):
    """`classify` over the merge commit, plus the base tip sha it was merged onto.

    That commit is GitHub's merge of the head into the base, so its first
    parent is the base and the diff to it is what the pull request proposes.
    Anything else checked out (a `ref:` input, a plain commit) has no second
    parent equal to the head, and is refused. The first parent is returned so
    that `ci / passed` can refuse when the base has moved since.

    PLUMBING ONLY. `diff-tree` and `cat-file` do not read `.gitattributes`, so
    the pull request cannot install a diff driver or a text filter here.
    """
    if not SHA.fullmatch(head_sha or ""):
        raise NotConfigOnly("the pull request's head sha is not known")
    parents = run_git("rev-list", "--parents", "-n", "1", "HEAD").decode().split()
    if len(parents) != 3 or parents[2] != head_sha:
        raise NotConfigOnly("HEAD is not the merge of this pull request's head")
    raw = run_git("diff-tree", "-r", "-z", "--raw", "--no-renames", "HEAD^1", "HEAD")
    fields = raw.decode().split("\0")
    if len(fields) != 3 or fields[2] != "":
        raise NotConfigOnly("the pull request does not change exactly one path")
    meta, path = fields[0].split(" "), fields[1]
    if path != CONFIG or len(meta) != 5 or meta[4] != "M":
        raise NotConfigOnly(f"the one changed path is {path!r} ({fields[0]!r})")
    if meta[0] != ":" + REGULAR_FILE or meta[1] != REGULAR_FILE:
        raise NotConfigOnly(f"{CONFIG} is not a regular 100644 file on both sides")
    old = run_git("cat-file", "blob", meta[2]).decode("utf-8")
    new = run_git("cat-file", "blob", meta[3]).decode("utf-8")
    return (*classify(old, new), parents[1])


def detect(env, run_git=git):
    """The lines `detect` appends to `$GITHUB_OUTPUT`, and one line saying why."""
    try:
        if env.get("EVENT") != "pull_request":
            raise NotConfigOnly("not a pull_request event")
        if env.get("ACTION") == "edited":
            raise NotConfigOnly("an edit; `text_edit` decides that run")
        before, after, base = from_merge_ref(env.get("HEAD_SHA", ""), run_git)
    except Exception as exc:  # any failure at all is a full run
        return ["config_only=false"], f"config_only=false: {exc}"
    return (
        [
            "config_only=true",
            f"config_only_from={before}",
            f"config_only_to={after}",
            f"config_only_base={base}",
        ],
        f"config_only=true: the yadgarhq/actions hook rev {before} -> {after} "
        f"is the whole change, so only detect, precommit and passed run.",
    )


def main():
    lines, why = detect(os.environ)
    print("\n".join(lines))
    print(why, file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
