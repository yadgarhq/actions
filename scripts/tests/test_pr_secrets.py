"""YAADGAAR 518 (h). What `pr_secrets.py` REFUSES: a secret on the pull request path.

THE GUARD ADR-1119's REFRESH NEEDS. A dependabot pull request's CI runs in
GitHub's dependabot sandbox: no repository secret reaches it. Once the sweeper
refreshes it, the App made the newest push, so the `synchronize` run is the
App's and the sandbox no longer applies. Dependabot-authored code then runs
with whatever secrets a pull-request job reads. That is safe only while no job
reachable from `pull_request`, `pull_request_target` or `workflow_run` reads a
secret other than `GITHUB_TOKEN`. This suite pins that the guard refuses each
way a secret can reach such a job, and that this repository's own workflows,
and the `ci-pr.yaml` every consumer calls, pass it.

Every green case below is paired with a red one that changes one thing.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import pr_secrets  # noqa: E402

SCRIPT = REPO / "scripts" / "pr_secrets.py"


def tree(tmp_path, **workflows):
    """A repository root holding `.github/workflows/<name>.yaml` for each argument."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, text in workflows.items():
        (wf / f"{name}.yaml").write_text(textwrap.dedent(text))
    return tmp_path


def problems(root, actions=None):
    return pr_secrets.check(root, actions or root)[0]


def one_job(on, job):
    return f"on: {on}\njobs:\n  j:\n" + textwrap.indent(textwrap.dedent(job), "    ")


STEP = "runs-on: ubuntu-latest\nsteps:\n  - run: echo hi\n    env:\n      X: ${{ %s }}\n"


@pytest.mark.parametrize("on", ["pull_request", "[pull_request]", "{pull_request: {types: [opened]}}",
                                "pull_request_target", "workflow_run", "[push, pull_request]"])
def test_a_pull_request_job_reading_a_secret_is_refused(tmp_path, on):
    root = tree(tmp_path, ci=one_job(on, STEP % "secrets.NPM_TOKEN"))
    found = problems(root)
    assert len(found) == 1 and "NPM_TOKEN" in found[0], found


@pytest.mark.parametrize("expr", ["secrets.GITHUB_TOKEN", "secrets.github_token", "github.token",
                                  "inputs.secrets_dir"])
def test_the_github_token_and_lookalikes_are_not_a_secret(tmp_path, expr):
    assert problems(tree(tmp_path, ci=one_job("pull_request", STEP % expr))) == []


@pytest.mark.parametrize("expr", ["toJSON(secrets)", "secrets['GITHUB_TOKEN']", "secrets[inputs.name]",
                                  "secrets.GITHUB_TOKEN_X", "format('{0}', secrets.APP_KEY)"])
def test_any_other_use_of_the_secrets_context_is_refused(tmp_path, expr):
    assert len(problems(tree(tmp_path, ci=one_job("pull_request", STEP % expr)))) == 1


@pytest.mark.parametrize("on", ["push", "[push, schedule, workflow_dispatch]", "workflow_call"])
def test_a_workflow_with_no_pull_request_trigger_is_not_judged(tmp_path, on):
    assert problems(tree(tmp_path, ci=one_job(on, STEP % "secrets.APP_KEY"))) == []


@pytest.mark.parametrize("cond", [
    "github.event_name == 'push'",
    "${{ github.event_name == 'push' }}",
    "github.event_name == 'push' && github.ref == 'refs/heads/main'",
    "github.event_name == 'PUSH'",  # GitHub compares strings ignoring case
    "startsWith(github.ref, 'refs/tags/')",
    "github.ref == 'refs/heads/main' && github.event_name == 'schedule'",
    "false",
])
def test_a_job_whose_if_excludes_every_pull_request_event_is_not_reachable(tmp_path, cond):
    job = f"if: {cond!r}\n" + STEP % "secrets.APP_KEY"
    on = "[pull_request, pull_request_target, workflow_run, push]"
    assert problems(tree(tmp_path, ci=one_job(on, job))) == []


