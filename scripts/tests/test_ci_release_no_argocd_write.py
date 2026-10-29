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


def lower_keys(mapping):
    """GitHub reads `with:` keys case-insensitively, so `Owner:` is `owner:`."""
    return {str(k).lower(): v for k, v in (mapping or {}).items()}


def repositories_of(step):
    """`with.repositories` can be a single name, or several separated by a
    comma, a newline, or both, exactly as `actions/create-github-app-token`
    accepts it."""
    repos = lower_keys(step.get("with")).get("repositories")
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


# ---------------------------------------------------------------------------
# YAADGAAR 414. STRUCTURAL GUARDS. The substring checks above miss the repo
# name passed through an env var, a `git push https://.../argocd`, a third-party
# `uses:` handed the token, and an unscoped mint (the worst: installation
# 158692002 has repository_selection all). These check the SHAPE of the nudge
# job and of every mint in every workflow. Each is paired with a mutation.
# ---------------------------------------------------------------------------

import json

WORKFLOWS = ROOT / ".github" / "workflows"
NUDGE_STEP_IDS = ["cred", "app", "mint_failed", "nudge"]
PINNED_MINT = re.compile(r"^actions/create-github-app-token@[0-9a-f]{40}$")
TOKEN_REF = "steps.app.outputs.token"
# Every spelling of a read of the minted token, plus `toJSON(steps` which dumps
# every step output at once.
TOKEN_READ = re.compile(
    r"""steps\s*(?:\.\s*app\b|\[\s*['"]app['"]\s*\])\s*(?:\.\s*outputs\b|\[\s*['"]outputs['"]\s*\])"""
    r"""|outputs\s*\[\s*['"]token['"]\s*\]"""
    r"""|steps\s*\.\s*\*"""
    r"""|tojson\s*\(\s*steps""",
    re.IGNORECASE,
)


def blob(node):
    """The node as one string, with JSON's quote escaping undone so a
    `steps["app"]` in a script still reads as written."""
    return json.dumps(node, sort_keys=True).replace('\\"', '"')


def nudge_job_violations(job):
    """Empty when the nudge job has exactly its shipped shape."""
    bad = []
    steps = job.get("steps", [])
    if [s.get("id") for s in steps] != NUDGE_STEP_IDS:
        bad.append("step ids are not exactly %s" % NUDGE_STEP_IDS)
    for s in steps:
        uses = s.get("uses")
        if uses is not None and not PINNED_MINT.match(str(uses)):
            bad.append("uses %r is not the SHA-pinned create-github-app-token" % uses)
        reads = bool(TOKEN_READ.search(blob(s)))
        if s.get("id") == "app":
            reads = False  # the mint step produces it; it does not read it
        if reads and s.get("id") != "nudge":
            bad.append("step %r reads the minted token" % s.get("id"))
    if TOKEN_READ.search(blob({k: v for k, v in job.items() if k != "steps"})):
        bad.append("job-level key reads the minted token")
    return bad


def secret_key_violations(jobs):
    """`secrets.RELEASE_APP_PRIVATE_KEY` is read only by a mint step's
    `with: private-key` and by a `cred` step's `env:`, nowhere else."""
    bad = []
    for job_id, job in jobs.items():
        if "RELEASE_APP_PRIVATE_KEY" in json.dumps({k: v for k, v in job.items() if k != "steps"}):
            bad.append(job_id + ": job-level key names the private key")
        for s in job.get("steps", []):
            if "secrets.RELEASE_APP_PRIVATE_KEY" not in json.dumps(s):
                continue
            rest = {k: v for k, v in s.items() if k not in ("with", "env", "uses")}
            if "create-github-app-token" in str(s.get("uses", "")):
                ok = "secrets.RELEASE_APP_PRIVATE_KEY" in str(lower_keys(s.get("with")).get("private-key"))
                ok = ok and "secrets.RELEASE_APP_PRIVATE_KEY" not in json.dumps(rest) + json.dumps(s.get("env"))
                ok = ok and sum(
                    "secrets.RELEASE_APP_PRIVATE_KEY" in str(v) for v in (s.get("with") or {}).values()
                ) == 1
            else:
                ok = s.get("id") == "cred" and "secrets.RELEASE_APP_PRIVATE_KEY" not in json.dumps(rest)
            if not ok:
                bad.append("%s/%s reads the private key outside its allowed place" % (job_id, s.get("id")))
    return bad


def test_the_private_key_is_read_only_by_mints_and_cred():
    jobs = real_jobs()
    assert secret_key_violations(jobs) == []
    # NOT VACUOUS: two mints and two cred steps do read it.
    assert json.dumps(jobs).count("secrets.RELEASE_APP_PRIVATE_KEY") == 4


