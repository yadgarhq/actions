#!/usr/bin/env python3
"""Publish a re-pin as a commit GitHub signs, and keep one repin PR current.

yaadgaar 517, ADR-1106. The `sweep` workflow merges a repin pull request once
`ci / passed` is green, but only when every commit on it is the App's own,
VERIFIED, committed by GitHub's `web-flow` and has exactly one parent (B3), and
every ref update of its branch was pushed by the App (B9). The commit
`repin.yaml` used to make with `git commit` + `git push` was unsigned, with the
App as committer, so every repin pull request waited for a person.

SO THE COMMIT IS MADE THROUGH THE GIT DATA API with the App's installation
token: blobs, a tree on the parent's tree, a commit with NO author and NO
committer, which GitHub then signs as the App with `web-flow` as committer.
That is checked on the create answer before any ref moves, and on the commits
API after, with `fan_out.check_created` / `check_landed`, the same facts the
fan-out's commits are held to. The branch moves through the refs API with the
same token: created when absent, else compare-and-swap from the head this read,
never a blind force and never through the base (a head equal to its base
closes the pull request, yaadgaar 466).

THREE OUTCOMES FOR THE HEAD BRANCH, which `repin.branch` derives from the bump:

    no pull request     one new commit, the branch, one pull request
    OPEN                REFRESH: the commit is rebuilt on the checkout, which
                        may itself be behind main. Nothing moves
                        when the head is already one signed bot commit on that
                        base with the same tree: a push re-runs CI and restarts
                        the sweeper's soak (B10).
    CLOSED / MERGED     nothing: a person declined this exact bump, or it landed

AND ONE REPIN PULL REQUEST PER REPOSITORY. A newer producer tag gives a new
branch name, and the older bump's pull request used to stay open beside the new
one (task-db #122 beside #125, project-db #97, iam-db #124, all closed by hand).
After its own pull request is opened or refreshed, this closes every other OPEN
pull request the App opened on a `repin/` head in this repository, with a
comment linking the new one. Their branches are KEPT: a `branch_deletion` on a
ref fails the sweeper's B9 if that name is ever proposed again.

THE PARENT IS THE CHECKOUT, EVEN WHEN THE DEFAULT BRANCH HAS MOVED PAST IT. The
tree is the parent's tree with this checkout's `Cargo.toml` and `Cargo.lock`
laid over it; on any other parent those two files would revert whatever the
default branch changed in them since. So a default branch that moved mid-run
gives a pull request BEHIND it, never a commit on the newer tip (RULED
2026-11-01, "Open behind main", yaadgaar 524). The sweeper (ADR-1119)
brings a behind bot pull request up to date with update-branch, and merges it
once CI is green on the new head. BEHIND, NOT ELSEWHERE: the default tip must contain the
parent, so a dispatch from a feature branch is a refusal. A tree equal to the parent's tree is a refusal: the commit
would change nothing.

A SUPERSEDED PULL REQUEST THAT A PERSON PUSHED TO IS KEPT OPEN, with a warning
on the run, the same rule the reset path applies to this run's own branch.

Commands (GH_TOKEN is the App's installation token, GITHUB_REPOSITORY o/r):

    repin_publish.py state   <branch> <title>                  -> proceed=true|false
    repin_publish.py publish <branch> <title> <body.md> <parent-sha>

Tests: python3 -m pytest scripts/tests/ -q
"""

import base64
import collections
import json
import os
import re
import sys
import urllib.parse

import fan_out
from repin import Refusal

# Byte-identical to `sweep.BOTS["repin"]`'s branch pattern, which a test holds
# equal: one segment under `repin/`, no `..`.
BRANCH = re.compile(r"repin/[A-Za-z0-9_+-]+(\.[A-Za-z0-9_+-]+)*")
FILES = ("Cargo.toml", "Cargo.lock")
MAX_BRANCH_COMMITS = 50


def _repin_files(files):
    if "Cargo.toml" not in files or not set(files) <= set(FILES):
        raise Refusal(f"a re-pin commits `Cargo.toml` and only {', '.join(FILES)}; got {sorted(files)}")


# ADR-1120. One bot flow, two lanes: the branch a lane may write, the head
# prefix whose other open pull requests it supersedes, the files it may commit,
# and the workflow named in the superseded comment. `rust_propagate.py` passes
# its own; every default here is the repin lane as it always was.
Lane = collections.namedtuple("Lane", "branch prefix check_files workflow")
REPIN = Lane(BRANCH, "repin/", _repin_files, "repin")


