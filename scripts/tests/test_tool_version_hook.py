"""LEDGER 1756 S3, ADR-0983. A system hook refuses a tool whose version is not the pinned one.

`hooks/tool_version.py` runs before gitleaks, hadolint, actionlint and
cargo-deny. It reads `versions.yaml` from the SAME CHECKOUT of
`yadgarhq/actions` it lives in, which under pre-commit is the clone at the
consumer's `rev`, asks the tool for its version, and refuses on any
difference. It refuses when the tool is absent and when the version output is
not in the shape measured for that tool.

EVERY CASE BUILDS THE CLONE LAYOUT. Each test copies the wrapper and
`scripts/tool_versions.py` into a temp tree with its own `versions.yaml`, and
puts fake binaries on a private PATH. The fake outputs are the REAL outputs of
the release artefacts at the pinned versions, captured on 2026-10-21 from the
tarballs whose sha256 `versions.yaml` carries:

    gitleaks version       -> "8.30.1"
    hadolint --version     -> "Haskell Dockerfile Linter 2.14.0"
    actionlint -version    -> "1.7.12" + two lines on how it was built
    cargo deny --version   -> "cargo-deny 0.20.2"

Run: python3 -m pytest scripts/tests/ -q
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "hooks" / "tool_version.py"
READER = REPO / "scripts" / "tool_versions.py"
SHA = "a" * 64

PINNED = {
    "gitleaks": "8.30.1",
    "hadolint": "2.14.0",
    "actionlint": "1.7.12",
    "cargo-deny": "0.20.2",
}

# The real first lines, and the real trailing lines where a tool prints more.
REAL_OUTPUT = {
    "gitleaks": "8.30.1\n",
    "hadolint": "Haskell Dockerfile Linter 2.14.0\n",
    "actionlint": (
        "1.7.12\n"
        "installed by downloading from release page\n"
        "built with go1.26.1 compiler for linux/amd64\n"
    ),
    "cargo-deny": "cargo-deny 0.20.2\n",
}


def versions_yaml(versions):
    lines = ["tools:"]
    for name, version in versions.items():
        lines.append(f'  {name}: {{ version: "{version}", sites: 1, sha256: "{SHA}" }}')
    return "\n".join(lines) + "\n"


@pytest.fixture
def clone(tmp_path):
    """The layout pre-commit gives a hook: the actions checkout, with a bin dir for PATH."""
    root = tmp_path / "actions"
    (root / "hooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(WRAPPER, root / "hooks" / "tool_version.py")
    shutil.copy(READER, root / "scripts" / "tool_versions.py")
    (root / "versions.yaml").write_text(versions_yaml(PINNED))
    (tmp_path / "bin").mkdir()
    # The fakes below need `cat`; nothing else from the host goes on PATH, so a
    # real gitleaks or cargo on this machine cannot answer for a fake.
    os.symlink(shutil.which("cat"), tmp_path / "bin" / "cat")
    return tmp_path


def fake(clone, name, stdout="", exit_code=0, stderr="", record=False):
    """A fake executable on the private PATH.

    With `record`, it writes its argv (one per line) to `<name>.argv` when it is
    called with anything other than the version flag, so a test can see what
    the wrapper exec'd.
    """
    path = clone / "bin" / name
    out = clone / f"{name}.stdout"
    err = clone / f"{name}.stderr"
    out.write_text(stdout)
    err.write_text(stderr)
    body = ["#!/bin/sh"]
    if record:
        body += [
            'case "$*" in *version*) ;; *)',
            f'  for a in "$@"; do printf "%s\\n" "$a"; done > "{clone / (name + ".argv")}"',
            "  exit 0 ;;",
            "esac",
        ]
    body += [f'cat "{out}"', f'cat "{err}" >&2', f"exit {exit_code}"]
    path.write_text("\n".join(body) + "\n")
    path.chmod(0o755)
    return path


def fake_cargo_deny(clone, stdout, record=False):
    """`cargo deny ...` dispatches to a `cargo-deny` binary; fake both halves."""
    fake(clone, "cargo-deny", stdout=stdout, record=record)
    cargo = clone / "bin" / "cargo"
    cargo.write_text(
        "#!/bin/sh\n"
        '[ "$1" = deny ] || { echo "unexpected cargo $1" >&2; exit 2; }\n'
        "shift\n"
        f'exec "{clone / "bin" / "cargo-deny"}" "$@"\n'
    )
    cargo.chmod(0o755)


def run(clone, *argv):
    env = {
        "PATH": str(clone / "bin"),
        "HOME": str(clone),
        "LANG": "C.UTF-8",
    }
    # `python3` resolves through the wrapper's shebang; pass the interpreter
    # explicitly so the test does not depend on what PATH holds.
    return subprocess.run(
        [sys.executable, str(clone / "actions" / "hooks" / "tool_version.py"), *argv],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=clone,
    )


def install_real(clone, tool, stdout=None, record=False):
    output = REAL_OUTPUT[tool] if stdout is None else stdout
    if tool == "cargo-deny":
        fake_cargo_deny(clone, output, record=record)
    else:
        fake(clone, tool, stdout=output, record=record)


# ---------------------------------------------------------------------------
# Accept: the exact pinned version, in the real output shape of each tool.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(PINNED))
def test_the_exact_pinned_version_passes(clone, tool):
    install_real(clone, tool)
    result = run(clone, tool)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("tool", sorted(PINNED))
def test_after_the_check_the_command_runs_with_every_argument(clone, tool):
    """pre-commit appends filenames after the entry; all of it reaches the tool."""
    install_real(clone, tool, record=True)
    if tool == "cargo-deny":
        command = ["cargo", "deny", "--all-features", "check"]
        recorded = clone / "cargo-deny.argv"
        expected = ["--all-features", "check", "Cargo.toml"]
    else:
        command = [tool, "first", "--flag"]
        recorded = clone / f"{tool}.argv"
        expected = ["first", "--flag", "Cargo.toml"]
    result = run(clone, tool, "--", *command, "Cargo.toml")
    assert result.returncode == 0, result.stderr
    assert recorded.read_text().splitlines() == expected


# ---------------------------------------------------------------------------
# Refuse: a different version, and the message names both and the fix.
# ---------------------------------------------------------------------------

WRONG = {
    "gitleaks": "8.29.0\n",
    "hadolint": "Haskell Dockerfile Linter 2.12.0\n",
    "actionlint": "1.7.11\ninstalled by building from source\nbuilt with go1.26.1 compiler for linux/amd64\n",
    "cargo-deny": "cargo-deny 0.18.9\n",
}


@pytest.mark.parametrize("tool", sorted(PINNED))
def test_a_different_version_is_refused_naming_expected_and_found(clone, tool):
    install_real(clone, tool, stdout=WRONG[tool], record=True)
    command = ["cargo", "deny", "check"] if tool == "cargo-deny" else [tool, "x"]
    result = run(clone, tool, "--", *command)
    assert result.returncode == 1
    found = WRONG[tool].splitlines()[0].split()[-1]
    assert PINNED[tool] in result.stderr
    assert found in result.stderr
    assert "versions.yaml" in result.stderr
    assert "install" in result.stderr.lower()
    # Refused BEFORE the tool ran: nothing was exec'd.
    assert not list(clone.glob("*.argv"))


@pytest.mark.parametrize(
    "tool,near_miss",
    [
        ("gitleaks", "8.30.10\n"),
        ("gitleaks", "8.30\n"),
        ("hadolint", "Haskell Dockerfile Linter 2.14.01\n"),
        ("actionlint", "1.7.120\n"),
        ("cargo-deny", "cargo-deny 0.20.2.1\n"),
    ],
)
def test_a_near_miss_is_not_a_match(clone, tool, near_miss):
    """Exact equality, never a prefix or a substring."""
    install_real(clone, tool, stdout=near_miss)
    result = run(clone, tool)
    assert result.returncode == 1
    assert PINNED[tool] in result.stderr


# ---------------------------------------------------------------------------
# Refuse: the version output is not in the measured shape.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,output",
    [
        ("gitleaks", ""),
        ("gitleaks", "v8.30.1\n"),
        ("gitleaks", "gitleaks version 8.30.1\n"),
        ("hadolint", "Haskell Dockerfile Linter 2.14.0-no-git\n"),
        ("hadolint", "2.14.0\n"),
        ("actionlint", "\n1.7.12\n"),
        ("actionlint", "actionlint 1.7.12\n"),
        ("cargo-deny", "cargo-deny\n"),
        ("cargo-deny", "garbage\n"),
    ],
)
def test_unparseable_version_output_is_refused_and_shown(clone, tool, output):
    install_real(clone, tool, stdout=output)
    result = run(clone, tool)
    assert result.returncode == 1
    assert PINNED[tool] in result.stderr
    assert "could not read" in result.stderr
    if output.strip():
        assert output.strip().splitlines()[0] in result.stderr


def test_a_version_command_that_fails_is_refused_with_its_output(clone):
    fake(clone, "gitleaks", stdout="", stderr="segmentation fault", exit_code=139)
    result = run(clone, "gitleaks")
    assert result.returncode == 1
    assert "segmentation fault" in result.stderr


# ---------------------------------------------------------------------------
# Refuse: the tool is absent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["gitleaks", "hadolint", "actionlint"])
def test_a_missing_binary_is_refused_naming_it_and_the_version_to_install(clone, tool):
    result = run(clone, tool, "--", tool)
    assert result.returncode == 1
    # The sentence a bare `language: system` entry printed, kept verbatim.
    assert f"Executable `{tool}` not found" in result.stderr
    assert PINNED[tool] in result.stderr


def test_missing_cargo_is_refused(clone):
    result = run(clone, "cargo-deny")
    assert result.returncode == 1
    assert "Executable `cargo` not found" in result.stderr
    assert PINNED["cargo-deny"] in result.stderr


def test_cargo_without_the_deny_subcommand_is_refused_as_missing_cargo_deny(clone):
    """Measured: cargo prints `error: no such command: `deny`` and exits 101."""
    fake(clone, "cargo", stderr="error: no such command: `deny`\n", exit_code=101)
    result = run(clone, "cargo-deny")
    assert result.returncode == 1
    assert "Executable `cargo-deny` not found" in result.stderr
    assert PINNED["cargo-deny"] in result.stderr


# ---------------------------------------------------------------------------
# The expected version comes from versions.yaml beside the wrapper, and only there.
# ---------------------------------------------------------------------------


def test_the_expected_version_is_read_from_the_checkout_the_wrapper_is_in(clone):
    """A rev that pins another version accepts that version and refuses the old one."""
    (clone / "actions" / "versions.yaml").write_text(versions_yaml({**PINNED, "gitleaks": "8.29.0"}))
    install_real(clone, "gitleaks", stdout="8.29.0\n")
    assert run(clone, "gitleaks").returncode == 0
    install_real(clone, "gitleaks")
    result = run(clone, "gitleaks")
    assert result.returncode == 1
    assert "8.29.0" in result.stderr


def test_a_missing_versions_yaml_is_refused(clone):
    (clone / "actions" / "versions.yaml").unlink()
    install_real(clone, "gitleaks")
    result = run(clone, "gitleaks")
    assert result.returncode == 1
    assert "versions.yaml" in result.stderr


def test_a_tool_absent_from_versions_yaml_is_refused(clone):
    without = {k: v for k, v in PINNED.items() if k != "hadolint"}
    (clone / "actions" / "versions.yaml").write_text(versions_yaml(without))
    install_real(clone, "hadolint")
    result = run(clone, "hadolint")
    assert result.returncode == 1
    assert "hadolint" in result.stderr


@pytest.mark.parametrize("argv", [[], ["prettier"], ["gitleaks", "gitleaks"]])
def test_a_bad_invocation_is_refused(clone, argv):
    result = run(clone, *argv)
    assert result.returncode == 1


def test_the_wrapper_leaves_no_bytecode_in_the_clone(clone):
    install_real(clone, "gitleaks")
    assert run(clone, "gitleaks").returncode == 0
    assert not list((clone / "actions").rglob("__pycache__"))


# ---------------------------------------------------------------------------
# The published hook definitions route through the wrapper.
# ---------------------------------------------------------------------------


def published_hooks():
    hooks = yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text())
    return {hook["id"]: hook for hook in hooks}


EXPECTED_ENTRIES = {
    "gitleaks": "hooks/tool_version.py gitleaks -- gitleaks git --pre-commit --staged --verbose",
    "hadolint": "hooks/tool_version.py hadolint -- hadolint",
    # Ledger 1285. `--all-features` is a TOP-LEVEL flag of cargo-deny 0.20.2:
    # measured, `cargo deny check --all-features` is a usage error.
    "cargo-deny": "hooks/tool_version.py cargo-deny -- cargo deny --all-features check",
    "actionlint": "hooks/actionlint.sh",
}


@pytest.mark.parametrize("hook_id", sorted(EXPECTED_ENTRIES))
def test_each_asserted_hook_is_a_script_through_the_wrapper(hook_id):
    hook = published_hooks()[hook_id]
    assert hook["language"] == "script"
    assert hook["entry"] == EXPECTED_ENTRIES[hook_id]


def test_the_file_selection_of_each_converted_hook_is_unchanged():
    hooks = published_hooks()
    assert hooks["gitleaks"].get("pass_filenames") is False
    assert "files" not in hooks["gitleaks"] and "types" not in hooks["gitleaks"]
    assert hooks["hadolint"]["types"] == ["dockerfile"]
    assert "pass_filenames" not in hooks["hadolint"]
    assert hooks["cargo-deny"]["pass_filenames"] is False
    assert hooks["cargo-deny"]["files"] == r"(Cargo\.(toml|lock)|deny\.toml)$"
    assert hooks["actionlint"]["pass_filenames"] is False
    assert hooks["actionlint"]["files"] == r"^\.github/workflows/.*\.ya?ml$"


def test_actionlint_sh_asserts_the_version_before_its_shellcheck_probe():
    text = (REPO / "hooks" / "actionlint.sh").read_text()
    assert "tool_version.py\" actionlint" in text
    assert text.index("tool_version.py\" actionlint") < text.index("probe=$(")


def test_the_wrapper_and_actionlint_sh_are_executable():
    for path in (WRAPPER, REPO / "hooks" / "actionlint.sh"):
        assert os.access(path, os.X_OK), path
        assert path.read_text().startswith("#!/usr/bin/env ")


# ---------------------------------------------------------------------------
# End to end through actionlint.sh: the version assert fires there too.
# ---------------------------------------------------------------------------


@pytest.fixture
def actionlint_clone(clone):
    shutil.copy(REPO / "hooks" / "actionlint.sh", clone / "actions" / "hooks" / "actionlint.sh")
    (clone / "actions" / "hooks" / "actionlint.sh").chmod(0o755)
    # `python3` on the private PATH is the interpreter running the tests.
    os.symlink(sys.executable, clone / "bin" / "python3")
    return clone


def run_actionlint_sh(clone):
    return subprocess.run(
        [shutil.which("bash"), str(clone / "actions" / "hooks" / "actionlint.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(clone / "bin"), "HOME": str(clone)},
        cwd=clone,
    )


def test_actionlint_sh_refuses_a_wrong_actionlint_version(actionlint_clone):
    fake(actionlint_clone, "actionlint", stdout=WRONG["actionlint"])
    result = run_actionlint_sh(actionlint_clone)
    assert result.returncode == 1
    assert "1.7.12" in result.stderr and "1.7.11" in result.stderr


def test_actionlint_sh_still_prints_the_bare_entry_sentence_when_absent(actionlint_clone):
    result = run_actionlint_sh(actionlint_clone)
    assert result.returncode == 1
    assert "Executable `actionlint` not found" in result.stderr
