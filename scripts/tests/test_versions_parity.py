"""`versions.yaml` is the one list of tool versions; `versions_parity.py` holds every copy to it.

MOST OF THIS FILE IS RED CASES. Each one copies the real repository into a temp
directory, drifts exactly one copy, and demands the gate name the file and line
of that copy. A suite that only reads the real tree passes whether the gate
works or not.

The real-tree case is the only one that touches the working copy: it is the
gate itself, run by `ci-self`'s `pytest` job and the `pytest-scripts` hook.

Run: python3 -m pytest scripts/tests/ -q
"""

import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import versions_parity  # noqa: E402

COPIED = [
    ".github/workflows",
    ".pre-commit-config.yaml",
    ".pre-commit-hooks.yaml",
    "scripts",
    "hooks",
    "containers",
    "versions.yaml",
]


@pytest.fixture
def tree(tmp_path):
    """A private copy of the files the gate reads, free to drift."""
    for name in COPIED:
        source = REPO / name
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache"),
            )
        else:
            shutil.copy(source, target)
    return tmp_path


def rewrite(root, relative, old, new, count=1):
    path = root / relative
    text = path.read_text()
    assert text.count(old) >= count, f"{old!r} not in {relative}"
    path.write_text(text.replace(old, new, count))


def line_of(root, relative, needle):
    for number, line in enumerate((root / relative).read_text().splitlines(), 1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not in {relative}")


def test_the_real_tree_agrees_with_versions_yaml():
    assert versions_parity.check(REPO) == []


def test_every_tool_the_task_lists_is_in_versions_yaml():
    tools = yaml.safe_load((REPO / "versions.yaml").read_text())["tools"]
    expected = {
        "gitleaks": "8.30.1",
        "hadolint": "2.14.0",
        "actionlint": "1.7.12",
        "cargo-deny": "0.20.2",
        "zizmor": "1.29.0",
        "helm": "3.18.4",
        "trivy": "0.74.0",
        "prettier": "3.9.6",
        "pytest": "9.1.1",
        "pyyaml": "6.0.3",
    }
    for name, version in expected.items():
        assert tools[name]["version"] == version, name


def test_a_drifted_gitleaks_pin_in_ci_self_names_file_and_line(tree):
    rewrite(tree, ".github/workflows/ci-self.yaml", "GITLEAKS_VERSION: 8.30.1", "GITLEAKS_VERSION: 8.30.0")
    line = line_of(tree, ".github/workflows/ci-self.yaml", "GITLEAKS_VERSION: 8.30.0")
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f".github/workflows/ci-self.yaml:{line}:") and "gitleaks" in p and "8.30.0" in p
        for p in problems
    ), problems


def test_a_drifted_helm_copy_names_file_and_line(tree):
    text = (tree / ".github/workflows/ci-pr.yaml").read_text().splitlines()
    hits = [i for i, l in enumerate(text, 1) if l.strip() == "version: v3.18.4"]
    assert len(hits) >= 3
    target = hits[1]
    text[target - 1] = text[target - 1].replace("v3.18.4", "v3.18.3")
    (tree / ".github/workflows/ci-pr.yaml").write_text("\n".join(text) + "\n")
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f".github/workflows/ci-pr.yaml:{target}:") and "helm" in p for p in problems
    ), problems


def test_a_drifted_helm_copy_in_ci_release_is_caught(tree):
    rewrite(tree, ".github/workflows/ci-release.yaml", "version: v3.18.4", "version: v4.2.4")
    assert any("ci-release.yaml" in p and "helm" in p for p in versions_parity.check(tree))


def test_a_drifted_trivy_constant_in_the_gate_script_is_caught(tree):
    rewrite(tree, "scripts/trivy_gate.py", 'TRIVY_VERSION = "0.74.0"', 'TRIVY_VERSION = "0.70.0"')
    assert any(p.startswith("scripts/trivy_gate.py:") and "trivy" in p for p in versions_parity.check(tree))


def test_a_drifted_trivy_action_version_is_caught(tree):
    # The first literal is in a comment; the pin is the line that is only the key.
    rewrite(tree, ".github/workflows/ci-pr.yaml", "          version: v0.74.0", "          version: v0.70.0")
    line = line_of(tree, ".github/workflows/ci-pr.yaml", "version: v0.70.0")
    assert any(
        p.startswith(f".github/workflows/ci-pr.yaml:{line}:") and "trivy" in p
        for p in versions_parity.check(tree)
    )


