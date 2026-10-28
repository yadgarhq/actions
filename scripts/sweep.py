#!/usr/bin/env python3
"""The sweeper: merges a hook adoption pull request that proves itself. YAADGAAR 442 (c).

`fan_out.py` PROPOSES AND NEVER MERGES (ADR-0982). It opens one `actions-adopt`
pull request per consumer, and each one waits for a person to merge a one-line
`rev:` bump. This file is the other half: it finds those pull requests and
merges one only when every fact below holds, read from the GitHub API in this
run. Nothing is taken from the pull request's own claims.

EVERY CONDITION FAILS CLOSED. A missing key, a value of the wrong type, a list
that may be truncated, an API error or an unexpected shape is a SKIP with a
reason, never a merge. The conditions, by the letters the design gives them:

    R  the repository: public, a consumer with one class topic, not excluded;
       the run is armed (`sweep.live` true in `classes.json`, from `main`)
    P  the pull request: opened by the release App (id 324366854, a Bot) from
       `actions-adopt` in the same repository, open, not draft, not locked, no
       `no-auto-merge` label, no change request, no unresolved thread, the
       exact generated `bump` title, and a body with no Changelog bullet
    K  the commits: exactly one, the head, verified, authored by the App,
       committed by `web-flow`, its subject the title; every force-push by the App
    C  the diff: only `.pre-commit-config.yaml`, mode 100644, the one `rev:` line
       of the actions block moved forward (`config_only.classify`), the files
       parse equal apart from that rev (`ci_verdict.parsed_agrees`)
    T  the target tag: a plain vX.Y.Z on `actions` main, cut by exactly one
       merged pull request into main, older than `sweep.soak_hours`
    V  CI on the exact head: every `ci / passed` check run green, posted by
       app 15368 in a suite that IS this consumer's `ci.yaml` pull_request run
       of this head, calling `ci-pr.yaml@main` at a commit on actions main, with
       `ci / precommit` green in the same suite. Commit statuses are ignored.
    U  up to date: not behind the base tip read in this run, base sha equal to
       it, and `mergeable` true

A BEHIND pull request whose base did not touch `.pre-commit-config.yaml` is
REFRESHED instead: `actions-adopt` is moved, in one ref write, to a new commit
on the base tip carrying the same file (`fan_out.Gh.rewrite_branch`; never
through the base tip itself, which closes the pull request, yaadgaar 466), so
CI runs fresh and a later sweep can merge it. Never through
`update-branch` (ADR-0693) and never through `fan_out.one`, which no-ops on a
behind pull request whose config is unchanged.

THE MERGE IS A SQUASH that sends the head sha, the title and the body itself.
The body carries no Conventional Commits bullet (P7), so the consumer's
`version` job cuts no release for it.

DRY BY DEFAULT. `classes.json` ships `"sweep": {"live": false}`; while it is
false the workflow mints no write token and runs no `act` job, and `evaluate`
writes its verdict table to the step summary only. Reads go through
`GITHUB_TOKEN`: R1 admits only public repositories, so nothing here needs to
read a private one. The merge and the branch rewrite, with the two reads the
rewrite makes, are the only calls made with the App token, minted per repository with `contents: write`.

Commands:

    sweep.py mode                       EVENT, DRY_RUN, REF -> `live=true|false`
    sweep.py check-name -- NAME         a matrix leg's repository, or exit 1
    sweep.py evaluate --owner O [--live]
    sweep.py act --owner O --repo R --n N --head SHA --action merge|refresh

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse

import ci_verdict
import config_only
import fan_out
import pr_body
from repin import Refusal

API = "https://api.github.com/"
BOT_ID = fan_out.BOT_ID  # yadgarhq-bot[bot], the release App. NOT pr_body.BOT_IDS: that admits dependabot.
WEB_FLOW_ID = fan_out.WEB_FLOW_ID  # GitHub's `web-flow`, which signs the App's API commits
CI_APP_ID = 15368  # GitHub Actions
PASSED = "ci / passed"
PRECOMMIT = "ci / precommit"
CI_WORKFLOW = ".github/workflows/ci.yaml"
CI_PR = "yadgarhq/actions/.github/workflows/ci-pr.yaml@main"
NO_AUTO_MERGE = "no-auto-merge"
ACTIONS = fan_out.ACTIONS_REPO
VERSION = r"v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}"
TITLE = re.compile(
    rf"chore\(deps\): bump yadgarhq/actions hooks from ({VERSION}) to ({VERSION})")
SHA = re.compile(r"[0-9a-f]{40}")
PAGE = 100  # a list of exactly this many may have a second page: refused
SWEEP_KEYS = {"live", "exclude", "soak_hours", "max_merges", "max_refreshes_per_pr_per_day"}
DAY = datetime.timedelta(hours=24)


class Skip(Exception):
    """This pull request is not merged or refreshed. The message says why."""


def need(fact, reason):
    """`fact` must be exactly `True`. `None`, `1` and a truthy string are not."""
    if fact is not True:
        raise Skip(reason)


def dig(obj, *keys):
    """`obj[k1][k2]...`, or a Skip naming the path. Never a default."""
    for key in keys:
        if isinstance(key, int):
            ok = isinstance(obj, list) and -len(obj) <= key < len(obj)
        else:
            ok = isinstance(obj, dict) and key in obj
        if not ok:
            raise Skip(f"unexpected API shape: no {'.'.join(map(str, keys))}")
        obj = obj[key]
    return obj


def is_int(value):
    return type(value) is int


def as_list(value, what):
    if not isinstance(value, list):
        raise Skip(f"unexpected API shape: {what} is not a list")
    if len(value) >= PAGE:
        raise Skip(f"{what} has {len(value)} entries and may be truncated")
    return value


# --------------------------------------------------------------------------
# the configuration
# --------------------------------------------------------------------------


def load(text):
    """`classes.json`, checked, and its `sweep` block, checked strictly."""
    classes = fan_out.load_classes(text)
    sweep = classes.get("sweep")
    if not isinstance(sweep, dict):
        raise Refusal("classes.json: missing `sweep`")
    if set(sweep) != SWEEP_KEYS:
        raise Refusal(f"classes.json: `sweep` must have exactly {sorted(SWEEP_KEYS)}")
    if not isinstance(sweep["live"], bool):
        raise Refusal("classes.json: `sweep.live` must be true or false")
    exclude = sweep["exclude"]
    if not isinstance(exclude, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in exclude.items()):
        raise Refusal("classes.json: `sweep.exclude` maps a name to a non-empty reason")
    for key in ("soak_hours", "max_merges", "max_refreshes_per_pr_per_day"):
        if not is_int(sweep[key]) or sweep[key] < 0:
            raise Refusal(f"classes.json: `sweep.{key}` must be a non-negative integer")
    return classes


def is_live(event, dry_run, ref, armed):
    """R4. Writes happen only when `sweep.live` is the JSON `true`, from `main`,
    on the schedule or on a dispatch that says `dry_run: false`."""
    if armed is not True or ref != "refs/heads/main":
        return False
    if event == "schedule":
        return True
    return event == "workflow_dispatch" and dry_run == "false"


# --------------------------------------------------------------------------
# the predicate, pure over what `api.get` returns
# --------------------------------------------------------------------------


def check_repo(meta, owner, classes):
    """R1-R3 over `GET repos/{owner}/{name}`."""
    need(dig(meta, "private") is False and dig(meta, "visibility") == "public",
         "R1: not a public repository")
    name = dig(meta, "name")
    need(isinstance(name, str) and bool(fan_out.NAME.fullmatch(name)),
         "R3: a repository name outside GitHub's alphabet")
    need(name not in classes["sweep"]["exclude"], f"R2: excluded: {classes['sweep']['exclude'].get(name)}")
    need(name not in classes["exempt"], "R2: exempt in classes.json")
    need(dig(meta, "owner", "login") == owner, f"R3: not owned by {owner}")
    need(dig(meta, "archived") is False and dig(meta, "disabled") is False
         and dig(meta, "fork") is False, "R3: archived, disabled or a fork")
    topics = dig(meta, "topics")
    need(isinstance(topics, list), "R3: no topic list")
    found = fan_out.class_of([t for t in topics if isinstance(t, str)], classes["topic_prefix"])
    need(len(found) == 1, f"R3: {len(found)} class topics, need exactly one")
    need(found[0] in classes["classes"], f"R3: no class `{found[0]}` in classes.json")


def check_pr(pr, meta, owner):
    """P1-P4, P6 (its shape), P7. Returns `(from, to)` from the title."""
    full = f"{owner}/{meta['name']}"
    need(is_int(dig(pr, "user", "id")) and pr["user"]["id"] == BOT_ID
         and dig(pr, "user", "type") == "Bot", "P1: not opened by the release App")
    need(dig(pr, "head", "ref") == fan_out.BRANCH, f"P2: head is not `{fan_out.BRANCH}`")
    need(dig(pr, "head", "repo", "full_name") == full
         and dig(pr, "base", "repo", "full_name") == full, "P2: head is not in this repository")
    need(dig(pr, "base", "ref") == dig(meta, "default_branch"), "P3: base is not the default branch")
    need(dig(pr, "state") == "open", "P3: not open")
    need(dig(pr, "draft") is False, "P3: a draft")
    need(dig(pr, "locked") is False, "P3: locked")
    labels = dig(pr, "labels")
    need(isinstance(labels, list), "P4: no label list")
    need(all(dig(label, "name") != NO_AUTO_MERGE for label in labels), f"P4: labelled `{NO_AUTO_MERGE}`")
    title = dig(pr, "title")
    match = TITLE.fullmatch(title) if isinstance(title, str) else None
    need(match is not None, "P6: not a generated `bump` title")
    frm, to = match.groups()
    need(fan_out.target_of(title) == to, "P6: the title targets another tag")
    text = pr.get("body") or ""
    need(isinstance(text, str), "P7: the body is not text")
    need(pr_body.commit_entries(text.splitlines()) == ([], [], []),
         "P7: the body carries a Conventional Commits bullet, which would cut a release")
    return frm, to


def check_reviews(reviews, threads):
    """P5: no change request, no unresolved review thread."""
    for review in as_list(reviews, "reviews"):
        need(dig(review, "state") != "CHANGES_REQUESTED", "P5: a change was requested")
    node = dig(threads, "data", "repository", "pullRequest", "reviewThreads")
    nodes = dig(node, "nodes")
    need(isinstance(nodes, list) and is_int(dig(node, "totalCount"))
         and node["totalCount"] == len(nodes), "P5: review threads not read in full")
    need(all(dig(t, "isResolved") is True for t in nodes), "P5: an unresolved review thread")


def check_commits(commits, commit, timeline, pr, head):
    """K1-K4."""
    need(isinstance(commits, list) and len(commits) == 1, "K1: not exactly one commit")
    need(dig(commits, 0, "sha") == head, "K1: the one commit is not the head")
    need(dig(commit, "sha") == head, "K2: the commits API answered for another sha")
    need(dig(commit, "commit", "verification", "verified") is True, "K2: not a verified commit")
    need(is_int(dig(commit, "author", "id")) and commit["author"]["id"] == BOT_ID
         and dig(commit, "author", "type") == "Bot", "K2: not authored by the release App")
    need(is_int(dig(commit, "committer", "id")) and commit["committer"]["id"] == WEB_FLOW_ID,
         "K2: not committed by web-flow")
    message = dig(commit, "commit", "message")
    need(isinstance(message, str) and message.split("\n")[0] == pr["title"]
         and bool(fan_out.GENERATED.match(message)), "K3: the commit subject is not the title")
    for event in as_list(timeline, "timeline"):
        if dig(event, "event") == "head_ref_force_pushed":
            need(dig(event, "actor", "id") == BOT_ID, "K4: someone else force-pushed the branch")


def refreshes_today(timeline, now):
    """How many force-pushes (fan-out resets and refreshes) the last 24 hours saw."""
    count = 0
    for event in timeline:
        if event.get("event") == "head_ref_force_pushed":
            if now - parse_time(dig(event, "created_at")) < DAY:
                count += 1
    return count


def parse_time(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise Skip(f"unexpected API shape: {value!r} is not a UTC time")
    try:
        return datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise Skip(f"unexpected API shape: {value!r} is not a UTC time") from None


def check_diff(base_text, head_text, changed, frm, to):
    """C2-C4 over the config at the base (tip or merge base) and at the head."""
    try:
        before, after = config_only.classify(base_text, head_text)
    except config_only.NotConfigOnly as exc:
        raise Skip(f"C2: {exc}") from None
    need((before, after) == (frm, to), f"P6: the title says {frm} -> {to}, the diff {before} -> {after}")
    try:
        ci_verdict.parsed_agrees(base_text, head_text, before, after)
    except ci_verdict.Refused as exc:
        raise Skip(f"C4: {exc}") from None
    need(ci_verdict.patch_lines(changed["patch"]) == ([f"    rev: {before}"], [f"    rev: {after}"]),
         "C1: the compare patch is not exactly the rev line")


def check_tag(api, owner, to, soak_hours, now):
    """T1-T5. Returns the commit the tag names."""
    need(bool(fan_out.TAG.fullmatch(to)), "T1: not a plain vX.Y.Z")
    ref = api.get(f"repos/{owner}/{ACTIONS}/git/ref/tags/{to}")
    kind, sha = dig(ref, "object", "type"), dig(ref, "object", "sha")
    if kind == "tag":
        tag = api.get(f"repos/{owner}/{ACTIONS}/git/tags/{sha}")
        kind, sha = dig(tag, "object", "type"), dig(tag, "object", "sha")
    need(kind == "commit" and isinstance(sha, str) and bool(SHA.fullmatch(sha)),
         f"T2: {to} does not name a commit")
    on_main(api, owner, sha, f"T3: {to} is not on actions main")
    pulls = api.get(f"repos/{owner}/{ACTIONS}/commits/{sha}/pulls")
    merged = [p for p in as_list(pulls, "the tag commit's pull requests") if dig(p, "merged_at")]
    need(len(merged) == 1, f"T4: {len(merged)} merged pull requests made {to}, not one")
    need(dig(merged[0], "base", "ref") == "main" and dig(merged[0], "merge_commit_sha") == sha,
         f"T4: {to} is not the merge commit of a pull request into main")
    commit = api.get(f"repos/{owner}/{ACTIONS}/commits/{sha}")
    need(dig(commit, "sha") == sha, "T5: the commits API answered for another sha")
    age = now - parse_time(dig(commit, "commit", "committer", "date"))
    need(age >= datetime.timedelta(hours=soak_hours),
         f"T5: {to} is younger than the {soak_hours}h soak")
    return sha


def on_main(api, owner, sha, reason):
    compare = api.get(f"repos/{owner}/{ACTIONS}/compare/{sha}...main")
    need(dig(compare, "status") in ("identical", "ahead") and dig(compare, "behind_by") == 0, reason)


def check_ci(api, owner, name, head):
    """V1-V3. Statuses (V4) are never read."""
    got = api.get(f"repos/{owner}/{name}/commits/{head}/check-runs?per_page={PAGE}")
    runs = dig(got, "check_runs")
    need(isinstance(runs, list) and is_int(dig(got, "total_count"))
         and got["total_count"] == len(runs) and len(runs) < PAGE, "V1: check runs not read in full")
    passed = [r for r in runs if dig(r, "name") == PASSED]
    need(len(passed) >= 1, f"V1: no `{PASSED}` check run")
    need(all(dig(r, "status") == "completed" and dig(r, "conclusion") == "success" for r in passed),
         f"V1: a `{PASSED}` check run is not a completed success")
    wf = api.get(f"repos/{owner}/{name}/actions/runs?head_sha={head}&per_page={PAGE}")
    wruns = dig(wf, "workflow_runs")
    need(isinstance(wruns, list) and is_int(dig(wf, "total_count"))
         and wf["total_count"] == len(wruns) and len(wruns) < PAGE, "V2: workflow runs not read in full")
    for check in passed:
        need(is_int(dig(check, "app", "id")) and check["app"]["id"] == CI_APP_ID,
             f"V2: `{PASSED}` was posted by app {dig(check, 'app', 'id')}, not GitHub Actions")
        need(dig(check, "head_sha") == head, f"V2: `{PASSED}` is for another sha")
        suite = dig(check, "check_suite", "id")
        need(is_int(suite), "V2: no check suite id")
        bound = [w for w in wruns if dig(w, "check_suite_id") == suite]
        need(len(bound) == 1, f"V2: {len(bound)} workflow runs own the `{PASSED}` suite, not one")
        run = bound[0]
        need(dig(run, "path") == CI_WORKFLOW, f"V2: `{PASSED}` came from {run.get('path')!r}")
        need(dig(run, "event") == "pull_request", f"V2: `{PASSED}` ran on {run.get('event')!r}")
        need(dig(run, "head_sha") == head and dig(run, "head_branch") == fan_out.BRANCH,
             "V2: the run is for another head")
        need(dig(run, "status") == "completed" and dig(run, "conclusion") == "success",
             "V2: the run is not a completed success")
        refs = [w for w in as_list(dig(run, "referenced_workflows"), "referenced workflows")
                if dig(w, "path") == CI_PR]
        need(len(refs) == 1, f"V2: the run does not call `{CI_PR}` exactly once")
        sha = dig(refs[0], "sha")
        need(isinstance(sha, str) and bool(SHA.fullmatch(sha)), "V2: the called ci-pr.yaml has no sha")
        on_main(api, owner, sha, "V2: the called ci-pr.yaml is not on actions main")
        pre = [r for r in runs if dig(r, "name") == PRECOMMIT and dig(r, "check_suite", "id") == suite]
        need(len(pre) >= 1 and all(dig(r, "status") == "completed" and dig(r, "conclusion") == "success"
                                   for r in pre), f"V3: `{PRECOMMIT}` is not green in the same suite")


def fetcher(api):
    """`ci_verdict`'s full-URL fetch, routed through `api.get`."""
    def fetch(url):
        if not url.startswith(API):
            raise Skip(f"refusing to fetch {url}")
        return api.get(url[len(API):])
    return fetch


