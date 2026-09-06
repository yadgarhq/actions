#!/usr/bin/env python3
"""No `docker/build-push-action` step under `.github/workflows/` declares a layer cache.

LEDGER 715. `containers/rust-build/Containerfile` runs `apt-get upgrade -y` in
its apt layer, and that line is the whole of what makes the weekly cron in
`base-images.yaml` deliver the security refresh its own comment promises. The
`FROM` is digest-pinned, so a rebuild alone resolves identical bytes and moves
nothing; the upgrade is the only thing that moves.

IT WORKS ONLY BECAUSE THAT `RUN` GENUINELY RE-EXECUTES ON EVERY BUILD. Neither
publishing workflow sets `cache-from`/`cache-to`, and `docker/setup-buildx-action`
provisions a fresh builder per run, so there is no cache for the layer to hit.

THE CHANGE THIS REFUSES IS THE OBVIOUS ONE. Adding a GitHub Actions cache or a
registry cache — `cache-from: type=gha` and a `cache-to` beside it — is the
first thing anybody reaches for to speed a container build up, and it is a
one-line addition to a step that looks entirely routine. It turns that `RUN`
into a permanent cache hit and freezes `libexpat1`, `libpcre2` and `libssh2` at
whatever versions the cache holds.

NOTHING ELSE WOULD SAY A WORD. Measured against the pinned base on 2026-09-06:
before the upgrade, trivy reports 53 fixed-available rows over 19 distinct ids —
14 MEDIUM in `libexpat1` at `2.5.0-1+deb12u2`, 5 UNKNOWN in `libpcre2` at
`10.42-1`; after, 0 rows at any severity. Every one of those is MEDIUM or
UNKNOWN, and the scan gate in `base-images.yaml` is `severity: CRITICAL,HIGH`,
so it never sees them. It would not see them even if it did: it scans the image
the build produced, and a cached image is internally consistent and simply old.
The delta gate in `estate-runner-image.yaml` has the same blind spot for the
same reason — it compares OUR layers against the base, and a frozen layer adds
nothing new to compare.

WHY A GATE ON THE WORKFLOW AND NOT A CHECK ON THE IMAGE. Three candidates were
weighed and this is the one that reddens on the change itself.

- ASSERT AT BUILD TIME THAT THE INSTALLED VERSIONS ARE THE SECURITY SUITE'S
  CURRENT CANDIDATES — run the candidate image and refuse if `apt-get -s
  upgrade` still proposes anything. That checks the PROPERTY rather than the
  mechanism, which is normally the better trade, and it was the strongest
  contender. It fails on when it fires: a cache introduced today is WARM today,
  so the check passes on the pull request that adds it and only reddens weeks
  later, on a cron run, naming a stale package rather than the change that
  froze it. It also cannot be run for `runtime`, which is distroless and has no
  apt, and it would red permanently on `estate-runner`, whose Containerfile
  deliberately does not upgrade. A check that reports the symptom long after the
  cause has merged is a report, not a gate.
- FORBID CACHING THAT LAYER FROM INSIDE THE CONTAINERFILE — a per-run `ARG` the
  apt layer consumes, so the layer can never hit a cache. That is a fix rather
  than a guard: it silently changes what a cache means for every other layer
  too, it reddens nothing when somebody removes it, and it would have to be
  repeated in every Containerfile anybody adds.
- REFUSE THE CACHE INPUT, which is this file. It reddens on the pull request
  that introduces the cache, in the `precommit` job that already feeds
  `ci / passed`, before anything is published, and it names the step.

IT PARSES THE WORKFLOWS RATHER THAN GREPPING FOR `cache`, for the reason
`helm_pin_agrees.py` gives at length: the word appears in PROSE in these files —
including in the comments that explain this very rule — so a grep gate reddens
pointing at a comment and passes when the comments happen to agree. This walks
`jobs.<id>.steps[]` and reads the `with:` mapping off the steps that actually
build an image.

ANY `with:` KEY CONTAINING `cache` IS REFUSED, not the two names known today.
`cache-from` and `cache-to` are what buildx offers now; an input added later
under a third name would slip past a two-name denylist while doing exactly what
those two do. An unknown cache-ish input therefore fails SAFE, the same way
`d80_portability.py` treats an unknown API group as CRD-bearing.

WITH ONE EXCEPTION, AND IT IS THE OPPOSITE INPUT. This action also takes
`no-cache` and `no-cache-filters`, which DISABLE a cache rather than declare
one. A rule matching the substring alone refuses them — so the first thing a
maintainer would reach for to GUARANTEE the apt `RUN` re-executes is refused by
the gate that exists to keep it re-executing, under a message saying it declared
a cache. That is a gate blocking its own reinforcement, and it is how a rule
becomes one people learn to work around rather than trust. Keys beginning
`no-cache` are therefore allowed, by prefix rather than by name, so a
`no-cache-*` sibling added later is allowed for the same reason its siblings
are. Nothing else is: `layer-cache-backend` still fails safe.

THE SUBJECT IS `docker/build-push-action` ALONE and not every step, which is the
same trade one level up. `docker/setup-buildx-action` takes a `cache-binary`
input that caches the BUILDX BINARY DOWNLOAD and not a single image layer; a
rule cast wide enough to reach it would be refusing another benign name on
another action.

THE FLOOR EXISTS BECAUSE A GATE WITH NOTHING TO CHECK REPORTS SUCCESS. There
are five build steps today across three workflows; below two this file is a
check that cannot fail, which is the defect this estate has now measured
several times over.

WHAT IT DOES NOT CHECK, said here rather than left to be found. A `run:` block
calling `docker buildx build --cache-from` directly would not be seen — no
workflow here builds that way, every one of the five goes through the action,
and grepping shell for a flag is the prose trap above in another costume; the
honest fix if that changes is to widen this then. Nor does it check the
`apt-get upgrade` line itself: removing that is a visible edit to the
Containerfile whose comment explains it, which a reviewer reads as what it is.
The cache is dangerous precisely because it is the opposite — a plausible
one-line speed-up, in a different file, that looks like it has nothing to do
with security.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import pathlib
import sys

import yaml

WORKFLOWS = pathlib.Path(".github/workflows")

# The action, matched on its OWNER/NAME and never on the whole `uses:` string,
# which carries the SHA pin and a version comment after it.
ACTION = "docker/build-push-action"

# A floor rather than the current count of five. More build steps is a
# legitimate change and must not redden; dropping below this silently turns the
# file into a no-op.
MINIMUM_SITES = 2

# The substring that marks an input as a layer cache. See the docstring for why
# this is a substring over one action's inputs rather than a list of names.
CACHE = "cache"

# ...and the prefix that marks one as the OPPOSITE. `no-cache` and
# `no-cache-filters` disable a cache; refusing them would refuse the very input
# that guarantees the apt layer re-executes. Matched as a PREFIX so a
# `no-cache-*` sibling added later is allowed for the same reason these are.
NOT_A_CACHE = "no-cache"


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


def build_sites(paths):
    """Every `docker/build-push-action` step found, as (where, cache inputs)."""
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
            keys = sorted(with_) if isinstance(with_, dict) else []
            cached = [
                key
                for key in keys
                if CACHE in str(key).lower()
                and not str(key).lower().startswith(NOT_A_CACHE)
            ]
            where = f"{path}: job `{job_id}`, step {index + 1}"
            sites.append((where, cached))
    return sites


def main() -> int:
    if not WORKFLOWS.is_dir():
        print(f"::error::{WORKFLOWS} does not exist")
        return 1

    paths = sorted(
        p for p in WORKFLOWS.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()
    )
    try:
        sites = build_sites(paths)
    except yaml.YAMLError as error:
        print(f"::error::a workflow under {WORKFLOWS} is not a YAML document: {error}")
        return 1

    if len(sites) < MINIMUM_SITES:
        print(
            f"::error::found {len(sites)} `{ACTION}` step(s) under {WORKFLOWS}, and "
            f"{MINIMUM_SITES} is the fewest this can inspect. This gate exists "
            "because a layer cache would freeze the apt security upgrade in "
            "`containers/rust-build/Containerfile`; with one build step or none it "
            "reports success having inspected nothing, which is the failure it was "
            "written to stop. If the images genuinely stopped being built here, "
            "delete this gate rather than leaving it green and blind."
        )
        return 1

    cached = [(where, keys) for where, keys in sites if keys]
    if cached:
        for where, keys in cached:
            print(
                f"::error::{where} — declares "
                + ", ".join(f"`{key}`" for key in keys)
                + f". No `{ACTION}` step here may declare a layer cache. "
                "`containers/rust-build/Containerfile` runs `apt-get upgrade -y`, "
                "and that is the ONLY thing delivering the weekly security refresh "
                "— the `FROM` is digest-pinned, so a rebuild on its own moves "
                "nothing. A cache turns that `RUN` into a permanent hit and freezes "
                "libexpat1, libpcre2 and libssh2 at whatever the cache holds. "
                "Nothing else would report it: the findings are MEDIUM and UNKNOWN, "
                "the scan gate is CRITICAL,HIGH, and a cached image is internally "
                "consistent and merely old. If a build here genuinely has to be "
                "cached, the apt layer has to stop being the security refresh "
                "first."
            )
        return 1

    print(
        f"{len(sites)} `{ACTION}` steps, none declaring a layer cache: "
        + "; ".join(where for where, _ in sites)
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
