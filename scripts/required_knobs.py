#!/usr/bin/env python3
"""YAADGAAR 571 (ADR-1143 decision 7): generate the REQUIRED list the gate reads.

`hooks/chart_required_defaults.py` narrows arm (a) by `hooks/data/required-knobs.json`.
This script writes that file from the per-chart tables of yadgarhq/docs
`plans/adr-0705-knob-classification.md`, the list ADR-1143 decision 1 makes binding:

    python3 scripts/required_knobs.py --plan <docs>/plans/adr-0705-knob-classification.md \
        --commit <docs sha> > hooks/data/required-knobs.json

A row whose class begins `R-` (R-a, R-b, R-c, with or without `(HARD)`) is REQUIRED.
A row whose class is `OK` or `OK (HARD)` is DEFAULT-OK. Any other class, a key that is
not a plain dotted key, a key listed twice in one table, or a table this script does
not know is an error. A `|` line that does not match the row form (a key in
backticks, then a class) is skipped, so its key is unclassified, which the gate
counts as REQUIRED (fail closed). A grouped row, `registry (roots, teams)`,
expands to `registry.roots` and `registry.teams`.

Only rows under `## Per-chart tables` are read, up to the next `## ` heading. The
plan's `### chart` table is the parent chart, whose Chart.yaml says `name: yadgar`.

`scripts/tests/test_required_knobs.py` holds the shipped file equal to a vendored
copy of those tables. ADR-1143's consequence: a plan row change and this file change
in the same pull request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SECTIONS = ("gateway", "iam", "iam-db", "task", "task-db", "project", "project-db", "platform", "chart", "seeder")
SECTION_TO_CHART = {"chart": "yadgar"}
START = "## Per-chart tables"
ROW = re.compile(r"^\|\s*`(?P<key>[^`]+)`\s*\|\s*(?P<klass>[^|]*?)\s*\|")
REQUIRED = re.compile(r"^R-[abc](?: \(HARD\))?$")
DEFAULT_OK = re.compile(r"^OK(?: \(HARD\))?$")
DOTTED_KEY = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")
GROUP = re.compile(r"^(?P<prefix>[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*) \((?P<names>[^()]+)\)$")


def rows(text: str) -> list:
    """(section, key as written, class) for every row of the per-chart tables."""
    found, section, inside = [], None, False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            inside = line.strip() == START
            section = None
            continue
        if not inside:
            continue
        if line.startswith("### "):
            section = line[4:].strip()
            if section not in SECTIONS:
                raise ValueError(f"line {number}: `### {section}` is not a section this script knows")
            continue
        match = ROW.match(line)
        if match is None:
            continue
        if section is None:
            raise ValueError(f"line {number}: a row before any `### <chart>` heading")
        found.append((section, match.group("key").strip(), match.group("klass").strip()))
    return found


def keys(written: str) -> list:
    group = GROUP.match(written)
    if group:
        return [f"{group.group('prefix')}.{name.strip()}" for name in group.group("names").split(",")]
    return [written]


def knobs(text: str) -> dict:
    """{chart name: {"required": [...], "default_ok": [...]}}, sorted. Raises ValueError."""
    charts: dict = {}
    every = rows(text)
    if not every:
        raise ValueError("no rows under `## Per-chart tables`")
    for section, written, klass in every:
        if REQUIRED.match(klass):
            kind = "required"
        elif DEFAULT_OK.match(klass):
            kind = "default_ok"
        else:
            raise ValueError(f"{section}: `{written}` has class `{klass}`, neither R-a/R-b/R-c nor OK")
        entry = charts.setdefault(SECTION_TO_CHART.get(section, section), {"required": [], "default_ok": []})
        for key in keys(written):
            if not DOTTED_KEY.match(key):
                raise ValueError(f"{section}: `{key}` is not a dotted key")
            if key in entry["required"] or key in entry["default_ok"]:
                raise ValueError(f"{section}: `{key}` is listed twice")
            entry[kind].append(key)
    return {name: {kind: sorted(entry[kind]) for kind in ("required", "default_ok")} for name, entry in sorted(charts.items())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--plan", required=True, help="docs plans/adr-0705-knob-classification.md")
    parser.add_argument("--commit", required=True, help="the yadgarhq/docs commit the plan was read at")
    arguments = parser.parse_args(argv)
    raw = Path(arguments.plan).read_bytes()
    try:
        charts = knobs(raw.decode("utf-8"))
    except ValueError as error:
        print(f"required_knobs: {error}", file=sys.stderr)
        return 1
    document = {
        "source": {
            "repository": "yadgarhq/docs",
            "path": "plans/adr-0705-knob-classification.md",
            "commit": arguments.commit,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "charts": charts,
    }
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
