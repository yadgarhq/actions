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


# ---------------------------------------------------------------------------
# yaadgaar 527 step 6 (4): the workspace, not every git-listed Cargo.toml.
# A test fixture or a vendored crate is not a package cargo builds here.
# ---------------------------------------------------------------------------

AT = WORKSPACE.replace("1.90", V)
FIXTURE = '[package]\nname = "fixture"\nrust-version = "1.60"\n'


def test_the_workspace_keeps_the_root_and_its_declared_members():
    files = {"Cargo.toml": AT, "crates/front/Cargo.toml": MEMBER,
             "tests/fixtures/old/Cargo.toml": FIXTURE, "vendor/x/Cargo.toml": FIXTURE,
             "rust-toolchain.toml": TOOLCHAIN, ".github/workflows/smoke.yaml": SMOKE}
    assert sorted(rv.workspace(files)) == [".github/workflows/smoke.yaml", "Cargo.toml",
                                           "crates/front/Cargo.toml", "rust-toolchain.toml"]


def test_a_fixture_crate_outside_the_workspace_is_no_finding():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": AT,
             "crates/front/Cargo.toml": MEMBER, "tests/fixtures/old/Cargo.toml": FIXTURE}
    assert rv.findings(rv.workspace(files), V) == []


def test_a_root_crate_with_no_workspace_keeps_only_itself():
    files = {"Cargo.toml": CARGO, "examples/x/Cargo.toml": FIXTURE}
    assert sorted(rv.workspace(files)) == ["Cargo.toml"]


def test_member_globs_and_exclude_follow_cargo():
    root = ('[workspace]\nmembers = ["crates/*", "tools/gen"]\nexclude = ["crates/old"]\n'
            '[workspace.package]\nrust-version = "1.90"\n')
    files = {"Cargo.toml": root, "crates/a/Cargo.toml": MEMBER, "crates/old/Cargo.toml": FIXTURE,
             "crates/a/deep/Cargo.toml": FIXTURE, "tools/gen/Cargo.toml": MEMBER,
             "tools/genx/Cargo.toml": FIXTURE}
    assert sorted(rv.workspace(files)) == ["Cargo.toml", "crates/a/Cargo.toml", "tools/gen/Cargo.toml"]


def test_a_member_that_drifts_is_still_a_finding():
    """The filter narrows WHICH files; a declared member is checked as before."""
    member = '[package]\nname = "front"\nrust-version = "1.90"\n'
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": AT,
             "crates/front/Cargo.toml": member}
    got = rv.findings(rv.workspace(files), V)
    assert len(got) == 1 and "crates/front/Cargo.toml" in got[0]


def test_an_unparseable_root_is_kept_and_stays_a_finding():
    """Fail closed: a root cargo cannot read is never dropped, and nested
    manifests are not let in by it."""
    files = {"Cargo.toml": "[package\n", "crates/front/Cargo.toml": FIXTURE}
    kept = rv.workspace(files)
    assert sorted(kept) == ["Cargo.toml"]
    assert any("not valid TOML" in f for f in rv.findings(kept, V))


def test_no_root_manifest_keeps_no_manifest():
    files = {"crates/front/Cargo.toml": FIXTURE, "rust-toolchain.toml": TOOLCHAIN}
    assert sorted(rv.workspace(files)) == ["rust-toolchain.toml"]


@pytest.mark.parametrize("members", ['"crates/front"', '[1]', '["../x"]', '["/abs"]'])
def test_a_members_list_cargo_would_not_read_is_refused(members):
    files = {"Cargo.toml": f"[workspace]\nmembers = {members}\n"}
    with pytest.raises(rv.Refusal):
        rv.workspace(files)


def test_cli_ignores_a_fixture_crate(tmp_path):
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
                     "Cargo.toml": CARGO.replace("1.90", V),
                     "tests/fixtures/old/Cargo.toml": FIXTURE})
    done = _check(tmp_path, "--version", V, "--mode", "fail")
    assert done.returncode == 0, done.stdout


# ---------------------------------------------------------------------------
# yaadgaar 527 step 6 (3): the grace window. While versions.yaml carries
# `previous`, a field still at EXACTLY that version passes, and only when the
# pull request did not put it there (the same field at the base is `previous`
# too). Without `previous`, or without a base, nothing changes.
# ---------------------------------------------------------------------------

