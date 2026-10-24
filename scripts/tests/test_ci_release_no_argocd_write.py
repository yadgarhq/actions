"""LEDGER 1270a (ADR-0848). `ci-release.yaml`'s `deployment` job used to write
the released version into `yadgarhq/argocd` — `versions/<module>.yaml`, by a
Contents API `PUT` under `repos/${OWNER}/argocd/contents/versions/...` — so
Argo CD would roll the new image. Nothing in `yadgarhq/argocd` ever read that
file: the `yadgar-modules` ApplicationSet it fed generates zero Applications
(ADR-0786). The write, and the App token minted only to make it
(`repositories: argocd`), are retired.

LEDGER 1487 (ledger 675 stage 7). The rest of that job — an App token scoped to
`repositories: estate` (step `estate_app`) and a `repository_dispatch` of type
`module-rolled` to `yadgarhq/estate` (step `estate_dispatch`) — is retired too,
and the `deployment` job with it. Since ADR-0786 a module release moves nothing
in the cluster; only an argocd pin PR does, and the settled-state gate judges
that. Every `module-rolled` smoke run was red with no verdict (ledger 1376).

EVERY CHECK HERE IS PAIRED WITH A MUTATION, for the reason `test_no_test_skips.py`
gives: a gate that only ever sees conforming input certifies the fixture, not the
gate. `_with_argocd_write_restored` below reinserts the deleted step's shape —
not a verbatim copy, but one that carries the properties the checks key on — and
every `test_mutation_*` function asserts its own check goes red against it.
`_with_estate_dispatch_restored` does the same for the estate steps. Two
further mutations probe the checks' OWN edges rather than the deleted step's
shape: a `repositories:` value can be a single comma-separated string, not only
newline-separated, so a scope check that only splits on `\n` would pass a mint
scoped to `chart,argocd`; and `contents/versions/` is its own tell in a `run:`
block, independent of `/argocd/` and `versions/<`/`versions/${`.

C7 (ADR-1004, plans/auto-deploy-parent-chart.md) NARROWS this guard; it does not
delete it. ADR-0946 / ADR-0948 R3-a put one argocd-scoped mint back: job
`argocd-pin-nudge`, run for `yadgarhq/chart` only, which sends a
`repository_dispatch` and writes nothing. So the checks below now allow exactly
that shape and nothing wider: an argocd mint only in that job, scoped to
`argocd` alone with `permission-contents: write` alone (the minimum
`POST /repos/{owner}/{repo}/dispatches` takes), and an argocd API path only as
`/argocd/dispatches` in that job. Every `versions` tell stays forbidden
everywhere, the nudge job included. Mutations below widen each edge and red.

Run: python3 -m pytest scripts/tests/ -q
"""

import copy
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"


def real_text():
    return CI_RELEASE.read_text(encoding="utf-8")


def real_jobs():
    return yaml.safe_load(real_text())["jobs"]


def repositories_of(step):
    """`with.repositories` can be a single name, or several separated by a
    comma, a newline, or both, exactly as `actions/create-github-app-token`
    accepts it."""
    repos = (step.get("with") or {}).get("repositories")
    if repos is None:
        return []
    if isinstance(repos, str):
        return [r.strip() for r in re.split(r"[,\n]+", repos) if r.strip()]
    return list(repos)


def run_blocks(jobs):
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            run = step.get("run")
            if run:
                yield job_id, step.get("name") or step.get("id"), run


# ---------------------------------------------------------------------------
# The four properties the card's acceptance names
# ---------------------------------------------------------------------------


NUDGE = "argocd-pin-nudge"
DISPATCH_CALL = 'gh api -X POST "repos/${OWNER}/argocd/dispatches" --input -'


def no_step_mints_a_token_scoped_to_argocd(jobs):
    """Outside the nudge job, no argocd mint at all. Inside it, exactly one,
    scoped to `argocd` alone with `permission-contents: write` alone. And no
    mint anywhere without `repositories:`: the App is installed on all
    repositories, so an unscoped mint is an argocd mint too (and every other
    repository's)."""
    for step in mint_steps(jobs):
        if not repositories_of(step):
            return False
    for job_id, job in jobs.items():
        argocd_mints = 0
        for step in job.get("steps", []):
            if "argocd" not in repositories_of(step):
                continue
            argocd_mints += 1
            if job_id != NUDGE or argocd_mints > 1:
                return False
            if repositories_of(step) != ["argocd"]:
                return False
            perms = {k: v for k, v in step["with"].items() if k.startswith("permission-")}
            if perms != {"permission-contents": "write"}:
                return False
    return True


def test_no_step_mints_a_token_scoped_to_argocd():
    assert no_step_mints_a_token_scoped_to_argocd(real_jobs())


def no_run_block_names_the_argocd_api_or_a_versions_write(jobs):
    for job_id, _step_name, run in run_blocks(jobs):
        # The nudge's dispatch is the one argocd API path allowed, in that job
        # only, and only as this exact call: a bare `/argocd/dispatches` strip
        # would pass `repos/o/argocd/dispatches/../contents/a`.
        named = run.replace(DISPATCH_CALL, "") if job_id == NUDGE else run
        if "/argocd/" in named:
            return False
        if "versions/<" in run or "versions/${" in run:
            return False
        if "contents/versions/" in run:
            return False
    return True


