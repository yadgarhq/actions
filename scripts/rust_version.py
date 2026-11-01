#!/usr/bin/env python3
"""The Rust version fields of a repository, checked and rewritten. ADR-1120.

ADR-1118 made `versions.yaml` rust-toolchain THE Rust version of the org.
ADR-1120 makes the MSRV that same value: every `rust-toolchain.toml` channel,
every `Cargo.toml` `rust-version` (a `[package]` or `[workspace.package]`
string; `rust-version.workspace = true` inherits it) and every literal
`toolchain:` input in a workflow. No org variable can carry it: cargo and
rustup read these static files directly.

TWO USERS, ONE DEFINITION OF THE FIELDS:

    ci-pr.yaml `rust_parity`   `check`: a field that differs from the version
                               ci-pr runs at is a finding. `--mode warn` prints
                               warnings and exits 0; `--mode fail` exits 1.
                               The check reads the channel and `rust-version`
                               only, never workflow literals.
    rust_propagate.py          `changes`: the files whose fields differ, each
                               rewritten line by line.

THE REWRITE IS TEXTUAL, so comments and layout stay. It replaces the value of
a column-0 `channel = "..."` or `rust-version = "..."` line, or of an indented
`toolchain: stable|X.Y[.Z]` line that is the `with:` input of a
`dtolnay/rust-toolchain` step (`dtolnay_inputs`); an expression, `nightly`,
`beta`, or a `toolchain:` anywhere else is never touched. Then it parses old and new with `tomllib` and refuses unless the only
parsed difference is the field it meant to move: a matching line in some other
table would move something that is not the MSRV.

STANDARD LIBRARY ONLY: `rust_parity` runs on the runner's own `python3` with
no `setup-python` step, so it adds no `python` or `pyyaml` site to
`versions.yaml`.

Commands:

    rust_version.py check --version X.Y.Z --mode warn|fail   in the repository root

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import copy
import os
import re
import subprocess
import sys
import tomllib

VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
TOOLCHAIN_FILE = "rust-toolchain.toml"
WORKFLOW = re.compile(r"\.github/workflows/[^/]+\.ya?ml")
CHANNEL_LINE = re.compile(r'^(channel[ \t]*=[ \t]*")([^"\n]*)("[ \t]*(?:#[^\n]*)?)$', re.M)
MSRV_LINE = re.compile(r'^(rust-version[ \t]*=[ \t]*")([^"\n]*)("[ \t]*(?:#[^\n]*)?)$', re.M)
WORKFLOW_LINE = re.compile(
    r"^([ \t]+toolchain:[ \t]*)(stable|[0-9]{1,4}\.[0-9]{1,4}(?:\.[0-9]{1,4})?)([ \t]*)$", re.M)
USES_DTOLNAY = re.compile(r"(?:- )?uses:[ \t]*dtolnay/rust-toolchain@\S+(?:[ \t]+#.*)?")
MAX_BYTES = 256 * 1024


class Refusal(Exception):
    """A field this cannot read or move safely."""


def kind_of(path):
    """`toolchain`, `cargo`, `workflow`, or None for a path in the repository."""
    if path == TOOLCHAIN_FILE:
        return "toolchain"
    if path == "Cargo.toml" or path.endswith("/Cargo.toml"):
        return "cargo"
    if WORKFLOW.fullmatch(path):
        return "workflow"
    return None


def check_version(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise Refusal(f"the Rust version {version!r} is not X.Y.Z")


def _toml(path, text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise Refusal(f"{path} is not valid TOML: {exc}") from None


def _msrv_slots(data):
    """`(where, value)` for each string `rust-version` cargo reads as the MSRV."""
    out = []
    package = data.get("package")
    if isinstance(package, dict) and isinstance(package.get("rust-version"), str):
        out.append(("package", package["rust-version"]))
    workspace = data.get("workspace")
    wp = workspace.get("package") if isinstance(workspace, dict) else None
    if isinstance(wp, dict) and isinstance(wp.get("rust-version"), str):
        out.append(("workspace.package", wp["rust-version"]))
    return out


def _channel(data):
    table = data.get("toolchain")
    return table.get("channel") if isinstance(table, dict) else None


def findings(files, version):
    """What differs from `version`, one line each. `files` maps path -> text."""
    check_version(version)
    cargo = sorted(p for p in files if kind_of(p) == "cargo")
    if not cargo:
        return []
    out = []
    if TOOLCHAIN_FILE not in files:
        out.append(f"{TOOLCHAIN_FILE} is missing; ADR-1118 asks for one at channel {version}")
    else:
        try:
            channel = _channel(_toml(TOOLCHAIN_FILE, files[TOOLCHAIN_FILE]))
            if channel != version:
                out.append(f"{TOOLCHAIN_FILE} channel is {channel!r}, not {version!r}")
        except Refusal as refusal:
            out.append(str(refusal))
    for path in cargo:
        try:
            slots = _msrv_slots(_toml(path, files[path]))
        except Refusal as refusal:
            out.append(str(refusal))
            continue
        for where, value in slots:
            if value != version:
                out.append(f"{path} [{where}] rust-version is {value!r}, not {version!r}")
    return out


def _expected(kind, data, version):
    want = copy.deepcopy(data)
    if kind == "toolchain":
        if isinstance(want.get("toolchain"), dict) and "channel" in want["toolchain"]:
            want["toolchain"]["channel"] = version
        return want
    if isinstance(want.get("package"), dict) and isinstance(want["package"].get("rust-version"), str):
        want["package"]["rust-version"] = version
    wp = want.get("workspace", {}).get("package") if isinstance(want.get("workspace"), dict) else None
    if isinstance(wp, dict) and isinstance(wp.get("rust-version"), str):
        wp["rust-version"] = version
    return want


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _is_key(line):
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def dtolnay_inputs(lines):
    """Indexes of the `toolchain:` lines that are the `with:` input of a
    `dtolnay/rust-toolchain` step: the line's parent is `with:`, the `with:`
    is a key of a step item, and that item `uses:` dtolnay/rust-toolchain. A
    `toolchain:` in a `run: |` script, under `env:`, nested deeper, or in
    another action's `with:` is not one."""
    out = set()
    for i, line in enumerate(lines):
        if not WORKFLOW_LINE.fullmatch(line):
            continue
        n = _indent(line)
        j = i - 1
        while j >= 0 and not (_is_key(lines[j]) and _indent(lines[j]) < n):
            j -= 1
        if j < 0 or lines[j].strip() != "with:":
            continue
        w = _indent(lines[j])
        k = j - 1
        while k >= 0 and not (_is_key(lines[k]) and _indent(lines[k]) < w):
            k -= 1
        if k < 0 or _indent(lines[k]) != w - 2 or not lines[k].lstrip().startswith("- "):
            continue
        end = k + 1
        while end < len(lines) and not (_is_key(lines[end]) and _indent(lines[end]) <= w - 2):
            end += 1
        keys = [lines[k].strip()] + [lines[x].strip() for x in range(k + 1, end)
                                     if _is_key(lines[x]) and _indent(lines[x]) == w]
        if any(USES_DTOLNAY.fullmatch(key) for key in keys):
            out.add(i)
    return out


