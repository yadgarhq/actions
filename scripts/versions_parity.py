#!/usr/bin/env python3
"""Every PINNED tool version listed in `versions.yaml` equals the canonical one there.

SCOPE: the tools in `versions.yaml` and the pin shapes in `SCANNERS`.
Nothing else is covered; see "WHAT IT DOES NOT DO".

A WORKFLOW READS, IT DOES NOT NAME (ledger 1756 S2b, ADR-0981). For the tools in
`READ_IN_WORKFLOWS`, a site under `.github/workflows/` must be a read of that
tool's output, `${{ needs.<job>.outputs.<tool> }}` or
`${{ steps.<id>.outputs.<tool> }}` (a `-` in the name becomes `_`), produced by
`scripts/tool_versions.py` from `versions.yaml` at the running commit. A literal
there is refused even at the right value, because it is a second copy of the
pin; `stable` and `3.x` are refused as floating. Sites outside the workflows
(gate scripts, hook definitions, Containerfiles) stay literals and must equal
the canonical value. For the tools in `SHA256_REQUIRED`, `versions.yaml` must
carry a 64-hex `sha256`; `scripts/tests/test_tool_installs.py` proves the
install steps check it.

`versions.yaml` at the repository root is the canonical list. Each tool in it has
a `version` and a `sites` count: the number of places this repository pins it
today. This module finds the places by KEY PATTERN, not by grepping the bare
number, and compares each to the canonical value.

WHY KEY PATTERNS. A version such as `3.18.4` appears in comments and docstrings
more often than in pins. A grep for the number passes when the prose agrees and
reddens on a comment when a pin moves. So each tool has scanners that match the
shape of a real pin: `GITLEAKS_VERSION:` as an env key, `version:` inside an
`azure/setup-helm` step, `TRIVY_VERSION =` in a script, `prettier@X` in
`additional_dependencies`, `pyyaml==X` in a pip line. Comment lines (first
non-blank character `#`) are never read.

WHY A `sites` COUNT. A new copy of a pin, even at the right value, is a new
place for a hand to move one copy and not the others. The count makes the new
copy fail until `versions.yaml` is edited in the same change. A count of zero
found is also a failure: a gate that finds nothing reports success having
checked nothing.

WHAT IT DOES NOT DO. It does not cover the apt `shellcheck` or `pre-commit`
itself, which still float. It does not check that a read step really reads
`versions.yaml` (`test_tool_versions.py` and the workflow wiring do that). The
image toolchain `rust-image` is spelt two ways: a `rust:` base-image tag that
names major.minor only, and a full rustup toolchain. A site marked `prefix` matches when its dotted
value is a leading part of the canonical one. It does not change any pin. It does not read
`scripts/tests/`. It does not check action SHAs (Dependabot moves those). The
`azure/setup-helm` steps are also held equal to each other by
`helm_pin_agrees.py`; this module adds the comparison to the canonical value.

Run: python3 scripts/versions_parity.py     (from the repository root)
Tests: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re
import sys

import yaml

MANIFEST = "versions.yaml"

VERSION = r"v?(\d[\w.]*)"
# Value after a key, with optional quotes: a whole `${{ ... }}` expression, or a
# literal stopped by a quote, space or comment.
VALUE = r"[\"']?v?(\$\{\{[^}]*\}\}|[^\s\"'#]+)"
# A read of a versions output. Group 1 is the output name.
READ = re.compile(r"^\$\{\{\s*(?:needs\.[\w-]+|steps\.[\w-]+)\.outputs\.([\w-]+)\s*\}\}$")
# Literals that are not versions at all: the toolchain or interpreter they name moves.
FLOATING = {"stable", "beta", "nightly", "latest", "3.x", "3", "x"}
WORKFLOWS = ".github/workflows/"

# Every workflow site of these reads `versions.yaml`; a literal is refused.
READ_IN_WORKFLOWS = {
    "gitleaks",
    "hadolint",
    "actionlint",
    "cargo-deny",
    "zizmor",
    "helm",
    "trivy",
    "rust-toolchain",
    "python",
}
# These are downloaded as artefacts; `versions.yaml` carries their sha256.
SHA256_REQUIRED = ("gitleaks", "hadolint", "actionlint", "cargo-deny")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def env(key):
    return ("line", re.compile(rf"^\s*{key}:\s*{VALUE}"))


def uses_input(action, key="version", require=True):
    """The `key:` input of every step that uses `action`."""
    return (
        "block",
        re.compile(rf"uses:\s*{re.escape(action)}@"),
        re.compile(rf"^\s*{re.escape(key)}:\s*{VALUE}"),
        require,
    )


def output_name(tool):
    return tool.replace("-", "_")


# tool -> scanner rules. A "line" rule yields one site per regex match on a line.
# A "block" rule yields one site per anchor line, read from a key inside the
# same list item; `require` says a missing key is itself a problem.
SCANNERS = {
    "gitleaks": [env("GITLEAKS_VERSION")],
    "hadolint": [env("HADOLINT_VERSION")],
    "actionlint": [env("ACTIONLINT_VERSION")],
    "cargo-deny": [env("CARGO_DENY_VERSION")],
    "zizmor": [env("ZIZMOR_VERSION"), ("line", re.compile(rf"zizmor=={VERSION}"))],
    "helm": [uses_input("azure/setup-helm")],
    # A trivy-action step with no `version:` runs the action's own default
    # (0.70.0, measured in trivy_gate.py), so a missing input is refused.
    "trivy": [
        uses_input("aquasecurity/trivy-action"),
        ("line", re.compile(rf"^TRIVY_VERSION\s*=\s*{VALUE}")),
    ],
    "rust-toolchain": [uses_input("dtolnay/rust-toolchain", "toolchain")],
    # The build images. `rust:1.98-bookworm` names major.minor only, so that
    # site is a PREFIX match; `--default-toolchain` names the full version.
    "rust-image": [
        ("line", re.compile(r"^FROM\s+(?:\S*/)?rust:(\d+(?:\.\d+)*)-"), "prefix"),
        ("line", re.compile(r"--default-toolchain\s+v?(\d[\w.]*)")),
    ],
    "python": [uses_input("actions/setup-python", "python-version")],
    "prettier": [("line", re.compile(rf"prettier@{VERSION}"))],
    "pytest": [("line", re.compile(rf"\bpytest=={VERSION}"))],
    "pyyaml": [("line", re.compile(rf"\bpyyaml=={VERSION}", re.IGNORECASE))],
    "shellcheck-py": [
        (
            "block",
            re.compile(r"repo:\s*\S*shellcheck-py/shellcheck-py\s*$"),
            re.compile(rf"^\s*rev:\s*{VALUE}"),
            True,
        )
    ],
    "pre-commit-hooks": [
        (
            "block",
            re.compile(r"repo:\s*\S*pre-commit/pre-commit-hooks\s*$"),
            re.compile(rf"^\s*rev:\s*{VALUE}"),
            True,
        )
    ],
    "cargo-chef": [("line", re.compile(rf"cargo-chef\b.*--version\s+{VERSION}"))],
}


def scanned_files(root):
    """Workflows, hook definitions, gate scripts, hook scripts and containers."""
    found = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        found += [p for p in workflows.iterdir() if p.suffix in (".yaml", ".yml")]
    for name in (".pre-commit-config.yaml", ".pre-commit-hooks.yaml"):
        if (root / name).is_file():
            found.append(root / name)
    scripts = root / "scripts"
    if scripts.is_dir():
        found += [p for p in scripts.glob("*.py")]
    for directory in ("hooks", "containers"):
        base = root / directory
        if base.is_dir():
            found += [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return sorted(set(found))


def indent_of(line):
    return len(line) - len(line.lstrip())


def item_start(lines, index):
    """Index of the `- ` list-item line that holds the key on line `index`."""
    if lines[index].lstrip().startswith("- "):
        return index
    base = indent_of(lines[index])
    for back in range(index - 1, -1, -1):
        line = lines[back]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if indent_of(line) < base:
            return back if line.lstrip().startswith("- ") else index
    return index


def sites_in(lines, rules):
    """Yield (line number, version or None, required, prefix) for every pin in `lines`.

    A block rule reads its key from anywhere inside the step's list item, so
    `uses:` need not be the first key (`- name: ...` first is common).
    """
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        for rule in rules:
            if rule[0] == "line":
                prefix = len(rule) > 2 and rule[2] == "prefix"
                for match in rule[1].finditer(line):
                    yield number, match.group(1), True, prefix
            else:
                _, anchor, key, require = rule
                if not anchor.search(line):
                    continue
                start = item_start(lines, number - 1)
                base = indent_of(lines[start])
                found = None
                for offset in range(start + 1, len(lines)):
                    follow = lines[offset]
                    if not follow.strip() or follow.lstrip().startswith("#"):
                        continue
                    if indent_of(follow) <= base:
                        break
                    match = key.match(follow)
                    if match:
                        found = (offset + 1, match.group(1))
                        break
                if found:
                    yield found[0], found[1], require, False
                else:
                    yield number, None, require, False


def load_manifest(root):
    path = root / MANIFEST
    if not path.is_file():
        return None, [f"{MANIFEST}: missing; it is the canonical list of tool versions"]
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        return None, [f"{MANIFEST}: not a YAML document: {error}"]
    tools = (data or {}).get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, dict):
        return None, [f"{MANIFEST}: needs a `tools:` mapping"]
    return tools, []


def judge(name, relative, found, prefix, want):
    """The problem with one found site, or None."""
    if found.startswith("${{"):
        read = READ.match(found)
        if not relative.startswith(WORKFLOWS):
            return f"{name} reads `{found}` outside a workflow; pin it to {want}"
        if not read:
            return (
                f"{name} reads `{found}`, which is not a versions output; "
                f"read `${{{{ needs.<job>.outputs.{output_name(name)} }}}}` or "
                f"`${{{{ steps.<id>.outputs.{output_name(name)} }}}}`"
            )
        if read.group(1) != output_name(name):
            return (
                f"{name} reads outputs.{read.group(1)}; it must read outputs.{output_name(name)}"
            )
        return None
    if relative.startswith(WORKFLOWS) and name in READ_IN_WORKFLOWS:
        floats = " `" + found + "` floats: it names no version at all." if found in FLOATING else ""
        return (
            f"{name} is hardcoded as {found} in a workflow.{floats} A workflow reads it "
            f"from {MANIFEST} at the running commit (ADR-0981): use "
            f"`${{{{ needs.detect.outputs.{output_name(name)} }}}}` or the job's "
            f"`${{{{ steps.versions.outputs.{output_name(name)} }}}}`"
        )
    value = found.lstrip("v")
    if value == want or (prefix and want.startswith(value + ".")):
        return None
    return f"{name} pinned {found}, but {MANIFEST} says {want}"


def check(root):
    """Return the problems found under `root`, each as `path:line: message`."""
    root = pathlib.Path(root)
    tools, problems = load_manifest(root)
    if tools is None:
        return problems

    for name in SCANNERS:
        if name not in tools:
            problems.append(f"{MANIFEST}: {name} is pinned in this repository but not listed")
    for name in tools:
        if name not in SCANNERS:
            problems.append(f"{MANIFEST}: {name} is listed but has no scanner in versions_parity.py")
    for name in SHA256_REQUIRED:
        entry = tools.get(name) or {}
        if not SHA256.match(str(entry.get("sha256", ""))):
            problems.append(
                f"{MANIFEST}: {name} is downloaded by the workflows and needs `sha256`, "
                "64 lowercase hex characters from the upstream checksum file"
            )
    for name, entry in tools.items():
        if isinstance(entry, dict) and "unpinned" in entry:
            problems.append(
                f"{MANIFEST}: {name} carries `unpinned`; there is no allowance for an "
                "unpinned step any more (ledger 1756 S2b). Pin the step"
            )

    texts = {p: p.read_text(errors="replace").splitlines() for p in scanned_files(root)}
    for name, rules in SCANNERS.items():
        if name not in tools:
            continue
        entry = tools[name] or {}
        where = []
        want = str(entry.get("version", "")).lstrip("v")
        expected_sites = entry.get("sites")
        count = 0
        for path, lines in texts.items():
            relative = path.relative_to(root).as_posix()
            for number, found, require, prefix in sites_in(lines, rules):
                if found is None:
                    if require:
                        problems.append(
                            f"{relative}:{number}: {name} is installed with no pinned version; "
                            f"read it from {MANIFEST} ({want})"
                        )
                    continue
                count += 1
                where.append(f"{relative}:{number}")
                problem = judge(name, relative, found, prefix, want)
                if problem:
                    problems.append(f"{relative}:{number}: {problem}")
        if count == 0:
            problems.append(f"{MANIFEST}: found no {name} pin sites; the scanner or the pin is gone")
        elif count != expected_sites:
            problems.append(
                f"{MANIFEST}: {name} has {count} pin sites, {MANIFEST} expects {expected_sites}. "
                f"Found: {', '.join(where)}. "
                "Update `sites` with the pin change, or delete the stray copy"
            )
    return problems


def main():
    problems = check(pathlib.Path.cwd())
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
