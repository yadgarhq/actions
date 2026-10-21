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
        # Measured, not chosen: `toolchain: stable` resolved to rustc 1.99.1 in
        # yadgarhq/gateway run 39572682285 (2026-10-21). The images stay 1.98.0.
        "rust-toolchain": "1.99.1",
        "rust-image": "1.98.0",
        "python": "3.14",
    }
    for name, version in expected.items():
        assert tools[name]["version"] == version, name


# The upstream checksum files at the pinned versions, cross-checked by hashing
# the downloaded artefacts (2026-10-21):
#   gitleaks   https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt
#   hadolint   https://github.com/hadolint/hadolint/releases/download/v2.14.0/hadolint-linux-x86_64.sha256
#   actionlint https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_checksums.txt
#   cargo-deny https://github.com/EmbarkStudios/cargo-deny/releases/download/0.20.2/cargo-deny-0.20.2-x86_64-unknown-linux-musl.tar.gz.sha256
def test_every_downloaded_tool_carries_its_upstream_sha256():
    tools = yaml.safe_load((REPO / "versions.yaml").read_text())["tools"]
    assert {name: tools[name].get("sha256") for name in versions_parity.SHA256_REQUIRED} == {
        "gitleaks": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        "hadolint": "6bf226944684f56c84dd014e8b979d27425c0148f61b3bd99bcc6f39e9dc5a47",
        "actionlint": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
        "cargo-deny": "9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f",
    }


# ---------------------------------------------------------------------------
# S2b: a workflow READS a version from versions.yaml; it never names one.
# ---------------------------------------------------------------------------

READ_SITES = [
    # (file, the read as written, a hardcoded replacement, tool)
    (".github/workflows/ci-pr.yaml", "GITLEAKS_VERSION: ${{ needs.detect.outputs.gitleaks }}", "GITLEAKS_VERSION: 8.30.1", "gitleaks"),
    (".github/workflows/ci-self.yaml", "GITLEAKS_VERSION: ${{ steps.versions.outputs.gitleaks }}", "GITLEAKS_VERSION: 8.30.1", "gitleaks"),
    (".github/workflows/ci-pr.yaml", "HADOLINT_VERSION: ${{ needs.detect.outputs.hadolint }}", "HADOLINT_VERSION: 2.14.0", "hadolint"),
    (".github/workflows/ci-pr.yaml", "ACTIONLINT_VERSION: ${{ needs.detect.outputs.actionlint }}", "ACTIONLINT_VERSION: 1.7.12", "actionlint"),
    (".github/workflows/ci-pr.yaml", "CARGO_DENY_VERSION: ${{ needs.detect.outputs.cargo_deny }}", "CARGO_DENY_VERSION: 0.20.2", "cargo-deny"),
    (".github/workflows/ci-pr.yaml", "ZIZMOR_VERSION: ${{ needs.detect.outputs.zizmor }}", 'ZIZMOR_VERSION: "1.29.0"', "zizmor"),
    (".github/workflows/ci-pr.yaml", "version: v${{ needs.detect.outputs.helm }}", "version: v3.18.4", "helm"),
    (".github/workflows/ci-release.yaml", "version: v${{ needs.detect.outputs.helm }}", "version: v3.18.4", "helm"),
    (".github/workflows/ci-pr.yaml", "version: v${{ needs.detect.outputs.trivy }}", "version: v0.74.0", "trivy"),
    (".github/workflows/ci-release.yaml", "version: v${{ needs.detect.outputs.trivy }}", "version: v0.74.0", "trivy"),
    (".github/workflows/base-images.yaml", "version: v${{ steps.versions.outputs.trivy }}", "version: v0.74.0", "trivy"),
    (".github/workflows/estate-runner-image.yaml", "version: v${{ steps.versions.outputs.trivy }}", "version: v0.74.0", "trivy"),
    (".github/workflows/ci-pr.yaml", "toolchain: ${{ needs.detect.outputs.rust_toolchain }}", "toolchain: 1.99.1", "rust-toolchain"),
    (".github/workflows/repin.yaml", "toolchain: ${{ steps.versions.outputs.rust_toolchain }}", "toolchain: 1.99.1", "rust-toolchain"),
    (".github/workflows/ci-pr.yaml", "python-version: ${{ needs.detect.outputs.python }}", 'python-version: "3.14"', "python"),
    (".github/workflows/ci-self.yaml", "python-version: ${{ steps.versions.outputs.python }}", 'python-version: "3.14"', "python"),
    (".github/workflows/seed-declaration.yaml", "python-version: ${{ steps.versions.outputs.python }}", 'python-version: "3.14"', "python"),
]


@pytest.mark.parametrize("relative,read,literal,tool", READ_SITES, ids=[f"{r}:{t}" for r, _, _, t in READ_SITES])
def test_a_workflow_that_hardcodes_a_version_instead_of_reading_it_is_refused(tree, relative, read, literal, tool):
    """Even at the RIGHT value: the literal is a second copy of the pin."""
    rewrite(tree, relative, read, literal)
    line = line_of(tree, relative, literal)
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f"{relative}:{line}:") and tool in p and "hardcoded" in p and "versions.yaml" in p
        for p in problems
    ), problems