def test_mutation_the_private_key_read_elsewhere_reddens():
    for where in ("env", "run", "mint_other_input", "nudge"):
        jobs = copy.deepcopy(real_jobs())
        if where == "env":
            jobs[NUDGE]["steps"][2]["env"] = {"K": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}"}
        elif where == "run":
            jobs[NUDGE]["steps"][3]["run"] += "echo ${{ secrets.RELEASE_APP_PRIVATE_KEY }}\n"
        elif where == "mint_other_input":
            jobs[NUDGE]["steps"][1]["with"]["client-id"] = "${{ secrets.RELEASE_APP_PRIVATE_KEY }}"
        else:
            jobs[NUDGE]["steps"][3]["env"]["K"] = "${{ secrets.RELEASE_APP_PRIVATE_KEY }}"
        assert secret_key_violations(jobs), where
    jobs = copy.deepcopy(real_jobs())
    jobs["parent"]["steps"][0]["run"] = "echo ${{ secrets.RELEASE_APP_PRIVATE_KEY }}"
    assert secret_key_violations(jobs)


def test_reading_only_the_outcome_of_the_mint_is_not_a_token_read():
    job = _nudge()
    job["steps"][2]["if"] = "steps['app'].outcome == 'failure'"
    assert nudge_job_violations(job) == []


def test_mutation_every_token_read_spelling_reddens():
    for spelling in (
        "${{ steps.app.outputs.token }}",
        "${{ steps['app'].outputs.token }}",
        '${{ steps["app"].outputs.token }}',
        "${{ steps.app.outputs['token'] }}",
        "${{ STEPS.APP.OUTPUTS.TOKEN }}",
        "${{ fromJSON(toJSON(x)).outputs['token'] }}",
        "${{ steps.*.outputs.token }}",
        "${{ steps .  * .outputs.token }}",
        "${{ toJSON(steps) }}",
        "${{ toJson( steps ) }}",
    ):
        job = _nudge()
        job["steps"][2]["env"] = {"T": spelling}
        assert nudge_job_violations(job), spelling
        # ... and the nudge step itself may read it in any spelling.
        job = _nudge()
        job["steps"][3]["env"]["T"] = spelling
        assert nudge_job_violations(job) == [], spelling


def test_nudge_job_has_exactly_its_shipped_shape():
    job = real_jobs()[NUDGE]
    assert nudge_job_violations(job) == []
    # NOT VACUOUS: the nudge step really does read the token.
    nudge = next(s for s in job["steps"] if s["id"] == "nudge")
    assert TOKEN_REF in json.dumps(nudge)


def _nudge():
    return copy.deepcopy(real_jobs()[NUDGE])


def test_mutation_an_extra_nudge_step_reddens():
    job = _nudge()
    job["steps"].append({"id": "push", "run": "true\n"})
    assert nudge_job_violations(job)


def test_mutation_a_missing_or_renamed_nudge_step_reddens():
    job = _nudge()
    job["steps"] = [s for s in job["steps"] if s["id"] != "mint_failed"]
    assert nudge_job_violations(job)
    job = _nudge()
    job["steps"][3]["id"] = "other"
    assert nudge_job_violations(job)


def test_mutation_a_third_party_uses_in_the_nudge_job_reddens():
    for uses in ("someone/else@bcd2ba49218906704ab6c1aa796996da409d3eb1", "actions/checkout@v4"):
        job = _nudge()
        job["steps"][0]["uses"] = uses
        assert nudge_job_violations(job), uses


def test_mutation_an_unpinned_mint_reddens():
    job = _nudge()
    job["steps"][1]["uses"] = "actions/create-github-app-token@v3"
    assert nudge_job_violations(job)


def test_mutation_another_step_reading_the_token_reddens():
    job = _nudge()
    job["steps"][2]["env"] = {"GH_TOKEN": "${{ steps.app.outputs.token }}"}
    assert nudge_job_violations(job)
    job = _nudge()
    job["steps"][0]["run"] = "git push https://x:${{ steps.app.outputs.token }}@github.com/o/argocd"
    assert nudge_job_violations(job)


def test_mutation_a_job_level_token_read_reddens():
    job = _nudge()
    job["env"] = {"T": "${{ steps.app.outputs.token }}"}
    assert nudge_job_violations(job)