def test_no_run_block_names_the_argocd_api_or_a_versions_write():
    assert no_run_block_names_the_argocd_api_or_a_versions_write(real_jobs())


def test_versions_pinned_is_never_mentioned():
    """Not scoped to `run:` blocks like the check above — this string must not
    appear anywhere in the file, comments included, since no write it could
    describe exists here any more."""
    assert "versions_pinned" not in real_text()


def mint_steps(jobs):
    for job in jobs.values():
        for step in job.get("steps", []):
            if "create-github-app-token" in str(step.get("uses", "")):
                yield step


def nothing_asks_estate_for_a_verdict(jobs):
    """No mint scoped to `estate`, no `run:` block that dispatches to it or
    names `module-rolled`, and no step left under either retired id."""
    for step in mint_steps(jobs):
        if "estate" in repositories_of(step):
            return False
    for _job_id, _step_name, run in run_blocks(jobs):
        if "/estate/dispatches" in run or "module-rolled" in run:
            return False
    for job in jobs.values():
        for step in job.get("steps", []):
            if step.get("id") in ("estate_app", "estate_dispatch"):
                return False
    return True


def test_nothing_asks_estate_for_a_verdict():
    jobs = real_jobs()
    # NOT VACUOUS: the scan must see the two mints that remain (`parent`'s,
    # scoped to `chart`, and the nudge's, scoped to `argocd`). A file that
    # parsed into no steps would otherwise pass.
    assert [repositories_of(s) for s in mint_steps(jobs)] == [["chart"], ["argocd"]]
    assert nothing_asks_estate_for_a_verdict(jobs)


def test_the_deployment_job_is_gone():
    assert "deployment" not in real_jobs()


# ---------------------------------------------------------------------------
# Mutation: reinsert the deleted step's shape, confirm every check above reds.
# ---------------------------------------------------------------------------


def _with_argocd_write_restored(jobs):
    jobs = copy.deepcopy(jobs)
    mint = {
        "name": "mint a token that can write to argocd and nowhere else",
        "id": "app",
        "if": "steps.cred.outputs.configured == 'true'",
        "uses": "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
        "with": {
            "client-id": "${{ secrets.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "argocd",
            "permission-contents": "write",
        },
    }
    write = {
        "name": "tell argocd which version to run",
        "if": "steps.cred.outputs.configured == 'true'",
        "run": (
            'api="repos/${OWNER}/argocd/contents/versions/${module}.yaml"\n'
            "gh api \"$api\" -X PUT -f content=\"$(base64 -w0 < \"$file\")\"\n"
        ),
    }
    cred_index = next(
        i for i, s in enumerate(jobs["parent"]["steps"]) if s.get("id") == "cred"
    )
    jobs["parent"]["steps"][cred_index + 1 : cred_index + 1] = [mint, write]
    return jobs


def test_mutation_restoring_the_write_reddens_the_mint_check():
    mutated = _with_argocd_write_restored(real_jobs())
    assert not no_step_mints_a_token_scoped_to_argocd(mutated)


def test_mutation_restoring_the_write_reddens_the_run_block_check():
    mutated = _with_argocd_write_restored(real_jobs())
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(mutated)


def test_mutation_a_contents_versions_path_reddens_without_the_other_two_tells():
    """`contents/versions/` is its own tell, independent of `/argocd/` and
    `versions/<`/`versions/${`: a path like `repos/${OWNER}/<repo>/contents/
    versions/release.yaml` matches neither older pattern but is still a
    versions-file write by Contents API."""
    jobs = copy.deepcopy(real_jobs())
    sneaky_write = {
        "name": "a hypothetical versions write under a different literal shape",
        "if": "steps.cred.outputs.configured == 'true'",
        "run": 'api="repos/${OWNER}/${REPO_NAME}/contents/versions/release.yaml"\n',
    }
    run = sneaky_write["run"]
    assert "/argocd/" not in run
    assert "versions/<" not in run and "versions/${" not in run
    jobs["parent"]["steps"].append(sneaky_write)
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(jobs)


def test_mutation_a_comma_scoped_mint_including_argocd_reddens_too():
    """The scope check must not be fooled by a list spelled as one string.
    `actions/create-github-app-token` accepts `repositories:` as a single
    comma-separated value as well as newline-separated, so a NEW mint scoped
    to `chart,argocd` must be caught exactly like a mint scoped to `argocd`
    alone."""
    jobs = copy.deepcopy(real_jobs())
    new_mint = {
        "name": "a hypothetical mint widened past chart",
        "id": "widened_app",
        "if": "steps.cred.outputs.configured == 'true'",
        "uses": "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
        "with": {
            "client-id": "${{ secrets.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "chart,argocd",
            "permission-contents": "write",
        },
    }
    jobs["parent"]["steps"].append(new_mint)
    assert "argocd" in repositories_of(new_mint)
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)


