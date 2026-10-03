"""LEDGER 1270a (ADR-0848). `ci-release.yaml`'s `deployment` job used to write
the released version into `yadgarhq/argocd` — `versions/<module>.yaml`, by a
Contents API `PUT` under `repos/${OWNER}/argocd/contents/versions/...` — so
Argo CD would roll the new image. Nothing in `yadgarhq/argocd` ever read that
file: the `yadgar-modules` ApplicationSet it fed generates zero Applications
(ADR-0786). The write, and the App token minted only to make it
(`repositories: argocd`), are retired. The `estate` dispatch — a different
mint, a different step — is untouched and still runs.

EVERY CHECK HERE IS PAIRED WITH A MUTATION, for the reason `test_no_test_skips.py`
gives: a gate that only ever sees conforming input certifies the fixture, not the
gate. `_with_argocd_write_restored` below reinserts the deleted step's shape —
not a verbatim copy, but one that carries the three properties the checks key
on — and every `test_mutation_*` function asserts its own check goes red against
it.

Run: python3 -m pytest scripts/tests/ -q
"""

import copy
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"


def real_text():
    return CI_RELEASE.read_text(encoding="utf-8")


def real_jobs():
    return yaml.safe_load(real_text())["jobs"]


def repositories_of(step):
    """`with.repositories` can be a single name or a newline-separated list,
    exactly as `actions/create-github-app-token` accepts it."""
    repos = (step.get("with") or {}).get("repositories")
    if repos is None:
        return []
    if isinstance(repos, str):
        return [r.strip() for r in repos.split("\n") if r.strip()]
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


def no_step_mints_a_token_scoped_to_argocd(jobs):
    for job_id, job in jobs.items():
        for step in job.get("steps", []):
            if "argocd" in repositories_of(step):
                return False
    return True


def test_no_step_mints_a_token_scoped_to_argocd():
    assert no_step_mints_a_token_scoped_to_argocd(real_jobs())


def no_run_block_names_the_argocd_api_or_a_versions_write(jobs):
    for _job_id, _step_name, run in run_blocks(jobs):
        if "/argocd/" in run:
            return False
        if "versions/<" in run or "versions/${" in run:
            return False
    return True


def test_no_run_block_names_the_argocd_api_or_a_versions_write():
    assert no_run_block_names_the_argocd_api_or_a_versions_write(real_jobs())


def test_versions_pinned_is_never_mentioned():
    """Not scoped to `run:` blocks like the check above — this string must not
    appear anywhere in the file, comments included, since no write it could
    describe exists here any more."""
    assert "versions_pinned" not in real_text()


def estate_steps_gated_only_on_cred(jobs):
    steps = {s.get("id"): s for s in jobs["deployment"]["steps"] if s.get("id")}
    for step_id in ("estate_app", "estate_dispatch"):
        if step_id not in steps:
            return False
        if steps[step_id].get("if") != "steps.cred.outputs.configured == 'true'":
            return False
    return True


def test_estate_app_and_estate_dispatch_still_exist_gated_only_on_cred():
    assert estate_steps_gated_only_on_cred(real_jobs())


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
        i for i, s in enumerate(jobs["deployment"]["steps"]) if s.get("id") == "cred"
    )
    jobs["deployment"]["steps"][cred_index + 1 : cred_index + 1] = [mint, write]
    return jobs


def test_mutation_restoring_the_write_reddens_the_mint_check():
    mutated = _with_argocd_write_restored(real_jobs())
    assert not no_step_mints_a_token_scoped_to_argocd(mutated)


def test_mutation_restoring_the_write_reddens_the_run_block_check():
    mutated = _with_argocd_write_restored(real_jobs())
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(mutated)
