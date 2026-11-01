"""What `repin_publish.py` asserts (yaadgaar 517, ADR-1106).

The sweeper merges a repin pull request only when every commit on it is the
App's own, VERIFIED, committed by GitHub's `web-flow`, with one parent (B3),
and every ref update of its branch was pushed by the App (B9). A `git push`
commit is unsigned, so the commit is made through the git data API with the
App's token, and the branch moves through the refs API with the same token.

Each property the sweeper reads is held here against a fake GitHub that
records every write, and each green case has a red twin.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fan_out  # noqa: E402
import repin  # noqa: E402
import repin_publish as rp  # noqa: E402
import sweep  # noqa: E402

BOT_ID = fan_out.BOT_ID
WEB_FLOW_ID = fan_out.WEB_FLOW_ID
BASE = "b" * 40
OLD_BASE = "a" * 40
BRANCH = "repin/dial-v0.2.29_store-v0.7.1"
OLDER = "repin/dial-v0.2.28_store-v0.7.1"
TITLE = "chore(deps): re-pin dial, store"
BODY = "## What\n\nre-pin\n"
FILES = {"Cargo.toml": b"[dependencies]\n", "Cargo.lock": b"# lock\n"}


def pr(number, head, state="open", merged=False, user=BOT_ID, same=True,
       title=TITLE, body=BODY, sha=None):
    return {"number": number, "state": state, "merged": merged,
            "url": f"https://github.com/o/r/pull/{number}", "title": title,
            "body": body, "head": head, "user_id": user, "same_repo": same,
            "sha": sha or f"{number:040x}"}


class Fake:
    """A GitHub that holds refs, commits and pull requests in dicts."""

    def __init__(self, prs=(), refs=None, signed=True, author=BOT_ID, unchanged=False,
                 authors=None, closers=None, relation="identical"):
        self.prs = list(prs)
        self.relation = relation
        self.compared = []
        self.unchanged = unchanged
        self.authors = dict(authors or {})
        self.closers = dict(closers or {})
        self.refs = {"main": BASE, **(refs or {})}
        self.commits = {}
        self.writes = []
        self.signed = signed
        self.author = author
        self.trees = 0

    # reads
    def repo(self):
        return {"default_branch": "main", "node_id": "R_node"}

    def branch_sha(self, branch):
        return self.refs.get(branch)

    def pulls(self, head=None):
        if head is None:
            return [p for p in self.prs if p["state"] == "open"]
        return [p for p in self.prs if p["head"] == head]

    def branch_authors(self, base, head):
        if head in self.authors:
            if isinstance(self.authors[head], Exception):
                raise self.authors[head]
            return self.authors[head]
        got = self.commits.get(head)
        return [got["author"]["id"]] if got else [self.author]

    def commit(self, sha):
        return self.commits[sha]

    def closed_by(self, number):
        return self.closers.get(number)

    def compare_status(self, base, head):
        self.compared.append((base, head))
        return self.relation

    def tree_of(self, sha):
        return "base-tree-" + sha

    # writes
    def make_tree(self, base_tree, files):
        self.trees += 1
        assert set(files) <= {"Cargo.toml", "Cargo.lock"}
        return base_tree if self.unchanged else "t-" + repr(sorted(files.items()))

    def make_commit(self, tree, parent, message):
        sha = f"{len(self.commits) + 1:040x}"
        self.writes.append(("commit", tree, parent, message))
        made = {"sha": sha, "verification": {"verified": self.signed},
                "author": {"email": fan_out.BOT_EMAIL},
                "committer": {"email": fan_out.WEB_FLOW_EMAIL}}
        self.commits[sha] = landed(sha, tree, parent, verified=self.signed)
        return made

    def create_ref(self, branch, sha):
        assert branch not in self.refs
        self.writes.append(("create_ref", branch, sha))
        self.refs[branch] = sha

    def swap_ref(self, node, branch, expect, sha):
        assert self.refs[branch] == expect
        self.writes.append(("swap_ref", branch, expect, sha))
        self.refs[branch] = sha

    def create_pr(self, base, branch, title, body):
        number = 100 + len(self.prs)
        self.prs.append(pr(number, branch, title=title, body=body))
        self.writes.append(("create_pr", base, branch, title))
        return f"https://github.com/o/r/pull/{number}"

    def update_pr(self, number, title, body):
        self.writes.append(("update_pr", number))

    def close_superseded(self, number, by):
        self.writes.append(("close", number, by))
        for p in self.prs:
            if p["number"] == number:
                p["state"] = "closed"

    def kinds(self):
        return [w[0] for w in self.writes]


def landed(sha, tree, parent, verified=True, author=BOT_ID, committer=WEB_FLOW_ID):
    return {"sha": sha, "parents": [{"sha": parent}],
            "commit": {"tree": {"sha": tree}, "verification": {"verified": verified}},
            "author": {"id": author, "type": "Bot"}, "committer": {"id": committer}}


def publish(fake, parent=BASE, branch=BRANCH):
    return rp.publish(fake, branch, TITLE, BODY, parent, dict(FILES))


# ---------------------------------------------------------------------------
# the decision before any cargo work: proceed, or why not
# ---------------------------------------------------------------------------


def test_no_pull_request_proceeds():
    assert rp.decide(Fake(), BRANCH) == "proceed"


def test_an_open_pull_request_on_the_same_branch_proceeds_to_refresh():
    """The defect: OPEN used to stop the run, so a BEHIND repin PR was never
    refreshed and only a person could bring it current."""
    assert rp.decide(Fake([pr(5, BRANCH)]), BRANCH) == "proceed"


def test_a_closed_pull_request_suppresses_the_bump():
    assert rp.decide(Fake([pr(5, BRANCH, state="closed")]), BRANCH) == "suppressed"


def test_a_merged_pull_request_stops():
    assert rp.decide(Fake([pr(5, BRANCH, state="closed", merged=True)]), BRANCH) == "merged"


def test_an_open_one_wins_over_an_older_closed_one():
    prs = [pr(4, BRANCH, state="closed"), pr(5, BRANCH)]
    assert rp.decide(Fake(prs), BRANCH) == "proceed"


# ---------------------------------------------------------------------------
# the commit the sweeper reads (B3, B7, B8)
# ---------------------------------------------------------------------------


def test_a_new_bump_is_one_api_commit_on_the_base_tip_then_a_new_ref_then_a_pr():
    fake = Fake()
    got = publish(fake)
    assert fake.kinds() == ["commit", "create_ref", "create_pr"]
    _, _, parent, message = fake.writes[0]
    assert parent == BASE
    assert message == TITLE  # B7: the squash title is the first commit subject
    assert got["status"] == "opened"
    sha = fake.refs[BRANCH]
    commit = fake.commits[sha]
    assert [p["sha"] for p in commit["parents"]] == [BASE]
    assert commit["commit"]["verification"]["verified"] is True
    assert commit["committer"]["id"] == WEB_FLOW_ID
    assert commit["author"]["id"] == BOT_ID


def test_the_commit_passes_the_sweepers_own_provenance_check():
    fake = Fake()
    publish(fake)
    commit = fake.commits[fake.refs[BRANCH]]
    commit["commit"]["message"] = TITLE
    assert sweep.check_bot_commits([commit], [], "repin", commit["sha"]) == TITLE


def test_an_unsigned_commit_moves_no_ref():
    fake = Fake(signed=False)
    with pytest.raises(fan_out.Unverified):
        publish(fake)
    assert "create_ref" not in fake.kinds() and "create_pr" not in fake.kinds()
    assert BRANCH not in fake.refs


def test_only_cargo_files_are_committed():
    with pytest.raises(repin.Refusal, match="only"):
        rp.publish(Fake(), BRANCH, TITLE, BODY, BASE, {"Cargo.toml": b"", "x.rs": b""})


def test_a_manifest_is_required():
    with pytest.raises(repin.Refusal):
        rp.publish(Fake(), BRANCH, TITLE, BODY, BASE, {"Cargo.lock": b""})


# ---------------------------------------------------------------------------
# the base: never overlay this checkout's Cargo files onto a different main
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relation", ["diverged", "behind"])
def test_a_checkout_not_on_the_default_branch_is_refused_before_any_write(relation):
    """A `workflow_dispatch` from a feature branch makes the parent a feature
    commit. Built on it, the PR would carry that person's commits to main and
    close the real repin PR as superseded. Behind main is fine; elsewhere is
    not: the default tip must contain the parent."""
    fake = Fake([pr(122, OLDER)], relation=relation)
    with pytest.raises(repin.Refusal, match="not on the default branch"):
        publish(fake, parent=OLD_BASE)
    assert fake.writes == []
    assert fake.compared == [(OLD_BASE, BASE)]


def test_a_checkout_behind_the_default_tip_opens_a_pr_behind_main():
    """RULED 2026-11-01 (Max), "Open behind main". The default branch moved
    mid-run: commit on the checkout's own HEAD, never on the newer tip (that
    would revert its Cargo changes), and open the PR behind main. ADR-1109
    merges a clean behind bot PR on a non-strict base."""
    fake = Fake(relation="ahead")
    got = publish(fake, parent=OLD_BASE)
    assert fake.compared == [(OLD_BASE, BASE)]
    assert fake.kinds() == ["commit", "create_ref", "create_pr"]
    assert fake.writes[0][2] == OLD_BASE
    assert fake.writes[2][1] == "main"
    assert [p["sha"] for p in fake.commits[fake.refs[BRANCH]]["parents"]] == [OLD_BASE]
    assert got["status"] == "opened"


def test_a_checkout_behind_the_default_tip_refreshes_an_open_pr_behind_main():
    old = "c" * 40
    older_base = "9" * 40
    fake = Fake([pr(7, BRANCH)], refs={BRANCH: old}, relation="ahead")
    fake.commits[old] = landed(old, "t-old", older_base)
    got = publish(fake, parent=OLD_BASE)
    assert fake.kinds() == ["commit", "swap_ref"]
    assert [p["sha"] for p in fake.commits[fake.refs[BRANCH]]["parents"]] == [OLD_BASE]
    assert got["status"] == "refreshed"


def test_a_head_already_current_on_the_behind_checkout_is_not_pushed_again():
    fake = Fake([pr(7, BRANCH)], relation="ahead")
    tree = fake.make_tree(fake.tree_of(OLD_BASE), dict(FILES))
    head = "d" * 40
    fake.refs[BRANCH] = head
    fake.commits[head] = landed(head, tree, OLD_BASE)
    got = publish(fake, parent=OLD_BASE)
    assert fake.writes == []
    assert got["status"] == "unchanged"


# ---------------------------------------------------------------------------
# an empty commit: the new tree equals the parent's tree
# ---------------------------------------------------------------------------


def test_a_tree_equal_to_the_parents_tree_is_refused_before_any_write():
    """The old `git commit` failed when nothing changed. Sweep B8 passes an
    empty file list, so an empty API commit would merge as a no-op."""
    fake = Fake([pr(7, BRANCH, title="older title"), pr(122, OLDER)], unchanged=True)
    with pytest.raises(repin.Refusal, match="nothing to commit"):
        publish(fake)
    assert fake.writes == []


# ---------------------------------------------------------------------------
# the branch name: only `repin/...`, exactly as the sweeper admits it
# ---------------------------------------------------------------------------


def test_the_branch_pattern_is_the_sweepers():
    assert rp.BRANCH.pattern == sweep.BOTS["repin"][2].pattern


@pytest.mark.parametrize("bad", ["main", "actions-adopt", "repin/../main", "repin/a/b", ""])
def test_a_branch_outside_repin_is_refused_before_any_write(bad):
    fake = Fake()
    with pytest.raises(repin.Refusal):
        publish(fake, branch=bad)
    assert fake.writes == []


# ---------------------------------------------------------------------------
# refresh: an open PR on the same branch is brought onto the new base (BEHIND)
# ---------------------------------------------------------------------------


def test_a_behind_open_pr_is_rebuilt_on_the_new_base_by_compare_and_swap():
    old = "c" * 40
    fake = Fake([pr(7, BRANCH, body="older text")], refs={BRANCH: old})
    fake.commits[old] = landed(old, "t-old", OLD_BASE)
    got = publish(fake)
    assert fake.kinds() == ["update_pr", "commit", "swap_ref"]
    assert fake.writes[2][2] == old  # compare-and-swap from the head that was read
    assert [p["sha"] for p in fake.commits[fake.refs[BRANCH]]["parents"]] == [BASE]
    assert got == {"status": "refreshed", "url": "https://github.com/o/r/pull/7", "closed": [],
                   "kept": []}


def test_the_pr_text_is_edited_before_the_push():
    """yaadgaar 440: an edit after the push puts both runs on the new head."""
    old = "c" * 40
    fake = Fake([pr(7, BRANCH, title="older title")], refs={BRANCH: old})
    fake.commits[old] = landed(old, "t-old", OLD_BASE)
    publish(fake)
    assert fake.kinds().index("update_pr") < fake.kinds().index("swap_ref")


def test_an_already_current_signed_head_is_not_pushed_again():
    """A push re-runs CI and restarts the B10 soak, so a daily run on an
    unchanged base must write nothing."""
    fake = Fake([pr(7, BRANCH)])
    tree = fake.make_tree(fake.tree_of(BASE), dict(FILES))
    head = "d" * 40
    fake.refs[BRANCH] = head
    fake.commits[head] = landed(head, tree, BASE)
    got = publish(fake)
    assert "commit" not in fake.kinds() and "swap_ref" not in fake.kinds()
    assert got["status"] == "unchanged"


def test_the_same_tree_on_an_unsigned_head_is_replaced():
    """Every repin PR open before this change carries a git-pushed, unsigned
    commit; the next run must replace it even though nothing else changed."""
    fake = Fake([pr(7, BRANCH)])
    tree = fake.make_tree(fake.tree_of(BASE), dict(FILES))
    head = "d" * 40
    fake.refs[BRANCH] = head
    fake.commits[head] = landed(head, tree, BASE, verified=False, committer=BOT_ID)
    got = publish(fake)
    assert fake.kinds() == ["commit", "swap_ref"]  # the text is unchanged: no edit
    assert got["status"] == "refreshed"


def test_a_branch_carrying_someone_elses_commit_is_refused_not_reset():
    fake = Fake([pr(7, BRANCH)], refs={BRANCH: "c" * 40}, author=12345)
    with pytest.raises(repin.Refusal, match="not made by"):
        publish(fake)
    assert "swap_ref" not in fake.kinds()


def test_a_leftover_branch_with_no_pr_is_moved_and_a_pr_opened():
    """A run that pushed and then died leaves a branch with no PR."""
    old = "c" * 40
    fake = Fake(refs={BRANCH: old})
    fake.commits[old] = landed(old, "t-old", OLD_BASE)
    got = publish(fake)
    assert fake.kinds() == ["commit", "swap_ref", "create_pr"]
    assert got["status"] == "opened"


def test_publish_refuses_a_suppressed_bump_raced_in_after_the_decision():
    fake = Fake([pr(5, BRANCH, state="closed")])
    with pytest.raises(repin.Refusal, match="closed"):
        publish(fake)
    assert fake.writes == []


# ---------------------------------------------------------------------------
# superseded: a newer bump closes the older bump's open PR (task-db #122/#125)
# ---------------------------------------------------------------------------


def test_a_newer_bump_closes_the_older_bumps_open_pr_after_opening_its_own():
    fake = Fake([pr(122, OLDER)])
    got = publish(fake)
    assert fake.kinds() == ["commit", "create_ref", "create_pr", "close"]
    assert fake.writes[-1] == ("close", 122, got["url"])
    assert got["closed"] == [122]


def test_only_the_bots_own_repin_prs_in_this_repo_are_closed():
    prs = [pr(1, "repin/x-v1.0.0", user=999), pr(2, "repin/y-v1.0.0", same=False),
           pr(3, "feature/x"), pr(4, "dependabot/cargo/x")]
    fake = Fake(prs)
    got = publish(fake)
    assert got["closed"] == [] and "close" not in fake.kinds()


def test_a_superseded_pr_carrying_a_persons_commit_is_kept_open():
    """The reset path refuses to discard a non-bot commit on its own branch;
    the supersede loop must not close a PR a person pushed a fix to either."""
    human = pr(122, OLDER)
    fake = Fake([human], authors={human["sha"]: [BOT_ID, 12345]})
    got = publish(fake)
    assert "close" not in fake.kinds()
    assert got["closed"] == []
    assert [n for n, _ in got["kept"]] == [122]


def test_a_superseded_pr_with_an_unknown_author_is_kept_open():
    human = pr(122, OLDER)
    fake = Fake([human], authors={human["sha"]: [BOT_ID, None]})
    assert [n for n, _ in publish(fake)["kept"]] == [122]


def test_a_superseded_pr_too_long_to_read_is_kept_not_a_red_run():
    """The new PR is already open; a refusal here would turn the run red."""
    long = pr(122, OLDER)
    fake = Fake([long], authors={long["sha"]: repin.Refusal("over 50 commits")})
    got = publish(fake)
    assert "create_pr" in fake.kinds() and "close" not in fake.kinds()
    assert got["kept"] == [(122, "over 50 commits")]


def test_the_compare_for_a_superseded_pr_is_from_the_tip_to_its_head_sha():
    seen = []
    other = pr(122, OLDER, sha="e" * 40)

    class Spy(Fake):
        def branch_authors(self, base, head):
            seen.append((base, head))
            return super().branch_authors(base, head)

    publish(Spy([other]))
    assert seen == [(BASE, "e" * 40)]


def test_a_kept_pr_is_a_warning_on_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    monkeypatch.setattr(rp, "read_files", lambda: dict(FILES))
    body = tmp_path / "body.md"
    body.write_text(BODY)
    human = pr(122, OLDER)
    fake = Fake([human], authors={human["sha"]: [12345]})
    assert rp.do_publish(fake, BRANCH, TITLE, str(body), BASE) == 0
    out = capsys.readouterr().out
    assert "::warning::" in out and "#122" in out


def test_the_open_list_is_read_before_any_write():
    """Task 524 item 6: the 100-cap refusal of the open list must fire before
    the push and the new PR, not after them."""
    class Capped(Fake):
        def pulls(self, head=None):
            if head is None:
                raise repin.Refusal("100 or more")
            return super().pulls(head)

    fake = Capped()
    with pytest.raises(repin.Refusal, match="100 or more"):
        publish(fake)
    assert fake.writes == []


def test_a_refresh_also_closes_an_older_open_one():
    old = "c" * 40
    fake = Fake([pr(122, OLDER), pr(125, BRANCH)], refs={BRANCH: old})
    fake.commits[old] = landed(old, "t-old", OLD_BASE)
    got = publish(fake)
    assert got["closed"] == [122]


# ---------------------------------------------------------------------------
# the real client: what it sends to GitHub
# ---------------------------------------------------------------------------


class Recorder(rp.Gh):
    def __init__(self, answers):
        super().__init__("o", "r")
        self.calls = []
        self.answers = list(answers)

    def _api(self, *args, payload=None, missing_ok=False):
        self.calls.append((args, payload))
        return json.dumps(self.answers.pop(0))


def test_the_tree_is_built_on_the_parents_tree_with_only_the_given_files():
    gh = Recorder([{"tree": {"sha": "T0"}}, {"sha": "B1"}, {"sha": "B2"}, {"sha": "T1"}])
    assert gh.tree_of(BASE) == "T0"
    assert gh.calls[0][0][-1] == f"repos/o/r/git/commits/{BASE}"
    assert gh.make_tree("T0", {"Cargo.toml": b"a", "Cargo.lock": b"b"}) == "T1"
    args, payload = gh.calls[-1]
    assert args[-1] == "repos/o/r/git/trees"
    assert payload["base_tree"] == "T0"
    assert sorted(e["path"] for e in payload["tree"]) == ["Cargo.lock", "Cargo.toml"]
    assert all(e["mode"] == "100644" and e["type"] == "blob" for e in payload["tree"])


def test_the_commit_sends_no_author_or_committer_so_github_signs_it():
    gh = Recorder([{"sha": "C"}])
    gh.make_commit("T1", BASE, TITLE)
    args, payload = gh.calls[0]
    assert args[-1] == "repos/o/r/git/commits"
    assert payload == {"message": TITLE, "tree": "T1", "parents": [BASE]}


def test_a_superseded_pr_gets_a_comment_then_is_closed_and_its_branch_is_kept():
    """No branch deletion: a `branch_deletion` on a ref the sweeper later reads
    would fail B9."""
    gh = Recorder([{}, {}])
    gh.close_superseded(122, "https://github.com/o/r/pull/125")
    (c_args, c_body), (p_args, p_body) = gh.calls
    assert c_args[-1] == "repos/o/r/issues/122/comments"
    assert "pull/125" in c_body["body"]
    assert p_args[-1] == "repos/o/r/pulls/122" and p_body == {"state": "closed"}
    assert not any("DELETE" in a for a, _ in gh.calls)


def test_pulls_are_normalised_with_the_opener_and_the_head_repository():
    raw = [{"number": 9, "state": "open", "merged_at": None, "html_url": "u",
            "title": "t", "body": None, "user": {"id": BOT_ID},
            "head": {"ref": BRANCH, "sha": "h" * 40, "repo": {"full_name": "o/r"}}}]
    gh = Recorder([raw])
    got = gh.pulls(BRANCH)
    assert got == [{"number": 9, "state": "open", "merged": False, "url": "u", "title": "t",
                    "body": "", "head": BRANCH, "user_id": BOT_ID, "same_repo": True,
                    "sha": "h" * 40}]
    assert "head=o%3Arepin" in gh.calls[0][0][-1]


def test_a_pull_from_a_deleted_fork_is_not_the_same_repo():
    raw = [{"number": 9, "state": "open", "merged_at": None, "html_url": "u",
            "title": "t", "body": "", "user": {"id": BOT_ID},
            "head": {"ref": BRANCH, "sha": "h" * 40, "repo": None}}]
    assert Recorder([raw]).pulls(BRANCH)[0]["same_repo"] is False


# ---------------------------------------------------------------------------
# the real client below `_api`: `gh api` faked at the subprocess, so the real
# transport (argv, `--input -`, the HTTP 404 arm) runs (yaadgaar 524 item 4)
# ---------------------------------------------------------------------------


class Http:
    """`gh api` answers keyed on (method, path); records every request."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.requests = []

    def __call__(self, argv, input=None, **kwargs):
        assert argv[:2] == ["gh", "api"]
        args = argv[2:]
        method = args[args.index("-X") + 1] if "-X" in args else ("POST" if args[0] == "graphql"
                                                                    else "GET")
        path = [a for a in args if a not in ("-X", method, "--input", "-")][0]
        payload = json.loads(input) if input is not None else None
        self.requests.append((method, path, payload))
        answer = self.routes.get((method, path))
        if answer is None:
            return subprocess.CompletedProcess(argv, 1, "", "gh: Not Found (HTTP 404)")
        if callable(answer):
            answer = answer()
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")