def _nudge_mint(jobs):
    return next(s for s in jobs[NUDGE]["steps"] if s.get("id") == "app")


def test_mutation_the_nudge_mint_widened_to_pull_requests_reddens():
    jobs = copy.deepcopy(real_jobs())
    _nudge_mint(jobs)["with"]["permission-pull-requests"] = "write"
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)


def test_mutation_the_nudge_mint_widened_to_a_second_repository_reddens():
    jobs = copy.deepcopy(real_jobs())
    _nudge_mint(jobs)["with"]["repositories"] = "argocd,chart"
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)


def test_mutation_the_nudge_mint_moved_into_parent_reddens():
    jobs = copy.deepcopy(real_jobs())
    jobs["parent"]["steps"].append(copy.deepcopy(_nudge_mint(jobs)))
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)


def test_mutation_the_dispatch_moved_into_parent_reddens():
    jobs = copy.deepcopy(real_jobs())
    jobs["parent"]["steps"].append(
        {"name": "dispatch", "run": 'gh api -X POST "repos/${OWNER}/argocd/dispatches"\n'}
    )
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(jobs)


def test_the_exact_dispatch_call_is_the_one_the_nudge_ships():
    """NOT VACUOUS: the allowed string must be the shipped one, or the strip
    allows nothing and the check above passes only by accident."""
    nudge = next(s for s in real_jobs()[NUDGE]["steps"] if s.get("id") == "nudge")
    assert DISPATCH_CALL in nudge["run"]


def test_mutation_a_dispatch_path_traversal_in_the_nudge_job_reddens():
    for run in (
        'gh api -X POST "repos/o/argocd/dispatches/../contents/a"\n',
        'gh api -X PUT "repos/${OWNER}/argocd/dispatches/../contents/a"\n',
        'gh api -X POST "repos/${OWNER}/argocd/dispatches"/../contents/a --input -\n',
    ):
        jobs = copy.deepcopy(real_jobs())
        jobs[NUDGE]["steps"].append({"name": "sneaky", "run": run})
        assert not no_run_block_names_the_argocd_api_or_a_versions_write(jobs), run


def test_mutation_a_second_argocd_mint_in_the_nudge_job_reddens():
    jobs = copy.deepcopy(real_jobs())
    second = copy.deepcopy(_nudge_mint(jobs))
    second["id"] = "app2"
    jobs[NUDGE]["steps"].append(second)
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)


def test_mutation_a_mint_without_repositories_reddens():
    for job_id in ("parent", NUDGE, "examples"):
        jobs = copy.deepcopy(real_jobs())
        unscoped = copy.deepcopy(_nudge_mint(jobs))
        del unscoped["with"]["repositories"]
        unscoped["id"] = "unscoped"
        jobs[job_id]["steps"].append(unscoped)
        assert not no_step_mints_a_token_scoped_to_argocd(jobs), job_id


def test_mutation_a_versions_write_in_the_nudge_job_still_reddens():
    jobs = copy.deepcopy(real_jobs())
    jobs[NUDGE]["steps"].append(
        {"name": "write", "run": 'gh api -X PUT "repos/${OWNER}/argocd/contents/versions/x.yaml"\n'}
    )
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(jobs)


# ---------------------------------------------------------------------------
# Mutation: reinsert the retired estate steps, confirm the check above reds.
# ---------------------------------------------------------------------------


def _with_estate_dispatch_restored(jobs):
    jobs = copy.deepcopy(jobs)
    jobs["deployment"] = {
        "needs": ["detect", "image"],
        "if": "needs.detect.outputs.chart == 'true' && needs.image.result == 'success'",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "mint a token that can ask estate for a verdict and nothing else",
                "id": "estate_app",
                "uses": "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
                "with": {"owner": "${{ github.repository_owner }}", "repositories": "estate"},
            },
            {
                "name": "ask estate for a verdict on this roll",
                "id": "estate_dispatch",
                "run": (
                    "jq -n '{event_type:\"module-rolled\"}' |\n"
                    'gh api -X POST "repos/${OWNER}/estate/dispatches" --input -\n'
                ),
            },
        ],
    }
    return jobs


def test_mutation_restoring_the_estate_dispatch_reddens_the_check():
    assert not nothing_asks_estate_for_a_verdict(_with_estate_dispatch_restored(real_jobs()))


def _bare(step):
    return {k: v for k, v in step.items() if k not in ("id",)}


def test_mutation_each_estate_tell_reddens_on_its_own():
    """Each of the three tells is independent: an estate mint under a new id, a
    dispatch under a new id, or a step that keeps an old id with new content."""
    restored = _with_estate_dispatch_restored({})["deployment"]["steps"]
    mint, dispatch = restored
    for extra in (
        _bare(mint),
        _bare(dispatch),
        {"name": "renamed", "id": "estate_dispatch", "run": "true\n"},
    ):
        jobs = copy.deepcopy(real_jobs())
        jobs["parent"]["steps"].append(extra)
        assert not nothing_asks_estate_for_a_verdict(jobs), extra