def evaluate(api, owner, name, n, classes, now):
    """The verdict for pull request `n` of `owner/name`, or a Skip. Never writes."""
    sweep = classes["sweep"]
    meta = api.get(f"repos/{owner}/{name}")
    check_repo(meta, owner, classes)
    pr = api.get(f"repos/{owner}/{name}/pulls/{int(n)}")
    need(dig(pr, "number") == n, "unexpected API shape: another pull request")
    frm, to = check_pr(pr, meta, owner)
    head = dig(pr, "head", "sha")
    need(isinstance(head, str) and bool(SHA.fullmatch(head)), "unexpected API shape: head sha")
    check_reviews(api.get(f"repos/{owner}/{name}/pulls/{n}/reviews?per_page={PAGE}"),
                  api.graphql_threads(owner, name, n))
    timeline = api.get(f"repos/{owner}/{name}/issues/{n}/timeline?per_page={PAGE}")
    check_commits(api.get(f"repos/{owner}/{name}/pulls/{n}/commits?per_page={PAGE}"),
                  api.get(f"repos/{owner}/{name}/commits/{head}"), timeline, pr, head)
    base = dig(pr, "base", "ref")
    tip = dig(api.get(f"repos/{owner}/{name}/git/ref/heads/{urllib.parse.quote(base, safe='/')}"),
              "object", "sha")
    need(isinstance(tip, str) and bool(SHA.fullmatch(tip)), "unexpected API shape: base tip")
    repo = f"{owner}/{name}"
    fetch = fetcher(api)
    compare = fetch(ci_verdict.compare_url(repo, tip, head))
    commits = dig(compare, "commits")
    need(dig(compare, "ahead_by") == 1 and dig(compare, "total_commits") == 1
         and isinstance(commits, list) and len(commits) == 1 and dig(commits, 0, "sha") == head,
         "C1: the head is not exactly one commit ahead")
    try:
        changed, merge_base = ci_verdict.compared_file(compare)
    except ci_verdict.Refused as exc:
        raise Skip(f"C1: {exc}") from None
    head_text = ci_verdict_text(fetch, repo, head)
    verdict = {"repo": name, "n": n, "head": head, "title": pr["title"], "body": pr.get("body") or ""}
    status, behind = dig(compare, "status"), dig(compare, "behind_by")
    if status == "ahead" and behind == 0:
        need(merge_base == tip, "U1: the merge base is not the base tip")
        check_diff(ci_verdict_text(fetch, repo, tip), head_text, changed, frm, to)
        check_tag(api, owner, to, sweep["soak_hours"], now)
        check_ci(api, owner, name, head)
        need(dig(pr, "base", "sha") == tip, "U2: the pull request's base sha is not the base tip")
        need(dig(pr, "mergeable") is True, "U3: GitHub does not call it mergeable")
        return dict(verdict, action="merge")
    need(status == "diverged" and is_int(behind) and behind > 0, f"U1: compare status {status!r}")
    # BEHIND: a refresh, never a merge. The base must not have touched the file.
    base_text = ci_verdict_text(fetch, repo, merge_base)
    check_diff(base_text, head_text, changed, frm, to)
    need(ci_verdict_text(fetch, repo, tip) == base_text,
         f"behind, and the base changed {config_only.CONFIG}: the fan-out regenerates it")
    check_tag(api, owner, to, sweep["soak_hours"], now)
    need(refreshes_today(timeline, now) < sweep["max_refreshes_per_pr_per_day"],
         "behind, and the refresh cap for today is reached")
    return dict(verdict, action="refresh", tip=tip, text=head_text)


