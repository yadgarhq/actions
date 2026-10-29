#!/usr/bin/env python3
"""K-8's order, enforced in the consumer rather than discovered on the parent's
main (C-A3).

`parent_bump.py` pins every module and platform release into `yadgarhq/chart`
main WITH NO PR CI (K-8: `parent_bump.py:20-33` writes one commit through the
git-data API; no clone, render, test or PR runs over it). A release that makes
a chart key required therefore breaks the parent's own renders -- its tests,
its `example/`, its `chart/ci/` fixture and the bare `helm lint --strict` sites
-- the moment it is pinned, unless the parent's fixture already sets the key
first. ADR-0835(b) put the same shape in `yadgarhq/platform`'s own PR CI for a
different automated pin (the Argo sync-timeout floor, cut by the same
`parent_bump.py`): a guard in the CONSUMER's own CI that asserts what the pin
is about to do, so a deadline change -- or here, a chart contract -- fails
before the pin runs rather than after.

WHAT THIS DOES. `ci-pr.yaml`'s `pre_pin_parent` job checks out `yadgarhq/chart`
at `main` and resolves ITS dependencies (a literal `helm dependency update
chart` step, ADR-0725 -- that is what populates `charts/` with the nine real
published pins). This script then PACKAGES the chart in THIS working tree --
the candidate pull request's own `chart/`, not what is published -- at the
exact version the parent already carries for it, and copies that tarball over
the one `helm dependency update` just fetched. Helm resolves `charts/` by
FILENAME, `<name>-<version>.tgz`, so the parent's next render reads the
candidate's SOURCE TREE under the pin it will be released at, with no second
fetch. Then it lints and templates the parent exactly as `helm_lint.py`
lints and templates a chart: `--strict` lint first, then `template`, both with
the parent's own `chart/ci/values.yaml` override when the parent carries one
(C-A2) and the parent's own declared API versions on the render (ADR-0806).

WHAT A REFUSAL MEANS, AND WHY THE MESSAGE SAYS SO. The parent's fixture is a
SEPARATE chart PR (K-8 step 2) that has to land before the module's contract
(K-8 step 3) -- C-A2's gate change made that order self-enforcing on
`yadgarhq/chart` main itself, and this job makes it self-enforcing in the
CONSUMER, before the pin. A refusal here is read the same way either way: the
candidate's own chart requires something the parent fixture does not yet set.
The failure prints the raw helm output AND names the order explicitly, because
the two repairs -- "the parent fixture has to land first" and "this chart has
a real bug" -- look identical in helm's own text and only the first is this
job's whole reason to exist.

THREE WAYS THIS NO-OPS, EACH NARRATED RATHER THAN SILENT (ledger 731's
discipline: a check that declines to run says so, as a fact about the tree,
never as quiet success):

  1. `github.repository == 'yadgarhq/chart'` -- the job itself is skipped in
     `ci-pr.yaml`'s `if:`, so this script never runs there. Nothing to vendor:
     the parent is not vendored into itself.
  2. No `chart/` in this working tree -- this repository renders no chart, so
     there is nothing to package.
  3. This chart's own `name:` is not among `yadgarhq/chart`'s `dependencies:`
     yet -- a chart-bearing repository the parent has not onboarded. Nothing
     to vendor until it is.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from api_versions import DeclarationError, api_version_flags
from chart_values_override import values_override_flags
from parent_pin import Refusal, pins

CANDIDATE = Path(os.environ.get("PRE_PIN_CANDIDATE_CHART", "chart"))
PARENT = Path(os.environ.get("PRE_PIN_PARENT_CHART", ".pre-pin-parent/chart"))


def chart_name(chart_directory: Path) -> str:
    """The `name:` a chart's own `Chart.yaml` declares."""
    document = yaml.safe_load((chart_directory / "Chart.yaml").read_text()) or {}
    name = document.get("name")
    if not isinstance(name, str) or not name:
        raise SystemExit(f"{chart_directory / 'Chart.yaml'} carries no `name:`.")
    return name