def http(monkeypatch, routes):
    fake = Http(routes)
    monkeypatch.setattr(fan_out.subprocess, "run", fake)
    return fake


COMPARE = f"repos/o/r/compare/{BASE}...{'e' * 40}?per_page=100"


def compare_commit(author):
    return {"author": author, "commit": {"message": "m"}}


def test_branch_authors_returns_every_commit_of_a_multi_commit_branch(monkeypatch):
    http(monkeypatch, {("GET", COMPARE): {"total_commits": 3, "commits": [
        compare_commit({"id": BOT_ID}), compare_commit({"id": 12345}),
        compare_commit({"id": BOT_ID})]}})
    assert rp.Gh("o", "r").branch_authors(BASE, "e" * 40) == [BOT_ID, 12345, BOT_ID]


def test_branch_authors_reads_a_commit_with_no_github_user_as_none(monkeypatch):
    """A commit whose email maps to no account has `"author": null`; None is
    not the App, so both callers treat it as someone else's commit."""
    http(monkeypatch, {("GET", COMPARE): {"total_commits": 1, "commits": [
        compare_commit(None)]}})
    assert rp.Gh("o", "r").branch_authors(BASE, "e" * 40) == [None]


@pytest.mark.parametrize("answer", [
    {"total_commits": rp.MAX_BRANCH_COMMITS + 1, "commits": []},
    {"commits": [compare_commit({"id": BOT_ID})]},
])
def test_branch_authors_refuses_over_the_cap_or_with_no_count(monkeypatch, answer):
    http(monkeypatch, {("GET", COMPARE): answer})
    with pytest.raises(repin.Refusal, match="over"):
        rp.Gh("o", "r").branch_authors(BASE, "e" * 40)


