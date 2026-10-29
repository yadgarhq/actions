"""YAADGAAR 442 (c). What `sweep.py` asserts before it merges or refreshes.

THE FIXTURES ARE RECORDED, NOT INVENTED. `fixtures/sweep/*.json` hold the
GitHub API responses `sweep.py` reads, captured read-only on 2026-10-28 for
three real adoption pull requests, keyed by the exact path `sweep.py` requests,
and trimmed to the fields it reads:

    store-45   the merge baseline (rust-lib, v1.37.1 -> v1.38.2)
    chart-75   a second merge baseline (chart, v1.37.0 -> v1.38.2)
    yadgar-98  the refresh baseline: BEHIND its base by two commits, and the
               base did not touch `.pre-commit-config.yaml`

store#45 and chart#75 were merged by hand while this was built. Each fixture's
`__about__.normalised` lists every field turned back into the open, green state
the pull request had before that merge: state, merged, merged_at,
merge_commit_sha, mergeable, mergeable_state, the merged/closed/
head_ref_deleted timeline events, and the base branch ref (moved by the merge
back to the pull request's `base.sha`). Nothing else was edited.

EVERY RED TEST CHANGES ONE FIELD of a green baseline and expects a SKIP whose
reason names the condition that failed. `test_the_baselines_are_green` is what
makes that meaningful: if trimming had dropped a field the predicate reads, the
baseline itself would skip.

Run: python3 -m pytest scripts/tests/ -q
"""

import base64
import copy
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import fan_out  # noqa: E402
import sweep  # noqa: E402
from repin import Refusal  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sweep"
WORKFLOW = REPO / ".github" / "workflows" / "sweep.yaml"
SCRIPT = REPO / "scripts" / "sweep.py"
CLASSES_TEXT = (REPO / "classes.json").read_text()
NOW = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.timezone.utc)
OWNER = "yadgarhq"
BOT, DEPENDABOT, WEB_FLOW = 324366854, 49699333, 19864447


def classes(**sweep_overrides):
    data = sweep.load(CLASSES_TEXT)
    data["sweep"].update(sweep_overrides)
    return data


# --------------------------------------------------------------------------
# the fake API
# --------------------------------------------------------------------------


class Fake:
    """Answers `get` from a recorded dict; records every write; never reaches GitHub."""

    def __init__(self, data, writes=None, refuse_merge=None):
        self.data = data
        self.writes = [] if writes is None else writes
        self.refuse_merge = refuse_merge

    def get(self, path, fresh=False):
        if path not in self.data:
            raise Refusal(f"gh api {path}: HTTP 404")
        return copy.deepcopy(self.data[path])

    def graphql_threads(self, owner, name, n):
        return copy.deepcopy(self.data["graphql"])

    def merge_pr(self, owner, name, n, head, title, message):
        self.writes.append(("merge", owner, name, n,
                            {"merge_method": "squash", "sha": head,
                             "commit_title": title, "commit_message": message}))
        if self.refuse_merge:
            raise Refusal(f"gh api merge: {self.refuse_merge}")
        pr = self.data[f"repos/{owner}/{name}/pulls/{n}"]
        pr.update(merged=True, state="closed", merge_commit_sha="f" * 40)

    def rewrite_branch(self, owner, name, branch, parent, path, text, message, exists,
                       expect=None):
        self.writes.append(("rewrite", owner, name, branch, parent, path, text, message, exists,
                            expect))


class Case:
    """One fixture, with named handles on the responses tests change."""

    def __init__(self, name):
        self.data = json.loads((FIXTURES / f"{name}.json").read_text())
        self.data.pop("__about__")
        repo, n = name.rsplit("-", 1)
        self.repo, self.n = repo, int(n)
        self.r = f"repos/{OWNER}/{repo}"
        self.pr = self.data[f"{self.r}/pulls/{self.n}"]
        self.head = self.pr["head"]["sha"]
        self.tip = self.one(rf"^{self.r}/git/ref/heads/")["object"]["sha"]
        self.data[f"{self.r}/git/ref/heads/{fan_out.BRANCH}"] = {
            "ref": f"refs/heads/{fan_out.BRANCH}", "object": {"sha": self.head, "type": "commit"}}
        for status in ("in_progress", "queued"):  # `act` asks again (yaadgaar 470)
            self.data[f"repos/{OWNER}/actions/actions/workflows/fan-out.yaml/runs?status={status}"] = {
                "total_count": 0}

    def one(self, pattern):
        hits = [v for k, v in self.data.items() if re.search(pattern, k)]
        assert len(hits) == 1, pattern
        return hits[0]

    @property
    def meta(self):
        return self.data[self.r]

    @property
    def commit(self):
        return self.data[f"{self.r}/commits/{self.head}"]

    @property
    def commits(self):
        return self.data[f"{self.r}/pulls/{self.n}/commits?per_page=100"]

    @property
    def timeline(self):
        return self.data[f"{self.r}/issues/{self.n}/timeline?per_page=100"]

    @property
    def reviews(self):
        return self.data[f"{self.r}/pulls/{self.n}/reviews?per_page=100"]

    @property
    def compare(self):
        return self.data[f"{self.r}/compare/{self.tip}...{self.head}"]

    @property
    def checks(self):
        return self.data[f"{self.r}/commits/{self.head}/check-runs?per_page=100"]

    def check(self, name):
        hits = [c for c in self.checks["check_runs"] if c["name"] == name]
        assert len(hits) == 1
        return hits[0]

    @property
    def run(self):
        hits = [r for r in self.data[f"{self.r}/actions/runs?head_sha={self.head}&per_page=100"]
                ["workflow_runs"] if r["path"] == sweep.CI_WORKFLOW]
        assert len(hits) == 1
        return hits[0]

    @property
    def runs(self):
        return self.data[f"{self.r}/actions/runs?head_sha={self.head}&per_page=100"]

    def tree(self, commit):
        return self.data[f"{self.r}/git/trees/{commit}"]

    def entry(self, commit):
        return [e for e in self.tree(commit)["tree"] if e["path"] == ".pre-commit-config.yaml"][0]

    def text(self, commit):
        blob = self.data[f"{self.r}/git/blobs/{self.entry(commit)['sha']}"]
        return base64.b64decode(blob["content"]).decode()

    def set_text(self, commit, text):
        """A new blob for `commit`'s config, so a base and head that shared one do not alias."""
        sha = hashlib.sha1(f"{commit}\0{text}".encode()).hexdigest()
        self.entry(commit)["sha"] = sha
        self.data[f"{self.r}/git/blobs/{sha}"] = {
            "sha": sha, "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}

    @property
    def tag_commit(self):
        ref = self.data["repos/yadgarhq/actions/git/ref/tags/v1.38.2"]["object"]
        return self.data[f"repos/yadgarhq/actions/git/tags/{ref['sha']}"]["object"]["sha"]

    def judge(self, cfg=None, now=NOW):
        return sweep.judge(Fake(self.data), OWNER, self.repo, self.n, cfg or classes(), now)


def merge_case():
    return Case("store-45")


def skipped(case, code, cfg=None, now=NOW):
    got = case.judge(cfg, now)
    assert got["action"] == "skip", got
    assert got["reason"].startswith(code), got["reason"]
    return got


# --------------------------------------------------------------------------
# the baselines
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["store-45", "chart-75"])
def test_the_baselines_are_green(name):
    got = Case(name).judge()
    assert got["action"] == "merge", got


