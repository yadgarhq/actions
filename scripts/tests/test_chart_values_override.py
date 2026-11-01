"""What `chart_values_override.py` reads out of a chart's `ci/values.yaml`
(C-A2), pinned the way `test_api_versions.py` pins ADR-0806's declaration.

THE CONTRACT IS TWO-SIDED. A chart with no `ci/values.yaml` must yield NO flag
and leave a values dict it is handed UNCHANGED, because every gate that calls
this promises a byte-identical argv (or dict) to the one it produced before
this module existed. A chart WITH one must yield `-f <path>` and deep-merge the
override's keys into whatever dict it is handed, not replace it.

Run: python3 -m pytest scripts/tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chart_values_override import (  # noqa: E402
    OVERRIDE,
    extra_values_files,
    merged_values,
    values_override,
    values_override_flags,
)


def chart(tmp_path, override=None):
    directory = tmp_path / "chart"
    directory.mkdir()
    if override is not None:
        path = directory / OVERRIDE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(override)
    return directory


def test_the_override_lives_in_the_charts_own_ci_directory():
    """`ci/` inside the chart, beside `api-versions.txt` -- so a chart archived
    at another revision carries its own override with it, the same reason
    `service_immutable.py`'s base side relies on for the declaration."""
    assert OVERRIDE == Path("ci") / "values.yaml"


def test_no_override_file_means_no_path_and_no_flag(tmp_path):
    directory = chart(tmp_path)
    assert values_override(directory) is None
    assert values_override_flags(directory) == []


def test_an_override_file_is_found_and_flagged(tmp_path):
    directory = chart(tmp_path, "tls:\n  enabled: true\n")
    path = values_override(directory)
    assert path == directory / "ci" / "values.yaml"
    assert values_override_flags(directory) == ["-f", str(path)]


def test_merged_values_is_a_no_op_with_no_override(tmp_path):
    directory = chart(tmp_path)
    base = {"image": {"tag": "latest"}}
    assert merged_values(directory, base) is base
    assert base == {"image": {"tag": "latest"}}


def test_merged_values_deep_merges_the_override_over_the_base(tmp_path):
    directory = chart(tmp_path, "tls:\n  enabled: true\nimage:\n  tag: pinned\n")
    base = {"image": {"tag": "latest", "repository": "ghcr.io/x"}, "keda": {"enabled": False}}
    result = merged_values(directory, base)
    assert result is base
    assert base == {
        "image": {"tag": "pinned", "repository": "ghcr.io/x"},
        "keda": {"enabled": False},
        "tls": {"enabled": True},
    }


def test_merged_values_with_an_empty_override_file_changes_nothing(tmp_path):
    directory = chart(tmp_path, "# nothing yet\n")
    base = {"image": {"tag": "latest"}}
    merged_values(directory, base)
    assert base == {"image": {"tag": "latest"}}


# ----------------------------------------- extra scan passes (yaadgaar #179)


def test_extra_values_files_are_every_dash_values_file_sorted(tmp_path):
    """RULED 2026-11-01, option (a): every `ci/*-values.yaml` is an extra
    trivy pass. Sorted, so the pass order is the same on every run."""
    directory = chart(tmp_path, "{}\n")
    for name in ("zeta-values.yaml", "estate-values.yaml"):
        (directory / "ci" / name).write_text("{}\n")
    assert extra_values_files(directory) == [
        directory / "ci" / "estate-values.yaml",
        directory / "ci" / "zeta-values.yaml",
    ]


def test_extra_values_files_never_include_the_primary_or_other_files(tmp_path):
    """`ci/values.yaml` stays the primary file, not an extra pass; a name that
    is not `<something>-values.yaml` is not a pass either."""
    directory = chart(tmp_path, "{}\n")
    for name in ("api-versions.txt", "estatevalues.yaml", "estate-values.yml", "-values.yaml.bak"):
        (directory / "ci" / name).write_text("{}\n")
    assert extra_values_files(directory) == []


def test_extra_values_files_is_empty_with_no_ci_directory(tmp_path):
    assert extra_values_files(chart(tmp_path)) == []
