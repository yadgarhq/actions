"""LEDGER 837. Every mechanism `hooks/no_test_skips.py` covers, with the input it
must refuse and the near-miss it must accept.

ADR-0606: a gate change is not reviewed until somebody has CONSTRUCTED the input
the gate must refuse AND the near-miss it must accept, run the gate against both,
and read the exit code. This file is that construction made permanent, so the
pairs outlive the pull request that argued for them.

EVERY RED CASE HERE IS PAIRED WITH A GREEN ONE ON THE SAME INPUT SHAPE — same
file, same structure, one property flipped. The pairs that matter most are the
ones probing the EXEMPTION, because an exemption nobody can falsify is the escape
hatch this gate exists to avoid:

  - `test_an_ignored_test_a_workflow_runs_is_accepted` against
    `test_an_ignored_test_nothing_runs_is_refused` — the same `#[ignore]`, and
    the only difference is whether a workflow executes it.
  - `test_the_tls_contract_without_the_shared_workflow_is_refused` — the source
    names the contract and STILL reds, because the other half of the fact (this
    repository calls the reusable workflow whose step reads that name) is absent.
    That is the case that proves the exemption keys on execution rather than on a
    string somebody typed.
  - `test_a_guard_after_the_first_statement_is_accepted` — `yadgarhq/yadgar`'s
    real shape, reduced. It must stay green, and `test_a_first_statement_env_guard_is_refused`
    must stay red, on bodies that differ only in where the guard sits.

THE GATE IS RUN AS A SUBPROCESS against files under `tmp_path`, exactly how
pre-commit invokes it in a consumer repository — the convention
`test_complexity.py` records, and for the same reason: an import would test a
function while the estate runs a program.

SKIP MARKERS IN THIS FILE ARE ASSEMBLED FROM HALVES, e.g. `"@pytest" +
".mark.skip"`. That is not obfuscation for its own sake. Layer 1 walks every
`.py` file in the tree, this one included, so a fixture that spelled a marker out
in full would make this suite fail the gate it tests — and a gate that cannot
survive its own rule is a gate somebody switches off. The concatenation is exact
at run time, which is the only place it has to be. `hooks/no_test_skips.py`
solves the same problem a different way, by writing its patterns in a form whose
own source text cannot satisfy them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

GATE = Path(__file__).resolve().parents[2] / "hooks" / "no_test_skips.py"
REPO = Path(__file__).resolve().parents[2]

# Assembled so that this file does not trip the gate it tests. See the header.
MARK_SKIP = "@pytest" + ".mark.skip"
MARK_SKIPIF = "@pytest" + ".mark.skipif"
MARK_XFAIL = "@pytest" + ".mark.xfail"
CALL_SKIP = "pytest" + ".skip(" + '"not today")'
CALL_XFAIL = "pytest" + ".xfail(" + '"not today")'
CALL_IMPORTORSKIP = "pytest" + ".importorskip(" + '"boto3")'
UNITTEST_SKIP = "@unittest" + ".skip(" + '"not today")'
UNITTEST_SKIPIF = "@unittest" + ".skipIf(" + 'True, "not today")'
UNITTEST_SKIPUNLESS = "@unittest" + ".skipUnless(" + 'False, "not today")'
UNITTEST_XFAIL = "@unittest" + ".expectedFailure"
SKIPTEST_CALL = "self" + ".skip" + 'Test("not today")'
RAISE_SKIPTEST = "raise " + "unittest" + ".SkipTest(" + '"not today")'
MODULE_MARK = "pytestmark" + " = " + "pytest" + ".mark.skip"

# Split across SOURCE LINES, not merely at a string boundary. Layer 1 reads the
# tail of the line an invocation sits on, so a filter or a `|| true` on the same
# source line as `cargo test` is a finding against this file however the quotes
# fall. The runtime value is unaffected.
CARGO_SKIP_FLAG = (
    "cargo test --all-features -- "
    "--skip the_slow_one"
)
CARGO_SUPPRESSED = (
    "cargo test --all-features "
    "|| true"
)
CARGO_CAPTURED = "cargo test --all-features " + "|| rc=$?"
PYTEST_FILTERED = (
    "pytest scripts/tests/ "
    "-k 'not slow'"
)
PYTEST_PLAIN = "pytest scripts/tests/ -q"


def run(root: Path):
    """Layer 1 over a tree, the way pre-commit runs it."""
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def audit(tmp_path: Path, text: str, name: str = "out.txt"):
    """Layer 2 over one captured `cargo test` output."""
    log = tmp_path / name
    log.write_text(text)
    return subprocess.run(
        [sys.executable, str(GATE), "--audit-cargo", str(log)],
        capture_output=True,
        text=True,
        check=False,
    )


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A throwaway repository. Every fixture is a whole tree, because the gate's
    verdict on an `#[ignore]` depends on the workflows beside it."""
    root = tmp_path / "repo"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    root.mkdir(parents=True, exist_ok=True)
    return root


