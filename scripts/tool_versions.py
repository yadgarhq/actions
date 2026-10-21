#!/usr/bin/env python3
"""Print `versions.yaml` as `key=value` step outputs, for `>> "$GITHUB_OUTPUT"`.

LEDGER 1756 S2b, ADR-0981. Every workflow reads its tool versions from
`versions.yaml` at the commit of `yadgarhq/actions` that is running. This is
the reader. Output, one line per value, nothing else on stdout:

    <tool>=<version>            (a `-` in the tool name becomes `_`)
    <tool>_sha256=<64 hex>      (only for a tool that carries `sha256`)

STANDARD LIBRARY ONLY, ON PURPOSE. It runs on the runner's system `python3`,
before `actions/setup-python` (whose own `python-version` comes from this
file), so PyYAML is not available. It therefore reads ONE shape and refuses
every other: under `tools:`, one tool per line as a single-line flow mapping,

    tools:
      helm: { version: "3.18.4", sites: 6 }
      gitleaks: { version: "8.30.1", sites: 2, sha256: "<64 hex>" }

`scripts/tests/test_tool_versions.py` holds its output equal to
`yaml.safe_load` of the real file, so the two readings cannot drift apart.

`accept` (LEDGER 1756 S3, ADR-0983): an optional list of further versions a
hook at this commit also accepts, `accept: ["8.31.0"]`. It is NEVER a step
output, because CI installs `version` only. `accepted()` returns it for
`hooks/tool_version.py`. Each entry is a dotted number of ASCII digits; a
duplicate, an empty list, or the tool's own `version` in its list is refused.

FAIL CLOSED. A missing file, a missing or malformed version, a malformed
sha256, a duplicate key, or a line in a shape it does not know: it prints
`::error::` on stderr, prints NOTHING on stdout, and exits 1. A partial set of
outputs would let a later step run with an empty version, and an empty
`version:` input makes several actions pick their own default.

Run: python3 scripts/tool_versions.py versions.yaml >> "$GITHUB_OUTPUT"
Tests: python3 -m pytest scripts/tests/ -q
"""

import re
import sys

ENTRY = re.compile(r"^  ([a-z0-9][a-z0-9-]*):\s*\{(.*)\}\s*(?:#.*)?$")
# One `key: value` of a flow mapping, then a comma or the end. A value is a
# quoted string, a bare integer, or a list of quoted strings (`accept` only).
PAIR = re.compile(r'\s*([a-z0-9_]+):\s*(?:"([^"]*)"|([0-9]+)|\[([^\]]*)\])\s*(?:,|$)')
ITEM = re.compile(r'^\s*"([^"]*)"\s*$')
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")  # ASCII digits only, never `\d`
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReadError(Exception):
    pass


def _tool_lines(text):
    """(line number, line) for every line inside the top-level `tools:` block."""
    lines = text.splitlines()
    inside = False
    found_block = False
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "tools:"
            found_block = found_block or inside
            continue
        if inside:
            yield number, line
    if not found_block:
        raise ReadError("versions.yaml has no `tools:` mapping")


def _fields(path, number, name, body):
    """The `key: value` pairs of one flow mapping, `accept` as a tuple."""
    fields = {}
    position = 0
    body = body.strip()
    while position < len(body):
        pair = PAIR.match(body, position)
        if not pair or pair.end() == position:
            raise ReadError(f"{path}:{number}: {name}: cannot read {body[position:].strip()!r}")
        key, text, integer, items = pair.groups()
        if key in fields:
            raise ReadError(f"{path}:{number}: {name}: `{key}` given twice")
        if items is not None:
            if key != "accept":
                raise ReadError(f"{path}:{number}: {name}: `{key}` cannot be a list")
            fields[key] = _accept(path, number, name, items)
        elif key == "accept":
            raise ReadError(f"{path}:{number}: {name}: cannot read `accept`; it is a list, [\"x.y.z\"]")
        else:
            fields[key] = text if text is not None else integer
        position = pair.end()
        if body[position - 1] == "," and position >= len(body.rstrip()):
            raise ReadError(f"{path}:{number}: {name}: cannot read a trailing comma")
    return fields


def _accept(path, number, name, items):
    if not items.strip():
        raise ReadError(f"{path}:{number}: {name}: `accept` is empty; delete it instead")
    versions = []
    for part in items.split(","):
        item = ITEM.match(part)
        if not item:
            raise ReadError(f"{path}:{number}: {name}: cannot read accept entry {part.strip()!r}")
        version = item.group(1)
        if not VERSION.match(version):
            raise ReadError(f"{path}:{number}: {name} accept {version!r} is not a dotted number")
        if version in versions:
            raise ReadError(f"{path}:{number}: {name} accept lists {version} twice")
        versions.append(version)
    return tuple(versions)


def _entries(path):
    """(line number, tool name, fields) for every tool, or raise ReadError."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as error:
        raise ReadError(f"cannot read {path}: {error}") from error
    for number, line in _tool_lines(text):
        match = ENTRY.match(line)
        if not match:
            raise ReadError(
                f"{path}:{number}: each tool must be a single-line flow mapping, "
                f'`  name: {{ version: "x.y.z", sites: n }}`; got {line.strip()!r}'
            )
        name, body = match.groups()
        yield number, name, _fields(path, number, name, body)


def accepted(path):
    """{tool: tuple of the extra versions its hook accepts}, every tool listed.

    Runs the full validation of `read()` first, so a file `read()` refuses is
    refused here too.
    """
    read(path)
    return {name: fields.get("accept", ()) for _, name, fields in _entries(path)}


def read(path):
    """Return the outputs as a dict, or raise ReadError."""
    outputs = {}
    for number, name, fields in _entries(path):
        key = name.replace("-", "_")
        if key in outputs:
            raise ReadError(
                f"{path}:{number}: {name} is listed twice, or its output `{key}` "
                "collides with another tool's `_sha256` output"
            )
        version = fields.get("version")
        if version is None:
            raise ReadError(f"{path}:{number}: {name} has no version")
        if not VERSION.match(version):
            raise ReadError(f"{path}:{number}: {name} version {version!r} is not a dotted number")
        outputs[key] = version
        if version in fields.get("accept", ()):
            raise ReadError(
                f"{path}:{number}: {name} accept lists {version}, which is its own version; "
                "delete it from `accept`"
            )
        if "sha256" in fields:
            if not SHA256.match(fields["sha256"]):
                raise ReadError(f"{path}:{number}: {name} sha256 is not 64 lowercase hex characters")
            if f"{key}_sha256" in outputs:
                raise ReadError(
                    f"{path}:{number}: {name}'s output `{key}_sha256` collides with another tool"
                )
            outputs[f"{key}_sha256"] = fields["sha256"]
    if not outputs:
        raise ReadError(f"{path}: `tools:` lists no tools")
    return outputs


def main(argv):
    if len(argv) != 2:
        print("::error::usage: tool_versions.py <versions.yaml>", file=sys.stderr)
        return 1
    try:
        outputs = read(argv[1])
    except ReadError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
