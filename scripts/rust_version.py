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

WHICH `Cargo.toml` (yaadgaar 527 step 6). `workspace` keeps the root manifest
and, when it declares `[workspace]`, the manifests of its declared `members`
(cargo's globs, less `exclude`) and its IMPLICIT members: a path dependency
(`dependencies`, `dev-dependencies`, `build-dependencies`, also under `target`)
of the root or of a member (`x.workspace = true` resolves through the root's
`[workspace.dependencies]`), whose directory lies under the root and outside
`exclude`, followed transitively as cargo does. A test fixture or a vendored
crate that nothing depends on is not a package cargo builds here, so its
`rust-version` is not the repository's MSRV. A path dependency named only in
`[workspace.dependencies]`, under the root, not a member and not excluded, is
REFUSED: cargo may build it, and reading it silently or not at all would hide
a drifted field (a pull request that sets `members = []` is the case). A root
cargo cannot parse is kept, so it stays a finding, and lets no nested manifest in.

THE GRACE WINDOW (yaadgaar 527 step 6, ADR-1120). Between the actions pull
request that moves `versions.yaml` rust-toolchain and each consumer's
rust-bump merge, every consumer's fields sit at the old version. While
`versions.yaml` carries `previous` (the old version, which only the detector's
pull request writes), `check --previous P --base REV` passes a field that is
EXACTLY `P` when the same field (the same file, the same table) is `P` at
`REV`, the pull request's base: a field the pull request did not move. A field
the pull request moved back to `P`, or added at `P`, is a finding as before.
No `previous`, or no base, is the check as before.

STANDARD LIBRARY ONLY: `rust_parity` runs on the runner's own `python3` with
no `setup-python` step, so it adds no `python` or `pyyaml` site to
`versions.yaml`.

Commands:

    rust_version.py check --version X.Y.Z --mode warn|fail [--previous P --base REV]
                                                             in the repository root

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


def key(version):
    """`X.Y.Z` as a tuple of ints, for ordering."""
    check_version(version)
    return tuple(int(part) for part in version.split("."))


def check_previous(previous, version):
    """The grace version: X.Y.Z and strictly older than `version`."""
    check_version(previous)
    if key(previous) >= key(version):
        raise Refusal(f"the grace version {previous!r} is not older than {version!r}")


def _glob(pattern):
    """A cargo `members` glob as a regex over a directory path: `*` and `?`
    stay inside one segment, `**` crosses segments."""
    if (not isinstance(pattern, str) or not pattern or pattern.startswith("/")
            or ".." in pattern.split("/") or "\\" in pattern):
        raise Refusal(f"Cargo.toml: workspace member {pattern!r} is not a relative path glob")
    pattern = pattern.removeprefix("./").rstrip("/")
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end < 0:
                raise Refusal(f"Cargo.toml: workspace member {pattern!r} has an open `[`")
            body = pattern[i + 1:end]
            out += "[" + ("^" + body[1:] if body.startswith("!") else body).replace("/", "") + "]"
            i = end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def _strings(value, what):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Refusal(f"Cargo.toml: `workspace.{what}` is not a list of strings")
    return value


DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _path_dirs(tables, base, inherited=None):
    """Directories (normalised, relative to the root) of the `path` dependencies
    in `tables`, a list of dependency tables whose manifest sits in `base`. A
    dependency `x.workspace = true` takes the `path` of `x` in `inherited`, the
    root's `[workspace.dependencies]`, which is relative to the root. A path
    that leaves the root, or is absolute, is dropped."""
    out = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        for name, dep in table.items():
            if not isinstance(dep, dict):
                continue
            path, where = dep.get("path"), base
            if dep.get("workspace") is True and isinstance(inherited, dict):
                shared = inherited.get(name)
                path = shared.get("path") if isinstance(shared, dict) else None
                where = "."
            if not isinstance(path, str) or os.path.isabs(path):
                continue
            norm = os.path.normpath(os.path.join(where, path))
            if norm != ".." and not norm.startswith("../"):
                out.append(norm)
    return out


def _manifest_deps(data):
    tables = [data.get(name) for name in DEP_TABLES]
    target = data.get("target")
    if isinstance(target, dict):
        for sub in target.values():
            if isinstance(sub, dict):
                tables += [sub.get(name) for name in DEP_TABLES]
    return tables


def workspace(files):
    """`files` less every `Cargo.toml` that is neither the root manifest, a
    declared workspace member nor an implicit one. Other kinds pass through."""
    out = {p: t for p, t in files.items() if kind_of(p) not in (None, "cargo")}
    root = files.get("Cargo.toml")
    if root is None:
        return out
    out["Cargo.toml"] = root
    try:
        data = tomllib.loads(root)
    except tomllib.TOMLDecodeError:
        return out  # kept, so `findings` reports it; nothing nested let in
    table = data.get("workspace")
    if table is None:
        return out
    if not isinstance(table, dict):
        raise Refusal("Cargo.toml: `workspace` is not a table")
    members = [_glob(m) for m in _strings(table.get("members", []), "members")]
    exclude = [e.removeprefix("./").rstrip("/") for e in _strings(table.get("exclude", []), "exclude")]
    def excluded(where):
        return any(where == e or where.startswith(e + "/") for e in exclude)

    for path, text in files.items():
        if kind_of(path) != "cargo" or path == "Cargo.toml":
            continue
        where = path[:-len("/Cargo.toml")]
        if any(m.fullmatch(where) for m in members) and not excluded(where):
            out[path] = text
    queue = list(out)
    while queue:  # implicit members: path dependencies of the root and of members
        path = queue.pop()
        try:
            parsed = tomllib.loads(out[path])
        except tomllib.TOMLDecodeError:
            continue  # a finding of its own; its dependencies are not followed
        base = os.path.dirname(path) or "."
        for where in _path_dirs(_manifest_deps(parsed), base, table.get("dependencies")):
            manifest = "Cargo.toml" if where == "." else f"{where}/Cargo.toml"
            if manifest in files and manifest not in out and not excluded(where):
                out[manifest] = files[manifest]
                queue.append(manifest)
    deps = table.get("dependencies")
    for where in _path_dirs([deps], "."):
        manifest = "Cargo.toml" if where == "." else f"{where}/Cargo.toml"
        if manifest in files and manifest not in out and not excluded(where):
            raise Refusal(f"Cargo.toml: `workspace.dependencies` names the path {where!r}, which "
                          "holds a Cargo.toml that is neither a declared nor an implicit workspace "
                          "member; cargo may build it, so its rust-version cannot go unread. "
                          "Declare it in `members` or `exclude` it")
    return out


def _base_channel(base):
    try:
        return _channel(_toml(TOOLCHAIN_FILE, base[TOOLCHAIN_FILE]))
    except (KeyError, TypeError, Refusal):
        return None


def _base_slots(base, path):
    try:
        return dict(_msrv_slots(_toml(path, base[path])))
    except (KeyError, TypeError, Refusal):
        return {}


def findings(files, version, previous=None, base=None):
    """What differs from `version`, one line each. `files` maps path -> text.

    EXACT STRING EQUALITY (ADR-1118: one X.Y.Z). `1.99` is not `1.99.1`, though
    cargo reads it as a lower bound: the field must be the whole version.

    With `previous` AND `base` (path -> text at the pull request's base), a
    field that is exactly `previous` here and in the same place at the base is
    no finding: the grace window, and only for a field left untouched."""
    check_version(version)
    if previous is not None:
        check_previous(previous, version)
    grace = previous is not None and base is not None
    cargo = sorted(p for p in files if kind_of(p) == "cargo")
    if not cargo:
        return []
    out = []
    if TOOLCHAIN_FILE not in files:
        out.append(f"{TOOLCHAIN_FILE} is missing; ADR-1118 asks for one at channel {version}")
    else:
        try:
            channel = _channel(_toml(TOOLCHAIN_FILE, files[TOOLCHAIN_FILE]))
            if channel != version and not (grace and channel == previous
                                           and _base_channel(base) == previous):
                out.append(f"{TOOLCHAIN_FILE} channel is {channel!r}, expected exactly {version!r}")
        except Refusal as refusal:
            out.append(str(refusal))
    for path in cargo:
        try:
            slots = _msrv_slots(_toml(path, files[path]))
        except Refusal as refusal:
            out.append(str(refusal))
            continue
        for where, value in slots:
            if value != version and not (grace and value == previous
                                         and _base_slots(base, path).get(where) == previous):
                out.append(f"{path} [{where}] rust-version is {value!r}, expected exactly {version!r}")
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
    """The Rust version files `git ls-files` lists, as `{path: text}`: the
    toolchain file, the root manifest and its declared members (`workspace`)."""
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
    return workspace(out)


def at(rev, paths, root="."):
    """`{path: text}` for each of `paths` present at commit `rev`. A `rev`
    that is not a commit here is a refusal, never an empty base."""
    if not isinstance(rev, str) or not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise Refusal(f"the base {rev!r} is not a 40-hex commit")
    ok = subprocess.run(["git", "-C", root, "cat-file", "-e", f"{rev}^{{commit}}"],
                        capture_output=True, check=False)
    if ok.returncode != 0:
        raise Refusal(f"the base {rev} is not a commit in this checkout")
    out = {}
    for path in paths:
        got = subprocess.run(["git", "-C", root, "cat-file", "blob", f"{rev}:{path}"],
                             capture_output=True, check=False)
        if got.returncode == 0 and len(got.stdout) <= MAX_BYTES:
            out[path] = got.stdout.decode("utf-8", errors="replace")
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
    check.add_argument("--previous", default="")
    check.add_argument("--base", default="")
    args = parser.parse_args(argv)
    try:
        check_version(args.version)
        files = tracked()
        previous = args.previous or None
        base = at(args.base, sorted(files)) if previous and args.base else None
        if previous and base is None:
            print(f"::notice::versions.yaml carries previous {previous}, but no base commit was "
                  "given; no grace (ADR-1120)")
        found = findings(files, args.version, previous=previous if base is not None else None,
                         base=base)
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
