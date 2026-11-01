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
       title=TITLE, body=BODY):
    return {"number": number, "state": state, "merged": merged,
            "url": f"https://github.com/o/r/pull/{number}", "title": title,
            "body": body, "head": head, "user_id": user, "same_repo": same}


class Fake:
    """A GitHub that holds refs, commits and pull requests in dicts."""

    def __init__(self, prs=(), refs=None, signed=True, author=BOT_ID):
        self.prs = list(prs)
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
        got = self.commits.get(head)
        return [got["author"]["id"]] if got else [self.author]

    def commit(self, sha):
        return self.commits[sha]

    # writes
    def make_tree(self, parent, files):
        self.trees += 1
        assert set(files) <= {"Cargo.toml", "Cargo.lock"}
        return "t-" + repr(sorted(files.items()))

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


def test_a_checkout_that_is_not_the_default_tip_is_refused_before_any_write():
    fake = Fake()
    with pytest.raises(repin.Refusal, match="default branch"):
        publish(fake, parent=OLD_BASE)
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
    assert got == {"status": "refreshed", "url": "https://github.com/o/r/pull/7", "closed": []}


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
    tree = fake.make_tree(BASE, dict(FILES))
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
    tree = fake.make_tree(BASE, dict(FILES))
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
    assert gh.make_tree(BASE, {"Cargo.toml": b"a", "Cargo.lock": b"b"}) == "T1"
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
            "head": {"ref": BRANCH, "repo": {"full_name": "o/r"}}}]
    gh = Recorder([raw])
    got = gh.pulls(BRANCH)
    assert got == [{"number": 9, "state": "open", "merged": False, "url": "u", "title": "t",
                    "body": "", "head": BRANCH, "user_id": BOT_ID, "same_repo": True}]
    assert "head=o%3Arepin" in gh.calls[0][0][-1]


def test_a_pull_from_a_deleted_fork_is_not_the_same_repo():
    raw = [{"number": 9, "state": "open", "merged_at": None, "html_url": "u",
            "title": "t", "body": "", "user": {"id": BOT_ID},
            "head": {"ref": BRANCH, "repo": None}}]
    assert Recorder([raw]).pulls(BRANCH)[0]["same_repo"] is False


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


def test_read_files_takes_the_lock_only_when_it_exists(tmp_path):
    (tmp_path / "Cargo.toml").write_bytes(b"m")
    assert rp.read_files(tmp_path) == {"Cargo.toml": b"m"}
    (tmp_path / "Cargo.lock").write_bytes(b"l")
    assert rp.read_files(tmp_path) == {"Cargo.toml": b"m", "Cargo.lock": b"l"}