MANIFEST = """\
[package]
name = "fixture"
version = "0.1.0"

[features]
default = []
extra = []
"""

IGNORED_TEST = """\
#[test]
#[ignore = "needs something"]
fn the_ignored_one() {
    assert!(true);
}
"""

# `cargo test --test suite -- --ignored`, in the shape a workflow writes it.
WORKFLOW_RUNS_IGNORED = """\
name: smoke
on: workflow_dispatch
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - run: cargo test --locked --test suite -- --ignored --nocapture
"""

WORKFLOW_PLAIN = """\
name: ci
on: pull_request
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - run: cargo test --all-features
"""

CALLS_SHARED = """\
name: ci
on: pull_request
jobs:
  pr:
    uses: yadgarhq/actions/.github/workflows/ci-pr.yaml@main
"""


# --------------------------------------------------------------------------
# `#[ignore]` — the exemption, and the two facts it rests on.
# --------------------------------------------------------------------------


def test_an_ignored_test_nothing_runs_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "the_ignored_one" in result.stderr
    assert "nothing in this repository executes it" in result.stderr


def test_an_ignored_test_a_workflow_runs_is_accepted(tmp_path):
    """The same `#[ignore]`, and the only difference is that something runs it."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
            ".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED,
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert "1 ignored attributes" in result.stdout


def test_a_workflow_running_a_different_target_does_not_cover_this_one(tmp_path):
    """`--test other` is not `--test suite`, and the exemption is per target."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED.replace(
                "--test suite", "--test other"
            ),
        },
    )
    assert run(root).returncode == 1


def test_a_workflow_without_ignored_does_not_cover_an_ignored_test(tmp_path):
    """A step that names the target but never passes `--ignored` runs none of them."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED.replace(
                " -- --ignored --nocapture", ""
            ),
        },
    )
    assert run(root).returncode == 1


def test_an_unfiltered_ignored_run_covers_a_unit_test(tmp_path):
    """An `#[ignore]` inside `src/` has no `--test` name, so only an unfiltered
    `--ignored` run reaches it."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": "#[cfg(test)]\nmod tests {\n" + IGNORED_TEST + "}\n",
            ".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED.replace(
                "--test suite ", ""
            ),
        },
    )
    assert run(root).returncode == 0


def test_a_unit_test_is_not_covered_by_a_target_filtered_run(tmp_path):
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": "#[cfg(test)]\nmod tests {\n" + IGNORED_TEST + "}\n",
            ".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED,
        },
    )
    assert run(root).returncode == 1


def test_the_tls_contract_with_the_shared_workflow_is_accepted(tmp_path):
    """`store`'s real shape: the source names the contract, and this repository
    calls the reusable workflow whose step reads that name."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/engine_tls.rs": IGNORED_TEST
            + '\nfn dsn() -> String { std::env::var("YADGAR_TEST_TLS_DSN").unwrap() }\n',
            ".github/workflows/ci.yaml": CALLS_SHARED,
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stderr


def test_the_tls_contract_without_the_shared_workflow_is_refused(tmp_path):
    """THE CASE THAT PROVES THE EXEMPTION IS A FACT. The source names the
    contract, and the repository does not call the workflow whose step acts on
    it, so nothing runs the suite and the exemption does not apply."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/engine_tls.rs": IGNORED_TEST
            + '\nfn dsn() -> String { std::env::var("YADGAR_TEST_TLS_DSN").unwrap() }\n',
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
        },
    )
    assert run(root).returncode == 1