def test_branch_authors_at_the_cap_is_read(monkeypatch):
    commits = [compare_commit({"id": BOT_ID})] * rp.MAX_BRANCH_COMMITS
    http(monkeypatch, {("GET", COMPARE): {"total_commits": rp.MAX_BRANCH_COMMITS,
                                          "commits": commits}})
    assert len(rp.Gh("o", "r").branch_authors(BASE, "e" * 40)) == rp.MAX_BRANCH_COMMITS


def test_create_ref_posts_the_full_ref_name_and_sha(monkeypatch):
    fake = http(monkeypatch, {("POST", "repos/o/r/git/refs"): {"ref": "x"}})
    rp.Gh("o", "r").create_ref(BRANCH, "f" * 40)
    assert fake.requests == [("POST", "repos/o/r/git/refs",
                              {"ref": f"refs/heads/{BRANCH}", "sha": "f" * 40})]


def test_create_ref_refuses_a_branch_outside_repin_before_any_request(monkeypatch):
    fake = http(monkeypatch, {})
    with pytest.raises(repin.Refusal):
        rp.Gh("o", "r").create_ref("main", "f" * 40)
    assert fake.requests == []


def test_swap_ref_is_a_compare_and_swap_on_the_repository_node(monkeypatch):
    fake = http(monkeypatch, {("POST", "graphql"): {"data": {"updateRefs": {"clientMutationId": None}}}})
    rp.Gh("o", "r").swap_ref("R_node", BRANCH, "c" * 40, "f" * 40)
    (method, path, payload), = fake.requests
    assert payload["variables"] == {"repo": "R_node", "updates": [{
        "name": f"refs/heads/{BRANCH}", "beforeOid": "c" * 40, "afterOid": "f" * 40,
        "force": True}]}


