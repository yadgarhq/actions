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