def test_the_shared_workflow_without_the_contract_is_refused(tmp_path):
    """The other half missing: the workflow is called, and no source names the
    contract, so the private-CA step selects nothing."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/engine_tls.rs": IGNORED_TEST,
            ".github/workflows/ci.yaml": CALLS_SHARED,
        },
    )
    assert run(root).returncode == 1


def test_the_tls_contract_in_a_lib_target_is_refused(tmp_path):
    """THE CASE THAT PROVES THE EXEMPTION KEYS ON WHAT THE STEP CAN SELECT.
    `ci-pr.yaml`'s private-CA step reads `cargo metadata` and runs
    `cargo test --test "$t"` only for targets of kind `test` — a file compiled
    into the lib target can never be named by `--test`. So the same marker
    that is genuinely covered in `tests/engine_tls.rs`
    (`test_the_tls_contract_with_the_shared_workflow_is_accepted`, the paired
    near-miss) grants no real coverage from a lib-target path, and moving a
    TLS test from `tests/` into `src/` — one ordinary refactor — must still
    red."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/engine/tests.rs": IGNORED_TEST
            + '\nfn dsn() -> String { std::env::var("YADGAR_TEST_TLS_DSN").unwrap() }\n',
            ".github/workflows/ci.yaml": CALLS_SHARED,
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "the_ignored_one" in result.stderr
    assert "nothing in this repository executes it" in result.stderr


# --------------------------------------------------------------------------
# `#[cfg]` narrowing a test out of every build there is.
# --------------------------------------------------------------------------


def test_a_test_behind_an_empty_any_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": "#[cfg(any())]\n#[test]\nfn never_runs() { assert!(false); }\n",
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "false by definition" in result.stderr


def test_a_test_behind_cfg_unix_is_accepted(tmp_path):
    """`yadgarhq/yadgar`'s real shape, and the estate's only cfg on a test."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": "#[cfg(unix)]\n#[test]\nfn runs_on_the_runner() { assert!(true); }\n",
        },
    )
    assert run(root).returncode == 0


def test_a_test_behind_an_undeclared_feature_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": '#[cfg(feature = "nobody-declares-this")]\n'
            "#[test]\nfn never_runs() { assert!(false); }\n",
        },
    )
    result = run(root)
    assert result.returncode == 1
    assert "nobody-declares-this" in result.stderr


def test_a_test_behind_a_declared_feature_is_accepted(tmp_path):
    """`--all-features` turns it on, so it runs."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": '#[cfg(feature = "extra")]\n#[test]\nfn runs() { assert!(true); }\n',
        },
    )
    assert run(root).returncode == 0


