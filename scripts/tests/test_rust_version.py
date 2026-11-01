"""What `rust_version.py` asserts (ADR-1120, ADR-1118).

Every Rust version field in a consumer equals `versions.yaml` rust-toolchain:
the `rust-toolchain.toml` channel and every `Cargo.toml` `rust-version`. The
parity check reports a field that differs; the rewrite moves exactly those
lines and refuses any edit whose parsed TOML changed elsewhere.

Run: python3 -m pytest scripts/tests/ -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rust_version as rv  # noqa: E402

V = "1.99.1"
TOOLCHAIN = '[toolchain]\nchannel = "1.98.0"\ncomponents = ["rustfmt", "clippy"]\n'
CARGO = '[package]\nname = "dial"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.90"\n'
WORKSPACE = ('[workspace]\nmembers = ["crates/front"]\n\n[workspace.package]\n'
             'rust-version = "1.90"\nedition = "2024"\n')
MEMBER = '[package]\nname = "front"\nrust-version.workspace = true\n'
SMOKE = ("jobs:\n  smoke:\n    steps:\n      - uses: dtolnay/rust-toolchain@683b # v1\n"
         "        with:\n          toolchain: stable\n")


# ---------------------------------------------------------------------------
# which files are Rust version fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,kind", [
    ("rust-toolchain.toml", "toolchain"),
    ("Cargo.toml", "cargo"),
    ("crates/front/Cargo.toml", "cargo"),
    (".github/workflows/smoke.yaml", "workflow"),
    (".github/workflows/ci.yml", "workflow"),
    ("crates/rust-toolchain.toml", None),
    ("Cargo.lock", None),
    ("docs/Cargo.toml.md", None),
    (".github/workflows/sub/x.yaml", None),
    (".github/dependabot.yml", None),
])
def test_kind_of(path, kind):
    assert rv.kind_of(path) == kind


# ---------------------------------------------------------------------------
# the parity check
# ---------------------------------------------------------------------------


def test_parity_green_when_every_field_equals_the_version():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
             "Cargo.toml": WORKSPACE.replace("1.90", V),
             "crates/front/Cargo.toml": MEMBER}
    assert rv.findings(files, V) == []


def test_parity_reports_rust_version_and_channel():
    files = {"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO}
    got = rv.findings(files, V)
    assert len(got) == 2
    assert any("rust-toolchain.toml" in f and "1.98.0" in f for f in got)
    assert any("Cargo.toml" in f and "1.90" in f for f in got)


def test_parity_today_only_rust_version_is_red():
    """origin/main on 2026-11-01: channel already 1.99.1, rust-version 1.90."""
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": CARGO}
    got = rv.findings(files, V)
    assert len(got) == 1 and "rust-version" in got[0]


def test_parity_reads_workspace_package_rust_version():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": WORKSPACE,
             "crates/front/Cargo.toml": MEMBER}
    got = rv.findings(files, V)
    assert len(got) == 1 and "workspace.package" in got[0]


def test_parity_a_missing_toolchain_file_is_a_finding():
    got = rv.findings({"Cargo.toml": CARGO.replace("1.90", V)}, V)
    assert len(got) == 1 and "rust-toolchain.toml" in got[0]


def test_parity_no_cargo_toml_is_not_rust():
    assert rv.findings({"README.md": "x"}, V) == []


def test_parity_an_unparseable_manifest_is_a_finding():
    got = rv.findings({"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
                       "Cargo.toml": "[package\n"}, V)
    assert len(got) == 1 and "not valid TOML" in got[0]


def test_parity_ignores_workflow_literals():
    """The brief: the check reads rust-toolchain.toml and rust-version only."""
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
             "Cargo.toml": CARGO.replace("1.90", V), ".github/workflows/smoke.yaml": SMOKE}
    assert rv.findings(files, V) == []


def test_a_version_that_is_not_x_y_z_is_refused():
    with pytest.raises(rv.Refusal):
        rv.findings({"Cargo.toml": CARGO}, "stable")
    with pytest.raises(rv.Refusal):
        rv.findings({"Cargo.toml": CARGO}, "1.99")


# ---------------------------------------------------------------------------
# the rewrite the propagation commits
# ---------------------------------------------------------------------------


def test_rewrite_moves_only_the_rust_version_line():
    new = rv.rewrite("Cargo.toml", CARGO, V)
    assert new == CARGO.replace('rust-version = "1.90"', f'rust-version = "{V}"')


def test_rewrite_moves_the_workspace_package_line_and_leaves_members():
    assert rv.rewrite("Cargo.toml", WORKSPACE, V) == WORKSPACE.replace("1.90", V)
    assert rv.rewrite("crates/front/Cargo.toml", MEMBER, V) == MEMBER


def test_rewrite_moves_the_channel_line():
    assert rv.rewrite("rust-toolchain.toml", TOOLCHAIN, V) == TOOLCHAIN.replace("1.98.0", V)


def test_rewrite_moves_a_workflow_toolchain_literal():
    assert rv.rewrite(".github/workflows/smoke.yaml", SMOKE, V) == SMOKE.replace("stable", V)
    numeric = SMOKE.replace("stable", "1.98.0")
    assert rv.rewrite(".github/workflows/smoke.yaml", numeric, V) == SMOKE.replace("stable", V)


def test_rewrite_leaves_an_expression_and_nightly_alone():
    expr = SMOKE.replace("stable", "${{ needs.detect.outputs.rust_toolchain }}")
    assert rv.rewrite(".github/workflows/smoke.yaml", expr, V) == expr
    nightly = SMOKE.replace("stable", "nightly")
    assert rv.rewrite(".github/workflows/smoke.yaml", nightly, V) == nightly


def test_rewrite_is_idempotent():
    once = rv.rewrite("Cargo.toml", CARGO, V)
    assert rv.rewrite("Cargo.toml", once, V) == once


def test_rewrite_refuses_a_rust_version_outside_a_package_table():
    """A top-level line the regex matches in another table would move a field
    that is not the MSRV; the parsed-TOML comparison refuses it."""
    text = '[package]\nname = "x"\n\n[metadata.foo]\nrust-version = "1.80"\n'
    with pytest.raises(rv.Refusal):
        rv.rewrite("Cargo.toml", text, V)


def test_rewrite_refuses_a_channel_outside_the_toolchain_table():
    text = '[toolchain]\ncomponents = ["clippy"]\n\n[other]\nchannel = "1.98.0"\n'
    with pytest.raises(rv.Refusal):
        rv.rewrite("rust-toolchain.toml", text, V)


def test_rewrite_refuses_an_unknown_path():
    with pytest.raises(rv.Refusal):
        rv.rewrite("Cargo.lock", "x", V)


def test_changes_lists_only_files_that_differ():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": WORKSPACE,
             "crates/front/Cargo.toml": MEMBER, ".github/workflows/smoke.yaml": SMOKE}
    got = rv.changes(files, V)
    assert sorted(got) == [".github/workflows/smoke.yaml", "Cargo.toml"]
    assert got["Cargo.toml"] == WORKSPACE.replace("1.90", V)


# ---------------------------------------------------------------------------
# the command line, against a real git tree
# ---------------------------------------------------------------------------


def _git_env():
    """Ledger 1131: a `git commit` exports GIT_DIR and GIT_INDEX_FILE, and a
    nested `git` would then address the outer repository. Strip them."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_git_env())
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=_git_env())
    return tmp_path