def ci_verdict_text(fetch, repo, commit):
    try:
        return ci_verdict.config_at(fetch, repo, commit)
    except ci_verdict.Refused as exc:
        raise Skip(f"C1: {exc}") from None


def judge(api, owner, name, n, classes, now):
    """`evaluate`, with every failure of any kind turned into a Skip row."""
    try:
        return evaluate(api, owner, name, n, classes, now)
    except Skip as skip:
        return {"repo": name, "n": n, "action": "skip", "reason": str(skip)}
    except Exception as exc:  # fail closed: an error is never a merge
        return {"repo": name, "n": n, "action": "skip",
                "reason": f"error: {type(exc).__name__}: {str(exc)[:200]}"}


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------


def fan_out_running(api, owner):
    for status in ("in_progress", "queued"):
        got = api.get(f"repos/{owner}/{ACTIONS}/actions/workflows/fan-out.yaml/runs?status={status}")
        if dig(got, "total_count") != 0:
            return True
    return False


def sweep(api, owner, classes, now):
    """Every public consumer's open adoption pull request, one row each, and the plan."""
    if fan_out_running(api, owner):
        return [{"repo": "-", "n": 0, "action": "skip", "reason": "fan-out running"}], []
    rows, listed = [], []
    for page in range(1, fan_out.MAX_PAGES + 1):
        got = api.get(f"orgs/{owner}/repos?type=public&per_page={PAGE}&page={page}")
        need(isinstance(got, list), "unexpected API shape: repository listing")
        listed += got
        if len(got) < PAGE:
            break
    else:
        raise Refusal("the repository listing did not end")
    names = sorted(m["name"] for m in listed if isinstance(m, dict) and isinstance(m.get("name"), str)
                   and fan_out.NAME.fullmatch(m["name"])
                   and len(fan_out.class_of(m.get("topics") or [], classes["topic_prefix"])) == 1)
    if len(names) > fan_out.MAX_CONSUMERS:
        raise Refusal(f"{len(names)} consumers, over the cap of {fan_out.MAX_CONSUMERS}")
    for name in names:
        if name in classes["sweep"]["exclude"] or name in classes["exempt"]:
            rows.append({"repo": name, "n": 0, "action": "skip", "reason": "excluded"})
            continue
        prs = api.get(f"repos/{owner}/{name}/pulls?head={owner}:{fan_out.BRANCH}&state=open&per_page=10")
        if not isinstance(prs, list) or not prs:
            rows.append({"repo": name, "n": 0, "action": "skip", "reason": "no open adoption pull request"})
            continue
        for pr in prs:
            number = pr.get("number") if isinstance(pr, dict) else None
            if not is_int(number):
                rows.append({"repo": name, "n": 0, "action": "skip", "reason": "unexpected API shape"})
                continue
            rows.append(judge(api, owner, name, number, classes, now))
    return rows, plan(rows, classes["sweep"]["max_merges"])