def test_a_test_behind_an_invented_cfg_flag_is_refused(tmp_path):
    """Nothing in this estate passes `--cfg slow_tests`, so a test behind it never
    runs and never reports that it did not."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "src/lib.rs": "#[cfg(slow_tests)]\n#[test]\nfn never_runs() { assert!(false); }\n",
        },
    )
    assert run(root).returncode == 1


# --------------------------------------------------------------------------
# The runtime guard, and where the line is drawn.
# --------------------------------------------------------------------------

FIRST_STATEMENT_GUARD = """\
#[test]
fn the_bent_one() {
    if std::env::var("CI").is_ok() {
        return;
    }
    assert_eq!(2 + 2, 4);
}
"""

# `yadgarhq/yadgar`'s real shape, reduced: the guard sits after the setup whose
# result it tests, and its block does real work before leaving.
LATER_GUARD = """\
#[test]
fn the_legitimate_one() {
    let home = scratch("fixture");
    install_with(&home).unwrap();
    set_permissions(&home, 0o200).unwrap();
    if std::fs::read_to_string(&home).is_ok() {
        set_permissions(&home, 0o644).unwrap();
        return; // Root, or a filesystem that ignores the mode.
    }
    assert!(drift(&home).is_empty());
}
"""


def test_a_first_statement_env_guard_is_refused(tmp_path):
    root = tree(tmp_path, {"Cargo.toml": MANIFEST, "src/lib.rs": FIRST_STATEMENT_GUARD})
    result = run(root)
    assert result.returncode == 1
    assert "the_bent_one" in result.stderr
    assert "before it asserts anything" in result.stderr


def test_a_guard_after_the_first_statement_is_accepted(tmp_path):
    """MUST STAY GREEN. `yadgarhq/yadgar` carries two of these, and they protect a
    real environmental impossibility after doing the work they exist to test."""
    root = tree(tmp_path, {"Cargo.toml": MANIFEST, "src/lib.rs": LATER_GUARD})
    result = run(root)
    assert result.returncode == 0, result.stderr


def test_a_first_statement_if_that_asserts_is_accepted(tmp_path):
    """An `if` that does work rather than leaving is not a guard."""
    body = FIRST_STATEMENT_GUARD.replace("        return;\n", "        assert!(true);\n")
    root = tree(tmp_path, {"Cargo.toml": MANIFEST, "src/lib.rs": body})
    assert run(root).returncode == 0


def test_a_first_statement_guard_with_no_env_probe_is_accepted(tmp_path):
    """A guard on a value the test computed is a branch, not an escape from the
    environment. Named here so the boundary is deliberate rather than accidental."""
    body = FIRST_STATEMENT_GUARD.replace('std::env::var("CI").is_ok()', "1 + 1 == 3")
    root = tree(tmp_path, {"Cargo.toml": MANIFEST, "src/lib.rs": body})
    assert run(root).returncode == 0


# --------------------------------------------------------------------------
# The Python skip family.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        MARK_SKIP,
        MARK_SKIPIF,
        MARK_XFAIL,
        CALL_SKIP,
        CALL_XFAIL,
        CALL_IMPORTORSKIP,
        UNITTEST_SKIP,
        UNITTEST_SKIPIF,
        UNITTEST_SKIPUNLESS,
        UNITTEST_XFAIL,
        SKIPTEST_CALL,
        RAISE_SKIPTEST,
        MODULE_MARK,
    ],
)
def test_every_python_skip_marker_is_refused(tmp_path, marker):
    root = tree(tmp_path, {"tests/test_thing.py": f"{marker}\ndef test_thing():\n    assert True\n"})
    result = run(root)
    assert result.returncode == 1, f"{marker} was accepted"


def test_a_plain_python_test_is_accepted(tmp_path):
    root = tree(tmp_path, {"tests/test_thing.py": "def test_thing():\n    assert True\n"})
    assert run(root).returncode == 0


def test_an_identifier_that_merely_contains_skip_is_accepted(tmp_path):
    """`skip_count` is not a skip. The near-miss for the whole marker family."""
    root = tree(
        tmp_path,
        {
            "tests/test_thing.py": "skip_count = 0\n"
            "def test_thing():\n    assert skip_count == 0\n",
        },
    )
    assert run(root).returncode == 0


# --------------------------------------------------------------------------
# Collection narrowed before any flag is passed.
# --------------------------------------------------------------------------


def test_addopts_narrowing_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {"pytest.ini": "[pytest]\n"
                       "addopts = -q --deselect tests/test_slow.py\n"},
    )
    result = run(root)
    assert result.returncode == 1
    assert "addopts" in result.stderr


def test_addopts_without_narrowing_is_accepted(tmp_path):
    root = tree(
        tmp_path,
        {"pytest.ini": "[pytest]\n"
                       "addopts = -q --strict-markers\n"},
    )
    assert run(root).returncode == 0


def test_collect_ignore_is_refused(tmp_path):
    root = tree(tmp_path, {"tests/conftest.py": 'collect_ignore = ["test_slow.py"]\n'})
    result = run(root)
    assert result.returncode == 1
    assert "collect_ignore" in result.stderr


def test_a_conftest_without_collect_ignore_is_accepted(tmp_path):
    root = tree(tmp_path, {"tests/conftest.py": "import pytest\n\npytest_plugins = []\n"})
    assert run(root).returncode == 0


# --------------------------------------------------------------------------
# The invocation itself — a filter and a suppressed exit code.
# --------------------------------------------------------------------------


def test_a_cargo_skip_filter_in_a_workflow_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {".github/workflows/ci.yaml": WORKFLOW_PLAIN.replace(
            "cargo test --all-features", CARGO_SKIP_FLAG
        )},
    )
    result = run(root)
    assert result.returncode == 1
    assert "narrowed by --skip" in result.stderr


def test_a_target_selector_and_ignored_are_accepted(tmp_path):
    """`--test <target>` selects a binary; it does not filter within one. The
    private-CA step in `ci-pr.yaml` depends on this staying green."""
    root = tree(tmp_path, {".github/workflows/smoke.yaml": WORKFLOW_RUNS_IGNORED})
    assert run(root).returncode == 0


def test_a_suppressed_cargo_exit_code_is_refused(tmp_path):
    root = tree(
        tmp_path,
        {".github/workflows/ci.yaml": WORKFLOW_PLAIN.replace(
            "cargo test --all-features", CARGO_SUPPRESSED
        )},
    )
    result = run(root)
    assert result.returncode == 1
    assert "suppressed" in result.stderr


def test_a_captured_cargo_exit_code_is_accepted(tmp_path):
    """`|| rc=$?` CAPTURES the status; `ci-pr.yaml`'s private-CA step then checks
    it. This is the near-miss the suppression pattern must not swallow."""
    root = tree(
        tmp_path,
        {".github/workflows/ci.yaml": WORKFLOW_PLAIN.replace(
            "cargo test --all-features", CARGO_CAPTURED
        )},
    )
    result = run(root)
    assert result.returncode == 0, result.stderr


def test_a_neighbouring_true_fallback_is_accepted(tmp_path):
    """`ci-pr.yaml` needs `|| true` on a `grep` that may match nothing, three
    times over, in the same block as a `cargo test`. Only suppression attached to
    the test invocation is a finding."""
    block = (
        "name: ci\non: pull_request\njobs:\n  ci:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: |\n"
        "          line=$(grep -E '^test result:' out.txt | tail -n1 || true)\n"
        "          cargo test --all-features\n"
    )
    root = tree(tmp_path, {".github/workflows/ci.yaml": block})
    result = run(root)
    assert result.returncode == 0, result.stderr


def test_a_pytest_k_filter_is_refused(tmp_path):
    root = tree(tmp_path, {"run.sh": f"#!/bin/sh\n{PYTEST_FILTERED}\n"})
    result = run(root)
    assert result.returncode == 1
    assert "narrowed by" in result.stderr


def test_a_plain_pytest_invocation_is_accepted(tmp_path):
    """`pytest scripts/tests/ -q` is how this repository runs its own suite."""
    root = tree(tmp_path, {"run.sh": f"#!/bin/sh\n{PYTEST_PLAIN}\n"})
    assert run(root).returncode == 0


def test_git_describe_exact_match_is_not_a_test_filter(tmp_path):
    """`ci-pr.yaml` runs `git describe --tags --exact-match`, and `--exact` is a
    cargo filter. The tail read starts at the test invocation, so an unrelated
    command carrying the same flag is not a finding."""
    block = (
        "name: ci\non: pull_request\njobs:\n  ci:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: |\n"
        "          git describe --tags --exact-match --match 'v*' HEAD\n"
        "          cargo test --all-features\n"
    )
    root = tree(tmp_path, {".github/workflows/ci.yaml": block})
    result = run(root)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Layer 1 says what it examined, even when it found nothing.
# --------------------------------------------------------------------------


def test_an_empty_tree_reports_what_it_examined(tmp_path):
    """A repository with no test suite of any kind — `argocd`, `proto` and
    `deploy` are three — is not a skip, and Layer 1 is a STATIC scan rather than
    an execution audit, so it passes. The counts are what makes that verdict
    readable rather than indistinguishable from a green one (ADR-0645)."""
    root = tree(tmp_path, {"README.md": "nothing here\n"})
    result = run(root)
    assert result.returncode == 0
    assert "0 rust files" in result.stdout
    assert "0 test items" in result.stdout


def test_the_gate_does_not_refuse_its_own_repository(tmp_path):
    """A gate that cannot survive its own rule is a gate somebody switches off.
    This is also the check that catches a pattern which matches its own source.

    THE SECOND ASSERTION IS NOT DECORATION (ADR-0645/0646), and this session
    falsified the version without it. While `.claude` was in `PRUNE` and the
    match was still made against the ABSOLUTE path, this test passed vacuously:
    the working clone sat under `~/.claude/jobs/...`, the whole tree was pruned,
    the walk examined nothing, and the gate exited 0. A green over zero files is
    the failure mode, so the file count is asserted beside the verdict."""
    result = run(REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 python files" not in result.stdout, result.stdout
    assert "0 workflows" not in result.stdout, result.stdout


# --------------------------------------------------------------------------
# Layer 2 — the execution audit over captured output.
# --------------------------------------------------------------------------

# Real lines, from `yadgarhq/store` job 102994206633.
REAL_STORE = """\
test result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 1.10s
test tls::a_valid_but_unrelated_ca_is_refused ... ignored
test result: ok. 0 passed; 0 failed; 3 ignored; 0 measured; 0 filtered out; finished in 0.00s
test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""


