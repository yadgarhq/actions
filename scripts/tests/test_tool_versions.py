"""LEDGER 1756 S2b. `tool_versions.py` turns `versions.yaml` into step outputs.

WHY A READER WITH NO PyYAML. The workflows read `versions.yaml` before
`actions/setup-python` has run, because `setup-python`'s own `python-version`
comes from that file. The runner's system `python3` carries the standard
library and nothing more is promised, so the reader parses the one shape
`versions.yaml` is written in (a single-line flow mapping per tool) and
refuses every other shape rather than guessing.

THE REAL FILE IS HELD TO `yaml.safe_load`: whatever the reader returns for
each tool must equal what a full YAML parser returns. The red cases below each
break one property and demand a refusal.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import tool_versions  # noqa: E402

READER = REPO / "scripts" / "tool_versions.py"
SHA = "a" * 64


def run(path):
    return subprocess.run(
        [sys.executable, "-I", str(READER), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def write(tmp_path, body):
    path = tmp_path / "versions.yaml"
    path.write_text(body)
    return path


def test_the_reader_agrees_with_yaml_for_every_tool():
    tools = yaml.safe_load((REPO / "versions.yaml").read_text())["tools"]
    outputs = tool_versions.read(REPO / "versions.yaml")
    for name, entry in tools.items():
        key = name.replace("-", "_")
        assert outputs[key] == str(entry["version"]), name
        if "sha256" in entry:
            assert outputs[f"{key}_sha256"] == entry["sha256"], name
        else:
            assert f"{key}_sha256" not in outputs, name


def test_the_cli_prints_one_output_line_per_value():
    proc = run(REPO / "versions.yaml")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert "helm=3.18.4" in lines
    assert all("=" in line for line in lines)
    assert dict(line.split("=", 1) for line in lines) == tool_versions.read(REPO / "versions.yaml")


def test_a_good_file_with_a_comment_and_a_sha_is_read(tmp_path):
    path = write(
        tmp_path,
        "# a comment\ntools:\n  # another\n"
        f'  gitleaks: {{ version: "8.30.1", sites: 2, sha256: "{SHA}" }}\n'
        '  helm: { version: "3.18.4", sites: 6 }\n',
    )
    assert tool_versions.read(path) == {
        "gitleaks": "8.30.1",
        "gitleaks_sha256": SHA,
        "helm": "3.18.4",
    }


@pytest.mark.parametrize(
    "body,needle",
    [
        ("", "no `tools:`"),
        ('tools:\n  helm:\n    version: "3.18.4"\n', "single-line flow mapping"),
        ('tools:\n  helm: { sites: 6 }\n', "no version"),
        ('tools:\n  helm: { version: "stable", sites: 6 }\n', "not a dotted number"),
        ('tools:\n  helm: { version: "3.x", sites: 6 }\n', "not a dotted number"),
        ('tools:\n  helm: { version: "3.18.4", sha256: "abc" }\n', "64 lowercase hex"),
        ('tools:\n  helm: { version: "3.18.4" }\n  helm: { version: "3.18.4" }\n', "twice"),
        ('tools:\n  helm: { version: "3.18.4", version: "3.0.0" }\n', "twice"),
        ("tools:\n", "no tools"),
        # Review round: ASCII digits only, and no output name may collide.
        ('tools:\n  helm: { version: "\u0663.18.4", sites: 6 }\n', "not a dotted number"),
        (f'tools:\n  helm: {{ version: "3.18.4", sha256: "{SHA}" }}\n  helm-sha256: {{ version: "1.0" }}\n', "collides"),
        (f'tools:\n  helm-sha256: {{ version: "1.0" }}\n  helm: {{ version: "3.18.4", sha256: "{SHA}" }}\n', "collides"),
    ],
)
def test_a_malformed_file_is_refused(tmp_path, body, needle):
    path = write(tmp_path, body)
    with pytest.raises(tool_versions.ReadError, match=needle):
        tool_versions.read(path)
    proc = run(path)
    assert proc.returncode == 1
    assert proc.stdout == "", "nothing may reach $GITHUB_OUTPUT from a refused file"
    assert "::error::" in proc.stderr


def test_a_missing_file_is_refused(tmp_path):
    proc = run(tmp_path / "absent.yaml")
    assert proc.returncode == 1 and proc.stdout == "" and "::error::" in proc.stderr
