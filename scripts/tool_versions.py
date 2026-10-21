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
PAIR = re.compile(r'^\s*([a-z0-9_]+):\s*(?:"([^"]*)"|([0-9]+))\s*$')
VERSION = re.compile(r"^\d+(?:\.\d+)*$")
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


def read(path):
    """Return the outputs as a dict, or raise ReadError."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as error:
        raise ReadError(f"cannot read {path}: {error}") from error
    outputs = {}
    for number, line in _tool_lines(text):
        match = ENTRY.match(line)
        if not match:
            raise ReadError(
                f"{path}:{number}: each tool must be a single-line flow mapping, "
                f'`  name: {{ version: "x.y.z", sites: n }}`; got {line.strip()!r}'
            )
        name, body = match.groups()
        fields = {}
        for part in body.split(","):
            pair = PAIR.match(part)
            if not pair:
                raise ReadError(f"{path}:{number}: {name}: cannot read {part.strip()!r}")
            key = pair.group(1)
            if key in fields:
                raise ReadError(f"{path}:{number}: {name}: `{key}` given twice")
            fields[key] = pair.group(2) if pair.group(2) is not None else pair.group(3)
        key = name.replace("-", "_")
        if key in outputs:
            raise ReadError(f"{path}:{number}: {name} is listed twice")
        version = fields.get("version")
        if version is None:
            raise ReadError(f"{path}:{number}: {name} has no version")
        if not VERSION.match(version):
            raise ReadError(f"{path}:{number}: {name} version {version!r} is not a dotted number")
        outputs[key] = version
        if "sha256" in fields:
            if not SHA256.match(fields["sha256"]):
                raise ReadError(f"{path}:{number}: {name} sha256 is not 64 lowercase hex characters")
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