def helm(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["helm", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=["helm", *args], returncode=127, stdout="", stderr="helm: command not found\n"
        )


def package_candidate(chart_directory: Path, version: str, destination: Path) -> Path:
    """`helm package <chart_directory> -u --version <version>` into `destination`.

    `-u` resolves the CANDIDATE's own dependencies first (ADR-0725) -- every
    module chart today has none, so this is a no-op for them, but a future
    chart with its own subcharts is packaged correctly rather than refused.
    """
    name = chart_name(chart_directory)
    result = helm(
        "package", str(chart_directory), "-u",
        "--version", version, "--app-version", version,
        "-d", str(destination),
    )
    if result.returncode != 0:
        raise SystemExit(
            f"`helm package {chart_directory} -u --version {version}` failed:\n"
            f"{result.stdout}{result.stderr}"
        )
    tarball = destination / f"{name}-{version}.tgz"
    if not tarball.is_file():
        raise SystemExit(
            f"expected {tarball} after packaging; found "
            f"{sorted(path.name for path in destination.iterdir())}"
        )
    return tarball


def vendor(parent_chart: Path, name: str, version: str, tarball: Path) -> Path:
    """Copy `tarball` over `<parent_chart>/charts/<name>-<version>.tgz`.

    Helm matches `charts/` by name and version -- the same technique
    `test_parent_chart.py`'s `parent_at()` uses to render a chart being cut
    before it is published. `helm dependency update` (the workflow step run
    before this script) already created `charts/` while fetching the real
    published pin; this overwrites that one file.
    """
    charts_directory = parent_chart / "charts"
    charts_directory.mkdir(exist_ok=True)
    destination = charts_directory / f"{name}-{version}.tgz"
    shutil.copyfile(tarball, destination)
    return destination


def example_values(parent_chart: Path) -> Path | None:
    """The parent's `example/values.yaml`, a sibling of its `chart/` (yaadgaar 345)."""
    path = parent_chart.parent / "example" / "values.yaml"
    return path if path.is_file() else None


PLACEHOLDER_ADMINISTRATOR = "REPLACE-WITH-A-UUIDV7-YOU-MINT"
CI_ADMINISTRATOR = "01900000-0000-7000-8000-000000000001"


def render_parent(values_flags: list[str], template_flags: list[str]):
    """Lint, template with the CI values, then template with the example values.

    Returns None when all pass, else `(verb, label, result)` of the first
    refusal, `label` naming the example file when that is what refused.

    The example is rendered from a TEMP COPY with chart's adopter placeholder
    replaced by a fixed uuidv7 (chart's own `_adopter_values_as_replaced`): the
    seeder render-check refuses the placeholder by design, so the shipped file
    can never render as is. The checkout is not touched.
    """
    lint = helm("lint", "--strict", str(PARENT), *values_flags)
    if lint.returncode != 0:
        return ("lint", "", lint)
    template = helm("template", "ci-render", str(PARENT), *template_flags, *values_flags)
    if template.returncode != 0:
        return ("template", "", template)
    example = example_values(PARENT)
    if example is not None:
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "example" / "values.yaml"
            copy.parent.mkdir()
            copy.write_text(example.read_text().replace(PLACEHOLDER_ADMINISTRATOR, CI_ADMINISTRATOR))
            rendered = helm("template", "ci-render-example", str(PARENT), *template_flags, "-f", str(copy))
        if rendered.returncode != 0:
            return ("template", str(example), rendered)
    return None


def parent_sha() -> str:
    """The parent checkout's head: `PARENT_SHA` from the workflow, else git."""
    given = os.environ.get("PARENT_SHA", "").strip()
    if given:
        return given
    try:
        done = subprocess.run(
            ["git", "-C", str(PARENT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return "an unknown commit"
    return done.stdout.strip() or "an unknown commit"


def baseline_message(sha: str, result: subprocess.CompletedProcess) -> str:
    """PURE: parent main is red before anything of this pull request is vendored."""
    return (
        f"yadgarhq/chart main is red at {sha} before this pull request's chart was "
        f"vendored; not this PR's K-8 order. Fix yadgarhq/chart main, then re-run this job.\n\n"
        f"{result.stdout}{result.stderr}"
    )


def failure_message(name: str, version: str, verb: str, result: subprocess.CompletedProcess, example: str = "") -> str:
    """PURE: the sentence a refused `lint`/`template` prints, K-8's order named.

    Both the green test and the red test call this, and the red test asserts
    the sentence rather than a substring (the preamble's own rule): a helm
    error and a K-8 violation read identically in helm's own text, so this is
    the one place that tells a reviewer which repair applies.
    """
    where = f" using {example}" if example else ""
    step2 = (
        f"{example} is a parent render too: K-8 step 2 puts the key there as well. "
        if example else ""
    )
    return (
        f"yadgarhq/chart main refuses to {verb} with {name!r} vendored at {version}{where}, "
        f"the pin yadgarhq/chart's own Chart.yaml already carries for it:\n\n"
        f"{result.stdout}{result.stderr}\n"
        f"{step2}"
        f"If this pull request makes a chart key required with no default, the "
        f"yadgarhq/chart fixture -- chart/ci/values.yaml, example/ values, and the "
        f"parent chart test -- has to land in yadgarhq/chart FIRST (K-8 order). "
        f"Land that fixture, then re-run this pull request; this job is what K-3's "
        f"order rule does not enforce on its own."
    )


def main() -> int:
    if not CANDIDATE.is_dir():
        print(
            f"pre-pin-parent: no {CANDIDATE} in this repository, so it renders no "
            f"chart and there is nothing to vendor into yadgarhq/chart."
        )
        return 0

    if not PARENT.is_dir():
        print(f"::error::{PARENT} is missing; the yadgarhq/chart checkout did not land where this job expects it.")
        return 1

    name = chart_name(CANDIDATE)
    parent_chart_yaml = PARENT / "Chart.yaml"
    try:
        parent_pins = pins(parent_chart_yaml.read_text())
    except Refusal as error:
        # NOTHING INSPECTED MUST NEVER BE EXIT 0 (parent_pin.py's own rule,
        # carried here): a parent chart with no `dependencies:` at all, or an
        # unreadable one, is not "nothing to vendor" -- it is the vendored
        # checkout not landing where this job expects it, and that is loud.
        print(f"::error::cannot read yadgarhq/chart's pins at {parent_chart_yaml}: {error}")
        return 1
    pin = parent_pins.get(name)
    if pin is None:
        print(
            f"pre-pin-parent: {name!r} is not among yadgarhq/chart's "
            f"`dependencies:` yet, so there is nothing to vendor."
        )
        return 0

    values_flags = values_override_flags(PARENT)
    try:
        template_flags = api_version_flags(PARENT)
    except DeclarationError as error:
        print(f"::error::{error}")
        return 1

    # BASELINE FIRST (yaadgaar 342): the parent exactly as checked out. If main
    # is already red (a bad pin written by `parent_bump.py` with no PR CI), say
    # so rather than blame this pull request's K-8 order.
    baseline_red = render_parent(values_flags, template_flags)

    # A red baseline does NOT stop here: a chart PR that repairs a red parent
    # main would deadlock otherwise. Vendor and render again.
    with tempfile.TemporaryDirectory() as workspace:
        tarball = package_candidate(CANDIDATE, pin, Path(workspace))
        vendor(PARENT, name, pin, tarball)

    red = render_parent(values_flags, template_flags)
    if red is not None:
        verb, label, result = red
        if baseline_red is not None:
            # Both red: name the pre-existing red, then ALSO the vendored
            # result, so a second failure this PR adds is not hidden.
            print(f"::error::{baseline_message(parent_sha(), baseline_red[2])}")
            print(f"::error::{failure_message(name, pin, verb, result, label)}")
        else:
            print(f"::error::{failure_message(name, pin, verb, result, label)}")
        return result.returncode or 1
    if baseline_red is not None:
        print(
            f"::warning::yadgarhq/chart main is red at {parent_sha()} before this pull "
            f"request's chart was vendored; this pull request repairs it ({name!r} "
            f"vendored at {pin} renders and lints)."
        )
        return 0

    print(
        f"pre-pin-parent: yadgarhq/chart main renders and lints with {name!r} "
        f"vendored at {pin}, the pin it already carries."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