def test_swap_ref_whose_answer_was_lost_reads_the_ref_again(monkeypatch):
    ref = f"repos/o/r/git/ref/heads/{BRANCH}"
    fake = http(monkeypatch, {("POST", "graphql"): {"errors": [{"message": "timeout"}]},
                              ("GET", ref): {"object": {"sha": "f" * 40}}})
    rp.Gh("o", "r").swap_ref("R_node", BRANCH, "c" * 40, "f" * 40)
    assert fake.requests[-1][:2] == ("GET", ref)


def test_swap_ref_on_a_branch_someone_else_moved_raises(monkeypatch):
    ref = f"repos/o/r/git/ref/heads/{BRANCH}"
    http(monkeypatch, {("POST", "graphql"): {"errors": [{"message": "stale"}]},
                       ("GET", ref): {"object": {"sha": "9" * 40}}})
    with pytest.raises(fan_out.RefMoved):
        rp.Gh("o", "r").swap_ref("R_node", BRANCH, "c" * 40, "f" * 40)


def test_swap_ref_refuses_a_branch_outside_repin_before_any_request(monkeypatch):
    fake = http(monkeypatch, {})
    with pytest.raises(repin.Refusal):
        rp.Gh("o", "r").swap_ref("R_node", "main", "c" * 40, "f" * 40)
    assert fake.requests == []