def all_mints():
    """(workflow file name, job id, step) for every mint in every workflow."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job_id, job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).items():
            for step in job.get("steps", []):
                if "create-github-app-token" in str(step.get("uses", "")):
                    out.append((path.name, job_id, step))
    return out


def mint_scope_violations(mints):
    """Every mint names its permissions. A mint with `owner:` and no
    `repositories:` is org-wide, so it is refused, except the one metadata-only
    lister (ADR-0987). In ci-release.yaml every mint must set `repositories:`,
    owner or not."""
    bad = []
    for wf, job_id, step in mints:
        w = lower_keys(step.get("with"))
        perms = {k: v for k, v in w.items() if k.startswith("permission-")}
        where = "%s/%s/%s" % (wf, job_id, step.get("id"))
        if not PINNED_MINT.match(str(step.get("uses", ""))):
            bad.append(where + ": mint is not pinned to a 40-hex sha")
        if wf == "ci-release.yaml" and "${{" in str(w.get("repositories", "")):
            bad.append(where + ": repositories: must be a literal in ci-release")
        if not perms:
            bad.append(where + ": no explicit permission-*")
        if repositories_of(step):
            continue
        if wf == "ci-release.yaml":
            bad.append(where + ": no repositories: in ci-release")
        elif "owner" in w:
            lister = (
                wf == "fan-out.yaml"
                and step.get("id") == "lister"
                and perms == {"permission-metadata": "read"}
            )
            if not lister:
                bad.append(where + ": owner without repositories is org-wide")
    return bad


def test_every_mint_in_every_workflow_is_scoped():
    mints = all_mints()
    assert mint_scope_violations(mints) == []
    # NOT VACUOUS: the scan sees the mints known to exist, in several files.
    files = {wf for wf, _j, _s in mints}
    assert {"ci-release.yaml", "ci-pr.yaml", "fan-out.yaml", "repin.yaml", "sweep.yaml"} <= files
    assert len(mints) >= 7


def _mint(wf="ci-release.yaml", **with_):
    base = {"permission-contents": "write"}
    base.update(with_)
    return (wf, "j", {"id": "m", "uses": "actions/create-github-app-token@" + "a" * 40, "with": base})


def test_mutation_a_mint_without_permissions_reddens():
    m = _mint(repositories="argocd")
    del m[2]["with"]["permission-contents"]
    assert mint_scope_violations([m])


def test_mutation_an_empty_repositories_reddens():
    for empty in ("", "  \n ", ",", None, []):
        assert mint_scope_violations([_mint(repositories=empty)]), repr(empty)


def test_mutation_an_owner_wide_mint_in_any_workflow_reddens():
    for wf in ("sweep.yaml", "repin.yaml", "ci-pr.yaml", "fan-out.yaml"):
        assert mint_scope_violations([_mint(wf, owner="o")]), wf


def test_mutation_the_lister_exception_is_exactly_metadata_read():
    ok = ("fan-out.yaml", "j", {"id": "lister", "uses": "actions/create-github-app-token@" + "a" * 40,
                                  "with": {"owner": "o", "permission-metadata": "read"}})
    assert mint_scope_violations([ok]) == []
    wide = copy.deepcopy(ok)
    wide[2]["with"]["permission-contents"] = "read"
    assert mint_scope_violations([wide])
    renamed = copy.deepcopy(ok)
    renamed[2]["id"] = "other"
    assert mint_scope_violations([renamed])
    moved = ("sweep.yaml",) + ok[1:]
    assert mint_scope_violations([moved])


def test_mutation_an_unscoped_mint_added_to_a_real_workflow_reddens():
    mints = all_mints() + [_mint("ci-release.yaml")]
    assert mint_scope_violations(mints)


def test_mutation_a_capitalised_owner_key_reddens():
    for wf in ("sweep.yaml", "ci-release.yaml", "repin.yaml"):
        m = _mint(wf, Owner="yadgarhq")
        assert mint_scope_violations([m]), wf
    m = _mint("sweep.yaml", Owner="yadgarhq", Repositories="x")
    assert mint_scope_violations([m]) == []
    # A capitalised `Permission-Contents` still counts as an explicit permission.
    m = _mint("sweep.yaml", Repositories="x")
    m[2]["with"]["Permission-Contents"] = m[2]["with"].pop("permission-contents")
    assert mint_scope_violations([m]) == []
    assert repositories_of(m[2]) == ["x"]


def test_mutation_a_templated_repositories_in_ci_release_reddens():
    assert mint_scope_violations([_mint(repositories="${{ inputs.repos }}")])
    assert mint_scope_violations([_mint("sweep.yaml", repositories="${{ matrix.repo }}")]) == []


def test_mutation_an_unpinned_mint_in_any_workflow_reddens():
    for uses in (
        "actions/create-github-app-token@v3",
        "actions/create-github-app-token@" + "a" * 39,
        "actions/create-github-app-token",
        "other/create-github-app-token@" + "a" * 40,
    ):
        m = _mint("sweep.yaml", repositories="x")
        m[2]["uses"] = uses
        assert mint_scope_violations([m]), uses