def test_the_real_store_shape_is_accepted(tmp_path):
    """MEASURED, NOT CONSTRUCTED. `store` reports `3 ignored` in every green run,
    and a doc-test line reporting `0 passed` beside it. Both must stay green: the
    ignored trio is executed later in the same job, and the floor on executed
    tests is an aggregate rather than per-line for exactly this reason."""
    result = audit(tmp_path, REAL_STORE)
    assert result.returncode == 0, result.stderr
    assert "3 ignored" in result.stdout
    assert "a_valid_but_unrelated_ca_is_refused" in result.stdout


def test_no_test_result_line_at_all_is_refused(tmp_path):
    """ADR-0645. An audit that examined nothing must not report green."""
    result = audit(tmp_path, "Compiling fixture v0.1.0\nFinished in 3.2s\n")
    assert result.returncode == 1
    assert "examined" in result.stderr


def test_a_summary_line_with_anything_before_it_is_not_counted(tmp_path):
    """THE ANCHORING IS DELIBERATE, and this fixes it in place.

    `CARGO_RESULT` is anchored with `^` and used with `.match()`, so a line that
    merely CONTAINS the phrase is not a summary — the raw job log carries the
    workflow's own echoed shell (`grep -E '^test result:' "$out"`), and an
    unanchored read would count that as a test binary. The cost of anchoring is
    that a progress line interleaved in front of a summary hides it, which is why
    `ci-pr.yaml` captures cargo's STDOUT alone rather than merging both
    descriptors: measured, libtest writes this line to stdout and cargo writes
    `Compiling`/`Running`/`Finished` to stderr.
    """
    result = audit(
        tmp_path,
        "   Compiling foo v0.1.0"
        "test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n",
    )
    assert result.returncode == 1
    assert "no `test result:` line" in result.stderr


