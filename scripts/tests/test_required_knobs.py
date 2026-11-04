"""YAADGAAR 571 (ADR-1143 decision 7). The REQUIRED list the gate reads matches the plan.

`hooks/data/required-knobs.json` is the one machine-readable copy of the ADR-0705
classification in yadgarhq/docs `plans/adr-0705-knob-classification.md`. The gate
`chart-required-defaults` reads it to narrow arm (a). The plan lives in another
repository, so this suite holds the JSON equal to a VENDORED COPY of the plan's
per-chart tables (`fixtures/knob_classification/per-chart-tables.txt`, a verbatim
slice whose header names the docs commit and the plan's sha256). A plan change
re-vendors the slice and regenerates the JSON in the same pull request:

    python3 scripts/required_knobs.py --plan <docs>/plans/adr-0705-knob-classification.md \
        --commit <docs sha> > hooks/data/required-knobs.json

Tests: python3 -m pytest scripts/tests/test_required_knobs.py -q
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "hooks" / "data" / "required-knobs.json"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "knob_classification" / "per-chart-tables.txt"

sys.path.insert(0, str(REPO / "scripts"))
import required_knobs  # noqa: E402

sys.path.pop(0)


def header(text: str) -> dict:
    found = {}
    for line in text.splitlines():
        if not line.strip():
            break
        name, _, value = line.partition(": ")
        found[name] = value
    return found


# ------------------------------------------------------------------ the sync test


def test_the_shipped_list_is_generated_from_the_vendored_plan_rows():
    text = FIXTURE.read_text(encoding="utf-8")
    meta = header(text)
    expected = required_knobs.knobs(text)
    shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    assert shipped["charts"] == expected, "regenerate hooks/data/required-knobs.json from the plan"
    assert shipped["source"] == {
        "repository": meta["source-repository"],
        "path": meta["source-path"],
        "commit": meta["source-commit"],
        "sha256": meta["source-sha256"],
    }


def test_every_plan_row_is_accounted_for():
    # 652 rows in ten per-chart tables at docs 8c181a6. A row the parser cannot read
    # is an error, never a dropped row.
    rows = required_knobs.rows(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 652
    by_section = {}
    for section, _, _ in rows:
        by_section[section] = by_section.get(section, 0) + 1
    assert by_section == {
        "gateway": 86, "iam": 67, "iam-db": 63, "task": 46, "task-db": 63,
        "project": 46, "project-db": 63, "platform": 163, "chart": 30, "seeder": 25,
    }
    required = sum(1 for _, _, klass in rows if klass.startswith("R-"))
    assert required == 153


def test_the_parent_section_is_keyed_by_its_chart_name():
    # The plan's `### chart` table is the parent, whose Chart.yaml says `name: yadgar`.
    charts = required_knobs.knobs(FIXTURE.read_text(encoding="utf-8"))
    assert set(charts) == {
        "gateway", "iam", "iam-db", "task", "task-db", "project", "project-db", "platform", "yadgar", "seeder",
    }
    assert "global.hostname" in charts["yadgar"]["required"]


def test_a_hard_row_keeps_its_recommended_class():
    charts = required_knobs.knobs(FIXTURE.read_text(encoding="utf-8"))
    assert "image.repository" in charts["gateway"]["required"]  # R-c (HARD)
    assert "trustUnauthenticatedHeaders" in charts["gateway"]["default_ok"]  # OK (HARD)
    assert "nats.user" in charts["gateway"]["default_ok"]  # OK
    assert "projectValidation.mode" in charts["gateway"]["required"]  # R-a


def test_a_grouped_row_expands_to_each_key():
    charts = required_knobs.knobs(FIXTURE.read_text(encoding="utf-8"))
    for name in ("roots", "projects", "teams", "allowMassArchive", "allowMassRevoke"):
        assert f"registry.{name}" in charts["seeder"]["required"]


# ------------------------------------------------------------------ the parser refuses


TABLE = "## Per-chart tables\n\n### gateway\n\n| key | class | reason | today |\n| --- | --- | --- | --- |\n{row}\n"


@pytest.mark.parametrize(
    "row, why",
    [
        ("| `a.b` | MAYBE | r | t |", "class"),
        ("| `a.*` | R-a | r | t |", "dotted key"),
        ("| `a[0]` | OK | r | t |", "dotted key"),
        ("| `a.b` | R-a | r | t |\n| `a.b` | OK | r | t |", "twice"),
    ],
)
def test_a_row_the_parser_cannot_read_is_an_error(row, why):
    with pytest.raises(ValueError, match=why):
        required_knobs.knobs(TABLE.format(row=row))


def test_an_unknown_section_is_an_error():
    with pytest.raises(ValueError, match="section"):
        required_knobs.knobs("## Per-chart tables\n\n### nope\n\n| `a` | OK | r | t |\n")


def test_no_tables_is_an_error():
    with pytest.raises(ValueError, match="no rows"):
        required_knobs.knobs("# nothing here\n")


def test_rows_after_the_per_chart_tables_are_not_read():
    text = TABLE.format(row="| `a.b` | R-a | r | t |") + "\n## Knobs that would CHANGE\n\n| `a.b` | OK | r | t |\n"
    assert required_knobs.knobs(text) == {"gateway": {"required": ["a.b"], "default_ok": []}}
