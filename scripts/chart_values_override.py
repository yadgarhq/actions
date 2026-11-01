"""The chart's own CI values override, `chart/ci/values.yaml` (C-A2).

WHY THIS EXISTS. ADR-0845 requires `*_TLS_ENABLED` and like keys with no
default, so a chart's `values.schema.json` can require the matching values key
with no default either. `helm lint --strict` validates every subchart's
schema against the coalesced values, so a shared gate that renders or lints
with NO values file refuses that chart's lint AND its render the day such a
key lands -- the gate would go red for a contract it was never told about.

ONE DECLARATION PER CHART REPOSITORY, READ BY EVERY SHARED GATE THAT RENDERS OR
LINTS THE CHART OFFLINE: the `helm-lint` hook, `d80_portability.py`,
`service_immutable.py` and `trivy_gate.py` (ledger 1150). This is the same
shape ADR-0806 already put behind `api_versions.py` for a chart's declared
operator API versions, so the four gates cannot drift apart on what values
the chart is rendered against either.

THE FILE is `chart/ci/values.yaml`, beside `chart/ci/api-versions.txt` in the
same directory for the same reason: `ci/` is the chart-testing (`ct`)
convention for "what this chart is exercised under" -- helm itself never reads
it, which is why every gate passes `-f` explicitly -- and the file sits INSIDE
the chart so a chart archived at another revision -- `service_immutable.py`'s
base side -- carries the override it had then (EACH SIDE READS ITS OWN
DECLARATION, the same rule `api_versions.py` documents for the base/HEAD
split).

A CHART WITH NO FILE GETS NO FLAG, and that is the whole backward-compatibility
argument: every gate appends `-f <path>` to the argv it already ran, so an
absent override leaves that argv byte-identical to what it was before this
module existed.

NO FORMAT VALIDATION HERE, unlike `api_versions.py`'s declaration. This file is
a values file in the ordinary helm sense -- the same thing `chart/values.yaml`
already is -- so a malformed one fails the same way a malformed
`chart/values.yaml` always has: a `yaml.YAMLError` out of whichever gate reads
it, uncaught, the same as today.

EXTRA SCAN PASSES, `chart/ci/*-values.yaml` (yaadgaar #179, ruled 2026-11-01,
option (a), the chart-testing convention). Each such file is one MORE values
set the chart is rendered under, ALONE -- never layered over `ci/values.yaml`,
because a chart like `platform` cannot render both shapes in one release
(ADR-0787's mixed-release guard). `ci/values.yaml` stays the primary file
above. ONLY `trivy_gate.py` reads the extra files today: `helm-lint`,
`d80_portability.py` and `service_immutable.py` keep the primary file alone.
The glob lives here, beside `OVERRIDE`, so there is still one reader per
declaration (ADR-0806, C-A2).

STANDARD LIBRARY ONLY at import time -- the `helm-lint` hook imports this
module and a `language: script` hook cannot install anything. `merged_values`
imports PyYAML lazily, inside the function, because only `d80_portability.py`
(which already depends on it) calls that one.
"""

from __future__ import annotations

from pathlib import Path

# Relative to the chart directory, never to the repository root: see above.
OVERRIDE = Path("ci") / "values.yaml"
EXTRA_GLOB = "*-values.yaml"


def values_override(chart_directory: Path) -> Path | None:
    """The `ci/values.yaml` override under `chart_directory`, or `None`."""
    path = Path(chart_directory) / OVERRIDE
    return path if path.is_file() else None


def extra_values_files(chart_directory: Path) -> list[Path]:
    """Every `ci/*-values.yaml` under `chart_directory`, sorted so the pass
    order is the same on every run; `[]` when there are none."""
    ci = Path(chart_directory) / OVERRIDE.parent
    return sorted(p for p in ci.glob(EXTRA_GLOB) if p.is_file())


def values_override_flags(chart_directory: Path) -> list[str]:
    """`-f <path>` for the override, or `[]` when the chart declares none."""
    path = values_override(chart_directory)
    return ["-f", str(path)] if path else []


def merged_values(chart_directory: Path, base: dict) -> dict:
    """`base` with the chart's override deep-merged on top; `base` unchanged
    (and returned as-is) when the chart declares no override.

    `base` is mutated in place, the same contract `flip_toggles` already has in
    `d80_portability.py` -- callers that need the pre-merge dict keep their own
    copy.
    """
    path = values_override(chart_directory)
    if not path:
        return base
    import yaml

    overlay = yaml.safe_load(path.read_text()) or {}
    _deep_merge(base, overlay)
    return base


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
