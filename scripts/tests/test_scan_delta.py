"""What `scan_delta.py` asserts, pinned so it cannot quietly stop asserting it.

THIS FILE EXISTS BECAUSE ITS ABSENCE SHIPPED TWO HOLES. The gate's behaviour was
stated only in a pull-request description, and the three cases that description
listed were the three well-formed ones — exactly the paths that already worked.
The degenerate path was never written down and was green when it should have
been red. A gate whose behaviour lives in prose is a gate nobody re-runs.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "scan_delta.py"


def report(tmp_path: Path, name: str, rows, artifact="an-image"):
    """A trivy-shaped report. Rows are (type, package, id) triples."""
    doc = {
        "ArtifactName": artifact,
        "Results": [
            {
                "Type": kind,
                "Vulnerabilities": [{"VulnerabilityID": vid, "PkgName": pkg}],
            }
            for kind, pkg, vid in rows
        ],
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc))
    return str(path)


def run(base, image):
    return subprocess.run(
        [sys.executable, str(GATE), base, image], capture_output=True, text=True
    )


BASE_ROWS = [("gobinary", "stdlib", "CVE-1"), ("gobinary", "x/net", "CVE-2")]


def test_identical_is_green(tmp_path):
    b = report(tmp_path, "b", BASE_ROWS)
    i = report(tmp_path, "i", BASE_ROWS)
    assert run(b, i).returncode == 0


def test_our_layers_adding_one_is_red_and_names_it(tmp_path):
    b = report(tmp_path, "b", BASE_ROWS)
    i = report(tmp_path, "i", BASE_ROWS + [("os-pkgs", "libssl-dev", "CVE-9")])
    result = run(b, i)
    assert result.returncode == 1
    assert "CVE-9" in result.stderr
    assert "libssl-dev" in result.stderr


def test_upstream_fixing_one_is_green(tmp_path):
    """An improvement must not red the build. This is why it is not a subset check."""
    b = report(tmp_path, "b", BASE_ROWS)
    i = report(tmp_path, "i", BASE_ROWS[:1])
    assert run(b, i).returncode == 0


def test_an_empty_image_report_is_red(tmp_path):
    """The hole this file was written for: a vacuous comparison read as success."""
    b = report(tmp_path, "b", BASE_ROWS)
    for name, doc in (
        ("no-results", {"ArtifactName": "x", "Results": []}),
        ("no-key", {"ArtifactName": "x"}),
        ("null-vulns", {"ArtifactName": "x", "Results": [{"Vulnerabilities": None}]}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(doc))
        result = run(b, str(path))
        assert result.returncode == 1, f"{name} was green"
        assert "EMPTY" in result.stderr


def test_both_empty_is_green(tmp_path):
    """Nothing to compare is not a failure — only an empty image against a base is."""
    empty = {"ArtifactName": "x", "Results": []}
    for name in ("b", "i"):
        (tmp_path / f"{name}.json").write_text(json.dumps(empty))
    assert run(str(tmp_path / "b.json"), str(tmp_path / "i.json")).returncode == 0


# ONE COMPONENT PER TEST, AND THAT IS THE WHOLE POINT OF SPLITTING THEM. The
# first version of this varied Type AND PkgName together, so it was killed by the
# Type component alone and pinned PkgName not at all — a mutant that drops
# PkgName from the key passed all seven tests. A test that moves two variables
# proves only that at least one of them matters.


def test_same_id_same_type_different_package_is_red(tmp_path):
    """Isolates PkgName: Type is held constant.

    The real case, from this repository's own Containerfile: the base carries a
    CVE against `libssl3`, and `apt-get install libssl-dev` brings the same CVE
    against a second binary package built from the same source. Routine on
    Ubuntu, and a genuine addition by our layers.
    """
    b = report(tmp_path, "b", [("os-pkgs", "libssl3", "CVE-1")])
    i = report(tmp_path, "i", [("os-pkgs", "libssl3", "CVE-1"), ("os-pkgs", "libssl-dev", "CVE-1")])
    result = run(b, i)
    assert result.returncode == 1
    assert "libssl-dev" in result.stderr


def test_same_id_same_package_different_type_is_red(tmp_path):
    """Isolates Type: PkgName is held constant."""
    b = report(tmp_path, "b", [("gobinary", "stdlib", "CVE-1")])
    i = report(tmp_path, "i", [("gobinary", "stdlib", "CVE-1"), ("os-pkgs", "stdlib", "CVE-1")])
    assert run(b, i).returncode == 1


def test_a_missing_report_is_loud(tmp_path):
    b = report(tmp_path, "b", BASE_ROWS)
    assert run(b, str(tmp_path / "absent.json")).returncode not in (0,)