def _check(tmp_path, *args):
    script = Path(__file__).resolve().parents[1] / "rust_version.py"
    return subprocess.run([sys.executable, str(script), "check", *args], cwd=tmp_path,
                          capture_output=True, text=True, check=False, env=_git_env())


def test_cli_warn_mode_exits_zero_with_warnings(tmp_path):
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO})
    done = _check(tmp_path, "--version", V, "--mode", "warn")
    assert done.returncode == 0
    assert done.stdout.count("::warning::") == 2


def test_cli_fail_mode_exits_one_with_errors(tmp_path):
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO})
    done = _check(tmp_path, "--version", V, "--mode", "fail")
    assert done.returncode == 1
    assert done.stdout.count("::error::") == 2


def test_cli_green_tree_exits_zero(tmp_path):
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
                     "Cargo.toml": CARGO.replace("1.90", V)})
    done = _check(tmp_path, "--version", V, "--mode", "fail")
    assert done.returncode == 0, done.stdout
    assert "::" not in done.stdout


def test_cli_reads_tracked_files_only(tmp_path):
    """An untracked stray `Cargo.toml` (a build output, a vendored copy) is not
    the repository's: `git ls-files` decides."""
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
                     "Cargo.toml": CARGO.replace("1.90", V)})
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Cargo.toml").write_text(CARGO)
    assert _check(tmp_path, "--version", V, "--mode", "fail").returncode == 0


def test_cli_a_bad_version_is_an_error_even_in_warn_mode(tmp_path):
    _repo(tmp_path, {"Cargo.toml": CARGO})
    done = _check(tmp_path, "--version", "", "--mode", "warn")
    assert done.returncode == 2


# ---------------------------------------------------------------------------
# a workflow `toolchain:` moves only as the `with:` input of dtolnay/rust-toolchain
# ---------------------------------------------------------------------------

STEPS = """jobs:
  a:
    steps:
      - uses: actions/checkout@abc # v7
        with:
          toolchain: stable
      - name: script
        run: |
          toolchain: stable
      - name: rust
        uses: dtolnay/rust-toolchain@683b # v1
        with:
          components: clippy
          toolchain: 1.98.0
      - uses: dtolnay/rust-toolchain@683b # v1
        with:
          toolchain: stable
        env:
          toolchain: stable
      - uses: other/thing@1
        with:
          nested:
            toolchain: stable
"""


def test_dtolnay_inputs_are_the_with_toolchain_of_dtolnay_steps_only():
    lines = STEPS.split("\n")
    got = sorted(rv.dtolnay_inputs(lines))
    assert [lines[i].strip() for i in got] == ["toolchain: 1.98.0", "toolchain: stable"]
    assert [i for i in got] == [13, 16]


def test_rewrite_leaves_every_other_toolchain_key_alone():
    new = rv.rewrite(".github/workflows/x.yaml", STEPS, V).split("\n")
    old = STEPS.split("\n")
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert changed == [13, 16]
    assert new[13] == f"          toolchain: {V}" and new[16] == f"          toolchain: {V}"