def test_repo_returns_the_default_branch_and_the_node_id(monkeypatch):
    fake = http(monkeypatch, {("GET", "repos/o/r"): {
        "default_branch": "trunk", "node_id": "R_x", "name": "r", "private": False}})
    assert rp.Gh("o", "r").repo() == {"default_branch": "trunk", "node_id": "R_x"}
    assert fake.requests == [("GET", "repos/o/r", None)]


def test_branch_sha_of_a_missing_branch_is_none(monkeypatch):
    http(monkeypatch, {})
    assert rp.Gh("o", "r").branch_sha(BRANCH) is None


@pytest.mark.parametrize("status", ["ahead", "identical", "behind", "diverged"])
def test_compare_status_reads_the_compare_status(monkeypatch, status):
    fake = http(monkeypatch, {("GET", f"repos/o/r/compare/{OLD_BASE}...{BASE}"): {
        "status": status, "total_commits": 0, "commits": []}})
    assert rp.Gh("o", "r").compare_status(OLD_BASE, BASE) == status
    assert fake.requests == [("GET", f"repos/o/r/compare/{OLD_BASE}...{BASE}", None)]


def test_closed_by_reads_the_issue_closer(monkeypatch):
    """The pulls list carries no closer; the issue does."""
    http(monkeypatch, {("GET", "repos/o/r/issues/5"): {"closed_by": {"id": BOT_ID}}})
    assert rp.Gh("o", "r").closed_by(5) == BOT_ID


