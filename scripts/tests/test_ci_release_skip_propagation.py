"""GitHub's job-level `if:` is not exercised by any offline harness this
repository runs, and that gap is what let `parent` ship dark.

THE DEFECT, MEASURED VIA THE API RATHER THAN TRANSCRIBED FROM THE UI.
`yadgarhq/config` cut `v0.1.7` — a chart-only repository, no `Containerfile` —
and run 35453443606's jobs read:

    detect      success
    image       skipped      <- no Containerfile
    chart       success      <- rescued itself with its own always()
    deployment  skipped
    parent      skipped      <- ZERO STEPS, started and completed the same second

`parent`'s `if:` at the time was `needs.chart.result == 'success' &&
github.repository != 'yadgarhq/chart'`. Both clauses read TRUE for that run —
`chart` concluded `success`, the repository is not `yadgarhq/chart` — and it
skipped anyway.

WHY 772 PASSING TESTS MISSED IT. `test_parent_bump.py` already evaluates this
exact condition, through `ci_verdict.evaluate`, against the same result matrix
`test_ci_verdict.py` uses for `chart`. That evaluator reports the TRUTH VALUE
of the expression text — which was `True` for `("success", "yadgarhq/config")`
before this file's fix, same as after. It has no way to be otherwise: nothing
in it models GitHub's own admission step, the one that runs BEFORE the
expression is evaluated at all.

THE ADMISSION STEP, INFERRED RATHER THAN PROVEN — said plainly because GitHub
publishes no algorithm for it and no offline harness here can execute a real
job-level `if:` to find out. `admits()` below encodes the reading that fits
the measurement above: a job whose `if:` calls none of `always()` /
`success()` / `failure()` / `cancelled()` is skipped whenever ANY job in the
TRANSITIVE closure of its `needs:` did not conclude `success` — not merely its
own DIRECT needs. `actions/runner#2205` carries a report of this same shape
("Jobs skipped when NEEDS job ran successfully"), which is corroborating
rather than confirming. What the measurement proves without inference: `chart`
concluded `success`, `parent` needed only `chart`, and `parent` still
concluded `skipped` with zero steps. `admits()` is falsifiable on exactly that
gap, whatever GitHub's real scope for the check turns out to be — and the fix
does not depend on which reading is right, because `always()` is what GitHub's
own docs name for a job that must run when its `needs` did not all conclude
`success` and its `if:` has no status function, regardless of the check's
precise scope.

`admits()` is that inferred admission step, modelled just far enough to be
falsifiable: given the real `needs:` graph and `if:` text read out of
`ci-release.yaml`, and the SAME per-job conclusions the real run reported, it
answers whether this model says GitHub would execute the job — which is a
different question from whether the job's own expression is true, and the one
the model answers `False` for exactly where the production run did.

THE GENERALISED FORM, past this one site: `suspect_jobs()` scans every job in
the real file's `needs:` graph and flags one whose `if:` has no override AND
whose needs are only satisfied by crossing an INDIRECT ancestor — a job not in
its own direct `needs:` list — that can independently skip on a tree fact
(a `needs.detect.outputs.*` comparison). `deployment` needs `[detect, image]`
and reads `needs.image.result` directly, so `image` skipping is exactly what
its own condition is FOR; it is not flagged. `parent`, before this fix, needed
only `[detect, chart]` yet was reachable from `image` only through `chart` — an
ancestor absent from its own `needs:` — and IS flagged. The distinction is what
keeps the check from crying wolf on `deployment`'s deliberate, correct skip.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ci_verdict  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"

_OVERRIDE = re.compile(r"\b(?:always|success|failure|cancelled)\s*\(\s*\)")


def real_jobs():
    return yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]


def has_override(if_expr):
    """Does this `if:` call one of the four functions that opt out of the
    implicit admission check? Textual, on purpose: this is exactly what
    GitHub itself keys on, not an approximation of it."""
    return bool(if_expr) and bool(_OVERRIDE.search(str(if_expr)))


def direct_needs(jobs, job_id):
    needs = jobs[job_id].get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def transitive_needs(jobs, job_id):
    """Every ancestor reachable via `needs:`, however many hops away."""
    seen = set()
    stack = list(direct_needs(jobs, job_id))
    while stack:
        anc = stack.pop()
        if anc in seen:
            continue
        seen.add(anc)
        stack.extend(direct_needs(jobs, anc))
    return seen


def admits(jobs, job_id, conclusions, repo="yadgarhq/config"):
    """Would GitHub run `job_id`, given each job's conclusion in `conclusions`?

    `conclusions` supplies a result for every job in the TRANSITIVE closure of
    `job_id`'s needs — the same set the real run reports, and the set
    `ci_verdict.evaluate` alone never looks at.
    """
    if_expr = jobs[job_id].get("if")
    if not has_override(if_expr):
        # THE STEP THE OLD TEST NEVER TOOK. Any non-`success` ancestor,
        # anywhere in the closure — not only among direct needs — blocks
        # admission before the expression text is asked anything.
        for ancestor in transitive_needs(jobs, job_id):
            if conclusions.get(ancestor) != "success":
                return False
    if if_expr is None:
        return True
    values = dict(conclusions_as_refs(jobs, job_id, conclusions))
    values["github.repository"] = repo

    def resolve(ref, expr):
        if ref not in values:
            raise ci_verdict.Refused(f"{ref} is not supplied by this test ({expr!r})")
        return values[ref]

    return ci_verdict.evaluate(if_expr, resolve)


def conclusions_as_refs(jobs, job_id, conclusions):
    for anc, result in conclusions.items():
        yield f"needs.{anc}.result", result


# ---------------------------------------------------------------------------
# The measured incident, replayed against the real file
# ---------------------------------------------------------------------------

# RUN 35453443606'S OWN NUMBERS. `image` is not in `parent`'s direct `needs:`
# at all — it only matters because `chart` depends on it — which is exactly
# the shape `admits()` exists to catch and `ci_verdict.evaluate` alone cannot.
CHART_ONLY_REPO = {"detect": "success", "image": "skipped", "chart": "success"}


def test_parent_admits_the_chart_only_repository_on_the_real_file():
    """THE FIX, checked against the model above. `admits()` says `False` for
    the pre-fix text and `True` for what ships — no offline run can ask
    GitHub itself, so this is the strongest claim available offline."""
    assert admits(real_jobs(), "parent", CHART_ONLY_REPO) is True


def test_removing_always_reproduces_the_measured_defect():
    """MUTATION PROOF, SCOPED HONESTLY. This reddens `admits()`'s model of
    GitHub, not GitHub itself — no offline harness can execute a real
    job-level `if:` to prove the model's exact scope. What it DOES pin
    mechanically: `parent`'s `if:` must carry a status-check function, or this
    test — and every case in the matrix below — goes red on the next edit
    that drops it, the same way it would have caught the shipped defect."""
    jobs = real_jobs()
    jobs["parent"]["if"] = jobs["parent"]["if"].replace("always() && ", "")
    assert "always()" not in jobs["parent"]["if"]
    assert admits(jobs, "parent", CHART_ONLY_REPO) is False


@pytest.mark.parametrize(
    "conclusions,repo,runs",
    [
        # THE MEASURED INCIDENT ITSELF.
        (CHART_ONLY_REPO, "yadgarhq/config", True),
        # THE ORDINARY CASE: an image repository, nothing skipped upstream.
        ({"detect": "success", "image": "success", "chart": "success"}, "yadgarhq/gateway", True),
        # REQUIREMENT 1 — THE PARENT DOES NOT PIN ITSELF. `always()` widens
        # WHEN this job runs, not WHAT it accepts; the self-pin guard still
        # excludes `yadgarhq/chart` even though everything upstream is green.
        ({"detect": "success", "image": "success", "chart": "success"}, "yadgarhq/chart", False),
        # REQUIREMENT 2 — A CHART THAT NEVER PUBLISHED MUST NOT BE PINNED.
        # `chart` itself failing or being cancelled still excludes `parent`;
        # `always()` did not turn that refusal into a skip nobody notices.
        ({"detect": "success", "image": "success", "chart": "failure"}, "yadgarhq/gateway", False),
        ({"detect": "success", "image": "success", "chart": "cancelled"}, "yadgarhq/gateway", False),
        # `chart` itself skipped (no `chart/` at all in this hypothetical) —
        # nothing to pin, so `parent` correctly stays out too.
        ({"detect": "success", "image": "success", "chart": "skipped"}, "yadgarhq/gateway", False),
    ],
)
def test_the_fixed_condition_admits_and_refuses_correctly(conclusions, repo, runs):
    assert admits(real_jobs(), "parent", conclusions, repo=repo) is runs


# ---------------------------------------------------------------------------
# Generalised: no OTHER job in this file may carry the same defect shape
# ---------------------------------------------------------------------------


def can_independently_skip(jobs, job_id):
    """Does this job's OWN `if:` compare a `detect` output — a fact about the
    tree, rather than a consequence of some other job's run — so that it can
    legitimately skip on its own, independent of anything upstream failing?

    `image` is the only job in this file shaped this way today. Deliberately
    narrow: this is the estate's own established pattern (ledger 729's ruling)
    for what a "reason", rather than a "consequence", looks like here.
    """
    if_expr = jobs[job_id].get("if")
    return bool(if_expr) and "needs.detect.outputs." in str(if_expr)


def suspect_jobs(jobs):
    """Every job that could reproduce this exact defect shape.

    Flagged: no override in its own `if:`, AND its needs graph reaches a job
    that can independently skip (`can_independently_skip`) ONLY through an
    INDIRECT ancestor — one absent from its own direct `needs:` list. That
    second clause is what spares `deployment`: it needs `image` DIRECTLY and
    reads `needs.image.result` itself, which is the condition doing exactly
    what it is for, not a job blindsided by a hop it never named.
    """
    suspects = []
    for job_id, spec in jobs.items():
        if has_override(spec.get("if")):
            continue
        direct = set(direct_needs(jobs, job_id))
        indirect_ancestors = transitive_needs(jobs, job_id) - direct
        for ancestor in indirect_ancestors:
            if can_independently_skip(jobs, ancestor):
                suspects.append((job_id, ancestor))
    return suspects


def test_no_job_in_the_real_file_carries_this_defect_shape():
    assert suspect_jobs(real_jobs()) == []


def test_deployment_is_not_a_false_positive():
    """The control for the generalised check: `deployment` needs `image`
    DIRECTLY and legitimately skips with it. A check that flagged it would be
    too broad to trust."""
    jobs = real_jobs()
    assert "image" in direct_needs(jobs, "deployment")
    assert not has_override(jobs["deployment"]["if"])
    assert suspect_jobs(jobs) == []


def test_the_generalised_check_catches_the_measured_defect_on_a_revert():
    """Revert `parent`'s fix and the GENERAL scanner — not only the narrow
    replay above — must name it."""
    jobs = real_jobs()
    jobs["parent"]["if"] = jobs["parent"]["if"].replace("always() && ", "")
    assert ("parent", "image") in suspect_jobs(jobs)
