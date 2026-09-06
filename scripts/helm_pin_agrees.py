#!/usr/bin/env python3
"""Every `azure/setup-helm` step under `.github/workflows/` installs the same helm.

THE PIN IS A CONSTANT WITH THREE COPIES, and until this file nothing compared
them. Ledger 635 set all three to `v3.18.4`; nothing kept them set. A pin that
one hand can move is a pin the estate does not have, and the failure it lets
back in is the one that was already measured: in `yadgarhq/iam` run 33968099875
the `precommit` job installed helm v4.2.4 while `portability` installed v3.18.4
— two helm MAJORS inside one CI run, so any check whose result depends on helm
was not reproducible. The reasoning for the number itself lives above the
`precommit` copy in `ci-pr.yaml`: v3.18.4 is what Argo CD v3.1.8 bundles, so the
linter and the renderer are the same program.

IT PARSES THE WORKFLOWS RATHER THAN GREPPING THE LITERAL, and that is the whole
design. `v3.18.4` appears about seven times in PROSE in these files — the
comments that explain the choice — against three times as a step input. A grep
gate passes vacuously when the comments agree and reddens pointing at a comment
when somebody bumps the steps, which is worse than no gate. So this walks
`jobs.<id>.steps[]` and reads `with.version` off the steps that actually install
helm.

IT REFUSES A COMPARISON IT CANNOT MAKE. Fewer than two sites means there is
nothing to disagree, and a gate that cannot fail is the defect this estate has
now measured six times — `one_source_per_knob.py` in `yadgarhq/config` names the
same failure in its own words ("a glob that matches nothing makes this whole
gate a check that cannot fail"). A step with no `version:` input is refused for
a second reason on top: with the input absent the action asks get.helm.sh for
the latest release AT RUN TIME and falls back to a hardcoded version when the
fetch fails, so the installed helm is decided by the network. That is the
original defect, not a neutral default.

EVERY `*.yaml` AND `*.yml` UNDER `.github/workflows/`, with nothing excluded —
and that directory is the whole of its reach, which is why the first line above
names it rather than saying "this repository". A composite action or a hook
could install helm without this noticing. Neither does today and the three
sites are all workflow steps, so widening the glob would be reach this gate has
no subject for; the honest fix if that changes is to widen it then.
`base-images.yaml` and `estate-runner-image.yaml` install no helm today, and
reading them is how this notices the day one of them does. An exclusion list
would put the blind spot exactly where the next helm step lands.

WHAT IT DOES NOT CHECK, said here rather than implied: the action's own SHA pin.
That is a second constant across the same three steps and the comment in
`ci-pr.yaml` treats the two as a pair, but it is Dependabot's to move rather
than a person's, and reddening this repository's own update pull request is not
what this gate is for. It also does not check that v3.18.4 is still Argo's helm
— that is the maintenance trigger recorded beside the pin, and it is a question
for a human.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import pathlib
import sys

import yaml

WORKFLOWS = pathlib.Path(".github/workflows")

# The action, matched on its OWNER/NAME and never on the whole `uses:` string,
# which carries the SHA pin and a version comment after it.
ACTION = "azure/setup-helm"

# A comparison needs two things to compare. This is a floor rather than the
# current count of three: a fourth site is a legitimate change and must not
# redden, while dropping to one silently turns this file into a no-op.
MINIMUM_SITES = 2


def steps_of(document):
    """Every step in a parsed workflow, as (job id, index, step)."""
    jobs = (document or {}).get("jobs")
    if not isinstance(jobs, dict):
        return
    for job_id, job in jobs.items():
        steps = (job or {}).get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if isinstance(step, dict):
                yield job_id, index, step


def helm_sites(paths):
    """Every `azure/setup-helm` step found, as (where, version or None)."""
    sites = []
    for path in paths:
        document = yaml.safe_load(path.read_text())
        for job_id, index, step in steps_of(document):
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            if uses.split("@", 1)[0].strip() != ACTION:
                continue
            with_ = step.get("with")
            version = with_.get("version") if isinstance(with_, dict) else None
            where = f"{path}: job `{job_id}`, step {index + 1}"
            sites.append((where, version if isinstance(version, str) else None))
    return sites


def main() -> int:
    if not WORKFLOWS.is_dir():
        print(f"::error::{WORKFLOWS} does not exist")
        return 1

    paths = sorted(
        p for p in WORKFLOWS.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()
    )
    try:
        sites = helm_sites(paths)
    except yaml.YAMLError as error:
        print(f"::error::a workflow under {WORKFLOWS} is not a YAML document: {error}")
        return 1

    if len(sites) < MINIMUM_SITES:
        print(
            f"::error::found {len(sites)} `{ACTION}` step(s) under {WORKFLOWS}, and "
            f"{MINIMUM_SITES} is the fewest this can compare. This gate exists "
            "because the helm pin is one constant with several copies; with one "
            "copy or none it reports success having checked nothing, which is the "
            "failure it was written to stop. If the pin genuinely moved to one "
            "site, delete this gate rather than leaving it green and blind."
        )
        return 1

    unpinned = [where for where, version in sites if not version]
    if unpinned:
        for where in unpinned:
            print(
                f"::error::{where} — `{ACTION}` with no `version:` input. The action "
                "then asks get.helm.sh for the latest release at run time and falls "
                "back to a hardcoded version when the fetch fails, so the helm this "
                "job renders with is decided by the network rather than by this "
                "repository. Pin it to the same version as the other sites."
            )
        return 1

    versions = {version for _, version in sites}
    if len(versions) > 1:
        print(
            f"::error::the {len(sites)} `{ACTION}` steps do not agree: "
            + "; ".join(f"{where} installs {version}" for where, version in sites)
            + ". These render and lint the same charts, so a disagreement puts two "
            "helms in one CI run and no check that depends on helm is reproducible. "
            "The number is Argo CD's bundled helm — see the reasoning beside the "
            "`precommit` pin in `ci-pr.yaml` — and all of them move together or "
            "none of them do."
        )
        return 1

    pinned = versions.pop()
    print(
        f"{len(sites)} `{ACTION}` steps, all pinned to {pinned}: "
        + "; ".join(where for where, _ in sites)
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
