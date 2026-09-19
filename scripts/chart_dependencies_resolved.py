#!/usr/bin/env python3
"""Every job that installs helm also resolves a chart's dependencies first.

ADR-0725, and it took two pull requests to close: `ci-release.yaml`'s `chart`
job (#83) and the three jobs in `ci-pr.yaml` this file was added alongside.
Both times the defect had the same shape — a job runs `azure/setup-helm` and
then invokes helm, with nothing resolving a dependency first. Every helm
subcommand these jobs use refuses outright on a chart with a `dependencies:`
key (measured against `yadgarhq/chart`'s parent, helm 3.18.4): "found in
Chart.yaml, but missing in charts/ directory: config, gateway, ...".

WHAT THIS CANNOT CHECK, said here rather than implied. In `precommit`, the
step that actually invokes helm is `pre-commit/action` — a `uses:` step whose
command lives in `.pre-commit-config.yaml`, a file this gate does not read.
In `portability` and `service_immutable`, the invoking step is
`python3 $RUNNER_TEMP/d80_portability.py` / `.../service_immutable.py` — helm
runs inside that script's own subprocess call, with no literal `helm` string
anywhere in the workflow YAML. So this gate cannot prove RESOLUTION PRECEDES
INVOCATION the way `no_build_cache.py` proves its property from the YAML
alone — that would need to read the pre-commit hook and both scripts too,
which a workflow-only parser cannot see. `service_immutable.py`'s OWN test
suite carries the ordering proof for its two render sites instead
(`test_a_dependency_declared_at_base_is_resolved`).

WHAT IT CAN AND DOES PROVE: no job installs helm and resolves NOTHING — the
exact shape of the defect this file exists to catch, since that is what
`ci-pr.yaml` did in three jobs until today. A future fourth `azure/setup-helm`
site with no resolution step anywhere in its own job reddens here rather than
shipping unnoticed a third time.

IT PARSES THE WORKFLOWS RATHER THAN GREPPING, for the reason
`helm_pin_agrees.py` gives at length: reading `step["run"]` strings alone
(never the YAML `#` comments beside them) is what keeps a passage of prose
explaining this very rule from being mistaken for the step it describes.
This walks `jobs.<id>.steps[]`, matching each step's `run:` line by line —
a `run:` block is shell, and every real call here is one command per line.

TWO SHAPES COUNT AS RESOLVING, matching the two this estate has written:
a standalone `helm dependency update <chart>` step, or `-u` /
`--dependency-update` on a `helm package` line — `ci-release.yaml`'s `chart`
job packages and resolves in the same command, so its resolution step and its
invoking step are the same step.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import pathlib
import re
import sys

import yaml

WORKFLOWS = pathlib.Path(".github/workflows")

# The action, matched on its OWNER/NAME and never on the whole `uses:` string,
# which carries the SHA pin and a version comment after it — same reasoning as
# `helm_pin_agrees.py`'s identical match.
SETUP_HELM = "azure/setup-helm"

# The two resolving shapes, each matched against ONE line of a `run:` block.
DEPENDENCY_UPDATE = re.compile(r"helm\s+dependency\s+update\b")
PACKAGE_WITH_UPDATE_FLAG = re.compile(r"helm\s+package\b.*(?:\s-u\b|\s--dependency-update\b)")

# A floor rather than the current count of four (the three jobs in
# `ci-pr.yaml` plus `ci-release.yaml`'s `chart` job): dropping below this
# silently turns the file into a no-op, the same reasoning `no_build_cache.py`
# and `helm_pin_agrees.py` give for their own floors.
MINIMUM_SITES = 2


def steps_of(document):
    """Every job's own step list, as (job id, steps)."""
    jobs = (document or {}).get("jobs")
    if not isinstance(jobs, dict):
        return
    for job_id, job in jobs.items():
        steps = (job or {}).get("steps")
        if not isinstance(steps, list):
            continue
        yield job_id, [step for step in steps if isinstance(step, dict)]


def installs_helm(steps) -> bool:
    return any(
        isinstance(step.get("uses"), str)
        and step["uses"].split("@", 1)[0].strip() == SETUP_HELM
        for step in steps
    )


def resolves_a_dependency(steps) -> bool:
    for step in steps:
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for line in run.splitlines():
            if DEPENDENCY_UPDATE.search(line) or PACKAGE_WITH_UPDATE_FLAG.search(line):
                return True
    return False


def helm_jobs(paths):
    """Every job installing helm, as (where, resolves-a-dependency)."""
    sites = []
    for path in paths:
        document = yaml.safe_load(path.read_text())
        for job_id, steps in steps_of(document):
            if not installs_helm(steps):
                continue
            where = f"{path}: job `{job_id}`"
            sites.append((where, resolves_a_dependency(steps)))
    return sites


def main() -> int:
    if not WORKFLOWS.is_dir():
        print(f"::error::{WORKFLOWS} does not exist")
        return 1

    paths = sorted(
        p for p in WORKFLOWS.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()
    )
    try:
        sites = helm_jobs(paths)
    except yaml.YAMLError as error:
        print(f"::error::a workflow under {WORKFLOWS} is not a YAML document: {error}")
        return 1

    if len(sites) < MINIMUM_SITES:
        print(
            f"::error::found {len(sites)} job(s) installing `{SETUP_HELM}` under "
            f"{WORKFLOWS}, and {MINIMUM_SITES} is the fewest this can inspect. "
            "This gate exists because a job that installs helm and resolves no "
            "chart dependency refuses outright on any chart with a "
            "`dependencies:` key (ADR-0725); with one job or none it reports "
            "success having inspected nothing, which is the failure it was "
            "written to stop. If helm genuinely stopped being installed this "
            "way, delete this gate rather than leaving it green and blind."
        )
        return 1

    unresolved = [where for where, resolved in sites if not resolved]
    if unresolved:
        for where in unresolved:
            print(
                f"::error::{where} — installs `{SETUP_HELM}` and no step in this "
                "job resolves a chart's dependencies (`helm dependency update` or "
                "`helm package ... -u`). ADR-0725: this is a no-op for a chart "
                "with no `dependencies:` key and a hard refusal for one that has "
                "them — `Error: found in Chart.yaml, but missing in charts/ "
                "directory: ...` — so a chart-bearing consumer of this job would "
                "fail outright. Add the resolution step before whatever in this "
                "job actually invokes helm."
            )
        return 1

    print(
        f"{len(sites)} job(s) install `{SETUP_HELM}`, all resolving a chart's "
        "dependencies first: " + "; ".join(where for where, _ in sites) + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
