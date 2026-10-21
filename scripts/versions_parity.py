#!/usr/bin/env python3
"""Every tool version pinned in this repository equals the one in `versions.yaml`.

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

WHAT IT DOES NOT DO. It does not change any pin. It does not read
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
# Value after a key, with optional quotes, stopped by a quote, space or comment.
VALUE = r"[\"']?v?([^\s\"'#]+)"


def env(key):
    return ("line", re.compile(rf"^\s*{key}:\s*{VALUE}"))


def helm_like(action, require):
    """The `version:` input of every step that uses `action`."""
    return (
        "block",
        re.compile(rf"uses:\s*{re.escape(action)}@"),
        re.compile(rf"^\s*version:\s*{VALUE}"),
        require,
    )


# tool -> scanner rules. A "line" rule yields one site per regex match on a line.
# A "block" rule yields one site per anchor line, read from a key inside the
# same list item; `require` says a missing key is itself a problem.
SCANNERS = {
    "gitleaks": [env("GITLEAKS_VERSION")],
    "hadolint": [env("HADOLINT_VERSION")],
    "actionlint": [env("ACTIONLINT_VERSION")],
    "cargo-deny": [env("CARGO_DENY_VERSION")],
    "zizmor": [env("ZIZMOR_VERSION"), ("line", re.compile(rf"zizmor=={VERSION}"))],
    "helm": [helm_like("azure/setup-helm", True)],
    "trivy": [
        helm_like("aquasecurity/trivy-action", False),
        ("line", re.compile(rf"^TRIVY_VERSION\s*=\s*{VALUE}")),
    ],
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


def sites_in(lines, rules):
    """Yield (line number, version or None, required) for every pin in `lines`."""
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        for rule in rules:
            if rule[0] == "line":
                for match in rule[1].finditer(line):
                    yield number, match.group(1), True
            else:
                _, anchor, key, require = rule
                if not anchor.search(line):
                    continue
                base = indent_of(line)
                found = None
                for offset, follow in enumerate(lines[number:], number + 1):
                    if not follow.strip() or follow.lstrip().startswith("#"):
                        continue
                    if indent_of(follow) <= base:
                        break
                    match = key.match(follow)
                    if match:
                        found = (offset, match.group(1))
                        break
                if found:
                    yield found[0], found[1], require
                else:
                    yield number, None, require


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

    texts = {p: p.read_text(errors="replace").splitlines() for p in scanned_files(root)}
    for name, rules in SCANNERS.items():
        if name not in tools:
            continue
        entry = tools[name] or {}
        want = str(entry.get("version", "")).lstrip("v")
        expected_sites = entry.get("sites")
        count = 0
        for path, lines in texts.items():
            relative = path.relative_to(root).as_posix()
            for number, found, require in sites_in(lines, rules):
                if found is None:
                    if require:
                        problems.append(
                            f"{relative}:{number}: {name} is installed with no pinned version; "
                            f"pin it to {want}"
                        )
                    continue
                count += 1
                if found.lstrip("v") != want:
                    problems.append(
                        f"{relative}:{number}: {name} pinned {found}, but {MANIFEST} says {want}"
                    )
        if count == 0:
            problems.append(f"{MANIFEST}: found no {name} pin sites; the scanner or the pin is gone")
        elif count != expected_sites:
            problems.append(
                f"{MANIFEST}: {name} has {count} pin sites, {MANIFEST} expects {expected_sites}; "
                "update `sites` with the pin change, or delete the stray copy"
            )
    return problems


def main():
    problems = check(pathlib.Path.cwd())
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
