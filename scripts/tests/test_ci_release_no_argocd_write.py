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
not a verbatim copy, but one that carries the properties the checks key on — and
every `test_mutation_*` function asserts its own check goes red against it. Two
further mutations probe the checks' OWN edges rather than the deleted step's
shape: a `repositories:` value can be a single comma-separated string, not only
newline-separated, so a scope check that only splits on `\n` would pass a mint
scoped to `estate,argocd`; and `contents/versions/` is its own tell in a `run:`
block, independent of `/argocd/` and `versions/<`/`versions/${`.

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
    jobs["deployment"]["steps"].append(sneaky_write)
    assert not no_run_block_names_the_argocd_api_or_a_versions_write(jobs)


def test_mutation_a_comma_scoped_mint_including_argocd_reddens_too():
    """The scope check must not be fooled by a list spelled as one string.
    `actions/create-github-app-token` accepts `repositories:` as a single
    comma-separated value as well as newline-separated, so a NEW mint scoped
    to `estate,argocd` must be caught exactly like a mint scoped to `argocd`
    alone."""
    jobs = copy.deepcopy(real_jobs())
    new_mint = {
        "name": "a hypothetical mint widened past estate",
        "id": "widened_app",
        "if": "steps.cred.outputs.configured == 'true'",
        "uses": "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
        "with": {
            "client-id": "${{ secrets.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "estate,argocd",
            "permission-contents": "write",
        },
    }
    jobs["deployment"]["steps"].append(new_mint)
    assert "argocd" in repositories_of(new_mint)
    assert not no_step_mints_a_token_scoped_to_argocd(jobs)