NEW = "1.100.0"
OLD_TREE = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": CARGO.replace("1.90", V)}


def test_grace_admits_an_untouched_field_at_the_previous_version():
    assert rv.findings(OLD_TREE, NEW, previous=V, base=OLD_TREE) == []


def test_without_grace_the_previous_version_is_a_finding():
    assert len(rv.findings(OLD_TREE, NEW)) == 2
    assert len(rv.findings(OLD_TREE, NEW, previous=None, base=OLD_TREE)) == 2


def test_grace_needs_a_base():
    assert len(rv.findings(OLD_TREE, NEW, previous=V, base=None)) == 2


@pytest.mark.parametrize("other", ["1.99.0", "1.98.0", "1.90", "1.99", "1.99.1 "])
def test_grace_admits_exactly_the_previous_version(other):
    head = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
            "Cargo.toml": CARGO.replace("1.90", other)}
    base = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
            "Cargo.toml": CARGO.replace("1.90", other)}
    got = rv.findings(head, NEW, previous=V, base=base)
    assert len(got) == 1 and "Cargo.toml" in got[0]


def test_grace_refuses_a_field_the_pull_request_moved_back():
    """The base is at the new version; the pull request moves it to previous."""
    base = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", NEW), "Cargo.toml": CARGO.replace("1.90", NEW)}
    got = rv.findings(OLD_TREE, NEW, previous=V, base=base)
    assert len(got) == 2


def test_grace_refuses_a_field_the_pull_request_added_at_previous():
    """A new member crate (absent at the base) at the previous version."""
    head = dict(OLD_TREE, **{"Cargo.toml": WORKSPACE.replace("1.90", NEW),
                             "crates/front/Cargo.toml": CARGO.replace("1.90", V)})
    base = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", NEW),
            "Cargo.toml": WORKSPACE.replace("1.90", NEW)}
    got = rv.findings(head, NEW, previous=V, base=base)
    assert any("crates/front/Cargo.toml" in f for f in got)


def test_grace_matches_the_same_slot_not_any_slot():
    """A base `[workspace.package]` at previous does not cover a `[package]` slot."""
    both = '[package]\nname = "x"\nrust-version = "{}"\n[workspace]\n[workspace.package]\nrust-version = "{}"\n'
    head = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", NEW), "Cargo.toml": both.format(V, NEW)}
    base = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", NEW), "Cargo.toml": both.format(NEW, V)}
    got = rv.findings(head, NEW, previous=V, base=base)
    assert len(got) == 1 and "[package]" in got[0]


@pytest.mark.parametrize("previous", [NEW, "1.101.0", "1.99", "", "x"])
def test_a_previous_that_is_not_an_older_x_y_z_is_refused(previous):
    with pytest.raises(rv.Refusal):
        rv.findings(OLD_TREE, NEW, previous=previous, base=OLD_TREE)


def _commit(tmp_path, message):
    """A fixture commit in a throwaway repository: no user or system config
    (as test_fan_out does), so the runner needs no identity of its own."""
    env = dict(_git_env(), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", message], check=True, env=env)
    return subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=_git_env()).stdout.strip()


def test_cli_grace_passes_an_untouched_tree(tmp_path):
    _repo(tmp_path, OLD_TREE)
    base = _commit(tmp_path, "base")
    done = _check(tmp_path, "--version", NEW, "--mode", "fail", "--previous", V, "--base", base)
    assert done.returncode == 0, done.stdout


def test_cli_grace_refuses_a_move_back_to_previous(tmp_path):
    _repo(tmp_path, {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", NEW),
                     "Cargo.toml": CARGO.replace("1.90", NEW)})
    base = _commit(tmp_path, "base")
    _repo(tmp_path, OLD_TREE)
    done = _check(tmp_path, "--version", NEW, "--mode", "fail", "--previous", V, "--base", base)
    assert done.returncode == 1 and done.stdout.count("::error::") == 2


def test_cli_empty_previous_and_base_mean_no_grace(tmp_path):
    _repo(tmp_path, OLD_TREE)
    _commit(tmp_path, "base")
    done = _check(tmp_path, "--version", NEW, "--mode", "fail", "--previous", "", "--base", "")
    assert done.returncode == 1