@pytest.mark.parametrize(
    "relative,read,floating,tool",
    [
        (".github/workflows/ci-pr.yaml", "toolchain: ${{ needs.detect.outputs.rust_toolchain }}", "toolchain: stable", "rust-toolchain"),
        (".github/workflows/repin.yaml", "toolchain: ${{ steps.versions.outputs.rust_toolchain }}", "toolchain: stable", "rust-toolchain"),
        (".github/workflows/ci-pr.yaml", "python-version: ${{ needs.detect.outputs.python }}", 'python-version: "3.x"', "python"),
        (".github/workflows/seed-declaration.yaml", "python-version: ${{ steps.versions.outputs.python }}", 'python-version: "3.x"', "python"),
    ],
)
def test_a_floating_toolchain_or_python_is_refused(tree, relative, read, floating, tool):
    rewrite(tree, relative, read, floating)
    line = line_of(tree, relative, floating)
    problems = versions_parity.check(tree)
    assert any(p.startswith(f"{relative}:{line}:") and tool in p and "floats" in p for p in problems), problems


def test_a_read_of_the_wrong_output_is_refused(tree):
    rewrite(tree, ".github/workflows/ci-pr.yaml", "version: v${{ needs.detect.outputs.helm }}", "version: v${{ needs.detect.outputs.trivy }}")
    line = line_of(tree, ".github/workflows/ci-pr.yaml", "version: v${{ needs.detect.outputs.trivy }}")
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f".github/workflows/ci-pr.yaml:{line}:") and "helm" in p and "outputs.helm" in p
        for p in problems
    ), problems


def test_a_read_through_an_unrecognised_expression_is_refused(tree):
    rewrite(tree, ".github/workflows/ci-pr.yaml", "toolchain: ${{ needs.detect.outputs.rust_toolchain }}", "toolchain: ${{ inputs.rust }}")
    problems = versions_parity.check(tree)
    assert any("ci-pr.yaml" in p and "rust-toolchain" in p and "inputs.rust" in p for p in problems), problems


def test_a_setup_python_with_no_version_is_refused(tree):
    rewrite(tree, ".github/workflows/seed-declaration.yaml", "python-version: ${{ steps.versions.outputs.python }}", "cache: pip")
    problems = versions_parity.check(tree)
    assert any("seed-declaration.yaml" in p and "python" in p and "no pinned version" in p for p in problems), problems


def test_a_trivy_action_step_with_no_version_is_refused(tree):
    """No `unpinned:` allowance any more: all four floating steps are pinned."""
    first = append(
        tree,
        ".github/workflows/ci-release.yaml",
        "      - name: extra scan\n"
        "        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0\n"
        "        with:\n"
        "          scan-type: fs\n",
    )
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f".github/workflows/ci-release.yaml:{first + 1}:") and "trivy" in p and "no pinned version" in p
        for p in problems
    ), problems


def test_an_unpinned_allowance_in_versions_yaml_is_refused(tree):
    rewrite(tree, "versions.yaml", '  trivy: { version: "0.74.0"', '  trivy: { unpinned: 1, version: "0.74.0"')
    assert any("trivy" in p and "unpinned" in p for p in versions_parity.check(tree))


@pytest.mark.parametrize("tool", ["gitleaks", "hadolint", "actionlint", "cargo-deny"])
def test_a_downloaded_tool_with_no_sha256_is_refused(tree, tool):
    data = yaml.safe_load((tree / "versions.yaml").read_text())
    del data["tools"][tool]["sha256"]
    (tree / "versions.yaml").write_text(yaml.safe_dump(data))
    problems = versions_parity.check(tree)
    assert any(p.startswith("versions.yaml:") and tool in p and "sha256" in p for p in problems), problems


def test_a_malformed_sha256_is_refused(tree):
    data = yaml.safe_load((tree / "versions.yaml").read_text())
    data["tools"]["hadolint"]["sha256"] = "6BF2" + "0" * 60
    (tree / "versions.yaml").write_text(yaml.safe_dump(data))
    assert any("hadolint" in p and "sha256" in p for p in versions_parity.check(tree))


def test_a_drifted_rust_image_tag_is_caught_by_its_prefix(tree):
    rewrite(tree, "containers/rust-build/Containerfile", "rust:1.98-bookworm", "rust:1.97-bookworm")
    problems = versions_parity.check(tree)
    assert any(p.startswith("containers/rust-build/Containerfile:3:") and "rust-image" in p for p in problems), problems


def test_a_drifted_estate_runner_toolchain_is_caught(tree):
    rewrite(tree, "containers/estate-runner/Containerfile", "--default-toolchain 1.98.0", "--default-toolchain 1.97.0")
    problems = versions_parity.check(tree)
    assert any(p.startswith("containers/estate-runner/Containerfile:") and "rust-image" in p for p in problems), problems


