"""What `api_versions.py` reads out of a chart's declaration, pinned (ADR-0806).

THE CONTRACT IS TWO-SIDED and both sides are here. A chart with no
`ci/api-versions.txt` must yield NO flags at all, because every gate that calls
this promises a byte-identical argv to the one it ran before the declaration
existed. A chart WITH one must yield exactly its entries, in order, and a line
the reader cannot read as one API version must be refused by file and line
rather than passed to helm as a flag nobody meant.

Run: python3 -m pytest scripts/tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_versions import (  # noqa: E402
    DECLARATION,
    DeclarationError,
    api_version_flags,
    declared_api_versions,
)


def chart(tmp_path, declaration=None):
    directory = tmp_path / "chart"
    directory.mkdir()
    if declaration is not None:
        path = directory / DECLARATION
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(declaration)
    return directory


def test_the_declaration_lives_in_the_charts_own_ci_directory():
    """`ci/` inside the chart, so a chart archived at another revision carries
    its own declaration with it. `service_immutable.py` relies on that."""
    assert DECLARATION == Path("ci") / "api-versions.txt"


def test_no_declaration_means_no_flags(tmp_path):
    directory = chart(tmp_path)
    assert declared_api_versions(directory) == []
    assert api_version_flags(directory) == []


def test_each_entry_becomes_one_flag_in_file_order(tmp_path):
    directory = chart(
        tmp_path,
        "keda.sh/v1alpha1\ncert-manager.io/v1\n",
    )
    assert api_version_flags(directory) == [
        "--api-versions",
        "keda.sh/v1alpha1",
        "--api-versions",
        "cert-manager.io/v1",
    ]


def test_comments_and_blank_lines_are_not_entries(tmp_path):
    directory = chart(
        tmp_path,
        "# the operators the default render needs\n"
        "\n"
        "   keda.sh/v1alpha1   # KEDA, for autoscaling.enabled\n"
        "\t\n"
        "k8s.mariadb.com/v1alpha1\n",
    )
    assert declared_api_versions(directory) == [
        "keda.sh/v1alpha1",
        "k8s.mariadb.com/v1alpha1",
    ]


def test_a_kind_qualified_entry_is_refused_and_says_why(tmp_path):
    """helm accepts `group/version/Kind`, but the estate's `require-api` asks
    `.Capabilities.APIVersions.Has "<group/version>"`, and a Kind-form entry
    never satisfies that (measured on helm 3 and 4). Accepting it would be a
    declaration that declares nothing the checks read."""
    directory = chart(tmp_path, "keda.sh/v1alpha1/ScaledObject\n")
    with pytest.raises(DeclarationError) as raised:
        declared_api_versions(directory)
    message = str(raised.value)
    assert str(directory / DECLARATION) + ":1" in message
    assert "keda.sh/v1alpha1" in message
    assert "Has" in message


def test_a_byte_order_mark_is_not_part_of_the_first_entry(tmp_path):
    """An editor that writes UTF-8 with a BOM must not turn the first line into
    a refusal -- or, worse, into a string helm is handed verbatim."""
    directory = chart(tmp_path)
    path = directory / DECLARATION
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbfkeda.sh/v1alpha1\n")
    assert declared_api_versions(directory) == ["keda.sh/v1alpha1"]


def test_a_file_that_is_not_utf8_is_refused_by_file_not_by_traceback(tmp_path):
    directory = chart(tmp_path)
    path = directory / DECLARATION
    path.parent.mkdir(parents=True)
    path.write_bytes(b"keda.sh/v1alpha1\n\xff\xfe\n")
    with pytest.raises(DeclarationError) as raised:
        declared_api_versions(directory)
    message = str(raised.value)
    assert str(path) in message
    assert "UTF-8" in message


def test_an_empty_declaration_means_no_flags(tmp_path):
    directory = chart(tmp_path, "# nothing yet\n\n")
    assert api_version_flags(directory) == []


@pytest.mark.parametrize(
    "line",
    [
        "keda.sh/v1alpha1 cert-manager.io/v1",  # two on one line
        "keda",  # no version at all
        "--api-versions keda.sh/v1alpha1",  # a flag, not an entry
        "keda.sh/v1alpha1,cert-manager.io/v1",  # helm's comma form
    ],
)
def test_a_line_that_is_not_one_api_version_is_refused_by_file_and_line(
    tmp_path, line
):
    directory = chart(tmp_path, "cert-manager.io/v1\n" + line + "\n")
    with pytest.raises(DeclarationError) as raised:
        declared_api_versions(directory)
    message = str(raised.value)
    assert str(directory / DECLARATION) + ":2" in message
    assert line.strip() in message
