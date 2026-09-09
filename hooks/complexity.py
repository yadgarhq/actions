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

# `TEST_PATHS` matches an integration-test *directory*. It does not match
# Rust's own convention for a unit-test *submodule*: a file literally named
# `tests.rs` (or `test.rs`), wired into its parent with `#[cfg(test)] mod
# tests;`. That convention is why `iam/src/service/tests.rs` and
# `gateway/src/http/tests.rs` were measured as production code -- neither
# path contains a `/tests/` segment or ends in `_test.rs`.
#
# Matching on the bare filename widens the exemption further than a
# directory segment does, so a whole-file exemption on the name alone is not
# enough: it is additionally required that the file carry a real `#[test]`
# (or `#[tokio::test]`, `#[async_std::test]`, ...) attribute. A file named
# `tests.rs`/`test.rs` holding no test function at all is not test code by
# any definition this gate can check, and stays measured. Smuggling
# production code past this predicate requires BOTH naming the file
# `tests.rs`/`test.rs` -- already unidiomatic for anything else in Rust --
# AND adding a `#[test]`-attributed function to it, which a reviewer sees
# immediately and which must itself compile and pass in CI.
TEST_FILE_NAMES = ("tests.rs", "test.rs")

FN_START = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?fn\s+(\w+)")

# A function actually marked as a test, wherever it lives -- including a
# `#[cfg(test)] mod tests { ... }` block inline in an otherwise-production
# file, which the whole-file exemptions above do not reach. `\btest\b`
# requires a word boundary immediately after "test", so `#[test_case(...)]`
# and similar non-test attributes do not match.
TEST_ATTR = re.compile(r"^\s*#\[\s*(\w+::)?test\b")


def _has_test_attr(lines: list[str]) -> bool:
    return any(TEST_ATTR.match(line) for line in lines)


def _is_test_file(path: Path, lines: list[str]) -> bool:
    p = path.as_posix()
    if any(t in f"/{p}" for t in TEST_PATHS) or p.endswith("_test.rs"):
        return True
    return path.name in TEST_FILE_NAMES and _has_test_attr(lines)


def _is_test_fn(lines: list[str], fn_line_index: int) -> bool:
    """Whether the function starting at 0-indexed `fn_line_index` carries a
    test attribute directly on it, walking upward past any other attributes
    or doc comments stacked above the `fn` line."""
    i = fn_line_index - 1
    while i >= 0:
        stripped = lines[i].strip()
        if TEST_ATTR.match(lines[i]):
            return True
        if stripped.startswith("#[") or stripped.startswith(("///", "//!")):
            i -= 1
            continue
        break
    return False


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
    if _is_test_file(path, lines):
        return problems

    if len(lines) > MAX_FILE_LINES:
        problems.append(
            f"{path}: {len(lines)} lines, over the {MAX_FILE_LINES} ceiling. "
            f"Split it along a seam that already exists rather than at the line "
            f"count — a file cut to satisfy a number is worse than a long one."
        )

    for name, line_no, length in _function_spans(lines):
        if length > MAX_FN_LINES and not _is_test_fn(lines, line_no - 1):
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