def test_a_missing_capture_file_is_refused(tmp_path):
    result = subprocess.run(
        [sys.executable, str(GATE), "--audit-cargo", str(tmp_path / "nope.txt")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_a_filtered_run_is_refused(tmp_path):
    line = (
        "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; "
        "7 filtered out; finished in 0.01s\n"
    )
    result = audit(tmp_path, line)
    assert result.returncode == 1
    assert "FILTERED OUT" in result.stderr


def test_a_measured_run_is_refused(tmp_path):
    line = (
        "test result: ok. 0 passed; 0 failed; 0 ignored; 4 measured; "
        "0 filtered out; finished in 0.01s\n"
    )
    result = audit(tmp_path, line)
    assert result.returncode == 1
    assert "benchmark mode" in result.stderr


def test_a_run_where_nothing_passed_is_refused(tmp_path):
    line = (
        "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; "
        "0 filtered out; finished in 0.00s\n"
    )
    result = audit(tmp_path, line)
    assert result.returncode == 1
    assert "zero passing tests" in result.stderr


def test_a_failed_run_is_refused(tmp_path):
    """MEASURED: `test result: FAILED. 3 passed; 1 failed; 0 ignored; 0 measured;
    0 filtered out` audited GREEN before this test existed — `totals["failed"]`
    was parsed and printed but never asserted on. In the wiring this repository
    ships, `set -euo pipefail` aborts before the audit ever sees a failing run,
    but `--audit-cargo` is a published entry point on its own, and anything
    wiring it outside that pipeline inherited the hole."""
    line = (
        "test result: FAILED. 3 passed; 1 failed; 0 ignored; 0 measured; "
        "0 filtered out; finished in 0.02s\n"
    )
    result = audit(tmp_path, line)
    assert result.returncode == 1
    assert "1 test(s) FAILED" in result.stderr


def test_one_real_line_beside_an_empty_doctest_line_is_accepted(tmp_path):
    """The near-miss for the case above: the same `0 passed` line is fine when
    another binary in the same run did execute something."""
    text = (
        "test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; "
        "0 filtered out; finished in 0.10s\n"
        "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; "
        "0 filtered out; finished in 0.00s\n"
    )
    result = audit(tmp_path, text)
    assert result.returncode == 0, result.stderr
    assert "2 `test result:` line(s)" in result.stdout


# --------------------------------------------------------------------------
# Layer 2 — the pytest wrapper, which is the half this repository can execute.
# --------------------------------------------------------------------------


def run_pytest_mode(tmp_path: Path, suite: str):
    path = tmp_path / "suite"
    path.mkdir()
    (path / "test_fixture.py").write_text(suite)
    return subprocess.run(
        [sys.executable, str(GATE), "--run-pytest", str(path), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )


def test_the_pytest_wrapper_accepts_a_suite_that_all_passed(tmp_path):
    result = run_pytest_mode(tmp_path, "def test_one():\n    assert True\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_the_pytest_wrapper_refuses_a_skipped_test(tmp_path):
    suite = f"import pytest\n\n{MARK_SKIP}\ndef test_one():\n    assert True\n\n" \
            "def test_two():\n    assert True\n"
    result = run_pytest_mode(tmp_path, suite)
    assert result.returncode == 1
    assert "skipped" in result.stderr


def test_the_pytest_wrapper_refuses_an_xfailed_test(tmp_path):
    suite = f"import pytest\n\n{MARK_XFAIL}\ndef test_one():\n    assert False\n\n" \
            "def test_two():\n    assert True\n"
    result = run_pytest_mode(tmp_path, suite)
    assert result.returncode == 1
    assert "xfailed" in result.stderr


def test_the_pytest_wrapper_refuses_a_suite_that_collected_nothing(tmp_path):
    """ADR-0645 again, at the other layer: pytest exits non-zero on an empty
    collection, and the wrapper must not turn that into a pass."""
    result = run_pytest_mode(tmp_path, "def helper():\n    return 1\n")
    assert result.returncode != 0


def test_the_pytest_wrapper_forwards_a_real_failure(tmp_path):
    """A wrapper that eats the failure report replaces a readable red with an
    unreadable one, so pytest's own output and exit code come through."""
    result = run_pytest_mode(tmp_path, "def test_one():\n    assert 1 == 2\n")
    assert result.returncode != 0
    assert "assert 1 == 2" in result.stdout


# --------------------------------------------------------------------------
# LEDGER 860 — the walk sees the FILESYSTEM, so a second checkout inside the
# tree is judged as though it were the repository.
# --------------------------------------------------------------------------

# THE FIXTURE IS A REAL DIRECTORY TREE rather than a constructed path list,
# because the defect is precisely that `walk` reads the filesystem: `tree()`
# writes these files to disk and the gate is a subprocess over that disk.
AGENT_WORKTREE = ".claude/worktrees/stale-branch/tests/suite.rs"


def test_an_ignored_test_in_an_agent_worktree_is_not_reported(tmp_path):
    """LEDGER 860, and it blocked every local commit in `yadgarhq/iam-db`.

    `.claude/worktrees/<name>` is a git worktree an agent left behind. It is a
    SECOND CHECKOUT, so its contents belong to another branch, and judging this
    repository by them is the same error `.ci-actions` is pruned for. The
    signature is a hook that refuses locally while CI stays green, because CI
    clones afresh and has no such directory.

    The assertion on the `examined:` counts is load-bearing. A `PRUNE` entry that
    pruned too much would also make the refusal disappear, so this reads the
    number of files the walk still visited: the repository's own one, and not the
    worktree's.
    """
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": "#[test]\nfn the_real_one() {\n    assert!(true);\n}\n",
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
            AGENT_WORKTREE: IGNORED_TEST,
        },
    )
    result = run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ".claude" not in result.stdout + result.stderr
    assert "1 rust files" in result.stdout
    assert "1 test items" in result.stdout


def test_the_same_ignored_test_in_the_repository_is_still_refused(tmp_path):
    """THE COMPANION ASSERTION ON THE PRISTINE INPUT (ADR-0646). The test above
    passes under a `PRUNE` that prunes everything, so it proves nothing on its
    own. This is the identical fixture at a path that IS the repository, and it
    must stay red — same file content, same tree shape, one property flipped."""
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": "#[test]\nfn the_real_one() {\n    assert!(true);\n}\n",
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
            "worktrees/stale-branch/tests/suite.rs": IGNORED_TEST,
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "worktrees/stale-branch/tests/suite.rs" in result.stderr
    assert "the_ignored_one" in result.stderr


# --------------------------------------------------------------------------
# LEDGER 842 — what FEEDS the audit, read out of the workflow rather than
# asserted about a constructed log.
# --------------------------------------------------------------------------

CI_PR = REPO / ".github" / "workflows" / "ci-pr.yaml"


def cargo_capture_lines():
    """Every command in `ci-pr.yaml` that captures a `cargo test` run into a file.

    PARSED, NOT GREPPED, and the distinction is the whole test. `ci-pr.yaml`
    carries three prose comments about `tee` inside `run:` blocks and one more in
    a YAML comment between steps, so a grep over the file text finds discussion
    rather than commands. This walks the parsed document, takes only `run:`
    scripts, drops comment-only lines, and rejoins `\\` continuations so that a
    refactor splitting `cargo test` from its pipe cannot slip past.
    """
    document = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))
    found = []
    for job_name, job in document["jobs"].items():
        for step in job.get("steps") or []:
            script = step.get("run")
            if not script:
                continue
            logical = []
            pending = ""
            for raw in script.splitlines():
                if raw.strip().startswith("#"):
                    continue
                if raw.rstrip().endswith("\\"):
                    pending += raw.rstrip()[:-1] + " "
                    continue
                logical.append(pending + raw)
                pending = ""
            if pending:
                logical.append(pending)
            for line in logical:
                if "cargo test" in line and "tee" in line:
                    found.append((job_name, step.get("name", "<unnamed>"), line.strip()))
    return found