def test_closed_by_with_no_closer_is_none(monkeypatch):
    http(monkeypatch, {("GET", "repos/o/r/issues/5"): {"closed_by": None}})
    assert rp.Gh("o", "r").closed_by(5) is None


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_state_writes_proceed_and_warns_on_a_suppressed_bump(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    assert rp.do_state(Fake([pr(5, BRANCH, state="closed")]), BRANCH, TITLE) == 0
    assert "proceed=false" in out.read_text()
    assert "::warning::" in capsys.readouterr().out
    out.write_text("")
    assert rp.do_state(Fake([pr(5, BRANCH)]), BRANCH, TITLE) == 0
    assert "proceed=true" in out.read_text()


def test_a_suppressed_bump_a_person_closed_names_a_person(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    fake = Fake([pr(5, BRANCH, state="closed")], closers={5: 64974579})
    rp.do_state(fake, BRANCH, TITLE)
    out = capsys.readouterr().out
    assert "declined this exact bump" in out
    assert "superseded" not in out


def test_a_suppressed_bump_the_bot_closed_as_superseded_does_not_blame_a_person(
        tmp_path, monkeypatch, capsys):
    """repin_publish.py:286-290 used to say a person declined it even when the
    `repin` workflow itself closed it as superseded."""
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    fake = Fake([pr(5, BRANCH, state="closed")], closers={5: BOT_ID})
    rp.do_state(fake, BRANCH, TITLE)
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "superseded" in out
    assert "declined" not in out
    assert "proceed=false" in (tmp_path / "out").read_text()


def test_the_latest_closed_pr_decides_who_closed_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    prs = [pr(4, BRANCH, state="closed"), pr(9, BRANCH, state="closed")]
    rp.do_state(Fake(prs, closers={4: 64974579, 9: BOT_ID}), BRANCH, TITLE)
    assert "superseded" in capsys.readouterr().out


def test_a_closed_pr_reopened_after_the_decision_is_not_a_crash(tmp_path, monkeypatch, capsys):
    """`_closed_by_bot` reads the branch's PRs again; one reopened or merged
    in between leaves no closed PR, which must not raise from `max()`."""
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))

    class Reopened(Fake):
        calls = 0

        def pulls(self, head=None):
            self.calls += 1
            if self.calls > 1:
                return [pr(5, BRANCH)]
            return super().pulls(head)

    assert rp.do_state(Reopened([pr(5, BRANCH, state="closed")]), BRANCH, TITLE) == 0
    assert "declined" in capsys.readouterr().out


def test_read_files_takes_the_lock_only_when_it_exists(tmp_path):
    (tmp_path / "Cargo.toml").write_bytes(b"m")
    assert rp.read_files(tmp_path) == {"Cargo.toml": b"m"}
    (tmp_path / "Cargo.lock").write_bytes(b"l")
    assert rp.read_files(tmp_path) == {"Cargo.toml": b"m", "Cargo.lock": b"l"}