def test_the_behind_baseline_refreshes_onto_the_base_tip():
    case = Case("yadgar-98")
    got = case.judge()
    assert got["action"] == "refresh", got
    assert got["tip"] == case.tip
    assert got["text"] == case.text(case.head)


def test_the_behind_baseline_really_is_behind():
    case = Case("yadgar-98")
    assert case.compare["status"] == "diverged" and case.compare["behind_by"] == 2
    assert case.text(case.tip) == case.text(case.compare["merge_base_commit"]["sha"])


# --------------------------------------------------------------------------
# R: the repository and the mode
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change, code", [
    (lambda m: m.update(private=True), "R1"),
    (lambda m: m.pop("private"), "unexpected API shape"),
    (lambda m: m.update(visibility="internal"), "R1"),
    (lambda m: m.pop("visibility"), "unexpected API shape"),
    (lambda m: m.update(archived=True), "R3"),
    (lambda m: m.update(disabled=True), "R3"),
    (lambda m: m.update(fork=True), "R3"),
    (lambda m: m.update(owner={"login": "someone"}), "R3"),
    (lambda m: m.update(name="bad name"), "R3"),
    (lambda m: m.update(topics=["yadgar-class-rust-lib", "yadgar-class-chart"]), "R3"),
    (lambda m: m.update(topics=[]), "R3"),
    (lambda m: m.update(topics=["yadgar-class-nope"]), "R3"),
])
def test_a_repository_that_is_not_a_public_consumer_is_skipped(change, code):
    case = merge_case()
    change(case.meta)
    skipped(case, code)


def test_an_excluded_repository_is_skipped():
    skipped(merge_case(), "R2: excluded", classes(exclude={"store": "a reason"}))


def test_the_producer_is_skipped():
    case = merge_case()
    case.meta["name"] = "actions"
    skipped(case, "R2: exempt")


def test_the_shipped_exclusions_are_docs_and_argocd_verify_with_reasons():
    shipped = sweep.load(CLASSES_TEXT)["sweep"]
    assert set(shipped["exclude"]) == {"docs", "argocd-verify"}
    assert all(reason.strip() for reason in shipped["exclude"].values())


@pytest.mark.parametrize("event, dry_run, ref, armed, live", [
    ("schedule", "", "refs/heads/main", True, True),
    ("workflow_dispatch", "false", "refs/heads/main", True, True),
    ("workflow_dispatch", "true", "refs/heads/main", True, False),
    ("workflow_dispatch", "", "refs/heads/main", True, False),
    ("schedule", "", "refs/heads/main", False, False),
    ("schedule", "", "refs/heads/main", "true", False),
    ("schedule", "", "refs/heads/main", 1, False),
    ("push", "false", "refs/heads/main", True, False),
    ("pull_request", "false", "refs/heads/main", True, False),
    ("workflow_dispatch", "false", "refs/heads/feature", True, False),
    ("schedule", "", "refs/tags/v1.0.0", True, False),
])
def test_only_an_armed_run_from_main_is_live(event, dry_run, ref, armed, live):
    assert sweep.is_live(event, dry_run, ref, armed) is live


# --------------------------------------------------------------------------
# P: the pull request
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change, code", [
    (lambda p: p["user"].update(id=DEPENDABOT), "P1"),
    (lambda p: p["user"].update(id=str(BOT)), "P1"),
    (lambda p: p["user"].update(type="User"), "P1"),
    (lambda p: p["head"].update(ref="actions-adopt-x"), "P2"),
    (lambda p: p["head"]["repo"].update(full_name="someone/store"), "P2"),
    (lambda p: p["base"].update(ref="release"), "P3"),
    (lambda p: p.update(draft=True), "P3"),
    (lambda p: p.update(locked=True), "P3"),
    (lambda p: p.update(state="closed"), "P3"),
    (lambda p: p.update(labels=[{"name": "no-auto-merge"}]), "P4"),
    (lambda p: p.update(title="chore(deps): reorder yadgarhq/actions hooks (v1.38.2)"), "P6"),
    (lambda p: p.update(title=p["title"] + " "), "P6"),
    (lambda p: p.update(body=p["body"] + "\n- fix: x\n"), "P7"),
    (lambda p: p.update(body=p["body"].replace(
        "## Changelog\n",
        "## Changelog\n\n- fix(sweep): a bullet long enough that the hard wrap at seventy\n"
        "  two columns breaks it onto a second line here\n")), "P7"),
    (lambda p: p.update(mergeable=None), "U3"),
    (lambda p: p.update(mergeable="true"), "U3"),
    (lambda p: p["base"].update(sha="0" * 40), "U2"),
])
def test_a_pull_request_that_is_not_the_bots_clean_bump_is_skipped(change, code):
    case = merge_case()
    change(case.pr)
    skipped(case, code)


def test_a_change_request_is_skipped():
    case = merge_case()
    case.reviews.append({"state": "CHANGES_REQUESTED", "user": {"id": 1}})
    skipped(case, "P5")


def test_an_unresolved_thread_is_skipped():
    case = merge_case()
    threads = case.data["graphql"]["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads.update(totalCount=1, nodes=[{"isResolved": False}])
    skipped(case, "P5")


def test_review_threads_not_read_in_full_are_skipped():
    case = merge_case()
    threads = case.data["graphql"]["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads.update(totalCount=101)
    skipped(case, "P5")


def test_a_title_whose_target_is_not_the_diffs_is_skipped():
    case = merge_case()
    title = case.pr["title"].replace("to v1.38.2", "to v1.38.3")
    case.pr["title"] = title
    case.commit["commit"]["message"] = title
    skipped(case, "P6")


# --------------------------------------------------------------------------
# K: the commits
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change, code", [
    (lambda c: c.commits.append({"sha": "a" * 40}), "K1"),
    (lambda c: c.commits[0].update(sha="a" * 40), "K1"),
    (lambda c: c.commit["commit"]["verification"].update(verified=False), "K2"),
    (lambda c: c.commit["author"].update(id=DEPENDABOT), "K2"),
    (lambda c: c.commit["author"].update(type="User"), "K2"),
    (lambda c: c.commit["committer"].update(id=12345, type="User"), "K2"),
    (lambda c: c.commit["committer"].update(id=BOT, type="Bot"), "K2"),
    (lambda c: c.commit.update(sha="a" * 40), "K2"),
    (lambda c: c.commit["commit"].update(message="chore(deps): something else"), "K3"),
    (lambda c: c.commit["commit"].update(
        message="chore(deps): bump yadgarhq/actions hooks from v1.0.0 to v1.38.2"), "K3"),
    (lambda c: c.timeline.append({"event": "head_ref_force_pushed", "actor": {"id": 12345},
                                  "created_at": "2026-10-28T12:00:00Z"}), "K4"),
])
def test_commits_that_are_not_the_bots_one_commit_are_skipped(change, code):
    case = merge_case()
    change(case)
    skipped(case, code)


def test_a_bot_force_push_is_accepted():
    case = merge_case()
    case.timeline.append({"event": "head_ref_force_pushed", "actor": {"id": BOT},
                          "created_at": "2026-10-28T12:00:00Z"})
    assert case.judge()["action"] == "merge"


