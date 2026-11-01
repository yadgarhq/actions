"""YAADGAAR 231, ADR-0983 step 2. What `hook_tools_adopted.py` refuses.

A pull request to `yadgarhq/actions` that moves the `version` of a tool a hook
asserts (gitleaks, hadolint, actionlint, cargo-deny) is red while any consumer
pins a `rev` whose `versions.yaml` does not accept the new version. Public
consumers are read with `GITHUB_TOKEN`; a private one is judged from the rev
`classes.json` attests for it, or is exempt there by a reviewed reason.

The client and `show` are fakes: the script is judged on what it decides from
the facts, and one test at the end builds a real merge commit to prove the
base and head it reads are the merge's two sides.

Run: python3 -m pytest scripts/tests/test_hook_tools_adopted.py -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import hook_tools_adopted as hta  # noqa: E402
from repin import Refusal  # noqa: E402

OWNER = "yadgarhq"
WORKFLOW = REPO / ".github" / "workflows" / "ci-pr.yaml"
REAL_CLASSES = json.loads((REPO / "classes.json").read_text())

# Version numbers here are invented and none is a pin: `versions_parity.py`
# scans the workflows, hooks and gate scripts, never `scripts/tests/`.
OLD, NEW = "8.30.1", "8.31.0"


def versions(gitleaks=OLD, accept=None, hadolint="2.14.0"):
    extra = f', accept: [{", ".join(json.dumps(a) for a in accept)}]' if accept else ""
    return (
        "tools:\n"
        f'  gitleaks: {{ version: "{gitleaks}", sites: 2{extra} }}\n'
        f'  hadolint: {{ version: "{hadolint}", sites: 1 }}\n'
        '  actionlint: { version: "1.7.12", sites: 1 }\n'
        '  cargo-deny: { version: "0.20.2", sites: 1 }\n'
        '  helm: { version: "3.18.4", sites: 6 }\n'
    )


def config(rev):
    return (
        "repos:\n"
        "  - repo: https://github.com/yadgarhq/actions\n"
        f"    rev: {rev}\n"
        "    hooks:\n"
        "      - id: gitleaks\n"
    )


def meta(name, topic="base", private=False, archived=False):
    return {"name": name, "owner": OWNER, "archived": archived, "fork": False,
            "disabled": False, "private": private, "default_branch": "main",
            "topics": [f"yadgar-class-{topic}"] if topic else []}


class Client:
    """The two reads the check makes, from dicts. Counts its calls."""

    def __init__(self, repos, configs):
        self.repos, self.configs, self.calls = repos, configs, 0

    def list_repos(self, owner):
        self.calls += 1
        assert owner == OWNER
        return self.repos

    def config(self, owner, repo, ref):
        self.calls += 1
        assert (owner, ref) == (OWNER, "main")
        return self.configs.get(repo)


class Unreachable:
    def list_repos(self, owner):
        raise AssertionError("no hook-asserted tool moved, so nothing may be read")

    config = list_repos


def classes(private=None, exclude=None):
    data = json.loads(json.dumps(REAL_CLASSES))
    data["sweep"]["exclude"] = exclude if exclude is not None else {}
    data["private_consumers"] = private if private is not None else {}
    return data


def run(client, show, base=versions(), head=versions(gitleaks=NEW), data=None):
    return hta.run(base, head, data if data is not None else classes(), client, OWNER, show)


def red_repos(rows):
    return sorted(r["repo"] for r in rows if not r["ok"])


# --------------------------------------------------------------------------
# what counts as a move
# --------------------------------------------------------------------------


def test_the_hook_asserted_set_is_the_parity_checks_and_the_hooks():
    import versions_parity

    assert hta.HOOK_ASSERTED == versions_parity.HOOK_ASSERTED
    assert set(hta.HOOK_ASSERTED) == set(hta.tool_version.TOOLS)


def test_a_change_with_no_hook_asserted_move_reads_nothing():
    head = versions().replace('"3.18.4"', '"3.19.0"')  # helm is not hook-asserted
    ok, rows, moves = run(Unreachable(), lambda rev: None, head=head)
    assert ok and rows == [] and moves == {}


def test_step_one_adding_to_accept_is_not_a_move():
    ok, _, moves = run(Unreachable(), lambda rev: None, head=versions(accept=[NEW]))
    assert ok and moves == {}


def test_a_moved_version_is_reported_old_and_new():
    assert hta.moves(versions(), versions(gitleaks=NEW, hadolint="2.15.0")) == {
        "gitleaks": (OLD, NEW), "hadolint": ("2.14.0", "2.15.0")}


def test_a_tool_new_on_the_head_is_not_a_move():
    base = "".join(l for l in versions().splitlines(True) if "hadolint" not in l)
    assert hta.moves(base, versions()) == {}


def test_an_unreadable_head_is_refused_not_passed():
    with pytest.raises(Refusal):
        hta.moves(versions(), "tools:\n  gitleaks: nonsense\n")


# --------------------------------------------------------------------------
# public consumers, read through the API
# --------------------------------------------------------------------------


def two_consumers(rev_a="v1.50.0", rev_b="v1.50.0"):
    return Client([meta("chart", "chart"), meta("store", "rust-lib")],
                  {"chart": config(rev_a), "store": config(rev_b)})


def test_every_consumer_on_a_rev_that_accepts_the_new_version_is_green():
    show = {"v1.50.0": versions(accept=[NEW])}.get
    ok, rows, _ = run(two_consumers(), show)
    assert ok, rows
    assert [r["repo"] for r in rows] == ["chart", "store"]


def test_a_rev_whose_own_version_is_the_new_one_is_adopted():
    ok, rows, _ = run(two_consumers(), {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert ok, rows


def test_one_consumer_behind_is_red_and_named_with_its_rev():
    show = {"v1.50.0": versions(accept=[NEW]), "v1.37.1": versions()}.get
    ok, rows, _ = run(two_consumers(rev_b="v1.37.1"), show)
    assert not ok
    assert red_repos(rows) == ["store"]
    row = next(r for r in rows if r["repo"] == "store")
    assert row["rev"] == "v1.37.1" and "gitleaks" in row["why"] and NEW in row["why"]


def test_the_rev_is_judged_on_its_own_file_not_on_the_head():
    """The head accepts NEW trivially; a consumer's hook reads its own rev."""
    show = {"v1.37.1": versions()}.get
    ok, _, _ = run(two_consumers("v1.37.1", "v1.37.1"), show)
    assert not ok