def q(part, safe="/"):
    return urllib.parse.quote(str(part), safe=safe)


def _check_branch(branch, lane=REPIN):
    if not isinstance(branch, str) or not lane.branch.fullmatch(branch):
        raise Refusal(f"refusing to write `{branch}`: only a `{lane.prefix}...` branch "
                      f"({lane.branch.pattern}) is ours")


def decide(client, branch):
    """`proceed`, `merged` or `suppressed` for the pull requests on `branch`."""
    prs = client.pulls(branch)
    if any(p["state"] == "open" for p in prs):
        return "proceed"
    if any(p["merged"] for p in prs):
        return "merged"
    if prs:
        return "suppressed"
    return "proceed"


def _current(client, head, tree, parent):
    """True when `head` is already one signed bot commit on `parent` with `tree`."""
    commit = client.commit(head)
    if [p.get("sha") for p in commit.get("parents") or []] != [parent]:
        return False
    if ((commit.get("commit") or {}).get("tree") or {}).get("sha") != tree:
        return False
    try:
        fan_out.check_landed(commit, head)
    except fan_out.Unverified:
        return False
    return True


def publish(client, branch, title, body, parent, files, lane=REPIN):
    """Open or refresh the pull request for `branch`; close superseded ones.

    Returns `{"status": opened|refreshed|unchanged, "url": ..., "closed": [n]}`.
    """
    _check_branch(branch, lane)
    lane.check_files(files)
    meta = client.repo()
    base = meta["default_branch"]
    tip = client.branch_sha(base)
    relation = client.compare_status(parent, tip)
    if relation not in ("ahead", "identical"):
        raise Refusal(
            f"this run checked out {parent}, which is not on the default branch `{base}` "
            f"(the tip {tip} is `{relation}` of it). A dispatch from another branch would "
            "carry that branch's commits into the repin pull request, so nothing was written.")
    state = decide(client, branch)
    if state != "proceed":
        raise Refusal(f"`{branch}` now carries a {'merged' if state == 'merged' else 'closed'} "
                      "pull request; nothing was written")
    open_pr = next((p for p in client.pulls(branch) if p["state"] == "open"), None)
    # Read before any write: its 100-cap refusal must not follow a push.
    others = client.pulls()
    head = client.branch_sha(branch)
    if head is not None:
        foreign = [a for a in client.branch_authors(tip, head) if a != fan_out.BOT_ID]
        if foreign:
            raise Refusal(f"`{branch}` carries {len(foreign)} commit(s) not made by the App; "
                          "refusing to reset them away")
    parent_tree = client.tree_of(parent)
    tree = client.make_tree(parent_tree, files)
    if tree == parent_tree:
        raise Refusal(f"the re-pin changes nothing on {parent}: its tree equals the parent's, "
                      "so there is nothing to commit and nothing was written")
    push = head is None or not _current(client, head, tree, parent)
    if open_pr is None:
        status = "opened"
    else:
        status = "refreshed" if push else "unchanged"
    # EDIT BEFORE PUSH (yaadgaar 440): the `edited` run then lands on the old head.
    if open_pr is not None and (open_pr["title"], open_pr["body"]) != (title, body):
        client.update_pr(open_pr["number"], title, body)
    if push:
        made = client.make_commit(tree, parent, title)
        fan_out.check_created(made)
        sha = made["sha"]
        if head is None:
            client.create_ref(branch, sha)
        else:
            client.swap_ref(meta["node_id"], branch, head, sha)
        fan_out.check_landed(client.commit(sha), sha)
    url = open_pr["url"] if open_pr else client.create_pr(base, branch, title, body)
    closed, kept = supersede(client, others, branch, tip, url, lane.prefix)
    return {"status": status, "url": url, "closed": closed, "kept": kept}


def supersede(client, others, branch, tip, url, prefix="repin/"):
    """Close every other open repin PR the App opened here, linking `url`.

    A PR whose branch carries a commit not made by the App (a `None` author
    too), or that is too long to read, is KEPT and returned as `(number,
    why)`: closing it would bury a person's fix. Never a refusal here, since
    the new pull request is already open.
    """
    closed, kept = [], []
    for other in others:
        if not (other["state"] == "open" and other["head"] != branch
                and other["head"].startswith(prefix) and other["user_id"] == fan_out.BOT_ID
                and other["same_repo"]):
            continue
        try:
            authors = client.branch_authors(tip, other["sha"])
        except Refusal as refusal:
            kept.append((other["number"], str(refusal)))
            continue
        foreign = [a for a in authors if a != fan_out.BOT_ID]
        if foreign:
            kept.append((other["number"], f"its branch carries {len(foreign)} commit(s) "
                                          "not made by the App"))
            continue
        client.close_superseded(other["number"], url)
        closed.append(other["number"])
    return closed, kept