def test_cli_an_unreadable_base_is_an_error(tmp_path):
    _repo(tmp_path, OLD_TREE)
    _commit(tmp_path, "base")
    done = _check(tmp_path, "--version", NEW, "--mode", "fail", "--previous", V, "--base", "f" * 40)
    assert done.returncode == 2


@pytest.mark.parametrize("other", ["1.99.0", "1.98.0", NEW + ".1"])
def test_grace_admits_exactly_the_previous_channel(other):
    """The toolchain file too: a channel at another version is a finding even
    when the base carries the same channel."""
    head = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", other), "Cargo.toml": CARGO.replace("1.90", NEW)}
    got = rv.findings(head, NEW, previous=V, base=dict(head))
    assert len(got) == 1 and "rust-toolchain.toml" in got[0]


def test_grace_admits_the_previous_channel_left_untouched():
    head = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": CARGO.replace("1.90", NEW)}
    assert rv.findings(head, NEW, previous=V, base=dict(head)) == []


def test_grace_refuses_a_channel_moved_off_previous():
    """The base is at previous; the pull request moves the channel elsewhere."""
    base = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": CARGO.replace("1.90", NEW)}
    head = dict(base, **{"rust-toolchain.toml": TOOLCHAIN})
    got = rv.findings(head, NEW, previous=V, base=base)
    assert len(got) == 1 and "rust-toolchain.toml" in got[0]


def test_exclude_covers_the_directory_and_below_but_not_a_sibling_prefix():
    root = '[workspace]\nmembers = ["crates/**"]\nexclude = ["crates/old"]\n'
    files = {"Cargo.toml": root, "crates/old/Cargo.toml": FIXTURE, "crates/old/sub/Cargo.toml": FIXTURE,
             "crates/older/Cargo.toml": MEMBER, "./crates/x": "", "crates/a/Cargo.toml": MEMBER}
    assert sorted(p for p in rv.workspace(files) if p.endswith("Cargo.toml")) == [
        "Cargo.toml", "crates/a/Cargo.toml", "crates/older/Cargo.toml"]


# ---------------------------------------------------------------------------
# yaadgaar 529: cargo implicit members, and exact equality.
# ---------------------------------------------------------------------------

ROOT_PKG = ('[package]\nname = "app"\nrust-version = "1.99.1"\n\n[workspace]\nmembers = []\n\n'
            '[dependencies]\ncore = { path = "crates/core" }\n')
CORE = '[package]\nname = "core"\nrust-version = "1.90"\n\n[dependencies]\nleaf = { path = "../leaf" }\n'
LEAF = '[package]\nname = "leaf"\nrust-version = "1.91"\n'


def test_a_path_dependency_of_the_root_is_an_implicit_member_even_with_empty_members():
    files = {"Cargo.toml": ROOT_PKG, "crates/core/Cargo.toml": CORE,
             "crates/leaf/Cargo.toml": LEAF, "tests/fixtures/old/Cargo.toml": FIXTURE}
    assert sorted(rv.workspace(files)) == ["Cargo.toml", "crates/core/Cargo.toml",
                                           "crates/leaf/Cargo.toml"]


def test_an_implicit_member_that_drifts_is_a_finding():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V), "Cargo.toml": ROOT_PKG,
             "crates/core/Cargo.toml": CORE, "crates/leaf/Cargo.toml": LEAF}
    found = rv.findings(rv.workspace(files), V)
    assert [f.split(" ")[0] for f in found] == ["crates/core/Cargo.toml", "crates/leaf/Cargo.toml"]


def test_an_excluded_or_outside_path_dependency_is_not_a_member():
    root = ROOT_PKG.replace("members = []", 'members = []\nexclude = ["crates/core"]')
    files = {"Cargo.toml": root, "crates/core/Cargo.toml": CORE, "crates/leaf/Cargo.toml": LEAF}
    assert sorted(rv.workspace(files)) == ["Cargo.toml"]
    up = ROOT_PKG.replace("crates/core", "../elsewhere")
    assert sorted(rv.workspace({"Cargo.toml": up, "crates/core/Cargo.toml": CORE})) == ["Cargo.toml"]


def test_a_target_specific_path_dependency_is_an_implicit_member():
    root = ('[package]\nname = "app"\n[workspace]\n'
            '[target.\'cfg(unix)\'.dependencies]\ncore = { path = "crates/core" }\n')
    files = {"Cargo.toml": root, "crates/core/Cargo.toml": LEAF}
    assert "crates/core/Cargo.toml" in rv.workspace(files)