def test_no_cargo_capture_merges_stderr_into_the_anchored_parse():
    """LEDGER 842, and the same defect PR #71 fixed twice in the two steps above.

    MEASURED in #71 on a scratch crate: libtest writes `test result:` to STDOUT
    while cargo writes `Compiling`, `Running` and `Finished` to STDERR. Every
    reader of these captures is ANCHORED — `--audit-cargo` matches `^test
    result:`, and the private-CA step runs `grep -E '^test result:'` — so one
    interleaved progress line in front of a summary makes that summary invisible
    and the reader reports "no test result line" on a HEALTHY run. It fails
    closed, which makes it a latent FALSE RED rather than a false green.

    Merging the two descriptors is the only way the interleave can happen, so
    capturing stdout alone removes the surface rather than tolerating it. Stderr
    is not lost: unredirected, it still reaches the job log.
    """
    captures = cargo_capture_lines()
    # ADR-0645, and the class this estate has now measured five times: a scan
    # that found nothing must not report green. Three capture sites exist — the
    # `--all-features` step, the `--no-default-features` step and the private-CA
    # step — so a refactor that deletes one reds here instead of passing over an
    # empty sweep.
    assert len(captures) >= 3, captures
    merged = [c for c in captures if "2>&1" in c[2]]
    assert merged == [], (
        "these captures merge cargo's stderr into a file read by an anchored "
        f"parse, so a progress line can hide the summary: {merged}"
    )