def read_files(root="."):
    """`Cargo.toml`, and `Cargo.lock` when the checkout carries one, as bytes."""
    out = {}
    for name in FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                out[name] = handle.read()
    return out


# --------------------------------------------------------------------------
# GitHub, through `gh api` with the App's token (GH_TOKEN)
# --------------------------------------------------------------------------


class Gh(fan_out.Gh):
    """`fan_out.Gh`'s transport, bound to one repository."""

    def __init__(self, owner, repo, lane=REPIN):
        super().__init__()
        self.owner, self.name, self.lane = owner, repo, lane
        self.at = f"repos/{q(owner)}/{q(repo)}"

    def _json(self, *args, payload=None):
        return json.loads(self._api(*args, payload=payload))

    def repo(self):
        got = self._json(self.at)
        return {"default_branch": got["default_branch"], "node_id": got["node_id"]}

    def branch_sha(self, *where):
        """`branch_sha(branch)` in this repository. The parent's own
        `(owner, repo, branch)` form is kept too: `fan_out.Gh._swap_ref` reads
        the ref back that way after a failed swap, and a one-argument override
        turned that read into a TypeError."""
        if len(where) == 1:
            where = (self.owner, self.name, *where)
        return super().branch_sha(*where)

    def pulls(self, head=None):
        """Pull requests on `head` (any state), or every OPEN one when None."""
        if head is None:
            path = f"{self.at}/pulls?state=open&per_page=100"
        else:
            path = f"{self.at}/pulls?head={q(self.owner + ':' + head, '')}&state=all&per_page=100"
        got = self._json(path)
        if len(got) >= 100:
            raise Refusal(f"{path} lists 100 or more pull requests; refused, not paged")
        full = f"{self.owner}/{self.name}"
        return [{"number": p["number"], "state": p["state"], "merged": bool(p.get("merged_at")),
                 "url": p["html_url"], "title": p["title"], "body": p.get("body") or "",
                 "head": p["head"]["ref"], "sha": p["head"]["sha"], "user_id": (p.get("user") or {}).get("id"),
                 "same_repo": ((p["head"].get("repo") or {}).get("full_name") == full)}
                for p in got]

    def branch_authors(self, base, head):
        got = self._json(f"{self.at}/compare/{q(base, '')}...{q(head, '')}?per_page=100")
        if got.get("total_commits", MAX_BRANCH_COMMITS + 1) > MAX_BRANCH_COMMITS:
            raise Refusal(f"the branch is over {MAX_BRANCH_COMMITS} commits ahead; refused")
        return [(c.get("author") or {}).get("id") for c in got["commits"]]

    def commit(self, sha):
        return self._json(f"{self.at}/commits/{q(sha, '')}")

    def compare_status(self, base, head):
        """`ahead` when `head` contains `base`; `identical`, `behind`, `diverged`."""
        return self._json(f"{self.at}/compare/{q(base, '')}...{q(head, '')}")["status"]

    def closed_by(self, number):
        """The id of whoever closed issue or pull request `number`, else None."""
        got = self._json(f"{self.at}/issues/{int(number)}")
        return (got.get("closed_by") or {}).get("id")

    def tree_of(self, sha):
        return self._json(f"{self.at}/git/commits/{q(sha, '')}")["tree"]["sha"]

    def make_tree(self, tree, files):
        entries = []
        for path in sorted(files):
            blob = self._json("-X", "POST", f"{self.at}/git/blobs", payload={
                "content": base64.b64encode(files[path]).decode(), "encoding": "base64"})["sha"]
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        return self._json("-X", "POST", f"{self.at}/git/trees",
                          payload={"base_tree": tree, "tree": entries})["sha"]

    def make_commit(self, tree, parent, message):
        # NO author, NO committer: GitHub then signs it as the App, via web-flow.
        return self._json("-X", "POST", f"{self.at}/git/commits",
                          payload={"message": message, "tree": tree, "parents": [parent]})

    def create_ref(self, branch, sha):
        _check_branch(branch, self.lane)
        self._api("-X", "POST", f"{self.at}/git/refs",
                  payload={"ref": f"refs/heads/{branch}", "sha": sha})

    def swap_ref(self, node, branch, expect, sha):
        _check_branch(branch, self.lane)
        self._swap_ref(self.owner, self.name, node, branch, expect, sha)

    def create_pr(self, base, branch, title, body):
        return super().create_pr(self.owner, self.name, base, branch, title, body)

    def update_pr(self, number, title, body):
        super().update_pr(self.owner, self.name, number, title, body)

    def close_superseded(self, number, by):
        what = "re-pins to newer tags" if self.lane is REPIN else "moves to a newer version"
        self._api("-X", "POST", f"{self.at}/issues/{int(number)}/comments", payload={
            "body": f"Superseded by {by}, which {what}. Closed by the "
                    f"`{self.lane.workflow}` workflow in yadgarhq/actions; the branch is kept."})
        self._api("-X", "PATCH", f"{self.at}/pulls/{int(number)}", payload={"state": "closed"})


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def _closed_by_bot(client, branch):
    """True when the App closed the latest closed pull request on `branch`,
    which `supersede` does; anyone else closing it declined the bump."""
    closed = [p for p in client.pulls(branch) if p["state"] == "closed" and not p["merged"]]
    latest = max(closed, key=lambda p: p["number"], default=None)
    if latest is None:  # reopened or merged since `decide` read it
        return False
    return client.closed_by(latest["number"]) == fan_out.BOT_ID


