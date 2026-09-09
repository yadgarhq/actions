"""LEDGER 718. `hooks/complexity.py`'s test exemption, made to match its own
stated intent.

THE DEFECT THIS FIXES. `TEST_PATHS = ("/tests/", "/benches/")` plus an
`_test.rs` suffix check exempts an integration-test *directory*, but not
Rust's own convention for a unit-test *submodule*: a file literally named
`tests.rs` (or `test.rs`), wired into its parent with `#[cfg(test)] mod
tests;`. Measured on the live estate 2026-09-09: `iam/src/service/tests.rs`
(3669 lines), `gateway/src/http/tests.rs` (2510 lines) and
`iam/src/crypto/tests.rs` (757 lines) -- ~6.9k lines of pure test code the
gate was counting against a comment that says it should not.

THE PREDICATE, AND WHY THE TWO CEILINGS GET DIFFERENT ANSWERS.

- MAX_FILE_LINES: a whole-file exemption by filename (`tests.rs`/`test.rs`),
  because the file-level check runs over the whole file and there is no
  cheap way to tell "mostly test" from "mostly production" at that
  granularity. Filename alone is unidiomatic-but-not-impossible for
  production code, so it is not sufficient on its own: the file must also
  carry a real `#[test]` (or `#[tokio::test]`, ...) attribute somewhere in
  it. `test_tests_rs_without_any_test_fn_is_still_red` below is the proof
  that naming alone does not buy the exemption.

- MAX_FN_LINES: attribute-based, not filename-based, and it applies
  everywhere -- including a `#[cfg(test)] mod tests { ... }` block inline in
  an ordinary production file, which the filename rule above cannot reach.
  Any function actually marked `#[test]`/`#[tokio::test]` is unambiguously
  test code wherever it lives, and forging that marker to smuggle an
  oversized production function past the ceiling means the function must
  also compile and pass as a test in CI.

EVERY GREEN CASE HERE IS PAIRED WITH A RED ONE ON THE SAME INPUT SHAPE
(ledger 731's standard): same size, same structure, one property flipped.
The pairs that specifically probe the smuggling risk the ledger asked about
are `test_tests_rs_without_any_test_fn_is_still_red` (naming alone) and
`test_production_file_named_helpers_rs_with_test_fn_is_still_red` (a
`#[test]` fn alone, wrong filename).

The gate is run as a SUBPROCESS against files under `tmp_path`, exactly how
pre-commit invokes it in a consumer repository -- not imported, so the
`if __name__ == "__main__":` argv path is what is actually exercised.

Run: python3 -m pytest scripts/tests/test_complexity.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[2] / "hooks" / "complexity.py"


def run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
    )


def write(tmp_path: Path, relative: str, body: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def padding(n: int) -> str:
    """`n` filler lines, each one incapable of matching a function signature."""
    return "// padding\n" * n


def fn_block(name: str, body_lines: int, attrs: tuple[str, ...] = ()) -> str:
    """A single function of `body_lines` + 2 lines, optionally preceded by
    attribute lines such as `#[test]`."""
    out = [f"{a}\n" for a in attrs]
    out.append(f"fn {name}() {{\n")
    out.extend(["    let _ = 0;\n"] * body_lines)
    out.append("}\n")
    return "".join(out)


# --- file-length ceiling: unchanged production behaviour (regression) ------


def test_production_file_under_ceiling_is_green(tmp_path):
    f = write(tmp_path, "src/lib.rs", padding(400))
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_production_file_over_ceiling_is_red(tmp_path):
    f = write(tmp_path, "src/lib.rs", padding(600))
    result = run(f)
    assert result.returncode == 1
    assert "600 lines, over the 500 ceiling" in result.stderr


# --- file-length ceiling: the pre-existing exemptions still hold -----------


def test_integration_tests_dir_stays_exempt(tmp_path):
    f = write(tmp_path, "tests/big.rs", padding(600))
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_same_size_file_outside_tests_dir_is_red(tmp_path):
    f = write(tmp_path, "src/big.rs", padding(600))
    result = run(f)
    assert result.returncode == 1


def test_legacy_underscore_test_suffix_stays_exempt(tmp_path):
    f = write(tmp_path, "src/big_test.rs", padding(600))
    result = run(f)
    assert result.returncode == 0, result.stderr


# --- file-length ceiling: the widened part, an inline test submodule ------


def test_tests_rs_with_test_attr_is_exempt(tmp_path):
    body = padding(590) + fn_block("a_case", 3, attrs=("#[test]",))
    f = write(tmp_path, "src/service/tests.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_test_rs_singular_with_test_attr_is_exempt(tmp_path):
    body = padding(590) + fn_block("a_case", 3, attrs=("#[test]",))
    f = write(tmp_path, "src/crypto/test.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_tokio_test_attr_also_earns_the_tests_rs_exemption(tmp_path):
    body = padding(590) + fn_block("a_case", 3, attrs=("#[tokio::test]",))
    f = write(tmp_path, "src/http/tests.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_tests_rs_without_any_test_fn_is_still_red(tmp_path):
    """The smuggling case for the file-level rule: a suspicious filename
    alone must not be enough to escape the 500-line ceiling."""
    f = write(tmp_path, "src/service/tests.rs", padding(600))
    result = run(f)
    assert result.returncode == 1
    assert "over the 500 ceiling" in result.stderr


def test_production_file_named_helpers_rs_with_test_fn_is_still_red(tmp_path):
    """The smuggling case in the other direction: a `#[test]` fn alone, in a
    file NOT named tests.rs/test.rs, must not exempt the whole file."""
    body = padding(590) + fn_block("a_case", 3, attrs=("#[test]",))
    f = write(tmp_path, "src/helpers.rs", body)
    result = run(f)
    assert result.returncode == 1
    assert "over the 500 ceiling" in result.stderr


# --- the three files actually measured on the live estate, by shape -------

# Content is synthetic -- only the (name, size, a real #[test] fn) shape of
# each real offender is reproduced, not its content.
REAL_OFFENDERS = {
    "iam/src/service/tests.rs": 3669,
    "gateway/src/http/tests.rs": 2510,
    "iam/src/crypto/tests.rs": 757,
}


@pytest.mark.parametrize("relative,total_lines", REAL_OFFENDERS.items())
def test_the_three_measured_offenders_are_exempt_by_shape(
    tmp_path, relative, total_lines
):
    fn = fn_block("a_case", 3, attrs=("#[test]",))
    body = padding(total_lines - fn.count("\n")) + fn
    f = write(tmp_path, relative, body)
    assert len(body.splitlines()) == total_lines  # the shape is the point
    result = run(f)
    assert result.returncode == 0, result.stderr


# --- function-length ceiling: unchanged production behaviour (regression) --


def test_function_under_ceiling_is_green(tmp_path):
    f = write(tmp_path, "src/service.rs", fn_block("a_short_fn", 5))
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_plain_fn_over_ceiling_is_red(tmp_path):
    f = write(tmp_path, "src/service.rs", fn_block("a_long_helper", 150))
    result = run(f)
    assert result.returncode == 1
    assert "over the 120 ceiling" in result.stderr


# --- function-length ceiling: the widened part, #[test]-attributed fns -----
# Deliberately in a file NOT named tests.rs/test.rs, to prove this exemption
# does not depend on the file-level one -- it must reach a #[cfg(test)] block
# living inline in an ordinary production file.


def test_test_attributed_fn_over_ceiling_is_exempt(tmp_path):
    body = fn_block("a_long_case", 150, attrs=("#[test]",))
    f = write(tmp_path, "src/service.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_tokio_test_attributed_fn_over_ceiling_is_exempt(tmp_path):
    body = fn_block("a_long_async_case", 150, attrs=("#[tokio::test]",))
    f = write(tmp_path, "src/service.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_test_attr_below_another_attribute_is_still_found(tmp_path):
    body = fn_block(
        "a_long_case",
        150,
        attrs=('#[should_panic(expected = "boom")]', "#[test]"),
    )
    f = write(tmp_path, "src/service.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_doc_comment_above_test_attr_does_not_defeat_detection(tmp_path):
    body = "/// explains the case\n" + fn_block(
        "a_documented_case", 150, attrs=("#[test]",)
    )
    f = write(tmp_path, "src/service.rs", body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_test_case_attribute_is_not_mistaken_for_test(tmp_path):
    """`#[test_case(...)]` is a real, different attribute (the `test-case`
    crate) that must not false-match `#[test]` on a `\\btest\\b` boundary."""
    body = fn_block("a_long_case", 150, attrs=("#[test_case(1)]",))
    f = write(tmp_path, "src/service.rs", body)
    result = run(f)
    assert result.returncode == 1
    assert "over the 120 ceiling" in result.stderr