def rewrite(path, text, version):
    """`text` with every Rust version field of `path` set to `version`."""
    check_version(version)
    kind = kind_of(path)
    if kind is None:
        raise Refusal(f"{path} carries no Rust version field this rewrites")
    if kind == "workflow":
        lines = text.split("\n")
        for i in dtolnay_inputs(lines):
            lines[i] = WORKFLOW_LINE.sub(lambda m: m.group(1) + version + m.group(3), lines[i])
        return "\n".join(lines)
    line = CHANNEL_LINE if kind == "toolchain" else MSRV_LINE
    new = line.sub(lambda m: m.group(1) + version + m.group(3), text)
    if new == text:
        return text
    if _toml(path, new) != _expected(kind, _toml(path, text), version):
        raise Refusal(f"{path}: a matching line sits outside the table cargo or rustup reads; "
                      "nothing was rewritten")
    return new


def changes(files, version):
    """`{path: new text}` for every file in `files` whose fields differ."""
    out = {}
    for path in sorted(files):
        if kind_of(path) is None:
            continue
        new = rewrite(path, files[path], version)
        if new != files[path]:
            out[path] = new
    return out


def tracked(root="."):
    """The Rust version files `git ls-files` lists, as `{path: text}`."""
    done = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True,
                          check=True)
    out = {}
    for path in done.stdout.decode().split("\0"):
        if not path or kind_of(path) not in ("toolchain", "cargo"):
            continue
        full = os.path.join(root, path)
        if os.path.getsize(full) > MAX_BYTES:
            raise Refusal(f"{path} is over {MAX_BYTES} bytes")
        with open(full, encoding="utf-8") as handle:
            out[path] = handle.read()
    return out


def summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv):
    parser = argparse.ArgumentParser(prog="rust_version.py")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--version", required=True)
    check.add_argument("--mode", required=True, choices=("warn", "fail"))
    args = parser.parse_args(argv)
    try:
        check_version(args.version)
        found = findings(tracked(), args.version)
    except Refusal as refusal:
        print(f"::error::{refusal}")
        return 2
    level = "warning" if args.mode == "warn" else "error"
    for line in found:
        print(f"::{level}::ADR-1120: {line}")
    if found:
        summary(f"## Rust version parity (ADR-1120)\n\nversions.yaml rust-toolchain is "
                f"`{args.version}`. {len(found)} field(s) differ ({args.mode} mode):\n\n"
                + "\n".join(f"- {line}" for line in found))
    else:
        summary(f"## Rust version parity (ADR-1120)\n\nEvery field equals `{args.version}`.")
    return 1 if found and args.mode == "fail" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
