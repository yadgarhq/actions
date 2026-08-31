#!/usr/bin/env python3
"""Mechanical ceilings on file and function size.

WHY MECHANICAL RATHER THAN REVIEWED. "Keep functions small" is advice, and advice
loses to a deadline every time. A number that fails the commit does not. The
point is not that 400 lines is meaningfully different from 410 — it is that
there is a line at all, enforced early, so growth is a decision someone makes
rather than something that happens.

WHY EARLY. This is cheap now and expensive later: a repository that crosses the
limit on its first day changes one file, and one that crosses it after a year
needs a refactor nobody scheduled. Adopting a limit only once it is already
breached means starting with an exemption list, which is how the limit stops
meaning anything.

WHAT THIS DOES NOT MEASURE. Line counts are a proxy. A 300-line function of flat
match arms is fine and a 40-line one with five levels of nesting is not; clippy's
`cognitive_complexity` catches the second and this catches the first. They are
complementary, and neither is a substitute for the test-quality audit that reads
what the code actually does.

Deliberately NOT configurable per repository. A per-repo override is a knob that
gets turned the first time it is inconvenient, and then the limit is whatever the
last person needed it to be.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The ceilings. Chosen to be generous rather than aspirational: nothing in this
# codebase is near them today, so the first file to hit one is genuinely growing
# rather than merely typical.
MAX_FILE_LINES = 500
MAX_FN_LINES = 120

# Test files hold long tables of cases, and splitting a test module to satisfy a
# line count makes tests harder to read for no benefit. Their real quality gate
# is the test-quality audit, which asks whether they assert anything.
TEST_PATHS = ("/tests/", "/benches/")

FN_START = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?fn\s+(\w+)")


def _is_test_file(path: Path) -> bool:
    p = path.as_posix()
    return any(t in f"/{p}" for t in TEST_PATHS) or p.endswith("_test.rs")


def _function_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """Find functions by brace depth.

    Not a parser. It tracks depth from the opening brace of a signature to the
    matching close, which is enough to measure length and wrong only for braces
    inside strings — a case that would make a function look longer, never
    shorter, so it cannot hide a violation.
    """
    spans: list[tuple[str, int, int]] = []
    i = 0
    while i < len(lines):
        m = FN_START.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(4)
        depth = 0
        opened = False
        start = i
        while i < len(lines):
            depth += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                opened = True
            if opened and depth <= 0:
                break
            i += 1
        if opened:
            spans.append((name, start + 1, i - start + 1))
        i += 1
    return spans


def check(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    problems: list[str] = []
    if _is_test_file(path):
        return problems

    if len(lines) > MAX_FILE_LINES:
        problems.append(
            f"{path}: {len(lines)} lines, over the {MAX_FILE_LINES} ceiling. "
            f"Split it along a seam that already exists rather than at the line "
            f"count — a file cut to satisfy a number is worse than a long one."
        )

    for name, line_no, length in _function_spans(lines):
        if length > MAX_FN_LINES:
            problems.append(
                f"{path}:{line_no}: fn {name} is {length} lines, over the "
                f"{MAX_FN_LINES} ceiling."
            )
    return problems


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for arg in argv:
        problems.extend(check(Path(arg)))
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(
            "\nThese are ceilings, not suggestions. If a limit is genuinely wrong "
            "for this codebase, change it in yadgarhq/actions for everyone and say "
            "why — do not add an exemption here.",
            file=sys.stderr,
        )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