def plan(rows, max_merges):
    """The `act` matrix: at most `max_merges` merges, and every refresh. M4."""
    out, merges = [], 0
    for row in rows:
        if row["action"] == "merge":
            if merges >= max_merges:
                row.update(action="skip", reason=f"M4: the cap of {max_merges} merges per run is reached")
                continue
            merges += 1
        if row["action"] in ("merge", "refresh"):
            out.append({"repo": row["repo"], "n": row["n"], "head": row["head"], "action": row["action"]})
    return out


def act(reader, writer, owner, name, n, head, action, classes, now):
    """M1-M3 for one leg: evaluate again from scratch, then merge or refresh."""
    verdict = judge(reader, owner, name, n, classes, now)
    if verdict["action"] != action:
        raise Skip(f"M1: re-evaluated as {verdict['action']}: {verdict.get('reason', '')}")
    need(verdict["head"] == head, "M1: the head moved since evaluate")
    if action == "merge":
        try:
            writer.merge_pr(owner, name, n, head, f"{verdict['title']} (#{n})", verdict["body"])
        except Refusal as exc:
            raise Skip(f"M2: the merge was refused: {exc}") from None
        after = reader.get(f"repos/{owner}/{name}/pulls/{n}", fresh=True)
        need(dig(after, "merged") is True, "M3: not merged after the merge call")
        return f"merged {owner}/{name}#{n} as {dig(after, 'merge_commit_sha')}"
    # A REFRESH. The branch must still be where evaluate read it.
    ref = reader.get(f"repos/{owner}/{name}/git/ref/heads/{fan_out.BRANCH}", fresh=True)
    need(dig(ref, "object", "sha") == head, "the branch moved since it was read: no reset")
    # ONE REF WRITE (yaadgaar 466). The new commit is built on the tip first and
    # the branch then moves straight to it: a reset to the tip first would make
    # head == base, and GitHub closes the pull request. A failure leaves the
    # branch where it was; a Refusal (an `Unverified` one included) reds the run.
    writer.rewrite_branch(owner, name, fan_out.BRANCH, verdict["tip"], config_only.CONFIG,
                          verdict["text"], verdict["title"], exists=True)
    return f"refreshed {owner}/{name}#{n} onto {verdict['tip']}"