def test_prune_is_not_matched_against_the_path_that_leads_to_the_tree(tmp_path):
    """LEDGER 860, the half that turns a false red into a FALSE GREEN.

    MEASURED against the shipped v1.20.0 gate: one `yadgarhq/store` checkout
    examined 18 Rust files at `/tmp/clean/store` and ZERO at `/tmp/target/store`,
    exiting 0 both times. `PRUNE` was matched over `path.parts` — the whole
    ABSOLUTE path — so a repository whose ancestor directory happened to be named
    `target`, `vendor`, `venv` or `node_modules` had this gate examine nothing and
    report green.

    It is the reason `.claude` could not just be appended to the set: this
    estate's agents work inside `~/.claude/worktrees/<name>`, so the absolute
    match would have made the gate a no-op in precisely those checkouts.

    THE REFUSAL IS THE ASSERTION, not the file count. A skip the gate must refuse
    is planted in the tree, and the tree is placed under a pruned ancestor name.
    """
    parent = tmp_path / "target" / "node_modules" / ".claude"
    parent.mkdir(parents=True)
    root = tree(
        parent,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
        },
    )
    result = run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "the_ignored_one" in result.stderr


def test_the_default_root_is_the_working_directory(tmp_path):
    """THE INVOCATION EIGHTEEN REPOSITORIES ACTUALLY USE, and it had no test.

    `.pre-commit-hooks.yaml` publishes this gate with `pass_filenames: false` and
    no arguments, so every consumer runs it with `--root` at its default of `"."`
    and the tree is whatever directory pre-commit is in. That is a DIFFERENT code
    path from the `--root <absolute>` every other test here uses: the walk yields
    relative paths, and `relative_to(Path("."))` has to be a no-op rather than an
    error for the pruning to work at all. Nothing asserted that until ledger 860
    changed the pruning, so this pins the real invocation in place.
    """
    root = tree(
        tmp_path,
        {
            "Cargo.toml": MANIFEST,
            "tests/suite.rs": IGNORED_TEST,
            ".github/workflows/ci.yaml": WORKFLOW_PLAIN,
            AGENT_WORKTREE: IGNORED_TEST,
        },
    )
    result = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )
    # The repository's own `#[ignore]` is refused; the worktree's is not reported.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "tests/suite.rs" in result.stderr
    assert ".claude" not in result.stdout + result.stderr
    assert "1 ignored attributes" in result.stderr