def do_state(client, branch, title):
    """THE CLOSED ARM IS A WARNING, not a summary line: nothing here proposes a
    declined bump again while it stays the greatest tag, and a re-run or a
    `workflow_dispatch` recomputes the same branch name and finds the same
    record (a pull request's `headRefName` outlives its branch)."""
    _check_branch(branch)
    state = decide(client, branch)
    output(proceed="true" if state == "proceed" else "false")
    if state == "suppressed" and _closed_by_bot(client, branch):
        print(f"::warning::{title} is SUPPRESSED. The `repin` workflow itself closed the pull "
              f"request on `{branch}` as superseded by a newer bump; no person closed it. "
              "Nothing here proposes it again while these stay the greatest tags. To take it: "
              "reopen that pull request, or re-pin the crate by hand.")
        summary(f"### Re-pin suppressed\n\n`{branch}` carries a pull request the `repin` "
                "workflow closed as superseded, so this bump is NOT re-proposed. See the "
                "warning on this run.")
    elif state == "suppressed":
        print(f"::warning::{title} is SUPPRESSED. A closed pull request on `{branch}` already "
              "declined this exact bump, and nothing here proposes it again while these stay "
              "the greatest tags. To take it: reopen that pull request, restoring its head "
              "branch first if it was deleted, or re-pin the crate by hand.")
        summary(f"### Re-pin suppressed\n\n`{branch}` carries a CLOSED pull request, so this "
                "bump was declined and is NOT re-proposed. See the warning on this run.")
    elif state == "merged":
        summary(f"_`{branch}` already carries a merged pull request, so nothing was opened._")
    return 0


def do_publish(client, branch, title, body_path, parent):
    with open(body_path, encoding="utf-8") as handle:
        body = handle.read()
    got = publish(client, branch, title, body, parent, read_files())
    print(f"::notice::{got['status']} {got['url']}")
    closed = ", ".join(f"#{n}" for n in got["closed"]) or "none"
    summary(f"{got['status'].capitalize()} {got['url']} from `{branch}`. "
            f"Superseded repin pull requests closed: {closed}.")
    for number, why in got["kept"]:
        print(f"::warning::superseded repin pull request #{number} was KEPT OPEN: {why}. "
              "A person may have pushed to it; close it by hand once it is no longer needed.")
    return 0


def main(argv):
    owner, _, repo = os.environ.get("GITHUB_REPOSITORY", "").partition("/")
    if not owner or not repo:
        print("::error::GITHUB_REPOSITORY is not `owner/repo`")
        return 2
    client = Gh(owner, repo)
    if len(argv) == 4 and argv[1] == "state":
        return do_state(client, argv[2], argv[3])
    if len(argv) == 6 and argv[1] == "publish":
        return do_publish(client, argv[2], argv[3], argv[4], argv[5])
    print("::error::usage: repin_publish.py state <branch> <title>"
          " | repin_publish.py publish <branch> <title> <body.md> <parent-sha>")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Refusal as refusal:
        print(f"::error::{refusal}")
        summary(f"### Re-pin refused\n\n{refusal}")
        sys.exit(1)