def test_a_full_timeline_page_is_skipped_as_maybe_truncated():
    case = merge_case()
    case.timeline.extend({"event": "labeled"} for _ in range(100))
    skipped(case, "timeline has")


# --------------------------------------------------------------------------
# C: the diff
# --------------------------------------------------------------------------


def edit_head(case, old, new, count=1):
    text = case.text(case.head)
    assert text.count(old) == count, old
    case.set_text(case.head, text.replace(old, new))


def edit_both(case, old, new):
    for commit in (case.tip, case.head):
        text = case.text(commit)
        assert text.count(old) == 1, old
        case.set_text(commit, text.replace(old, new))


@pytest.mark.parametrize("change, code", [
    (lambda c: c.compare["files"].append(dict(c.compare["files"][0], filename="README.md")), "C1"),
    (lambda c: c.compare["files"][0].update(previous_filename="old.yaml"), "C1"),
    (lambda c: c.compare["files"][0].update(status="added"), "C1"),
    (lambda c: c.compare["files"][0].update(status="removed"), "C1"),
    (lambda c: c.compare["files"][0].pop("patch"), "C1"),
    (lambda c: c.compare["files"].extend(dict(c.compare["files"][0]) for _ in range(300)), "C1"),
    (lambda c: c.compare.update(ahead_by=2, total_commits=2), "C1"),
    (lambda c: c.compare.update(ahead_by=2), "C1"),
    (lambda c: c.compare.update(total_commits=2), "C1"),
    (lambda c: c.compare["files"][0].update(
        patch="@@ -1 +1 @@\n-    rev: v1.0.0\n+    rev: v1.0.1"), "C1"),
    (lambda c: c.compare.update(status="ahead", behind_by=1), "U1"),
    (lambda c: c.compare.update(status="identical"), "U1"),
    (lambda c: c.entry(c.head).update(mode="100755"), "C1"),
    (lambda c: c.tree(c.head).update(truncated=True), "C1"),
])
def test_a_compare_that_is_not_one_config_file_is_skipped(change, code):
    case = merge_case()
    change(case)
    skipped(case, code)


@pytest.mark.parametrize("old, new, code", [
    ("      - id: gitleaks\n", "      - id: gitleaks2\n", "C2"),
    ("    rev: v1.38.2\n", "    rev: v1.38.2\n  # extra\n", "C2"),
    ("    rev: v1.38.2\n", "    rev: v1.37.0\n", "C2"),
    ("    rev: v1.38.2\n", "    rev: v1.37.1\n", "C2"),
    ("    rev: v1.38.2\n", "    rev: v1.38.2-rc1\n", "C2"),
    ("    rev: v1.38.2\n", "    rev: v1.38.2\r\n", "C2"),
])
def test_a_head_config_outside_the_class_is_skipped(old, new, code):
    case = merge_case()
    edit_head(case, old, new)
    skipped(case, code)


def test_a_rev_outside_the_actions_block_is_skipped():
    case = merge_case()
    text = case.text(case.head).replace("    rev: v1.38.2\n", "    rev: v1.37.1\n")
    text = text.replace("    rev: v5.0.0\n", "    rev: v5.0.1\n")
    case.set_text(case.head, text)
    skipped(case, "C2")


def test_two_lines_differing_is_skipped():
    case = merge_case()
    edit_head(case, "      - id: check-yaml\n", "      - id: check-yml\n")
    skipped(case, "C2")


def test_a_duplicate_yaml_key_is_skipped():
    case = merge_case()
    edit_both(case, "repos:\n", "fail_fast: false\nfail_fast: false\nrepos:\n")
    skipped(case, "C4")


def test_two_actions_blocks_are_skipped():
    case = merge_case()
    extra = "  - repo: https://github.com/yadgarhq/actions\n    rev: v1.0.0\n    hooks: []\n"
    edit_both(case, "repos:\n", "repos:\n" + extra)
    skipped(case, "C4")


# --------------------------------------------------------------------------
# T: the target tag
# --------------------------------------------------------------------------


def test_a_missing_tag_is_skipped():
    case = merge_case()
    del case.data["repos/yadgarhq/actions/git/ref/tags/v1.38.2"]
    got = case.judge()
    assert got["action"] == "skip" and "404" in got["reason"]