@pytest.mark.parametrize("cond", [
    "github.event_name != 'push'",
    "github.event_name == 'push' || github.event_name == 'pull_request'",
    "(github.event_name == 'push')",  # not proven: fail closed
    "github.ref == 'refs/heads/main'",  # pull_request_target runs on the base ref
    "always()",
    "github.event_name == 'pull_request_target'",
    "true",
])
def test_a_job_whose_if_does_not_provably_exclude_the_event_is_reachable(tmp_path, cond):
    job = f"if: {cond!r}\n" + STEP % "secrets.APP_KEY"
    on = "[pull_request, pull_request_target, workflow_run]"
    assert problems(tree(tmp_path, ci=one_job(on, job))), cond


def test_each_event_is_judged_on_its_own(tmp_path):
    """`!= 'pull_request'` excludes pull_request only: pull_request_target is still reachable."""
    job = "if: github.event_name != 'pull_request'\n" + STEP % "secrets.APP_KEY"
    found = problems(tree(tmp_path, ci=one_job("[pull_request, pull_request_target]", job)))
    assert len(found) == 1 and "pull_request_target" in found[0], found
    assert problems(tree(tmp_path / "b", ci=one_job("[pull_request]", job))) == []


def test_the_event_name_is_compared_ignoring_case_as_github_does(tmp_path):
    job = "if: github.event_name != 'PULL_REQUEST'\n" + STEP % "secrets.APP_KEY"
    assert problems(tree(tmp_path / "a", ci=one_job("[pull_request]", job))) == []
    assert problems(tree(tmp_path / "b", ci=one_job("[pull_request_target]", job)))


def test_a_bare_if_expression_is_scanned_too(tmp_path):
    job = "runs-on: ubuntu-latest\nsteps:\n  - if: secrets.APP_KEY != ''\n    run: echo\n"
    found = problems(tree(tmp_path, ci=one_job("pull_request", job)))
    assert len(found) == 1 and "APP_KEY" in found[0], found


@pytest.mark.parametrize("callee", [False, True])
def test_a_secret_in_the_workflow_env_on_the_pull_request_path_is_refused(tmp_path, callee):
    """A top-level `env:` reaches every job, so it is read for a called workflow too."""
    env = "env:\n  K: ${{ secrets.APP_KEY }}\n"
    if callee:
        root = tree(tmp_path, ci=caller(), callee=env + CALLEE)
    else:
        root = tree(tmp_path, ci=env + one_job("pull_request", STEP % "github.token"))
    found = problems(root)
    assert len(found) == 1 and "workflow env" in found[0] and "APP_KEY" in found[0], found
    push = tree(tmp_path / "push", ci=env + one_job("push", STEP % "github.token"))
    assert problems(push) == []


CALLER = """
on: [pull_request, push]
jobs:
  ci:
    if: {cond}
    uses: {uses}
    {extra}
"""

CALLEE = """
on:
  workflow_call:
    secrets:
      APP_KEY:
        required: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps: [{run: echo}]
  version:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps: [{run: echo, env: {K: "${{ secrets.APP_KEY }}"}}]
"""


def caller(cond="github.event_name == 'pull_request'", uses="./.github/workflows/callee.yaml", extra=""):
    return CALLER.format(cond=f"{cond!r}", uses=uses, extra=extra)


def test_a_reusable_workflow_is_followed_and_its_push_only_job_is_not_reachable(tmp_path):
    root = tree(tmp_path, ci=caller(), callee=CALLEE)
    found, reached = pr_secrets.check(root, root)
    assert found == []
    assert ("callee.yaml", "build", "pull_request") in {(p.name, j, e) for p, j, e in reached}
    assert "version" not in {j for _, j, _ in reached}


def test_a_secret_in_a_reachable_job_of_a_called_workflow_is_refused(tmp_path):
    callee = CALLEE.replace("if: github.event_name == 'push' && ", "if: ")
    found = problems(tree(tmp_path, ci=caller(), callee=callee))
    assert len(found) == 1 and "callee.yaml" in found[0] and "version" in found[0], found