def test_a_drifted_prettier_pin_in_the_published_hook_is_caught(tree):
    rewrite(tree, ".pre-commit-hooks.yaml", "prettier@3.9.6", "prettier@3.8.0")
    assert any(p.startswith(".pre-commit-hooks.yaml:") and "prettier" in p for p in versions_parity.check(tree))


def test_a_drifted_pyyaml_copy_is_caught_in_every_file_kind(tree):
    rewrite(tree, ".pre-commit-config.yaml", "pyyaml==6.0.3", "pyyaml==6.0.2")
    rewrite(tree, ".github/workflows/ci-pr.yaml", "pyyaml==6.0.3", "pyyaml==6.0.1")
    problems = versions_parity.check(tree)
    assert any(p.startswith(".pre-commit-config.yaml:") and "pyyaml" in p for p in problems)
    assert any(p.startswith(".github/workflows/ci-pr.yaml:") and "pyyaml" in p for p in problems)


@pytest.mark.parametrize(
    "key,old,new",
    [
        ("HADOLINT_VERSION", "HADOLINT_VERSION: 2.14.0", "HADOLINT_VERSION: 2.13.0"),
        ("ACTIONLINT_VERSION", "ACTIONLINT_VERSION: 1.7.12", "ACTIONLINT_VERSION: 1.7.11"),
        ("CARGO_DENY_VERSION", "CARGO_DENY_VERSION: 0.20.2", "CARGO_DENY_VERSION: 0.19.0"),
        ("ZIZMOR_VERSION", 'ZIZMOR_VERSION: "1.29.0"', 'ZIZMOR_VERSION: "1.28.0"'),
    ],
)
def test_a_drifted_env_pin_in_ci_pr_is_caught(tree, key, old, new):
    rewrite(tree, ".github/workflows/ci-pr.yaml", old, new)
    line = line_of(tree, ".github/workflows/ci-pr.yaml", new)
    assert any(p.startswith(f".github/workflows/ci-pr.yaml:{line}:") for p in versions_parity.check(tree))


def test_a_pipx_zizmor_literal_is_a_site(tree):
    rewrite(tree, ".github/workflows/ci-pr.yaml", 'pipx run "zizmor==${ZIZMOR_VERSION}"', "pipx run zizmor==1.20.0")
    assert any("zizmor" in p and "1.20.0" in p for p in versions_parity.check(tree))


def test_an_unknown_extra_copy_of_a_listed_tool_is_refused_even_at_the_right_value(tree):
    path = tree / ".github/workflows/seed-declaration.yaml"
    path.write_text(path.read_text() + "\n# appended\nenv:\n  GITLEAKS_VERSION: 8.30.1\n")
    problems = versions_parity.check(tree)
    assert any("gitleaks" in p and "sites" in p for p in problems), problems


def test_a_removed_copy_is_refused_so_the_gate_cannot_go_blind(tree):
    rewrite(tree, ".github/workflows/ci-self.yaml", "GITLEAKS_VERSION: 8.30.1", "GITLEAKS: gone")
    problems = versions_parity.check(tree)
    assert any("gitleaks" in p and "sites" in p for p in problems), problems


def test_a_comment_naming_another_version_is_not_a_site(tree):
    path = tree / ".github/workflows/ci-pr.yaml"
    path.write_text(path.read_text() + "\n# GITLEAKS_VERSION: 1.0.0 and pyyaml==1.0 and version: v9.9.9\n")
    assert versions_parity.check(tree) == []


def test_a_tool_missing_from_versions_yaml_is_refused(tree):
    data = yaml.safe_load((tree / "versions.yaml").read_text())
    del data["tools"]["gitleaks"]
    (tree / "versions.yaml").write_text(yaml.safe_dump(data))
    assert any("gitleaks" in p and "versions.yaml" in p for p in versions_parity.check(tree))


def test_a_missing_versions_yaml_is_refused(tree):
    (tree / "versions.yaml").unlink()
    problems = versions_parity.check(tree)
    assert problems and "versions.yaml" in problems[0]


def test_a_pin_with_no_matching_pattern_still_cannot_hide_in_versions_yaml_keys():
    """Every tool in versions.yaml has a scanner, and every scanner a tool."""
    tools = set(yaml.safe_load((REPO / "versions.yaml").read_text())["tools"])
    assert tools == set(versions_parity.SCANNERS)


def test_versions_are_plain_dotted_numbers():
    tools = yaml.safe_load((REPO / "versions.yaml").read_text())["tools"]
    for name, entry in tools.items():
        assert re.fullmatch(r"\d+(\.\d+)+", str(entry["version"])), name
        assert isinstance(entry["sites"], int) and entry["sites"] >= 1, name