@pytest.mark.parametrize("change, code", [
    (lambda c: c.data[f"repos/yadgarhq/actions/compare/{c.tag_commit}...main"].update(
        status="diverged", behind_by=3), "T3"),
    (lambda c: c.data[f"repos/yadgarhq/actions/compare/{c.tag_commit}...main"].update(
        status="behind"), "T3"),
    (lambda c: c.data.update({f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls": []}), "T4"),
    (lambda c: [p.update(merged_at=None)
                for p in c.data[f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls"]], "T4"),
    (lambda c: [p["base"].update(ref="develop")
                for p in c.data[f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls"]], "T4"),
    (lambda c: [p.update(merge_commit_sha="b" * 40)
                for p in c.data[f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls"]], "T4"),
    (lambda c: c.data[f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls"].append(
        dict(c.data[f"repos/yadgarhq/actions/commits/{c.tag_commit}/pulls"][0], number=1)), "T4"),
])
def test_a_tag_not_cut_on_main_by_one_merged_pull_request_is_skipped(change, code):
    case = merge_case()
    change(case)
    skipped(case, code)


def test_a_tag_younger_than_the_soak_is_skipped():
    case = merge_case()
    skipped(case, "T5", now=datetime.datetime(2026, 10, 28, 10, 10, tzinfo=datetime.timezone.utc))
    skipped(case, "T5", classes(soak_hours=1000))


def test_a_fractional_soak_compares_in_minutes():
    """ADR-1068: the soak is 0.5 h. v1.38.2's commit is 2026-10-28T10:00:07Z."""
    case = merge_case()
    half = classes(soak_hours=0.5)
    at = lambda h, m, s: datetime.datetime(2026, 10, 28, h, m, s, tzinfo=datetime.timezone.utc)  # noqa: E731
    skipped(case, "T5", half, now=at(10, 30, 6))
    assert case.judge(half, now=at(10, 30, 7))["action"] == "merge"


def test_a_tag_object_naming_a_tree_is_skipped():
    case = merge_case()
    ref = case.data["repos/yadgarhq/actions/git/ref/tags/v1.38.2"]["object"]
    case.data[f"repos/yadgarhq/actions/git/tags/{ref['sha']}"]["object"]["type"] = "tree"
    skipped(case, "T2")


# --------------------------------------------------------------------------
# V: CI on the exact head
# --------------------------------------------------------------------------


@pytest.mark.parametrize("change, code", [
    (lambda c: c.checks["check_runs"].remove(c.check("ci / passed")), "V1"),
    (lambda c: c.check("ci / passed").update(status="in_progress", conclusion=None), "V1"),
    (lambda c: c.checks["check_runs"].append(dict(c.check("ci / passed"), conclusion="failure")),
     "V1"),
    (lambda c: c.checks.update(total_count=c.checks["total_count"] + 1), "V1"),
    (lambda c: c.check("ci / passed")["app"].update(id=99), "V2"),
    (lambda c: c.check("ci / passed").update(head_sha="a" * 40), "V2"),
    (lambda c: c.run.update(path=".github/workflows/other.yaml"), "V2"),
    (lambda c: c.run.update(event="pull_request_target"), "V2"),
    (lambda c: c.run.update(event="push"), "V2"),
    (lambda c: c.run.update(head_sha="a" * 40), "V2"),
    (lambda c: c.run.update(head_branch="main"), "V2"),
    (lambda c: c.run.update(conclusion="failure"), "V2"),
    (lambda c: c.run.update(referenced_workflows=[w for w in c.run["referenced_workflows"]
                                                  if "ci-pr.yaml" not in w["path"]]), "V2"),
    (lambda c: [c.run["referenced_workflows"][0].update(sha="c" * 40), c.data.update({
        f"repos/yadgarhq/actions/compare/{'c' * 40}...main": {
            "status": "diverged", "ahead_by": 1, "behind_by": 1}})], "V2"),
    (lambda c: c.run.update(check_suite_id=1), "V2"),
    (lambda c: c.runs.update(total_count=c.runs["total_count"] + 1), "V2"),
    (lambda c: c.check("ci / precommit").update(conclusion="skipped"), "V3"),
    (lambda c: c.check("ci / precommit").update(conclusion="failure"), "V3"),
    (lambda c: c.check("ci / precommit")["check_suite"].update(id=1), "V3"),
])
def test_ci_that_is_not_this_heads_own_green_run_is_skipped(change, code):
    case = merge_case()
    change(case)
    skipped(case, code)


def test_a_green_status_with_no_check_run_is_not_enough():
    case = merge_case()
    case.checks["check_runs"].remove(case.check("ci / passed"))
    case.checks["total_count"] -= 1
    case.data[f"{case.r}/commits/{case.head}/status"] = {
        "state": "success", "statuses": [{"context": "ci / passed", "state": "success"}]}
    skipped(case, "V1")


# --------------------------------------------------------------------------
# the sweep, the plan, the merge
# --------------------------------------------------------------------------


def estate(*names):
    data, listing = {}, []
    for name in names:
        case = Case(name)
        data.update(case.data)
        listing.append({"name": case.repo, "topics": case.meta["topics"]})
        data[f"repos/{OWNER}/{case.repo}/pulls?head={OWNER}:actions-adopt&state=open&per_page=10"] = [
            {"number": case.n}]
    # `type=public` never lists a private repository: docs and argocd-verify are
    # absent here as they are from the real listing (yaadgaar 467).
    listing += [{"name": "actions", "topics": []}]
    data[f"orgs/{OWNER}/repos?type=public&per_page=100&page=1"] = listing
    for status in ("in_progress", "queued"):
        data[f"repos/{OWNER}/actions/actions/workflows/fan-out.yaml/runs?status={status}"] = {
            "total_count": 0}
    return data


def test_the_sweep_plans_one_merge_and_every_refresh():
    rows, todo = sweep.sweep(Fake(estate("store-45", "chart-75", "yadgar-98")), OWNER,
                             classes(max_merges=1), NOW)
    by = {r["repo"]: r for r in rows}
    assert by["argocd-verify"]["reason"].startswith("R2: excluded: Private")
    assert "actions" not in by  # no class topic, so not a consumer
    assert [(t["repo"], t["action"]) for t in todo] == [("chart", "merge"), ("yadgar", "refresh")]
    assert by["store"]["reason"].startswith("M4")


def test_every_exclusion_is_a_report_row_with_its_reason_though_never_listed():
    """YAADGAAR 467. The listing is public-only, so an excluded private repository
    is never listed; its row comes from classes.json, not from a private read."""
    shipped = classes()["sweep"]["exclude"]
    data = estate("store-45")
    listed = [m["name"] for m in data[f"orgs/{OWNER}/repos?type=public&per_page=100&page=1"]]
    assert not set(shipped) & set(listed)
    rows, _ = sweep.sweep(Fake(data), OWNER, classes(), NOW)
    got = {r["repo"]: r["reason"] for r in rows if r["repo"] in shipped}
    assert got == {name: f"R2: excluded: {why}" for name, why in shipped.items()}
    assert all(r["action"] == "skip" for r in rows if r["repo"] in shipped)
    assert "R2: excluded: Private" in sweep.report(rows, True)


def test_a_listed_excluded_repository_is_one_row_and_never_read():
    data = estate("store-45")
    rows, todo = sweep.sweep(Fake(data), OWNER, classes(exclude={"store": "a reason"}), NOW)
    assert [r for r in rows if r["repo"] == "store"] == [
        {"repo": "store", "n": 0, "action": "skip", "reason": "R2: excluded: a reason"}]
    assert todo == []


def test_a_listed_exempt_repository_is_one_row_with_its_reason():
    data = estate("store-45")
    listing = data[f"orgs/{OWNER}/repos?type=public&per_page=100&page=1"]
    [producer] = [m for m in listing if m["name"] == "actions"]
    producer["topics"] = ["yadgar-class-base"]  # listed as a consumer, so exempt must catch it
    rows, _ = sweep.sweep(Fake(data), OWNER, classes(), NOW)
    assert [r for r in rows if r["repo"] == "actions"] == [
        {"repo": "actions", "n": 0, "action": "skip",
         "reason": f"R2: exempt: {classes()['exempt']['actions']}"}]


def test_the_merge_cap_is_respected_at_zero_and_two():
    data = estate("store-45", "chart-75")
    assert sweep.sweep(Fake(data), OWNER, classes(max_merges=0), NOW)[1] == []
    assert len(sweep.sweep(Fake(data), OWNER, classes(max_merges=2), NOW)[1]) == 2


def test_a_running_fan_out_stops_the_sweep():
    data = estate("store-45")
    data[f"repos/{OWNER}/actions/actions/workflows/fan-out.yaml/runs?status=queued"] = {"total_count": 1}
    rows, todo = sweep.sweep(Fake(data), OWNER, classes(), NOW)
    assert todo == [] and rows[0]["reason"] == "fan-out running"


@pytest.mark.parametrize("action, n", [("merge", 45), ("refresh", 98)])
def test_a_fan_out_running_at_act_time_writes_nothing(action, n):
    """YAADGAAR 470. `evaluate` saw no fan-out, but one may have started since."""
    case = merge_case() if action == "merge" else refresh_case()
    case.data[f"repos/{OWNER}/actions/actions/workflows/fan-out.yaml/runs?status=in_progress"] = {
        "total_count": 1}
    writes = []
    with pytest.raises(sweep.Skip, match="fan-out running"):
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, case.repo, n, case.head,
                  action, classes(), NOW)
    assert writes == []


def test_a_re_evaluation_that_errors_reds_the_act_leg():
    """YAADGAAR 469. An `error:` row is a read that failed, not a pull request
    that stopped qualifying: it must not pass as a Skip (which exits 0)."""
    case = merge_case()
    del case.data[f"{case.r}/pulls/45/commits?per_page=100"]
    writes = []
    with pytest.raises(Refusal, match="M1: the re-evaluation failed: error:") as raised:
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "store", 45, case.head,
                  "merge", classes(), NOW)
    assert not isinstance(raised.value, sweep.Skip)
    assert writes == []


def test_the_merge_sends_squash_the_head_the_title_and_the_body():
    case = merge_case()
    writes = []
    done = sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "store", 45, case.head,
                     "merge", classes(), NOW)
    assert done.startswith("merged yadgarhq/store#45")
    assert writes == [("merge", OWNER, "store", 45, {
        "merge_method": "squash", "sha": case.head,
        "commit_title": f"{case.pr['title']} (#45)", "commit_message": case.pr["body"]})]


@pytest.mark.parametrize("refusal", ["HTTP 405", "HTTP 409", "HTTP 422"])
def test_a_refused_merge_is_a_skip(refusal):
    case = merge_case()
    with pytest.raises(sweep.Skip, match="M2"):
        sweep.act(Fake(case.data), Fake(case.data, refuse_merge=refusal), OWNER, "store", 45,
                  case.head, "merge", classes(), NOW)


@pytest.mark.parametrize("denial", ["gh: Bad credentials (HTTP 401)",
                                    "gh: Resource not accessible by integration (HTTP 403)"])
def test_a_denied_merge_is_not_a_skip(denial):
    """A 401 or 403 is the write token misconfigured, not the pull request: it
    must red the `act` job, never pass as a Skip (which exits 0)."""
    case = merge_case()
    with pytest.raises(Refusal, match="M2") as raised:
        sweep.act(Fake(case.data), Fake(case.data, refuse_merge=denial), OWNER, "store", 45,
                  case.head, "merge", classes(), NOW)
    assert not isinstance(raised.value, sweep.Skip)


@pytest.mark.parametrize("failure", [
    "gh: Not Found (HTTP 404)",
    "gh: Server Error (HTTP 500)",
    "To get started with GitHub CLI, please run:  gh auth login",
    "gh: Pull Request is not mergeable",
])
def test_a_merge_failure_that_is_not_a_known_refusal_reds(failure):
    """YAADGAAR 469. Only 405, 409 and 422 are GitHub refusing this pull request.
    A 404 (the token cannot see the repository), no usable auth, or an error with
    no HTTP code is not a verdict on the pull request: it reds."""
    case = merge_case()
    with pytest.raises(Refusal, match="M2") as raised:
        sweep.act(Fake(case.data), Fake(case.data, refuse_merge=failure), OWNER, "store", 45,
                  case.head, "merge", classes(), NOW)
    assert not isinstance(raised.value, sweep.Skip)


def test_the_real_clients_403_text_is_read_as_a_denial(monkeypatch):
    """`gh api`'s own stderr, through `SweepGh._api`, not a string the fake builds."""
    case = merge_case()
    writer = sweep.SweepGh("token")
    denied = sweep.subprocess.CompletedProcess(
        [], 1, stdout="", stderr="gh: Resource not accessible by integration (HTTP 403)\n")
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: denied)
    with pytest.raises(Refusal, match="M2: the merge call was denied") as raised:
        sweep.act(Fake(case.data), writer, OWNER, "store", 45, case.head, "merge", classes(), NOW)
    assert not isinstance(raised.value, sweep.Skip)


def test_a_denied_merge_reds_the_act_run(tmp_path, monkeypatch, capsys):
    case = merge_case()
    monkeypatch.setenv("EVENT", "schedule")
    monkeypatch.setenv("REF", "refs/heads/main")
    monkeypatch.setenv("SWEEP_WRITE_TOKEN", "token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    armed = classes(live=True)
    monkeypatch.setattr(sweep, "load", lambda text: armed)
    clients = iter([Fake(case.data), Fake(case.data, refuse_merge="gh: Forbidden (HTTP 403)")])
    monkeypatch.setattr(sweep, "SweepGh", lambda token: next(clients))
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)
    code = sweep.main(["act", "--owner", OWNER, "--repo", "store", "--n", "45",
                       "--head", case.head, "--action", "merge"])
    assert code == 1
    assert "::error::M2" in capsys.readouterr().err


def test_the_act_job_re_evaluates_and_writes_nothing_when_that_fails():
    case = merge_case()
    case.check("ci / passed").update(conclusion="failure")
    writes = []
    with pytest.raises(sweep.Skip, match="M1"):
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "store", 45, case.head,
                  "merge", classes(), NOW)
    assert writes == []


def test_the_act_job_refuses_a_head_that_moved():
    case = merge_case()
    writes = []
    with pytest.raises(sweep.Skip, match="M1"):
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "store", 45, "a" * 40,
                  "merge", classes(), NOW)
    assert writes == []


# --------------------------------------------------------------------------
# the refresh
# --------------------------------------------------------------------------


def refresh_case():
    return Case("yadgar-98")


def test_a_refresh_is_one_rewrite_onto_the_tip_with_the_same_file():
    """YAADGAAR 466: no reset to the tip first. Head == base closes the pull request."""
    case = refresh_case()
    writes = []
    sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "yadgar", 98, case.head,
              "refresh", classes(), NOW)
    assert writes == [
        ("rewrite", OWNER, "yadgar", "actions-adopt", case.tip, ".pre-commit-config.yaml",
         case.text(case.head), case.pr["title"], True, case.head),
    ]
    assert case.tip != case.head


def test_a_foreign_commit_is_never_reset():
    case = refresh_case()
    case.commit["author"].update(id=12345, type="User")
    writes = []
    with pytest.raises(sweep.Skip):
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "yadgar", 98, case.head,
                  "refresh", classes(), NOW)
    assert writes == []


def test_a_base_that_changed_the_config_is_not_refreshed():
    case = refresh_case()
    case.set_text(case.tip, case.text(case.tip) + "# moved on\n")
    got = skipped(case, "behind, and the base changed")
    assert "refresh" not in got["action"]


def test_a_branch_that_moved_is_not_reset():
    case = refresh_case()
    case.data[f"{case.r}/git/ref/heads/actions-adopt"]["object"]["sha"] = "d" * 40
    writes = []
    with pytest.raises(sweep.Skip, match="moved"):
        sweep.act(Fake(case.data, writes), Fake(case.data, writes), OWNER, "yadgar", 98, case.head,
                  "refresh", classes(), NOW)
    assert writes == []


def test_a_ref_moved_under_the_swap_is_a_skip_that_keeps_the_leg_green(tmp_path, monkeypatch):
    """YAADGAAR 470. The compare-and-swap refused because the fan-out moved the
    branch after the read: that is the fan-out's newer commit winning, not a fault."""
    case = refresh_case()

    class Raced(Fake):
        def rewrite_branch(self, *args, **kwargs):
            raise fan_out.RefMoved("`actions-adopt` moved under the swap")

    armed_act(tmp_path, monkeypatch, case, Raced)
    code = sweep.main(["act", "--owner", OWNER, "--repo", "yadgar", "--n", "98",
                       "--head", case.head, "--action", "refresh"])
    assert code == 0
    assert "skipped yadgarhq/yadgar#98" in (tmp_path / "summary").read_text()


def test_an_act_leg_with_no_write_token_reds_before_any_call(tmp_path, monkeypatch, capsys):
    case = merge_case()
    armed_act(tmp_path, monkeypatch, case, Fake)
    monkeypatch.setenv("SWEEP_WRITE_TOKEN", "")
    monkeypatch.setattr(sweep, "SweepGh", lambda token: pytest.fail("no client may be built"))
    code = sweep.main(["act", "--owner", OWNER, "--repo", "store", "--n", "45",
                       "--head", case.head, "--action", "merge"])
    assert code == 1
    assert "SWEEP_WRITE_TOKEN" in capsys.readouterr().err


def armed_act(tmp_path, monkeypatch, case, writer_class):
    monkeypatch.setenv("EVENT", "schedule")
    monkeypatch.setenv("REF", "refs/heads/main")
    monkeypatch.setenv("SWEEP_WRITE_TOKEN", "token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    armed = classes(live=True)
    monkeypatch.setattr(sweep, "load", lambda text: armed)
    clients = iter([Fake(case.data), writer_class(case.data)])
    monkeypatch.setattr(sweep, "SweepGh", lambda token: next(clients))
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)


def test_the_refresh_cap_counts_todays_force_pushes():
    case = refresh_case()
    for hour in (1, 5, 9):
        case.timeline.append({"event": "head_ref_force_pushed", "actor": {"id": BOT},
                              "created_at": f"2026-10-30T0{hour}:00:00Z"})
    skipped(case, "behind, and the refresh cap")
    case.timeline.pop()
    assert case.judge()["action"] == "refresh"


def test_a_behind_pull_request_is_never_merged():
    case = refresh_case()
    assert case.judge(classes(max_refreshes_per_pr_per_day=0))["action"] == "skip"


def test_rewrite_branch_refuses_any_other_branch_before_any_call():
    gh = sweep.SweepGh("token")
    calls = []
    gh._api = lambda *a, **k: calls.append(a)
    with pytest.raises(Refusal):
        gh.rewrite_branch(OWNER, "yadgar", "main", "a" * 40, ".pre-commit-config.yaml", "x",
                          "m", True)
    assert calls == []


def test_the_sweeper_writes_through_the_fan_outs_one_helper():
    assert sweep.SweepGh.rewrite_branch is fan_out.Gh.rewrite_branch
    assert not hasattr(sweep.SweepGh, "reset_ref") and not hasattr(sweep.SweepGh, "put_file")
    assert (sweep.BOT_ID, sweep.WEB_FLOW_ID) == (fan_out.BOT_ID, fan_out.WEB_FLOW_ID) \
        == (BOT, WEB_FLOW)


def test_the_real_client_merges_with_exactly_the_four_fields(monkeypatch):
    gh = sweep.SweepGh("token")
    calls = []
    gh._api = lambda *a, **k: calls.append((a, k))
    gh.merge_pr(OWNER, "store", 45, "a" * 40, "t (#45)", "b")
    assert calls == [(("-X", "PUT", "repos/yadgarhq/store/pulls/45/merge"), {"payload": {
        "merge_method": "squash", "sha": "a" * 40, "commit_title": "t (#45)", "commit_message": "b"}})]


# --------------------------------------------------------------------------
# classes.json
# --------------------------------------------------------------------------


def test_the_sweep_ships_armed_with_the_half_hour_soak():
    """ADR-1068: Max ruled the soak at 0.5 h on 2026-10-29, and armed the
    sweeper on 2026-10-29 after a dry run passed all 19 adoption pull requests
    (yaadgaar 465). Rollback is a pull request that sets `live` back to false
    and changes this assertion with it. Max raised the cap from 1 to 10 merges
    per run on 2026-10-29, after the first live merge (argocd#189) proved out."""
    shipped = sweep.load(CLASSES_TEXT)["sweep"]
    assert shipped["live"] is True
    assert shipped["soak_hours"] == 0.5
    assert shipped["max_merges"] == 10
    assert shipped["max_refreshes_per_pr_per_day"] == 3


@pytest.mark.parametrize("change", [
    lambda s: s.update(live="true"),
    lambda s: s.update(live=1),
    lambda s: s.pop("live"),
    lambda s: s.update(extra=1),
    lambda s: s.update(soak_hours=-1),
    lambda s: s.update(soak_hours=True),
    lambda s: s.update(soak_hours="24"),
    lambda s: s.update(soak_hours="0.5"),
    lambda s: s.update(soak_hours=float("nan")),
    lambda s: s.update(soak_hours=float("inf")),
    lambda s: s.update(soak_hours=-0.5),
    lambda s: s.update(soak_hours=None),
    lambda s: s.update(max_merges=1.5),
    lambda s: s.update(max_refreshes_per_pr_per_day=0.5),
    lambda s: s.update(exclude={"docs": ""}),
    lambda s: s.update(exclude=["docs"]),
])
def test_a_malformed_sweep_block_is_refused(change):
    data = json.loads(CLASSES_TEXT)
    change(data["sweep"])
    with pytest.raises(Refusal):
        sweep.load(json.dumps(data))


@pytest.mark.parametrize("soak", [0, 0.5, 1, 24, 0.0])
def test_a_whole_or_fractional_soak_is_accepted(soak):
    data = json.loads(CLASSES_TEXT)
    data["sweep"]["soak_hours"] = soak
    assert sweep.load(json.dumps(data))["sweep"]["soak_hours"] == soak


def test_a_missing_sweep_block_is_refused():
    data = json.loads(CLASSES_TEXT)
    del data["sweep"]
    with pytest.raises(Refusal):
        sweep.load(json.dumps(data))


# --------------------------------------------------------------------------
# the command line, dry
# --------------------------------------------------------------------------


def outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def unarmed(tmp_path):
    """The shipped classes.json with `sweep.live` false: the shipped file is armed."""
    data = json.loads(CLASSES_TEXT)
    data["sweep"]["live"] = False
    path = tmp_path / "classes.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_mode_is_dry_while_unarmed(tmp_path, monkeypatch):
    out = tmp_path / "out"
    for key, value in {"EVENT": "schedule", "REF": "refs/heads/main", "DRY_RUN": "",
                       "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(key, value)
    assert sweep.main(["mode", "--classes", unarmed(tmp_path)]) == 0
    assert outputs(out) == {"live": "false"}


def test_mode_is_live_on_the_shipped_file(tmp_path, monkeypatch):
    out = tmp_path / "out"
    for key, value in {"EVENT": "schedule", "REF": "refs/heads/main", "DRY_RUN": "",
                       "GITHUB_OUTPUT": str(out)}.items():
        monkeypatch.setenv(key, value)
    assert sweep.main(["mode", "--classes", str(REPO / "classes.json")]) == 0
    assert outputs(out) == {"live": "true"}


def test_a_dry_evaluate_plans_nothing_even_when_it_would_merge(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(sweep, "SweepGh", lambda token: Fake(estate("store-45")))
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)
    assert sweep.main(["evaluate", "--owner", OWNER, "--classes", str(REPO / "classes.json")]) == 0
    assert outputs(out) == {"plan": "[]"}
    assert "would merge" in summary.read_text()


def run_evaluate(tmp_path, monkeypatch, data, now=NOW):
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(sweep, "SweepGh", lambda token: Fake(data))
    monkeypatch.setattr(sweep, "now_utc", lambda: now)
    code = sweep.main(["evaluate", "--owner", OWNER, "--classes", str(REPO / "classes.json")])
    return code, summary.read_text()


def test_a_read_that_fails_reds_the_evaluate_run(tmp_path, monkeypatch, capsys):
    """INFRA-GREEN GUARD. A 403 on every cross-repository read must not be a green run."""
    data = estate("store-45", "chart-75")
    del data["repos/yadgarhq/chart/pulls/75"]
    code, summary = run_evaluate(tmp_path, monkeypatch, data)
    assert code == 1
    assert "::error::1 rows could not be evaluated: chart" in capsys.readouterr().err
    assert "1 rows could not be evaluated: chart" in summary
    assert "| `store` |" in summary  # the table is still written


def test_an_exception_that_is_not_a_refusal_reds_the_evaluate_run(tmp_path, monkeypatch, capsys):
    """F1. Any exception inside evaluation, not only a Refusal, is an `error:` row
    that reds the run: a prefix narrowed to `error: Refusal` must fail here."""
    data = estate("store-45", "chart-75")

    class Broken(Fake):
        def get(self, path, fresh=False):
            if path == "repos/yadgarhq/chart/pulls/75":
                raise KeyError("head")
            return super().get(path, fresh)

    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(sweep, "SweepGh", lambda token: Broken(data))
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)
    code = sweep.main(["evaluate", "--owner", OWNER, "--classes", str(REPO / "classes.json")])
    assert code == 1
    assert "error: KeyError" in summary.read_text()
    assert "::error::1 rows could not be evaluated: chart" in capsys.readouterr().err


@pytest.mark.parametrize("code_letter", ["V1", "T5"])
def test_an_ordinary_skip_keeps_the_evaluate_run_green(tmp_path, monkeypatch, capsys, code_letter):
    case = merge_case()
    data = estate("store-45")
    now = NOW
    if code_letter == "V1":
        runs = data[f"{case.r}/commits/{case.head}/check-runs?per_page=100"]["check_runs"]
        [r for r in runs if r["name"] == "ci / passed"][0]["conclusion"] = "failure"
    else:
        now = datetime.datetime(2026, 10, 28, 10, 10, tzinfo=datetime.timezone.utc)
    code, summary = run_evaluate(tmp_path, monkeypatch, data, now)
    assert code == 0
    assert f"| skip | {code_letter}:" in summary
    assert "::error::" not in capsys.readouterr().err


@pytest.mark.parametrize("failure, message", [
    (Refusal("gh api git/refs: HTTP 500"), "gh api git/refs: HTTP 500"),
    (fan_out.Unverified("the new commit is not verified; no ref was moved"),
     "the new commit is not verified"),
])
def test_a_failed_rewrite_reds_the_act_run(tmp_path, monkeypatch, capsys, failure, message):
    """A failed rewrite is never a Skip (which exits 0). An unverified commit
    least of all: every later refresh would make another one."""
    case = refresh_case()
    writes = []

    class Broken(Fake):
        def rewrite_branch(self, *args, **kwargs):
            raise failure

    monkeypatch.setenv("EVENT", "schedule")
    monkeypatch.setenv("REF", "refs/heads/main")
    monkeypatch.setenv("SWEEP_WRITE_TOKEN", "token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    armed = classes(live=True)
    monkeypatch.setattr(sweep, "load", lambda text: armed)
    clients = iter([Fake(case.data, writes), Broken(case.data, writes)])
    monkeypatch.setattr(sweep, "SweepGh", lambda token: next(clients))
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)
    code = sweep.main(["act", "--owner", OWNER, "--repo", "yadgar", "--n", "98",
                       "--head", case.head, "--action", "refresh"])
    assert code == 1
    assert writes == []
    assert f"::error::{message}" in capsys.readouterr().err


def test_live_flags_refuse_while_unarmed(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT", "schedule")
    monkeypatch.setenv("REF", "refs/heads/main")
    monkeypatch.setattr(sweep, "SweepGh", lambda token: pytest.fail("no client may be built"))
    classes = unarmed(tmp_path)
    assert sweep.main(["evaluate", "--owner", OWNER, "--live", "--classes", classes]) == 1
    assert sweep.main(["act", "--owner", OWNER, "--repo", "store", "--n", "45",
                       "--head", "a" * 40, "--action", "merge", "--classes", classes]) == 1


@pytest.mark.parametrize("name, code", [("store", 0), ("", 1), ("..", 1), ("a/b", 1), ("a b", 1)])
def test_a_leg_must_name_one_repository(name, code):
    assert sweep.main(["check-name", "--", name]) == code


# --------------------------------------------------------------------------
# structure: where a merge can come from, and what the workflow may hold
# --------------------------------------------------------------------------


def test_only_sweep_py_can_merge():
    assert not hasattr(fan_out.Gh, "merge_pr")
    holders = [p for p in [*(REPO / ".github" / "workflows").glob("*.y*ml"),
                           *(REPO / "scripts").glob("*.py")]
               if "/merge" in p.read_text() or "pr merge" in p.read_text()]
    assert holders == [SCRIPT]


def test_nothing_calls_update_branch():
    for path in (SCRIPT, WORKFLOW):
        assert "/update-branch" not in path.read_text()
        assert "pr update-branch" not in path.read_text()


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def steps(job):
    return workflow()["jobs"][job]["steps"]


def mints(job):
    return [s for s in steps(job) if "create-github-app-token" in s.get("uses", "")]


def test_the_workflow_grants_no_write_at_the_top_or_on_a_job():
    wf = workflow()
    assert wf["permissions"] == {}
    for job in wf["jobs"].values():
        assert all(level in ("read", "none") for level in job["permissions"].values())


def test_the_workflow_triggers_and_concurrency():
    wf = workflow()
    on = wf[True]  # YAML 1.1 reads `on:` as the boolean true
    assert on["schedule"] == [{"cron": "13,43 * * * *"}]
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    # The group is on `evaluate`, not the workflow: a waiting run holds nothing.
    assert "concurrency" not in wf
    assert wf["jobs"]["evaluate"]["concurrency"] == {"group": "sweep", "cancel-in-progress": False}


def test_the_evaluate_job_mints_nothing():
    assert mints("evaluate") == []


def test_every_write_mint_names_one_repository_and_only_contents_write():
    found = mints("act")
    assert len(found) == 1
    with_ = found[0]["with"]
    assert with_["repositories"] == "${{ matrix.repo }}"
    assert {k: v for k, v in with_.items() if k.startswith("permission-")} == {
        "permission-contents": "write"}


def test_the_act_job_runs_only_live_and_checks_the_name_before_the_mint():
    job = workflow()["jobs"]["act"]
    assert "needs.evaluate.outputs.live == 'true'" in job["if"]
    assert "needs.evaluate.outputs.plan != '[]'" in job["if"]
    names = [s.get("id") or s.get("name") for s in job["steps"]]
    assert names.index("leg") < names.index("app")


def test_every_checkout_drops_its_credentials():
    for job in workflow()["jobs"]:
        for step in steps(job):
            if "actions/checkout" in step.get("uses", ""):
                assert step["with"]["persist-credentials"] is False


def test_the_act_job_may_read_whether_a_fan_out_is_running():
    """YAADGAAR 470: `act` asks `fan_out_running` again, with GITHUB_TOKEN."""
    assert workflow()["jobs"]["act"]["permissions"].get("actions") == "read"


# --------------------------------------------------------------------------
# yaadgaar 468, ADR-1073: the fan-out dispatches this run, and it waits first
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, minutes", [("", 0), ("0", 0), ("7", 7), ("45", 45), ("60", 60)])
def test_the_wait_input_is_an_integer_from_zero_to_the_cap(text, minutes):
    assert sweep.MAX_WAIT_MINUTES == 60
    assert sweep.wait_minutes(text) == minutes


@pytest.mark.parametrize("text", ["61", "99", "100", "-1", "1.5", "5.0", "1e1", "abc", " 5", "5 ",
                                  "5\n", "07", "+5", "٣", "true"])
def test_a_wait_input_that_is_not_an_integer_in_range_is_refused(text):
    with pytest.raises(Refusal):
        sweep.wait_minutes(text)


def test_the_wait_ends_at_the_soak_plus_the_ci_margin():
    assert sweep.CI_MARGIN == datetime.timedelta(minutes=5)
    tagged = NOW - datetime.timedelta(minutes=3)
    # 30 min soak + 5 min margin, 3 min already gone: 32 min left.
    assert sweep.wait_seconds(tagged, 0.5, NOW, 45) == 32 * 60
    # The cap clamps it.
    assert sweep.wait_seconds(tagged, 0.5, NOW, 20) == 20 * 60
    # A soak already past waits nothing.
    assert sweep.wait_seconds(NOW - datetime.timedelta(hours=2), 0.5, NOW, 45) == 0
    # A cap of zero waits nothing, whatever the tag.
    assert sweep.wait_seconds(tagged, 0.5, NOW, 0) == 0
    # An unreadable tag waits the whole cap: later, never earlier.
    assert sweep.wait_seconds(None, 0.5, NOW, 45) == 45 * 60


class TagApi:
    """The four reads `newest_tag_time` makes, and nothing else."""

    def __init__(self, refs, broken=False):
        self.refs, self.broken, self.calls = refs, broken, []

    def get(self, path):
        self.calls.append(path)
        if self.broken:
            raise Refusal("gh api: HTTP 502")
        if path == f"repos/{OWNER}/actions/git/matching-refs/tags/v":
            return self.refs
        if path == f"repos/{OWNER}/actions/git/tags/{'b' * 40}":
            return {"object": {"type": "commit", "sha": "c" * 40}}
        if path == f"repos/{OWNER}/actions/commits/{'c' * 40}":
            return {"sha": "c" * 40, "commit": {"committer": {"date": "2026-10-30T11:57:00Z"}}}
        raise AssertionError(path)


def ref(name, kind, sha):
    return {"ref": f"refs/tags/{name}", "object": {"type": kind, "sha": sha}}


def test_the_newest_tag_is_the_semver_greatest_and_an_annotated_tag_is_followed():
    api = TagApi([ref("v1.9.0", "commit", "a" * 40), ref("v1.10.0", "tag", "b" * 40),
                  ref("v1", "commit", "d" * 40), ref("v2.0.0-rc1", "commit", "e" * 40)])
    assert sweep.newest_tag_time(api, OWNER) == NOW - datetime.timedelta(minutes=3)
    assert api.calls == [f"repos/{OWNER}/actions/git/matching-refs/tags/v",
                         f"repos/{OWNER}/actions/git/tags/{'b' * 40}",
                         f"repos/{OWNER}/actions/commits/{'c' * 40}"]


@pytest.mark.parametrize("refs", [[], [ref("v1", "commit", "d" * 40)], {"message": "Not Found"},
                                  [ref("v1.10.0", "blob", "b" * 40)], [{"ref": 5}]])
def test_a_tag_time_that_cannot_be_read_is_none(refs):
    assert sweep.newest_tag_time(TagApi(refs), OWNER) is None
    assert sweep.newest_tag_time(TagApi(refs, broken=True), OWNER) is None


def run_wait(monkeypatch, tmp_path, minutes, api):
    slept = []
    monkeypatch.setattr(sweep, "SweepGh", lambda token: api)
    monkeypatch.setattr(sweep, "now_utc", lambda: NOW)
    monkeypatch.setattr(sweep.time, "sleep", slept.append)
    monkeypatch.setenv("WAIT_MINUTES", minutes)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    return sweep.main(["wait", "--owner", OWNER]), slept


def test_the_wait_command_sleeps_until_the_soak_and_ci_should_be_done(monkeypatch, tmp_path):
    api = TagApi([ref("v1.10.0", "tag", "b" * 40)])
    code, slept = run_wait(monkeypatch, tmp_path, "45", api)
    assert (code, slept) == (0, [32 * 60])


def test_a_zero_wait_reads_nothing_and_sleeps_nothing(monkeypatch, tmp_path):
    for minutes in ("", "0"):
        api = TagApi([ref("v1.10.0", "tag", "b" * 40)])
        code, slept = run_wait(monkeypatch, tmp_path, minutes, api)
        assert (code, slept, api.calls) == (0, [], [])


def test_an_unreadable_tag_waits_the_whole_cap(monkeypatch, tmp_path):
    code, slept = run_wait(monkeypatch, tmp_path, "45", TagApi([], broken=True))
    assert (code, slept) == (0, [45 * 60])


def test_a_bad_wait_input_reds_before_any_read_or_sleep(monkeypatch, tmp_path, capsys):
    api = TagApi([ref("v1.10.0", "tag", "b" * 40)])
    code, slept = run_wait(monkeypatch, tmp_path, "61", api)
    assert (code, slept, api.calls) == (1, [], [])
    assert "::error::" in capsys.readouterr().err


def test_the_workflow_takes_a_wait_input_and_waits_before_it_evaluates():
    wf = workflow()
    wait = wf[True]["workflow_dispatch"]["inputs"]["wait_minutes"]
    assert wait["type"] == "string" and wait["default"] == "0"
    assert wf["jobs"]["evaluate"]["needs"] == "wait"
    names = [s.get("id") for s in steps("wait")]
    step = steps("wait")[names.index("wait")]
    assert step["env"]["WAIT_MINUTES"] == "${{ inputs.wait_minutes }}"
    assert step["run"].strip() == 'python3 scripts/sweep.py wait --owner "$OWNER"'
    assert "wait" not in [s.get("id") for s in steps("evaluate")]
    # The input reaches the shell through env only, never templated into a run block.
    for job in wf["jobs"].values():
        for s in job["steps"]:
            assert "inputs." not in s.get("run", "")


def test_the_wait_job_holds_no_concurrency_group_and_writes_nothing():
    """YAADGAAR 468 review. A waiting run must not hold `sweep`: the next tag's
    dispatched run would go pending, and a cron would cancel it."""
    job = workflow()["jobs"]["wait"]
    assert "concurrency" not in job
    assert "needs" not in job
    assert job["permissions"] == {"contents": "read"}
    assert mints("wait") == []


def test_two_runs_never_act_on_one_repository_at_once():
    assert workflow()["jobs"]["act"]["concurrency"] == {
        "group": "sweep-act-${{ matrix.repo }}", "cancel-in-progress": False}


def test_the_wait_job_has_time_for_the_longest_wait():
    assert workflow()["jobs"]["wait"]["timeout-minutes"] >= sweep.MAX_WAIT_MINUTES + 1
    assert workflow()["jobs"]["evaluate"]["timeout-minutes"] <= 20