def test_a_prefix_is_not_a_loose_match(tree):
    """`1.9` is not a prefix of `1.98.0` in the dotted sense."""
    rewrite(tree, "containers/rust-build/Containerfile", "rust:1.98-bookworm", "rust:1.9-bookworm")
    assert any("rust-image" in p for p in versions_parity.check(tree))


# ---------------------------------------------------------------------------
# Unchanged S1 behaviour: literals outside the workflows are held to the value.
# ---------------------------------------------------------------------------


def test_a_drifted_trivy_constant_in_the_gate_script_is_caught(tree):
    rewrite(tree, "scripts/trivy_gate.py", 'TRIVY_VERSION = "0.74.0"', 'TRIVY_VERSION = "0.70.0"')
    assert any(p.startswith("scripts/trivy_gate.py:") and "trivy" in p for p in versions_parity.check(tree))


def test_a_drifted_prettier_pin_in_the_published_hook_is_caught(tree):
    rewrite(tree, ".pre-commit-hooks.yaml", "prettier@3.9.6", "prettier@3.8.0")
    assert any(p.startswith(".pre-commit-hooks.yaml:") and "prettier" in p for p in versions_parity.check(tree))


def test_a_drifted_pyyaml_copy_is_caught_in_every_file_kind(tree):
    rewrite(tree, ".pre-commit-config.yaml", "pyyaml==6.0.3", "pyyaml==6.0.2")
    rewrite(tree, ".github/workflows/ci-pr.yaml", "pyyaml==6.0.3", "pyyaml==6.0.1")
    problems = versions_parity.check(tree)
    assert any(p.startswith(".pre-commit-config.yaml:") and "pyyaml" in p for p in problems)
    assert any(p.startswith(".github/workflows/ci-pr.yaml:") and "pyyaml" in p for p in problems)


def test_a_pipx_zizmor_literal_is_a_site(tree):
    rewrite(tree, ".github/workflows/ci-pr.yaml", 'pipx run "zizmor==${ZIZMOR_VERSION}"', "pipx run zizmor==1.20.0")
    assert any("zizmor" in p and "1.20.0" in p for p in versions_parity.check(tree))


def test_an_unknown_extra_read_of_a_listed_tool_is_refused(tree):
    path = tree / ".github/workflows/seed-declaration.yaml"
    path.write_text(path.read_text() + "\n# appended\nenv:\n  GITLEAKS_VERSION: ${{ steps.versions.outputs.gitleaks }}\n")
    problems = versions_parity.check(tree)
    assert any("gitleaks" in p and "sites" in p for p in problems), problems


def test_a_removed_copy_is_refused_so_the_gate_cannot_go_blind(tree):
    rewrite(tree, ".github/workflows/ci-self.yaml", "GITLEAKS_VERSION: ${{ steps.versions.outputs.gitleaks }}", "GITLEAKS: gone")
    problems = versions_parity.check(tree)
    assert any("gitleaks" in p and "sites" in p for p in problems), problems


def test_a_comment_naming_another_version_is_not_a_site(tree):
    path = tree / ".github/workflows/ci-pr.yaml"
    path.write_text(
        path.read_text()
        + "\n# GITLEAKS_VERSION: 1.0.0 and pyyaml==1.0 and version: v9.9.9 and toolchain: stable\n"
    )
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


def append(root, relative, text):
    """Append `text` to a file and return the line number of its first line."""
    path = root / relative
    before = path.read_text()
    if not before.endswith("\n"):
        before += "\n"
    path.write_text(before + text)
    return before.count("\n") + 1


def test_the_site_count_message_lists_every_site_found(tree):
    first = append(tree, ".github/workflows/seed-declaration.yaml", "env:\n  GITLEAKS_VERSION: ${{ steps.versions.outputs.gitleaks }}\n")
    problems = versions_parity.check(tree)
    count = [p for p in problems if "gitleaks" in p and "sites" in p]
    assert count, problems
    message = count[0]
    assert f".github/workflows/seed-declaration.yaml:{first + 1}" in message
    assert f".github/workflows/ci-pr.yaml:{line_of(tree, '.github/workflows/ci-pr.yaml', 'GITLEAKS_VERSION: ${{')}" in message
    assert f".github/workflows/ci-self.yaml:{line_of(tree, '.github/workflows/ci-self.yaml', 'GITLEAKS_VERSION: ${{')}" in message


def test_a_helm_step_whose_first_key_is_name_is_still_read(tree):
    first = append(
        tree,
        ".github/workflows/ci-release.yaml",
        "      - name: extra helm\n"
        "        uses: azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310 # v5.0.1\n"
        "        with:\n"
        "          version: v3.0.0\n",
    )
    problems = versions_parity.check(tree)
    assert any(
        p.startswith(f".github/workflows/ci-release.yaml:{first + 3}:") and "helm" in p and "hardcoded" in p
        for p in problems
    ), problems


def test_pre_commit_hooks_rev_is_a_site(tree):
    rewrite(tree, ".pre-commit-config.yaml", "rev: v5.0.0", "rev: v4.6.0")
    assert any(p.startswith(".pre-commit-config.yaml:4:") and "pre-commit-hooks" in p for p in versions_parity.check(tree))