@pytest.mark.parametrize("rev", ["main", "v1", "1.2.3", "v1.2.3.4", "-v1.2.3", "abc" * 13 + "a"])
def test_a_rev_that_is_not_a_plain_tag_is_red(rev):
    """Red before `show` is asked, even if git would resolve the name."""
    ok, rows, _ = run(two_consumers(rev_a=rev), lambda r: versions(gitleaks=NEW))
    assert not ok and red_repos(rows) == ["chart"]


def test_a_tag_this_repository_does_not_carry_is_red():
    ok, rows, _ = run(two_consumers(rev_a="v9.9.9"), {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert not ok and red_repos(rows) == ["chart"]


def test_an_unreadable_versions_file_at_the_rev_is_red():
    show = {"v1.50.0": versions(gitleaks=NEW), "v1.10.0": "tools:\n  oops\n"}.get
    ok, rows, _ = run(two_consumers(rev_a="v1.10.0"), show)
    assert not ok and red_repos(rows) == ["chart"]


def test_a_consumer_with_no_config_is_red():
    client = two_consumers()
    del client.configs["store"]
    ok, rows, _ = run(client, {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert not ok and red_repos(rows) == ["store"]


def test_a_consumer_with_no_actions_block_is_red():
    client = two_consumers()
    client.configs["store"] = "repos: []\n"
    ok, rows, _ = run(client, {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert not ok and red_repos(rows) == ["store"]


def test_a_failed_read_is_red_and_named():
    class Failing(Client):
        def config(self, owner, repo, ref):
            if repo == "chart":
                raise Refusal("gh api ...: HTTP 502")
            return super().config(owner, repo, ref)

    client = Failing(two_consumers().repos, two_consumers().configs)
    ok, rows, _ = run(client, {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert not ok and red_repos(rows) == ["chart"]
    assert "502" in next(r for r in rows if r["repo"] == "chart")["why"]


def test_non_consumers_are_not_read():
    client = Client(
        [meta("chart", "chart"), meta("deploy", None), meta("old", archived=True),
         meta("actions", None), meta("two", "base") | {"topics": ["yadgar-class-a", "yadgar-class-b"]}],
        {"chart": config("v1.50.0")})
    ok, rows, _ = run(client, {"v1.50.0": versions(gitleaks=NEW)}.get)
    assert ok, rows
    assert [r["repo"] for r in rows] == ["chart"]


def test_a_failed_listing_is_refused():
    class NoList(Client):
        def list_repos(self, owner):
            raise Refusal("gh api orgs/yadgarhq/repos: HTTP 403")

    with pytest.raises(Refusal):
        run(NoList([], {}), lambda rev: None)


# --------------------------------------------------------------------------
# private consumers, attested in classes.json
# --------------------------------------------------------------------------


EXCLUDE = {"docs": "Private.", "argocd-verify": "Private."}


def test_a_private_consumer_with_no_attestation_is_red_and_named():
    data = classes(private={"docs": {"rev": "v1.50.0"}}, exclude=EXCLUDE)
    ok, rows, _ = run(two_consumers(), {"v1.50.0": versions(gitleaks=NEW)}.get, data=data)
    assert not ok and red_repos(rows) == ["argocd-verify"]
    row = next(r for r in rows if r["repo"] == "argocd-verify")
    assert "private_consumers" in row["why"]


def test_an_attested_rev_is_judged_like_a_public_one():
    data = classes(private={"docs": {"rev": "v1.37.1"}, "argocd-verify": {"rev": "v1.50.0"}},
                   exclude=EXCLUDE)
    show = {"v1.50.0": versions(accept=[NEW]), "v1.37.1": versions()}.get
    ok, rows, _ = run(two_consumers(), show, data=data)
    assert not ok and red_repos(rows) == ["docs"]
    assert next(r for r in rows if r["repo"] == "docs")["source"] == "attested"


def test_every_private_consumer_attested_adopted_is_green():
    data = classes(private={"docs": {"rev": "v1.50.0"}, "argocd-verify": {"rev": "v1.50.0"}},
                   exclude=EXCLUDE)
    ok, rows, _ = run(two_consumers(), {"v1.50.0": versions(accept=[NEW])}.get, data=data)
    assert ok, rows
    assert len(rows) == 4


def test_an_exempt_private_consumer_is_green_and_says_why():
    data = classes(private={"docs": {"exempt": "Decided 2026-10-30: docs follows by hand."},
                            "argocd-verify": {"rev": "v1.50.0"}}, exclude=EXCLUDE)
    ok, rows, _ = run(two_consumers(), {"v1.50.0": versions(accept=[NEW])}.get, data=data)
    assert ok, rows
    row = next(r for r in rows if r["repo"] == "docs")
    assert row["source"] == "exempt" and "by hand" in row["why"]


def test_an_attestation_for_a_repository_the_listing_reads_is_red():
    """A measurement is never overridden by an attestation: delete the entry."""
    data = classes(private={"chart": {"exempt": "no"}})
    ok, rows, _ = run(two_consumers(), {"v1.50.0": versions(accept=[NEW])}.get, data=data)
    assert not ok and red_repos(rows) == ["chart"]


def test_a_private_repository_in_the_listing_is_never_read():
    """A local token sees private repositories; CI's never does. Both decide alike."""
    client = Client([meta("chart", "chart"), meta("docs", private=True)], {"chart": config("v1.50.0")})
    data = classes(private={"docs": {"rev": "v1.37.1"}}, exclude={"docs": "Private."})
    show = {"v1.50.0": versions(accept=[NEW]), "v1.37.1": versions()}.get
    ok, rows, _ = run(client, show, data=data)
    assert not ok and red_repos(rows) == ["docs"]
    assert client.calls == 2  # the listing and chart's config; never docs


def test_a_private_consumer_in_the_listing_without_attestation_is_red():
    client = Client([meta("docs", private=True)], {})
    ok, rows, _ = run(client, {}.get, data=classes())
    assert not ok and red_repos(rows) == ["docs"]


@pytest.mark.parametrize("entry", [
    {}, {"rev": "v1.2.3", "exempt": "x"}, {"rev": "main"}, {"rev": 1}, {"exempt": " "},
    {"exempt": "x", "note": "y"}, "v1.2.3", None,
])
def test_a_malformed_attestation_is_refused(entry):
    with pytest.raises(Refusal):
        hta.load_private({"private_consumers": {"docs": entry}})


def test_a_missing_attestation_block_is_refused():
    with pytest.raises(Refusal):
        hta.load_private({})


def test_the_real_classes_json_attests_every_excluded_private_consumer():
    private = hta.load_private(REAL_CLASSES)
    assert set(REAL_CLASSES["sweep"]["exclude"]) <= set(private)
    for entry in private.values():
        assert "rev" in entry or "exempt" in entry


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_the_report_names_every_row_and_the_move():
    show = {"v1.50.0": versions(accept=[NEW]), "v1.37.1": versions()}.get
    ok, rows, moves = run(two_consumers(rev_b="v1.37.1"), show)
    text = hta.report(ok, rows, moves)
    assert f"gitleaks {OLD} -> {NEW}" in text
    assert "`chart`" in text and "`store`" in text and "behind" in text


def test_the_report_cell_cannot_inject_a_workflow_command():
    rows = [{"repo": "x", "source": "public", "rev": "::error::x|y", "ok": False, "why": "`a`"}]
    text = hta.report(False, rows, {"gitleaks": (OLD, NEW)})
    assert "::error" not in text and "|y" not in text


# --------------------------------------------------------------------------
# the workflow
# --------------------------------------------------------------------------


def jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_the_job_is_gated_by_passed():
    assert "hook_tools_adopted" in jobs()["passed"]["needs"]


def test_the_job_runs_on_pull_requests_to_actions_only():
    cond = " ".join(jobs()["hook_tools_adopted"]["if"].split())
    for part in ("github.event_name != 'push'", "needs.detect.outputs.text_edit != 'true'",
                 "needs.detect.outputs.config_only != 'true'",
                 "github.repository == 'yadgarhq/actions'"):
        assert part in cond


def test_the_job_carries_no_secret_and_reads_with_github_token():
    job = jobs()["hook_tools_adopted"]
    text = json.dumps(job)
    assert "secrets." not in text
    envs = [s.get("env", {}) for s in job["steps"]]
    assert any(e.get("GH_TOKEN") == "${{ github.token }}" for e in envs)
    assert "permissions" not in job  # the workflow's contents: read, nothing wider


def test_the_job_checks_out_every_tag():
    checkout = jobs()["hook_tools_adopted"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False


# --------------------------------------------------------------------------
# the base and head, from a real merge commit
# --------------------------------------------------------------------------


def _git_env():
    """Hermetic: `git commit` exports GIT_DIR to the hook running this suite."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
         "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_git_env(),
    ).stdout


def merge_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "versions.yaml").write_text(versions(accept=[NEW]))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "tag", "v1.50.0")
    git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "versions.yaml").write_text(versions(gitleaks=NEW))
    git(tmp_path, "commit", "-q", "-am", "move")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "other").write_text("x\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "main moves on")
    git(tmp_path, "merge", "-q", "--no-ff", "-m", "merge", "pr")


def test_sides_are_the_merge_commits_first_parent_and_itself(tmp_path):
    merge_repo(tmp_path)
    base, head = hta.sides(tmp_path, env=_git_env())
    assert hta.moves(base, head) == {"gitleaks": (OLD, NEW)}
    assert hta.git_show(tmp_path, "v1.50.0", env=_git_env()) == versions(accept=[NEW])
    assert hta.git_show(tmp_path, "v9.9.9", env=_git_env()) is None


def test_sides_refuse_a_head_that_is_not_a_merge(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "versions.yaml").write_text(versions())
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "only")
    with pytest.raises(Refusal):
        hta.sides(tmp_path, env=_git_env())


@pytest.mark.parametrize("repo, result, ok", [
    ("yadgarhq/actions", "success", True),
    ("yadgarhq/actions", "skipped", False),
    ("yadgarhq/store", "skipped", True),
    ("yadgarhq/store", "success", False),
])
def test_the_merge_gate_holds_the_job_to_its_condition(repo, result, ok):
    import ci_verdict

    conditions = ci_verdict.load_conditions(WORKFLOW, "passed")
    ctx = {job: {"result": "success", "outputs": {}} for job, _ in conditions}
    for job in ("test", "proto", "rust_parity"):
        ctx[job]["result"] = "skipped"
    ctx["hook_tools_adopted"]["result"] = result
    ctx["detect"]["outputs"] = {"rust": "false", "proto": "false", "text_edit": "false",
                                "config_only": "false"}
    fail = lambda url: pytest.fail(url)  # noqa: E731 - text_edit and config_only are false
    ci_verdict.register_deferred_reference(
        ci_verdict.TEXT_EDIT_REF,
        ci_verdict.make_text_edit_resolver(ctx, repo, "0" * 40, "1", "ci / passed", "", fetch=fail))
    ci_verdict.register_deferred_reference(
        ci_verdict.CONFIG_ONLY_REF,
        ci_verdict.make_config_only_resolver(ctx, repo, "main", "0" * 40, "t", fetch=fail))
    try:
        resolve = ci_verdict.make_resolver("pull_request", "User", repo, ctx)
        got, _ = ci_verdict.verdict(conditions, ctx, resolve)
    finally:
        ci_verdict._DEFERRED.clear()
    assert got is ok