def test_a_push_only_caller_may_pass_secrets_and_a_pull_request_caller_may_not(tmp_path):
    extra = "secrets:\n      APP_KEY: ${{ secrets.APP_KEY }}"
    assert problems(tree(tmp_path / "a", ci=caller("github.event_name == 'push'", extra=extra),
                         callee=CALLEE)) == []
    found = problems(tree(tmp_path / "b", ci=caller(extra=extra), callee=CALLEE))
    assert len(found) == 1 and "ci.yaml" in found[0] and "APP_KEY" in found[0], found


def test_secrets_inherit_on_the_pull_request_path_is_refused(tmp_path):
    found = problems(tree(tmp_path, ci=caller(extra="secrets: inherit"), callee=CALLEE))
    assert len(found) == 1 and "inherit" in found[0], found


def test_the_actions_reusable_workflow_is_read_from_the_actions_tree(tmp_path):
    actions = tree(tmp_path / "actions", callee=CALLEE.replace("if: github.event_name == 'push' && ", "if: "))
    uses = "yadgarhq/actions/.github/workflows/callee.yaml@main"
    consumer = tree(tmp_path / "consumer", ci=caller(uses=uses))
    found = problems(consumer, actions)
    assert len(found) == 1 and "version" in found[0], found


@pytest.mark.parametrize("uses", ["someone/else/.github/workflows/x.yaml@v1",
                                  "./.github/workflows/missing.yaml",
                                  "./.github/workflows/../../evil.yaml"])
def test_a_reusable_workflow_the_guard_cannot_read_is_refused(tmp_path, uses):
    found = problems(tree(tmp_path, ci=caller(uses=uses), callee=CALLEE))
    assert len(found) == 1 and "cannot read" in found[0], found


def test_a_call_cycle_ends(tmp_path):
    loop = "on: [pull_request, workflow_call]\njobs:\n  again:\n    uses: ./.github/workflows/ci.yaml\n"
    assert problems(tree(tmp_path, ci=loop)) == []


def test_an_unparseable_or_jobless_workflow_is_refused(tmp_path):
    assert problems(tree(tmp_path / "a", ci="on: pull_request\njobs: [x]\n"))
    assert problems(tree(tmp_path / "b", ci="on: pull_request\n"))
    assert problems(tree(tmp_path / "c", ci="on: [pull_request\n"))


# This repository: the guard over its own workflows, and over the `ci-pr.yaml`
# every consumer calls (through `ci-self.yaml`'s `ci` job).


def test_this_repository_has_no_secret_on_the_pull_request_path():
    found, reached = pr_secrets.check(REPO, REPO)
    assert found == []
    names = {(p.name, j) for p, j, _ in reached}
    # Not vacuous: ci-self's pull request jobs and ci-pr's are judged, and the
    # push-only jobs that do read the App secrets are the ones left out.
    assert ("ci-self.yaml", "pytest") in names and ("ci-pr.yaml", "passed") in names
    assert ("ci-pr.yaml", "test") in names and ("seed-declaration.yaml", "declaration") in names
    assert ("ci-self.yaml", "main") not in names and ("ci-pr.yaml", "version") not in names


@pytest.mark.parametrize("path, job, cond", [
    ("ci-self.yaml", "main", "always()"),
    ("ci-pr.yaml", "version", "github.ref == 'refs/heads/main'"),
])
def test_opening_a_push_only_secret_job_to_pull_requests_is_refused(tmp_path, path, job, cond):
    """The same check over a copy of this repository with ONE job's `if` widened."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for f in (REPO / ".github" / "workflows").glob("*.yaml"):
        (wf / f.name).write_text(f.read_text())
    doc = yaml.safe_load((wf / path).read_text())
    doc["jobs"][job]["if"] = cond
    (wf / path).write_text(yaml.safe_dump(doc, sort_keys=False))
    found = pr_secrets.check(tmp_path, tmp_path)[0]
    assert found and all(job in f for f in found), found


def test_the_command_line_exits_red_on_a_problem_and_green_on_this_repository(tmp_path):
    ok = subprocess.run([sys.executable, str(SCRIPT), str(REPO)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    root = tree(tmp_path, ci=one_job("pull_request", STEP % "secrets.APP_KEY"))
    bad = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True)
    assert bad.returncode == 1 and "::error" in bad.stdout and "APP_KEY" in bad.stdout, bad.stdout
