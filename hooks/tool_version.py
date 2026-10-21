#!/usr/bin/env python3
"""Refuse a system tool whose version is not the one `versions.yaml` pins, then run it.

LEDGER 1756 S3, ADR-0983. Usage, as a `language: script` hook entry:

    hooks/tool_version.py <tool> [-- <command> <args>...]

`<tool>` is one of gitleaks, hadolint, actionlint, cargo-deny. The wrapper asks
that tool for its version, compares it with `versions.yaml`, and refuses on any
difference. When the versions agree it replaces itself with `<command>` and every
argument after it, so the filenames pre-commit appends reach the tool. With no
`--`, it only checks (`hooks/actionlint.sh` uses it that way).

WHICH `versions.yaml`. The one in the same checkout of `yadgarhq/actions` as this
file. pre-commit runs a `language: script` entry from its clone of this
repository at the consumer's `rev`, so that file is the pin at the consumer's
`rev` -- not `main`, and not the version CI installed. A `language: system`
entry runs from the consumer's tree and cannot find the clone, which is why the
four hooks are scripts.

WHY THIS CAN TURN A CONSUMER RED, AND WHY THAT IS RIGHT. CI installs each tool
from `versions.yaml` at the commit of `yadgarhq/actions` that is running
(ADR-0981); this checks the `rev` the consumer pins. ADR-0983 keeps the two equal:
a hook-asserted version moves on `main` only after every consumer has adopted a
`rev` that accepts it. A refusal here means a developer's binary, or a staged
bump, is out of step, and a green tick would otherwise mean two things.

EXACT EQUALITY, AND ONE MEASURED SHAPE PER TOOL. The version line of each tool,
measured on the release artefacts at the pinned versions:

    gitleaks version       8.30.1
    hadolint --version     Haskell Dockerfile Linter 2.14.0
    actionlint -version    1.7.12            (first of three lines)
    cargo deny --version   cargo-deny 0.20.2

Any other shape is refused with the raw output, rather than guessed at: a
`-no-git` suffix or a `v` prefix is a build this pin did not measure.

`cargo deny`, NOT `cargo-deny`. The hook runs `cargo deny`, and cargo resolves
that subcommand from `$CARGO_HOME/bin` before `$PATH`. Asking `cargo-deny` on
`$PATH` could check a different binary from the one that runs.

STANDARD LIBRARY ONLY, and no bytecode. It reads `versions.yaml` with
`scripts/tool_versions.py`, the reader the workflows use, so the hook and CI
cannot read the file two ways. `sys.dont_write_bytecode` keeps a `__pycache__`
out of pre-commit's clone.

NO VERSION LITERAL HERE. `scripts/versions_parity.py` scans `hooks/`, and a
literal would be a second copy of the pin.

Tests: python3 -m pytest scripts/tests/test_tool_version_hook.py -q
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import tool_versions  # noqa: E402

DOTTED = r"([0-9]+(?:\.[0-9]+)*)"

# tool -> (version command, regex for its FIRST output line, how to install).
TOOLS = {
    "gitleaks": (
        ["gitleaks", "version"],
        re.compile(rf"^{DOTTED}$"),
        "https://github.com/gitleaks/gitleaks/releases/tag/v{version}",
    ),
    "hadolint": (
        ["hadolint", "--version"],
        re.compile(rf"^Haskell Dockerfile Linter {DOTTED}$"),
        "https://github.com/hadolint/hadolint/releases/tag/v{version}",
    ),
    "actionlint": (
        ["actionlint", "-version"],
        re.compile(rf"^{DOTTED}$"),
        "https://github.com/rhysd/actionlint/releases/tag/v{version}",
    ),
    "cargo-deny": (
        ["cargo", "deny", "--version"],
        re.compile(rf"^cargo-deny {DOTTED}$"),
        "cargo install --locked cargo-deny --version {version}  "
        "(or https://github.com/EmbarkStudios/cargo-deny/releases/tag/{version})",
    ),
}


class Refused(Exception):
    pass


def where_the_pin_comes_from(tool, expected):
    return (
        f"{tool} {expected} is the version in versions.yaml of yadgarhq/actions at the "
        f"`rev` this repository pins in .pre-commit-config.yaml ({ROOT / 'versions.yaml'})."
    )


def how_to_install(tool, expected):
    return "Install it: " + TOOLS[tool][2].format(version=expected)


def expected_version(tool):
    try:
        outputs = tool_versions.read(ROOT / "versions.yaml")
    except tool_versions.ReadError as error:
        raise Refused(f"cannot read the pinned {tool} version: {error}") from error
    key = tool.replace("-", "_")
    if key not in outputs:
        raise Refused(f"{ROOT / 'versions.yaml'} pins no version for {tool}")
    return outputs[key]


def missing(binary, tool, expected):
    # The first line is the sentence a bare `language: system` entry printed.
    return Refused(
        f"Executable `{binary}` not found\n"
        f"{where_the_pin_comes_from(tool, expected)}\n"
        f"{how_to_install(tool, expected)}"
    )


def installed_version(tool, expected):
    command, shape, _ = TOOLS[tool]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    except FileNotFoundError:
        raise missing(command[0], tool, expected) from None
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused(f"`{' '.join(command)}` did not run: {error}") from error
    output = (proc.stdout or "") + (proc.stderr or "")
    if tool == "cargo-deny" and "no such command" in proc.stderr and "deny" in proc.stderr:
        raise missing("cargo-deny", tool, expected)
    if proc.returncode != 0:
        raise Refused(
            f"`{' '.join(command)}` exited {proc.returncode}, so the {tool} version "
            f"could not be checked against {expected}. Its output:\n{output.rstrip()}"
        )
    lines = proc.stdout.splitlines()
    match = shape.match(lines[0]) if lines else None
    if not match:
        raise Refused(
            f"could not read a {tool} version from `{' '.join(command)}`; expected "
            f"{tool} {expected}. Its output:\n{output.rstrip() or '(nothing)'}\n"
            f"{how_to_install(tool, expected)}"
        )
    return match.group(1)


def check(tool):
    expected = expected_version(tool)
    found = installed_version(tool, expected)
    if found != expected:
        raise Refused(
            f"{tool} is {found}, expected {expected}.\n"
            f"{where_the_pin_comes_from(tool, expected)}\n"
            f"{how_to_install(tool, expected)}\n"
            "CI installs the same version, from versions.yaml (ADR-0981, ADR-0983)."
        )


def main(argv):
    args = argv[1:]
    if not args or args[0] not in TOOLS:
        print(
            f"usage: tool_version.py <{'|'.join(sorted(TOOLS))}> [-- <command> <args>...]",
            file=sys.stderr,
        )
        return 1
    tool, rest = args[0], args[1:]
    if rest and rest[0] != "--":
        print(f"tool_version.py: expected `--` after {tool}, got {rest[0]!r}", file=sys.stderr)
        return 1
    command = rest[1:]
    try:
        check(tool)
    except Refused as error:
        print(f"{tool} hook refused: {error}", file=sys.stderr)
        return 1
    if not command:
        return 0
    try:
        os.execvp(command[0], command)
    except OSError as error:
        print(f"tool_version.py: cannot run {command[0]}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