def test_a_virtual_workspace_with_empty_members_and_a_workspace_path_dep_is_refused():
    root = ('[workspace]\nmembers = []\n[workspace.dependencies]\n'
            'core = { path = "crates/core" }\n')
    with pytest.raises(rv.Refusal, match="workspace.dependencies"):
        rv.workspace({"Cargo.toml": root, "crates/core/Cargo.toml": LEAF})


def test_a_workspace_path_dep_that_members_name_or_exclude_lists_is_not_refused():
    base = '[workspace]\nmembers = {m}\nexclude = {e}\n[workspace.dependencies]\ncore = {{ path = "crates/core" }}\n'
    files = {"crates/core/Cargo.toml": LEAF}
    named = base.format(m='["crates/core"]', e="[]")
    assert "crates/core/Cargo.toml" in rv.workspace({"Cargo.toml": named, **files})
    excluded = base.format(m="[]", e='["crates/core"]')
    assert sorted(rv.workspace({"Cargo.toml": excluded, **files})) == ["Cargo.toml"]


ESTATE_ROOT = (
    '[workspace]\nresolver = "2"\nmembers = ["crates/harness", "crates/front"]\n\n'
    '[workspace.package]\nversion = "0.1.0"\nrust-version = "1.99.1"\n\n'
    '[workspace.dependencies]\nanyhow = "1"\nestate-harness = { path = "crates/harness" }\n')
ESTATE_HARNESS = '[package]\nname = "estate-harness"\nrust-version.workspace = true\n'
ESTATE_FRONT = ('[package]\nname = "estate-front"\nrust-version.workspace = true\n\n'
                '[dependencies]\nanyhow.workspace = true\nestate-harness.workspace = true\n')


def test_the_estate_shape_is_unchanged():
    """The real estate main: both crates are declared members, and front reaches
    harness through a `[workspace.dependencies]` path. Nothing is refused."""
    files = {"Cargo.toml": ESTATE_ROOT, "crates/harness/Cargo.toml": ESTATE_HARNESS,
             "crates/front/Cargo.toml": ESTATE_FRONT}
    assert sorted(rv.workspace(files)) == sorted(files)


def test_a_workspace_inherited_path_dependency_is_an_implicit_member_not_a_refusal():
    root = ('[workspace]\nmembers = ["crates/a"]\n[workspace.dependencies]\n'
            'b = { path = "crates/b" }\n')
    a = '[package]\nname = "a"\n[dependencies]\nb.workspace = true\n'
    files = {"Cargo.toml": root, "crates/a/Cargo.toml": a, "crates/b/Cargo.toml": LEAF,
             "tests/fixtures/old/Cargo.toml": FIXTURE}
    assert sorted(rv.workspace(files)) == ["Cargo.toml", "crates/a/Cargo.toml", "crates/b/Cargo.toml"]


def test_a_workspace_dependency_that_is_not_inherited_by_name_is_still_refused():
    root = ('[workspace]\nmembers = ["crates/a"]\n[workspace.dependencies]\n'
            'b = { path = "crates/b" }\n')
    a = '[package]\nname = "a"\n[dependencies]\nc.workspace = true\n'
    with pytest.raises(rv.Refusal, match="workspace.dependencies"):
        rv.workspace({"Cargo.toml": root, "crates/a/Cargo.toml": a, "crates/b/Cargo.toml": LEAF})


@pytest.mark.parametrize("short", ["1.99", "1.99.0", "1.99.10", "^1.99.1", ">=1.99.1", "1.99.1-beta"])
def test_parity_is_exact_string_equality_and_says_what_it_expected(short):
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", V),
             "Cargo.toml": CARGO.replace('"1.90"', f'"{short}"')}
    assert rv.findings(files, V) == [
        f"Cargo.toml [package] rust-version is {short!r}, expected exactly {V!r}"]


def test_a_channel_off_by_a_patch_says_what_it_expected():
    files = {"rust-toolchain.toml": TOOLCHAIN.replace("1.98.0", "1.99"),
             "Cargo.toml": CARGO.replace('"1.90"', f'"{V}"')}
    assert rv.findings(files, V) == ["rust-toolchain.toml channel is '1.99', expected exactly '1.99.1'"]