# --------------------------------------------------------------------------
# GitHub, through `gh api`
# --------------------------------------------------------------------------


class SweepGh(fan_out.Gh):
    """`fan_out.Gh` with its own token, a read cache, GraphQL, and the merge.

    THE MERGE LIVES HERE AND NOWHERE ELSE. `fan_out.Gh` has no merge method,
    and `test_sweep.py` holds that.
    """

    def __init__(self, token):
        super().__init__()
        self.token = token
        self.cache = {}

    def _api(self, *args, payload=None, missing_ok=False):
        argv = ["gh", "api", *args] + (["--input", "-"] if payload is not None else [])
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        env["GH_TOKEN"] = self.token
        try:
            done = subprocess.run(
                argv, input=json.dumps(payload) if payload is not None else None,
                capture_output=True, text=True, check=False, timeout=fan_out.GH_TIMEOUT, env=env)
        except subprocess.TimeoutExpired as expired:
            raise Refusal(f"gh api timed out after {fan_out.GH_TIMEOUT}s") from expired
        if done.returncode != 0:
            if missing_ok and "HTTP 404" in done.stderr:
                return None
            raise Refusal(f"gh api {' '.join(a for a in args if a != '--input')}: "
                          f"{done.stderr.strip()[:300]}")
        return done.stdout

    def get(self, path, fresh=False):
        if fresh or path not in self.cache:
            self.cache[path] = json.loads(self._api(path))
        return self.cache[path]

    def graphql_threads(self, owner, name, n):
        query = ("query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name)"
                 "{pullRequest(number:$n){reviewThreads(first:100){totalCount nodes{isResolved}}}}}")
        return json.loads(self._api("graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                                    "-f", f"name={name}", "-F", f"n={int(n)}"))

    def merge_pr(self, owner, name, n, head, title, message):
        self._api("-X", "PUT", f"repos/{fan_out.q(owner)}/{fan_out.q(name)}/pulls/{int(n)}/merge",
                  payload={"merge_method": "squash", "sha": head,
                           "commit_title": title, "commit_message": message})


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def report(rows, live):
    out = [f"## actions sweep — {'LIVE' if live else 'DRY RUN (no writes)'}", "",
           "| repository | pull request | verdict | reason |", "| --- | --- | --- | --- |"]
    for r in rows:
        verdict = r["action"] if live or r["action"] == "skip" else f"would {r['action']}"
        out.append(f"| `{r['repo']}` | {('#' + str(r['n'])) if r['n'] else '—'} | {verdict} "
                   f"| {fan_out.clean(r.get('reason', '')) or '—'} |")
    return "\n".join(out) + "\n"


def armed_from(path):
    with open(path, encoding="utf-8") as handle:
        return load(handle.read())


def env_live(classes):
    return is_live(os.environ.get("EVENT", ""), os.environ.get("DRY_RUN", ""),
                   os.environ.get("REF", ""), classes["sweep"]["live"])


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mode_cmd = sub.add_parser("mode")
    mode_cmd.add_argument("--classes", default="classes.json")
    name_cmd = sub.add_parser("check-name")
    name_cmd.add_argument("name")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--owner", required=True)
    ev.add_argument("--live", action="store_true")
    ev.add_argument("--classes", default="classes.json")
    go = sub.add_parser("act")
    go.add_argument("--owner", required=True)
    go.add_argument("--repo", required=True)
    go.add_argument("--n", required=True, type=int)
    go.add_argument("--head", required=True)
    go.add_argument("--action", required=True, choices=("merge", "refresh"))
    go.add_argument("--classes", default="classes.json")
    args = parser.parse_args(argv)

    try:
        if args.command == "check-name":
            # An empty `repositories:` beside `owner:` mints for EVERY repository.
            if not fan_out.NAME.fullmatch(args.name) or args.name in (".", ".."):
                raise Refusal("the leg names no single repository")
            return 0
        classes = armed_from(args.classes)
        if args.command == "mode":
            live = env_live(classes)
            fan_out.output("live", "true" if live else "false")
            print(f"live={'true' if live else 'false'}")
            return 0
        if args.command == "evaluate":
            if args.live and not env_live(classes):
                raise Refusal("--live, but the run is not armed")
            reader = SweepGh(os.environ.get("GH_TOKEN", ""))
            rows, todo = sweep(reader, args.owner, classes, now_utc())
            fan_out.summary(report(rows, args.live))
            fan_out.output("plan", json.dumps(todo if args.live else [], separators=(",", ":")))
            print(f"{len(rows)} rows to the step summary; {len(todo)} would act "
                  f"({'live' if args.live else 'dry'}).")
            # NO INFRA-GREEN. `judge` turns an API error into a skip row so one
            # repository cannot stop the sweep, but a run that could not READ is
            # not a run that found nothing to do: it reds.
            broken = [r["repo"] for r in rows if r.get("reason", "").startswith("error:")]
            if broken:
                message = f"{len(broken)} rows could not be evaluated: {', '.join(broken)}"
                fan_out.summary(f"\n**{fan_out.clean(message, 1000)}**\n")
                print(f"::error::{fan_out.annotation(message)}", file=sys.stderr)
                return 1
            return 0
        if not env_live(classes):
            raise Refusal("`act` runs only when the sweep is armed")
        if not (fan_out.NAME.fullmatch(args.repo) and SHA.fullmatch(args.head)):
            raise Refusal("`act` was handed a malformed repository or head")
        reader = SweepGh(os.environ.get("GH_TOKEN", ""))
        writer = SweepGh(os.environ.get("SWEEP_WRITE_TOKEN", ""))
        try:
            done = act(reader, writer, args.owner, args.repo, args.n, args.head, args.action,
                       classes, now_utc())
        except Skip as skip:
            done = f"skipped {args.owner}/{args.repo}#{args.n}: {skip}"
        fan_out.summary(fan_out.clean(done, 500) + "\n")
        print(fan_out.annotation(done))
        return 0
    except (OSError, ValueError, Refusal, Skip) as refusal:
        print(f"::error::{fan_out.annotation(refusal)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
