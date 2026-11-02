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

THEN THE PARENT'S OWN PYTEST (yaadgaar 536). With the candidate vendored and
both renders green, `python3 -m pytest scripts/tests/` runs in the parent
checkout's root, the command `yadgarhq/chart`'s own CI runs, with four changes:

  * ISOLATED (`PYTEST_ISOLATION`): `-c /dev/null --rootdir=. --confcutdir=.
    -p no:cacheprovider`, and no `PYTHONPATH` or `PYTEST_*` from the job. The
    parent checkout sits INSIDE the pull request's working tree, and the parent
    carries no pytest config, so without these a `pytest.ini` or `conftest.py`
    at the pull request's root became the rootdir and could force the run green.
  * MINUS A NAMED DESELECT LIST (Max's ruling, 2026-11-03, option (a)):
    `LITERAL_COUNT_TESTS` pin object counts and sets as literals that the
    parent moves AFTER a pin (its `ci.yaml`), so a module PR that adds one
    object could never land if they blocked. `PUBLISHED_PIN_TESTS` render the
    `packaged` fixture, whose `helm package -u` re-pulls every dependency from
    GHCR and so tests the published pin, not the vendored candidate. Each id is
    matched EXACTLY (or with its `[params]`); a listed id the parent no longer
    collects, or a deselect that would also catch an unlisted test, refuses.
  * ATTRIBUTED AGAINST PARENT MAIN: only when the vendored run has failures,
    the published tarball is put back and the same selection runs on parent
    main as checked out. A failure on both is named as parent main's and does
    not refuse; a failure only after vendoring refuses and is named.
  * FAIL CLOSED: a missing `scripts/tests`, no pytest, no interpreter, nothing
    collected, a collection error, an exit other than 0 or 1, a failing run
    that names no test, or the whole budget `PARENT_TESTS_TIMEOUT_SECONDS`
    spent all refuse.

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
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


PARENT_TESTS_TIMEOUT_SECONDS = 20 * 60
"""Ceiling on ALL the parent's pytest runs together (collect, the vendored run,
and the parent-main run when that one is needed); the job's own
`timeout-minutes` is 30. Measured 2026-11-03 on yadgarhq/chart@c218688: one
full run is 680 tests in 4m48s on a laptop."""

PARENT_TEST_FILE = "scripts/tests/test_parent_chart.py"

LITERAL_COUNT_TESTS: dict[str, str] = {
    PARENT_TEST_FILE + "::test_global_image_and_labels_still_render": "len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_platform_enabled_false_removes_the_whole_platform_layer": "modules-only kinds == EXPECTED",
    PARENT_TEST_FILE + "::test_the_adopter_values_render_the_whole_platform_layer": "kinds == ADOPTER_EXPECTED",
    PARENT_TEST_FILE + "::test_an_object_added_to_the_platform_layer_reddens_the_object_set_gate": "object-set gate: ADOPTER_OBJECTS + 1",
    PARENT_TEST_FILE + "::test_dropping_the_second_preflight_probe_reddens_the_hook_job_and_triple_gates": "hook gate: ADOPTER_OBJECTS - OBJECTS_PER_PROBE_JOB",
    PARENT_TEST_FILE + "::test_the_rbac_triple_names_are_asserted_independently_of_the_job_count": "kinds == ADOPTER_EXPECTED",
    PARENT_TEST_FILE + "::test_a_vendored_job_that_lost_its_hook_annotation_reddens_the_hook_gate": "hook gate: len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_a_mutation_helm_never_reads_is_refused_rather_than_passing_silently": "len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_a_renamed_iam_keys_secret_with_the_toggle_off_still_renders_the_whole_estate": "len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_a_vendored_job_that_renamed_the_minted_secret_reddens_the_drift_guard": "len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_every_operator_key_stated_false_still_renders_the_whole_estate": "len(documents) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_the_refusals_are_satisfied_at_the_defaults_and_unreachable_without_the_platform": "len(render) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_k1_the_defaults_render_what_the_adopter_values_render": "k1: len(defaults) == ADOPTER_OBJECTS",
    PARENT_TEST_FILE + "::test_k1_a_reverted_default_reddens_the_equality_and_names_the_key": "k1: len(stated) == ADOPTER_OBJECTS",
}
"""Max's ruling 2026-11-03, option (a): tests that pin the parent's rendered
object counts or sets as LITERALS. yadgarhq/chart moves those literals after a
pin lands (its `ci.yaml` runs against the published pins), so a module PR that
adds, removes or renames one rendered object turns them red here and could
never land if they blocked. Measured by vendoring `config` 0.2.7 with one extra
ConfigMap on yadgarhq/chart@c218688: these 14 and
`test_the_directory_and_the_package_render_the_same_objects` (below) are the
15 tests that went red; nothing else did."""

PUBLISHED_PIN_TESTS: dict[str, str] = {
    PARENT_TEST_FILE + "::test_the_packaged_chart_renders_the_whole_estate": "packaged; also == ADOPTER_EXPECTED / EXPECTED",
    PARENT_TEST_FILE + "::test_the_directory_and_the_package_render_the_same_objects": "directory (candidate) vs packaged (published): false red on any identity change",
    PARENT_TEST_FILE + "::test_the_package_carries_every_subchart_so_an_adopter_needs_no_registry": "packaged",
    PARENT_TEST_FILE + "::test_an_adopter_value_lands_in_the_rendered_child_object": "packaged (its CHART half is lost too)",
    PARENT_TEST_FILE + "::test_a_value_for_one_child_does_not_reach_another": "packaged",
    PARENT_TEST_FILE + "::test_no_knob_is_stated_in_two_configmaps": "packaged: the cross-chart knob gate is blind to the candidate",
    PARENT_TEST_FILE + "::test_the_kind_example_on_heads_chart_probes_prometheus": "packaged",
}
"""Tests over chart's `packaged` fixture (`test_parent_chart.py`), which runs
`helm package chart -u`: `-u` re-pulls every dependency from GHCR over the
vendored candidate, so these test the PUBLISHED pin, never this pull request.
They cannot see the candidate, and the directory-versus-package comparison
reds falsely on any identity change. Deselected until chart's fixture packages
what `charts/` already holds (chart-side follow-up)."""

DESELECTED: dict[str, str] = {**LITERAL_COUNT_TESTS, **PUBLISHED_PIN_TESTS}

PYTEST_ISOLATION = ["-p", "no:cacheprovider", "--rootdir=.", "--confcutdir=.", "-c", os.devnull]
"""No ini file, rootdir and conftest search pinned to the parent checkout's
root, no cache writes. The parent checkout is `.pre-pin-parent/` INSIDE the
pull request's working tree and yadgarhq/chart carries no pytest config, so
without these pytest walks up into the pull request's own root and takes its
`pytest.ini` (addopts) and `conftest.py` (hooks) as the run's own."""

FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def failing_tests(result: subprocess.CompletedProcess) -> list[str]:
    """PURE: the test ids a `-rfE` summary names as FAILED or ERROR, in order."""
    return list(dict.fromkeys(FAILED_TEST.findall(f"{result.stdout}{result.stderr}")))


def listed_match(listed: str, nodeid: str) -> bool:
    """PURE: `nodeid` IS the listed test -- the same id, or it with `[params]`."""
    return nodeid == listed or nodeid.startswith(listed + "[")


def selection(collected: list[str], listed) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """PURE: `(deselect, stale, overreach)` for the collected ids.

    `deselect` is every collected id a listed id matches EXACTLY. `stale` is
    every listed id that matches nothing collected: a renamed or deleted parent
    test, which refuses rather than silently deselecting nothing. `overreach` is
    `(deselected, kept)` pairs where pytest's own `--deselect`, a nodeid PREFIX
    match, would also drop a test nobody listed (`test_x` swallows `test_x_y`).
    """
    listed = list(listed)
    deselect = [nodeid for nodeid in collected if any(listed_match(entry, nodeid) for entry in listed)]
    stale = [entry for entry in listed if not any(listed_match(entry, nodeid) for nodeid in collected)]
    dropped = set(deselect)
    overreach = [
        (gone, kept)
        for gone in deselect
        for kept in collected
        if kept not in dropped and kept.startswith(gone)
    ]
    return deselect, stale, overreach


def parent_tests_message(name: str, version: str, result: subprocess.CompletedProcess, failing: list[str] | None = None) -> str:
    """PURE: the sentence a red parent suite prints, the failing tests named."""
    output = f"{result.stdout}{result.stderr}"
    if failing is None:
        failing = failing_tests(result)
    named = "".join(f"\n  {test}" for test in failing) or "\n  (none named; read the output below)"
    return (
        f"yadgarhq/chart's own tests fail with {name!r} vendored at {version}, "
        f"the pin yadgarhq/chart's own Chart.yaml already carries for it "
        f"(exit {result.returncode}). Failing tests:{named}\n\n{output}"
    )


def pre_existing_message(sha: str, failing: list[str]) -> str:
    """PURE: parent main's own red tests, named as parent main's."""
    named = "".join(f"\n  {test}" for test in failing)
    return (
        f"yadgarhq/chart main's own tests already fail at {sha} with nothing of "
        f"this pull request vendored; not this PR's failures:{named}"
    )


class Budget:
    """One deadline across every pytest run of this job (`PARENT_TESTS_TIMEOUT_SECONDS`)."""

    def __init__(self) -> None:
        try:
            self.ceiling = int(os.environ.get("PRE_PIN_PARENT_TESTS_TIMEOUT", PARENT_TESTS_TIMEOUT_SECONDS))
        except ValueError:
            self.ceiling = PARENT_TESTS_TIMEOUT_SECONDS
        self.deadline = time.monotonic() + self.ceiling

    def remaining(self) -> float:
        return max(0.001, self.deadline - time.monotonic())


def pytest_in_parent(budget: Budget, *arguments: str) -> subprocess.CompletedProcess:
    """`python3 -m pytest <PYTEST_ISOLATION> scripts/tests/ <arguments>` in the
    parent checkout's root, the command yadgarhq/chart's own CI runs (`ci.yaml`).

    `PYTHONPATH` is dropped (the workflow points it at the staged gate, whose
    module names must not shadow the parent's own), and so is every `PYTEST_*`
    variable (`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`). Any failure to RUN -- no
    interpreter, no pytest, the budget spent -- comes back as a non-zero
    result, never an exception: nothing run is never a pass.
    """
    interpreter = os.environ.get("PRE_PIN_PARENT_PYTHON") or sys.executable
    environment = {
        key: value for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith("PYTEST_")
    }
    command = [interpreter, "-m", "pytest", *PYTEST_ISOLATION, "scripts/tests/", *arguments]
    try:
        return subprocess.run(
            command, cwd=str(PARENT.parent), env=environment,
            capture_output=True, text=True, check=False, timeout=budget.remaining(),
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", f"{interpreter}: command not found\n")
    except subprocess.TimeoutExpired as expired:
        partial = expired.stdout or ""
        partial = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
        return subprocess.CompletedProcess(
            command, 124, partial, f"pytest did not finish within {budget.ceiling} seconds\n"
        )


class Unusable(Exception):
    """A parent pytest run that proves nothing; carries the refusal text."""


def run_selection(budget: Budget, listed) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Collect, check the deselect list against what is collected, then run.

    Returns `(result, failing)` for a run that exited 0 or 1 and, on 1, named
    at least one failing test. Everything else raises `Unusable`.
    """
    collect = pytest_in_parent(budget, "--collect-only", "-q")
    if collect.returncode != 0:
        raise Unusable(
            f"yadgarhq/chart's own tests cannot be collected (exit {collect.returncode}):\n\n"
            f"{collect.stdout}{collect.stderr}"
        )
    collected = [line.strip() for line in collect.stdout.splitlines() if "::" in line]
    deselect, stale, overreach = selection(collected, listed)
    if stale:
        raise Unusable(
            "the deselect list in yadgarhq/actions `scripts/pre_pin_parent.py` names tests "
            "yadgarhq/chart no longer collects; update `LITERAL_COUNT_TESTS` / "
            "`PUBLISHED_PIN_TESTS` to the parent's current names:"
            + "".join(f"\n  {entry}" for entry in stale)
        )
    if overreach:
        raise Unusable(
            "deselecting the listed tests would also drop tests nobody listed "
            "(pytest's `--deselect` matches by prefix):"
            + "".join(f"\n  {gone} would drop {kept}" for gone, kept in overreach)
        )
    flags = [flag for nodeid in deselect for flag in ("--deselect", nodeid)]
    result = pytest_in_parent(budget, "-q", "-rfE", *flags)
    failing = failing_tests(result)
    if result.returncode not in (0, 1) or (result.returncode == 1 and not failing):
        raise Unusable(parent_tests_message_raw(result))
    return result, failing


def parent_tests_message_raw(result: subprocess.CompletedProcess) -> str:
    """PURE: a run that proves nothing, with its exit and output."""
    return (
        f"yadgarhq/chart's own tests did not run to a verdict (exit {result.returncode}); "
        f"a pin nothing tested is not a pin that passed.\n\n{result.stdout}{result.stderr}"
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
    # The published tarball's bytes are kept so parent main can be tested
    # again, unmodified, if the vendored run fails (attribution, below).
    published_path = PARENT / "charts" / f"{name}-{pin}.tgz"
    published = published_path.read_bytes() if published_path.is_file() else None
    with tempfile.TemporaryDirectory() as workspace:
        tarball = package_candidate(CANDIDATE, pin, Path(workspace))
        vendored = vendor(PARENT, name, pin, tarball)

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

    # THE PARENT'S OWN PYTEST (yaadgaar 536), with the candidate vendored: the
    # other half of what yadgarhq/chart's CI asks of a pin. Fail closed.
    if not (PARENT.parent / "scripts" / "tests").is_dir():
        print(
            f"::error::{PARENT.parent / 'scripts' / 'tests'} is missing; yadgarhq/chart's own "
            f"tests cannot run, and a pin nothing tested is not a pin that passed."
        )
        return 1
    budget = Budget()
    try:
        tests, failing = run_selection(budget, DESELECTED)
    except Unusable as error:
        print(f"::error::{error}")
        return 1
    if failing:
        # ATTRIBUTION (yaadgaar 536 fix 4): put the published tarball back and
        # run the same selection on parent main as checked out. Only a failure
        # that main does not already have is this pull request's.
        if published is None:
            # No published tarball to put back: parent main would render WITHOUT
            # this dependency, which is no baseline for it, so nothing is
            # excused (fail closed, the same as an unusable run).
            print(
                f"::warning::there is no published {vendored.name} in the parent's charts/ "
                f"to restore, so parent main cannot be run as a baseline and no failure is excused."
            )
            main_failing = []
        else:
            vendored.write_bytes(published)
            try:
                _, main_failing = run_selection(budget, DESELECTED)
            except Unusable as error:
                # Parent main proves nothing, so nothing is subtracted: every
                # vendored failure stays this pull request's (fail closed).
                print(f"::warning::parent main's own tests could not be run as a baseline, so no failure is excused:\n{error}")
                main_failing = []
        new = [test for test in failing if test not in main_failing]
        already = [test for test in failing if test in main_failing]
        if already:
            print(f"::warning::{pre_existing_message(parent_sha(), already)}")
        if new:
            print(f"::error::{parent_tests_message(name, pin, tests, new)}")
            return 1
    if baseline_red is not None:
        print(
            f"::warning::yadgarhq/chart main is red at {parent_sha()} before this pull "
            f"request's chart was vendored; this pull request repairs it ({name!r} "
            f"vendored at {pin} renders and lints, and yadgarhq/chart's own tests "
            f"add no failure)."
        )
        return 0

    print(
        f"pre-pin-parent: yadgarhq/chart main renders and lints with {name!r} "
        f"vendored at {pin}, the pin it already carries, and yadgarhq/chart's own tests "
        f"(minus {len(DESELECTED)} named literal-count and published-pin tests) add no failure."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
