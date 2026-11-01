#!/usr/bin/env python3
"""The sweeper: merges a hook adoption pull request that proves itself. YAADGAAR 442 (c).

ADR-1106 ADDS BOT PULL REQUESTS, AND CHANGES NOTHING BELOW FOR ADOPTION ONES. In
every public repository of the owner (consumer or not, `exempt` or not; never
one in `sweep.exclude`, archived, disabled or a fork), an open pull request is
judged by `evaluate_bot` when `bot_kind` names it:

    dependabot  opener id 49699333, type Bot, login `dependabot[bot]`, head
                `dependabot/<ecosystem>/...`
    repin       opener id 324366854, type Bot, login `yadgarhq-bot[bot]`, head
                `repin/<crate>-<tag>_...` (`repin.branch`)

Any other pull request, the App's `actions-adopt` included, goes to `evaluate`
exactly as before. A bot pull request needs: head and base in this repository,
base the default branch, open, not draft, not locked, no `no-auto-merge` label
(B2); no change request and no unresolved thread (P5); PROVENANCE (B3): every
commit authored by that bot, verified, committed by `web-flow`, and not a merge,
save the sweeper's own update-branch commit in its exact shape (ADR-1119),
and every force-push the bot's; every changed file MODIFIED and owned by the
ecosystem (B8: `FILES` for dependabot's cargo, github_actions and docker,
Cargo files for repin; any other ecosystem is left for a person); every ref
update of the head branch pushed by the bot, or by the App for an
update-branch commit B3 admitted, the newest one the head (B9, the
repository activity API); `ci / passed` green on the head from the
repository's own pull_request CI run on that branch (V1, V2; `ci-self.yaml` in
the producer, which calls `ci-pr.yaml` by local path; V3 is not asked); and
`mergeable` true (U3); and, to merge, the head pushed at least
`sweep.bot_soak_hours` ago (B10, parity with T5). All dependabot update types
qualify, majors included.
Of several open repin pull requests in one repository only the newest is
judged (B4). repin.yaml makes its commit through the git data API
(actions#153), so GitHub signs it and `web-flow` commits it; a repin pull
request from before that carries an unsigned `git push` commit, fails B3 and
waits for a person.

Up to date (U1, U2): a squash merge at the head sha (GitHub refuses it if the
head moved), titled with the bot's own first commit subject (B7) and with the
fixed `BOT_MESSAGE`, never the editable pull request title or body, so no text
a person typed reaches history or `derive`; then the bot's branch is deleted, as `delete_branch_on_merge` is
off. A pull request merges ONLY when its head contains the base tip.

BEHIND (ADR-1119, superseding ADR-1109 and, for bot pull requests, ADR-0693):
never a merge. A behind bot pull request that passes every other gate (B2-B10,
V1 and V2 green on its current head, U3 mergeable) is REFRESHED: one
update-branch with `expected_head_sha` the judged head (GitHub answers 422 if
it moved), which merges the base tip into the branch. The row is due when CI
on the new head and the restarted B10 soak should be done
(`REFRESH_WAIT_MINUTES` or the soak, whichever is longer), so the follow-up
(ADR-1108) asks for a later hop, which judges the new head from scratch and
merges it. The commit GitHub makes for it is the one merge commit B3 admits
(App author, `web-flow` committer, verified, parents [previous head, a commit
on the base tip], GitHub's message), and B9 admits the App's push of exactly
that commit. Strict up-to-date rules and `mergeable_state` (the old U4 and U5)
are no longer read. Dependabot stops rebasing a pull request once someone else
has pushed to it, so a refreshed dependabot pull request is kept current by
this sweeper from then on. Red, pending or conflicting: a skip, left for a
person. `act` judges the pull request again from scratch, B3 included, on the
same head before it writes. The plan takes adoption writes first, then at most
one bot write per repository per run (M5) within the same merge cap (M4); a
refresh is a write but not a merge.

A BEHIND pull request whose base did not touch `.pre-commit-config.yaml` is
REFRESHED instead: `actions-adopt` is moved, in one ref write, to a new commit
on the base tip carrying the same file (`fan_out.Gh.rewrite_branch`; never
through the base tip itself, which closes the pull request, yaadgaar 466), so
CI runs fresh and a later sweep can merge it. That write is a compare-and-swap
from the head evaluated (yaadgaar 470): a fan-out rewrite that lands first is
kept, and the refresh is a skip. An adoption pull request is still never
update-branched (ADR-0693 stands there; ADR-1119 sanctions it for bot pull
requests only): its one commit is what K2 proves. Never through
`fan_out.one`, which no-ops on a behind pull request whose config is unchanged.

THE MERGE IS A SQUASH that sends the head sha, the title and the body itself.
The body carries no Conventional Commits bullet (P7), so the consumer's
`version` job cuts no release for it. A merge GitHub refuses (405, 409, 422) is
a skip; any other failure (401, 403, 404, no usable auth, an error with no
HTTP code) is the write token or the call, not the pull request, and reds the
`act` job. So does a re-evaluation that could not read (yaadgaar 469).

A LOST CHECK (V1r, yaadgaar 518 item 4). GitHub can drop a `ci / passed`
check run from `commits/<head>/check-runs` although the job that posts it
succeeded: ten adoption pull requests sat BLOCKED that way on 2026-11-01, and
one rerun each fixed them. When V1 finds no `ci / passed` check run at all,
`lost_check` looks for that case: the pull request's own ci run for the head
(exactly one), completed and green, at attempt 1, whose attempt-1 jobs hold
one green `ci / passed` job whose check run, read by id, is green on this
head in that run's suite, and the run was created less than `LOST_MAX_AGE`
ago. The probe's verdict counts only once every gate that can still refuse
the pull request has passed (yaadgaar 518 item 5 (c)): U2 and U3 for an
adoption pull request; U3, U2 or U1, and B10 for a bot one. A behind bot pull
request with a lost check is rerun, never refreshed: a refresh needs
`ci / passed` proven green, and a later hop refreshes it. Then the row is `rerun`:
`act` reruns that run once, re-reading the attempt up to `RERUN_READS` times
(item 5 (d)), and the row is handed to the follow-up as due
`RERUN_WAIT_MINUTES` later. A run already rerun and completed, still running
at attempt 1, red, too old, or one of several is never rerun: V1 skips as
before, and says why. A check run that exists but is red or pending is V1 as
before, and never looked at for a rerun. A V1 skip whose run is a rerun still
running (attempt 2 or more, started within `RERUN_RUNNING_WINDOW`) carries a
due `RERUN_WAIT_MINUTES` later (item 5 (b)), so a slow CI does not end the
chain.

THE FOLLOW-UP (ADR-1108: bot merges within minutes). GitHub fires the
cron every 3-4 hours, and no event in another repository reaches this one
without a new credential or a webhook. So a LIVE run that leaves a pull request
held back only by time (B10, whose row carries `due`), refreshed (ADR-1119,
due once CI on the new head should be done), or held by its own plan (M4,
M5) dispatches the next run of this workflow for when that pull request is due:
`follow_up` gives the minutes, the `follow-up` job sleeps them and dispatches
with GITHUB_TOKEN (`actions: write`, this repository only). A planned rerun's
due is not in `follow_up`: `reruns_due` hands it on, and after `act` the
`follow-up` command (`after_act`) keeps it only if the run is now past attempt
1, so a rerun GitHub refused is not planned again every hop (yaadgaar 518 item
5 (a)). The dispatched run
carries `hop` + 1 and judges everything from scratch; nothing it was handed
admits anything. A run at `MAX_HOPS`, or one whose soonest due row is an hour
or more away, asks for nothing, and the cron picks that up.

DRY UNTIL ARMED. `classes.json` arms the sweep with `"sweep": {"live": true}`
(yaadgaar 465). While it is false the workflow mints no write token and runs
no `act` job, and `evaluate` writes its verdict table to the step summary only. Reads go through
`GITHUB_TOKEN`: R1 admits only public repositories, so nothing here needs to
read a private one. The merge and the branch rewrite, with the reads the
rewrite makes (the repository's node id, the parent commit, the new commit, and
the branch after a failed swap), are the only calls made with the App token,
minted per repository with `contents: write`. A bot leg's token (ADR-1106) is
minted per repository with `contents` and `workflows: write`, for the merge and
the branch delete. A bot refresh leg's token (ADR-1119) is minted per
repository with `contents`, `pull_requests` and `workflows: write`, for the
one update-branch. A rerun leg's token (V1r) is minted per repository with
`actions: write` only, for the one rerun.

Commands:

    sweep.py mode                       EVENT, DRY_RUN, REF -> `live=true|false`
    sweep.py wait --owner O             WAIT_MINUTES -> sleep until the soak should be done
    sweep.py check-name -- NAME         a matrix leg's repository, or exit 1
    sweep.py evaluate --owner O [--live]    HOP -> `plan`, and live `follow_up` and `next_hop`
    sweep.py act --owner O --repo R --n N --head SHA --action merge|refresh|rerun [--kind dependabot|repin]
    sweep.py follow-up --owner O        FOLLOW_UP, RERUNS, NEXT_HOP -> `minutes`, or nothing

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
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
# ADR-1106. The two bots whose pull requests merge when CI is green, each by
# the id GitHub assigned it AND the head branch its own tooling writes. The
# login is checked too, but it is the id and the branch that admit.
DEPENDABOT_ID = 49699333
BOTS = {
    "dependabot": (DEPENDABOT_ID, "dependabot[bot]", re.compile(r"dependabot/[A-Za-z0-9_.+-]+(/[A-Za-z0-9_.+-]+)+")),
    # `repin.branch`: `repin/` and `<crate>-<tag>` parts joined by `_`. One
    # segment, no `..`: the name goes into a ref path on delete.
    "repin": (BOT_ID, "yadgarhq-bot[bot]", re.compile(r"repin/[A-Za-z0-9_+-]+(\.[A-Za-z0-9_+-]+)*")),
}
CI_SELF = ".github/workflows/ci-self.yaml"
# F1. A bot merge never carries the pull request's editable body: a `feat!`
# bullet typed there would reach `next_version.derive`. Fixed text with no
# bullet, so `derive` takes its synthetic `fix(deps)` path as for any bot merge.
BOT_MESSAGE = "Merged by the yadgarhq/actions sweeper (ADR-1106): ci / passed was green on this head."
TITLE_MAX = 200
# F3. What a bot pull request may change, by the ecosystem its branch names.
# Exactly the ecosystems the estate's dependabot.yml files use (2026-11-01:
# cargo, github-actions, docker); any other is left for a person. Each entry is
# matched against the WHOLE path.
_ANY = r"(?:[^/]+/)*"
FILES = {
    "cargo": re.compile(rf"{_ANY}Cargo\.(?:toml|lock)"),
    "github_actions": re.compile(rf"\.github/workflows/[^/]+\.ya?ml|{_ANY}action\.ya?ml"),
    "docker": re.compile(rf"{_ANY}(?:Dockerfile|Containerfile)[^/]*"),
}
REPIN_FILES = FILES["cargo"]
# The only merge failures that are GitHub's verdict on the pull request. Any
# other (401/403/404, 5xx, no usable auth, an error with no HTTP code) is not,
# and reds the leg (yaadgaar 469).
REFUSED = re.compile(r"\bHTTP (405|409|422)\b")
PAGE = 100  # a list of exactly this many may have a second page: refused
SWEEP_KEYS = {"live", "exclude", "soak_hours", "bot_soak_hours", "max_merges",
              "max_refreshes_per_pr_per_day"}
DAY = datetime.timedelta(hours=24)
# ADR-1073 (yaadgaar 468). The fan-out dispatches this workflow with a
# `wait_minutes` cap; the run sleeps until the newest tag's soak plus this
# margin for the consumers' CI, never longer than the cap, never past this one.
CI_MARGIN = datetime.timedelta(minutes=5)
MAX_WAIT_MINUTES = 60
WAIT_INPUT = re.compile(r"0|[1-9][0-9]?")
# The follow-up chain: a run dispatched by a run carries `hop` + 1, and a run
# at MAX_HOPS dispatches nothing. Six hops cover a soak plus the M5 queue of a
# busy repository; the cron is the backstop beyond that.
MAX_HOPS = 6
HOP_INPUT = re.compile(r"[0-9]")
HELD = ("M4:", "M5:")
WRITES = ("merge", "refresh", "rerun")  # the actions a plan row can carry
# V1r (yaadgaar 518 item 4). A rerun row is due this long after the run that
# planned it: a rerun of a config-only adoption run took about 2.5 minutes
# (yadgar run 41290991069, 2026-11-01), a cargo suite takes longer. GitHub's
# run timestamps were skewed that day, so no duration is read from them.
RERUN_WAIT_MINUTES = 15
# ADR-1119. A refresh row is due this long after the run that planned it, or
# after the bot soak if that is longer: ci p95 is under 5 minutes, the cold
# maximum 13.
REFRESH_WAIT_MINUTES = 15
# (b) yaadgaar 518 item 5. A V1-pending row whose own ci run is at attempt 2 or
# more, not completed, and started (`run_started_at`, the latest attempt's
# start) less than this long ago, either way, is due RERUN_WAIT_MINUTES later.
RERUN_RUNNING_WINDOW = datetime.timedelta(minutes=MAX_WAIT_MINUTES)
# A run created (`created_at`, the first attempt's) this long ago or more is
# never rerun: a lost check is seen within hours, and GitHub reruns a run for
# 30 days only, so an older run would answer every cron with a refusal.
LOST_MAX_AGE = datetime.timedelta(hours=24)
# (d) GitHub may show the new attempt a moment after the rerun POST answers.
RERUN_READS = 3
RERUN_READ_PAUSE = 5  # seconds
FOLLOW_INPUT = re.compile(r"[1-9][0-9]?")


class Skip(Exception):
    """This pull request is not merged or refreshed. The message says why."""


class Lost(Skip):
    """V1r: `ci / passed` is missing from the head's check runs, but the job that
    posts it succeeded in the pull request's own ci run, attempt 1. That run is
    rerun once. Still a Skip anywhere else: it never admits a merge."""

    def __init__(self, message, head, run):
        super().__init__(message)
        self.head, self.run, self.kind = head, run, ""


class NotYet(Skip):
    """A Skip that only time lifts: the pull request is due at `at` (B10)."""

    def __init__(self, message, at):
        super().__init__(message)
        self.at = at


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
    for key in ("max_merges", "max_refreshes_per_pr_per_day"):
        if not is_int(sweep[key]) or sweep[key] < 0:
            raise Refusal(f"classes.json: `sweep.{key}` must be a non-negative integer")
    # Hours may be fractional (ADR-1068 sets 0.5); a bool, a string, NaN and
    # infinity are not hours.
    for key in ("soak_hours", "bot_soak_hours"):
        soak = sweep[key]
        if type(soak) not in (int, float) or not math.isfinite(soak) or soak < 0:
            raise Refusal(f"classes.json: `sweep.{key}` must be a non-negative finite number")
    return classes


def is_live(event, dry_run, ref, armed):
    """R4. Writes happen only when `sweep.live` is the JSON `true`, from `main`,
    on the schedule or on a dispatch that says `dry_run: false`."""
    if armed is not True or ref != "refs/heads/main":
        return False
    if event == "schedule":
        return True
    return event == "workflow_dispatch" and dry_run == "false"


def wait_minutes(text):
    """The `wait_minutes` input. Empty (the schedule sends none) is 0; anything
    else must be a plain integer from 0 to MAX_WAIT_MINUTES."""
    if text == "":
        return 0
    if not (isinstance(text, str) and WAIT_INPUT.fullmatch(text)) or int(text) > MAX_WAIT_MINUTES:
        raise Refusal(f"wait_minutes must be an integer from 0 to {MAX_WAIT_MINUTES}")
    return int(text)


def wait_seconds(tag_time, soak_hours, now, cap_minutes):
    """Seconds until `tag_time` + soak + CI_MARGIN, clamped to [0, cap].

    THE WAIT IS NOT A GATE. T5 is still checked by `evaluate` and again by
    `act`, so a wait that is too long only costs time and one that is too short
    only finds nothing to merge. An unreadable tag (None) waits the whole cap:
    later, never earlier."""
    cap = cap_minutes * 60
    if cap == 0:
        return 0
    if tag_time is None:
        return cap
    left = (tag_time + datetime.timedelta(hours=soak_hours) + CI_MARGIN - now).total_seconds()
    return max(0, min(cap, math.ceil(left)))


def newest_tag_time(api, owner):
    """The committer date of the commit the greatest plain vX.Y.Z tag on
    `actions` names: where the T5 soak counts from (ADR-1072). None when any
    read or shape fails."""
    try:
        refs = api.get(f"repos/{owner}/{ACTIONS}/git/matching-refs/tags/v")
        need(isinstance(refs, list), "unexpected API shape: tag refs")
        names = {}
        for r in refs:
            name = dig(r, "ref")
            if isinstance(name, str) and name.startswith("refs/tags/"):
                names[name[len("refs/tags/"):]] = r
        tag = fan_out.greatest([n for n in names if fan_out.TAG.fullmatch(n)])
        need(tag is not None, "no plain tag")
        kind, sha = dig(names[tag], "object", "type"), dig(names[tag], "object", "sha")
        if kind == "tag":
            obj = api.get(f"repos/{owner}/{ACTIONS}/git/tags/{sha}")
            kind, sha = dig(obj, "object", "type"), dig(obj, "object", "sha")
        need(kind == "commit" and isinstance(sha, str) and bool(SHA.fullmatch(sha)), "not a commit")
        commit = api.get(f"repos/{owner}/{ACTIONS}/commits/{sha}")
        need(dig(commit, "sha") == sha, "the commits API answered for another sha")
        return parse_time(dig(commit, "commit", "committer", "date"))
    except (Skip, Refusal, ValueError, TypeError, KeyError):
        return None


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


def ci_shape(name):
    """The workflow that posts a repository's `ci / passed`, and the reusable
    workflow it must call (None: not read). The producer tests `ci-pr.yaml` by
    local path from `ci-self.yaml`; every other repository calls it at main."""
    if name == ACTIONS:
        return CI_SELF, None
    return CI_WORKFLOW, CI_PR


def check_ci(api, owner, name, head, *, now, branch=fan_out.BRANCH, workflow=CI_WORKFLOW, caller=CI_PR,
             precommit=True):
    """V1-V3. Statuses (V4) are never read. The defaults are the adoption pull
    request's; a bot pull request (ADR-1106) passes its own branch and
    `ci_shape`, and does not ask for V3. A `Lost` (V1r) or a `NotYet` (a rerun
    still running) is still a skip: neither ever reaches V2."""
    got = api.get(f"repos/{owner}/{name}/commits/{head}/check-runs?per_page={PAGE}")
    runs = dig(got, "check_runs")
    need(isinstance(runs, list) and is_int(dig(got, "total_count"))
         and got["total_count"] == len(runs) and len(runs) < PAGE, "V1: check runs not read in full")
    passed = [r for r in runs if dig(r, "name") == PASSED]
    if not passed:
        try:
            run = lost_check(api, owner, name, head, branch, workflow, now)
        except NotYet as later:  # (b): keep its due
            raise NotYet(f"V1: no `{PASSED}` check run; not rerun: {later}", later.at) from None
        except Skip as why:
            raise Skip(f"V1: no `{PASSED}` check run; not rerun: {why}") from None
        raise Lost(f"V1r: the `{PASSED}` job of run {run} succeeded but its check run is missing "
                   f"on the head; rerun run {run} once", head, run)
    if not all(dig(r, "status") == "completed" and dig(r, "conclusion") == "success" for r in passed):
        red = f"V1: a `{PASSED}` check run is not a completed success"
        if any(r.get("status") != "completed" for r in passed):
            try:
                due = rerun_running(own_ci_run(api, owner, name, head, branch, workflow), now)
            except Skip:
                due = None
            if due is not None:
                raise NotYet(f"{red}; {due[1]}", due[0])
        raise Skip(red)
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
        need(dig(run, "path") == workflow, f"V2: `{PASSED}` came from {run.get('path')!r}")
        need(dig(run, "event") == "pull_request", f"V2: `{PASSED}` ran on {run.get('event')!r}")
        need(dig(run, "head_sha") == head and dig(run, "head_branch") == branch,
             "V2: the run is for another head")
        need(dig(run, "status") == "completed" and dig(run, "conclusion") == "success",
             "V2: the run is not a completed success")
        if caller is not None:
            refs = [w for w in as_list(dig(run, "referenced_workflows"), "referenced workflows")
                    if dig(w, "path") == caller]
            need(len(refs) == 1, f"V2: the run does not call `{caller}` exactly once")
            sha = dig(refs[0], "sha")
            need(isinstance(sha, str) and bool(SHA.fullmatch(sha)), "V2: the called ci-pr.yaml has no sha")
            on_main(api, owner, sha, "V2: the called ci-pr.yaml is not on actions main")
        if not precommit:
            continue
        pre = [r for r in runs if dig(r, "name") == PRECOMMIT and dig(r, "check_suite", "id") == suite]
        need(len(pre) >= 1 and all(dig(r, "status") == "completed" and dig(r, "conclusion") == "success"
                                   for r in pre), f"V3: `{PRECOMMIT}` is not green in the same suite")


def own_ci_run(api, owner, name, head, branch, workflow):
    """The pull request's own ci run for this head: exactly one run of
    `workflow` on `pull_request` for this head and branch, read in full."""
    wf = api.get(f"repos/{owner}/{name}/actions/runs?head_sha={head}&per_page={PAGE}")
    wruns = dig(wf, "workflow_runs")
    need(isinstance(wruns, list) and is_int(dig(wf, "total_count"))
         and wf["total_count"] == len(wruns) and len(wruns) < PAGE, "workflow runs not read in full")
    mine = [w for w in wruns if isinstance(w, dict) and w.get("path") == workflow
            and w.get("event") == "pull_request" and w.get("head_sha") == head
            and w.get("head_branch") == branch]
    need(len(mine) == 1, f"{len(mine)} ci runs for this head, not one")
    return mine[0]


def rerun_running(run, now):
    """(b) yaadgaar 518 item 5. `(due, why)` when `run` is a rerun still going:
    an integer attempt of 2 or more, a status that is not `completed`, and a
    readable `run_started_at` less than RERUN_RUNNING_WINDOW from `now`. Else
    None. Only a due: the row stays a skip, and the next run judges it again."""
    attempt, status = run.get("run_attempt"), run.get("status")
    if not (is_int(attempt) and attempt >= 2 and isinstance(status, str) and status != "completed"):
        return None
    try:
        started = parse_time(run.get("run_started_at"))
    except Skip:
        return None
    if abs(now - started) >= RERUN_RUNNING_WINDOW:
        return None
    return (now + datetime.timedelta(minutes=RERUN_WAIT_MINUTES),
            f"run {run.get('id')} is at attempt {attempt} and still {status}: due again in "
            f"{RERUN_WAIT_MINUTES} min")


def lost_check(api, owner, name, head, branch, workflow, now):
    """V1r. The id of the run whose `ci / passed` check GitHub lost, or a Skip
    saying why this is not that (yaadgaar 518 item 4: ten adoption pull requests
    sat BLOCKED although the job had succeeded; one rerun each fixed it).

    Exactly one run of `workflow` on `pull_request` for this head and branch
    (`own_ci_run`), completed and a success, at attempt 1: a run already rerun
    is never rerun again, and one still running is not lost but pending (a
    second run while one is in progress blocks the pull request, task-db#51);
    a rerun still running is a `NotYet` (`rerun_running`). Created less than
    LOST_MAX_AGE ago. Its attempt-1 jobs,
    read in full, hold exactly one `ci / passed` job, completed and a success,
    whose own check run, read by id in this repository, is a completed success
    by GitHub Actions for this head in that run's suite. So the check was made
    and lost from the listing, not never made."""
    run = own_ci_run(api, owner, name, head, branch, workflow)
    rid, suite = dig(run, "id"), dig(run, "check_suite_id")
    need(is_int(rid) and is_int(suite), "the ci run has no id or suite")
    due = rerun_running(run, now)
    if due is not None:
        raise NotYet(due[1], due[0])
    need(dig(run, "status") == "completed" and dig(run, "conclusion") == "success",
         f"run {rid} is not a completed success")
    attempt = run.get("run_attempt")
    need(is_int(attempt), f"run {rid} has no attempt number")
    need(attempt == 1, f"run {rid} is at attempt {attempt}: it was rerun already")
    try:
        created = parse_time(run.get("created_at"))
    except Skip:
        raise Skip(f"run {rid} has no readable creation time") from None
    need(now - created < LOST_MAX_AGE, f"run {rid} was created {LOST_MAX_AGE} or more ago")
    got = api.get(f"repos/{owner}/{name}/actions/runs/{rid}/attempts/1/jobs?per_page={PAGE}")
    jobs = dig(got, "jobs")
    need(isinstance(jobs, list) and is_int(dig(got, "total_count"))
         and got["total_count"] == len(jobs) and len(jobs) < PAGE, f"run {rid}'s jobs not read in full")
    job = [j for j in jobs if isinstance(j, dict) and j.get("name") == PASSED]
    need(len(job) == 1, f"run {rid} has {len(job)} `{PASSED}` jobs, not one")
    need(dig(job[0], "status") == "completed" and dig(job[0], "conclusion") == "success",
         f"run {rid}'s `{PASSED}` job is not a completed success")
    url = dig(job[0], "check_run_url")
    prefix = f"{API}repos/{owner}/{name}/check-runs/"
    tail = url[len(prefix):] if isinstance(url, str) and url.startswith(prefix) else ""
    need(tail.isascii() and tail.isdigit(), f"run {rid}'s `{PASSED}` job names no check run here")
    check = api.get(url[len(API):])
    need(dig(check, "name") == PASSED and dig(check, "head_sha") == head
         and is_int(dig(check, "app", "id")) and check["app"]["id"] == CI_APP_ID
         and dig(check, "check_suite", "id") == suite
         and dig(check, "status") == "completed" and dig(check, "conclusion") == "success",
         f"run {rid}'s `{PASSED}` check run is not its green check on this head")
    return rid


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
        lost = None
        try:
            check_ci(api, owner, name, head, now=now)
        except Lost as found:  # (c): rerun only a pull request the gates below admit
            lost = found
        need(dig(pr, "base", "sha") == tip, "U2: the pull request's base sha is not the base tip")
        need(dig(pr, "mergeable") is True, "U3: GitHub does not call it mergeable")
        if lost is not None:
            raise lost
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


# --------------------------------------------------------------------------
# ADR-1106: dependabot and repin pull requests
# --------------------------------------------------------------------------


def bot_kind(pr):
    """`dependabot`, `repin`, or None, from the opener's id, type and login and
    the head branch. A name alone is a claim: all four must agree."""
    if not isinstance(pr, dict):
        return None
    user, head = pr.get("user"), pr.get("head")
    if not (isinstance(user, dict) and isinstance(head, dict)):
        return None
    for kind, (bot_id, login, branch) in BOTS.items():
        if (is_int(user.get("id")) and user["id"] == bot_id and user.get("type") == "Bot"
                and user.get("login") == login and isinstance(head.get("ref"), str)
                and branch.fullmatch(head["ref"])):
            return kind
    return None


def bot_candidate(pr):
    """The listing's cheap pre-filter: a bot login and its branch prefix. `judge`
    then decides from the pull request itself."""
    if not isinstance(pr, dict) or not is_int(pr.get("number")):
        return False
    login = (pr.get("user") or {}).get("login") if isinstance(pr.get("user"), dict) else None
    ref = (pr.get("head") or {}).get("ref") if isinstance(pr.get("head"), dict) else None
    return isinstance(ref, str) and any(
        login == bot_login and ref.startswith(branch.pattern.split("/")[0] + "/")
        for _, bot_login, branch in BOTS.values())


def check_bot_repo(meta, owner, classes):
    """R1-R3 for a bot pull request: every public repository of `owner` that is
    not excluded, archived, disabled or a fork. No class topic is asked for and
    `exempt` does not apply: that is exemption from ADOPTION, and ADR-1106
    covers every repository, the producer and `.github` included."""
    need(dig(meta, "private") is False and dig(meta, "visibility") == "public",
         "R1: not a public repository")
    name = dig(meta, "name")
    need(isinstance(name, str) and bool(fan_out.NAME.fullmatch(name)),
         "R3: a repository name outside GitHub's alphabet")
    need(name not in classes["sweep"]["exclude"], f"R2: excluded: {classes['sweep']['exclude'].get(name)}")
    need(dig(meta, "owner", "login") == owner, f"R3: not owned by {owner}")
    need(dig(meta, "archived") is False and dig(meta, "disabled") is False
         and dig(meta, "fork") is False, "R3: archived, disabled or a fork")


def check_bot_pr(pr, meta, owner):
    """P1 (B1), B2 and P4 for a bot pull request. Returns `(kind, branch)`."""
    kind = bot_kind(pr)
    need(kind is not None, "P1: not a dependabot or repin pull request (ADR-1106)")
    full = f"{owner}/{meta['name']}"
    need(dig(pr, "head", "repo", "full_name") == full
         and dig(pr, "base", "repo", "full_name") == full, "B2: head is not in this repository")
    need(dig(pr, "base", "ref") == dig(meta, "default_branch"), "B2: base is not the default branch")
    need(dig(pr, "state") == "open", "B2: not open")
    need(dig(pr, "draft") is False, "B2: a draft")
    need(dig(pr, "locked") is False, "B2: locked")
    labels = dig(pr, "labels")
    need(isinstance(labels, list), "B2: no label list")
    need(all(dig(label, "name") != NO_AUTO_MERGE for label in labels), f"B2: labelled `{NO_AUTO_MERGE}`")
    return kind, pr["head"]["ref"]


def check_bot_commits(commits, timeline, kind, head, base, branch, on_base):
    """B3, PROVENANCE. EVERY commit is the bot's own (author id and type Bot),
    verified, and committed by GitHub's `web-flow`, with exactly one parent;
    or it is the sweeper's own update-branch commit (ADR-1119), and nothing
    else. An author id is only an email match, so it never admits a commit
    alone. repin.yaml makes its commit through the git data API (actions#153),
    which GitHub signs and `web-flow` commits; an older repin pull request's
    `git push` commit is unsigned and committed by the App, so it fails this
    and waits for a person. Every force-push is the bot's.

    THE ONE MERGE COMMIT ADMITTED is the shape GitHub's update-branch makes,
    measured on gateway#159 (a person's): authored here by the App (BOT_ID,
    type Bot), verified, committed by `web-flow`, EXACTLY two parents, the
    first the commit before it in this list, the second on the base branch
    tip at judge time (`on_base`, a compare), and the message
    `Merge branch '<base>' into <branch>`. Never the first commit: there is no
    bot commit for it to bring up to date. A merge can carry any edit, and
    this shape does not prove there is none. What bounds it: GitHub's own
    update-branch never resolves a conflict, so a merge GitHub made is a clean
    one; after it the merge base is the base tip, so an edit to a file outside
    the ecosystem's allowlist is a changed file B8 refuses. An edit INSIDE a
    file B8 admits (more `Cargo.lock`, another workflow file for
    github_actions) is not caught here: a forged commit of this shape needs the
    App's key, for the commit and for the push B9 binds to it, which is the
    App-key trust repin and the ruleset bypass already rest on. Any other
    merge commit, whoever made or signed it, is refused.

    Returns the B7 subject and the set of admitted update-branch commits."""
    bot_id = BOTS[kind][0]
    commits = as_list(commits, "commits")
    need(len(commits) >= 1 and dig(commits, -1, "sha") == head, "B3: the last commit is not the head")
    refreshes = set()
    for i, commit in enumerate(commits):
        author, committer = dig(commit, "author"), dig(commit, "committer")
        need(dig(commit, "commit", "verification", "verified") is True
             and isinstance(committer, dict) and is_int(committer.get("id"))
             and committer["id"] == WEB_FLOW_ID, "B3: a commit not verified and committed by GitHub")
        parents = dig(commit, "parents")
        need(isinstance(parents, list) and len(parents) in (1, 2), "B3: a root commit or an octopus merge")
        if len(parents) == 1:
            need(isinstance(author, dict) and is_int(author.get("id")) and author["id"] == bot_id
                 and author.get("type") == "Bot", f"B3: a commit not authored by the {kind} bot")
            continue
        sha, first, second = dig(commit, "sha"), dig(parents, 0, "sha"), dig(parents, 1, "sha")
        need(i > 0 and isinstance(author, dict) and is_int(author.get("id")) and author["id"] == BOT_ID
             and author.get("type") == "Bot"
             and dig(commit, "commit", "message") == f"Merge branch '{base}' into {branch}"
             and first == dig(commits, i - 1, "sha")
             and isinstance(second, str) and bool(SHA.fullmatch(second)) and on_base(second)
             and isinstance(sha, str) and bool(SHA.fullmatch(sha)),
             "B3: a merge commit that is not the sweeper's update-branch (ADR-1119)")
        refreshes.add(sha)
    for event in as_list(timeline, "timeline"):
        if dig(event, "event") == "head_ref_force_pushed":
            need(dig(event, "actor", "id") == bot_id, "B3: someone else force-pushed the branch")
    # B7. The squash title is the bot's own first commit subject, which B3 just
    # proved the bot wrote, never the pull request title anyone can edit.
    message = dig(commits, 0, "commit", "message")
    subject = message.split("\n")[0].strip() if isinstance(message, str) else ""
    need(0 < len(subject) <= TITLE_MAX, "B7: the bot's commit has no usable subject for a title")
    return subject, refreshes


def check_bot_files(files, pr, kind, branch):
    """B8. Every file the pull request changes is MODIFIED (not added, removed,
    renamed or copied) and is one its ecosystem owns: `FILES` by the
    `dependabot/<ecosystem>/` segment, Cargo files for repin. A listing that
    may be truncated, or disagrees with `changed_files`, is a skip."""
    allowed = REPIN_FILES if kind == "repin" else FILES.get(branch.split("/")[1])
    need(allowed is not None, f"B8: no file allowlist for `{branch}`; left for a person")
    need(isinstance(files, list) and len(files) < PAGE, "B8: the files listing may be truncated")
    count = dig(pr, "changed_files")
    need(is_int(count) and count == len(files), "B8: the files listing disagrees with changed_files")
    for f in files:
        need(dig(f, "status") == "modified", f"B8: {f.get('filename')!r} is {f.get('status')!r}")
        name = dig(f, "filename")
        need(isinstance(name, str) and bool(allowed.fullmatch(name)),
             f"B8: {name!r} is not a file this pull request's ecosystem changes")


PUSHES = ("branch_creation", "push", "force_push")


def check_bot_pushes(activity, kind, head, refreshes):
    """B9. WHO PUSHED, which a commit cannot claim. The repository activity API
    names the actor of every ref update of the head branch, a plain push
    included. Every one must be the bot, or a plain `push` by the App
    (BOT_ID) of an update-branch commit B3 admitted (ADR-1119: GitHub records
    the App as the pusher of its update-branch, as gateway#159 recorded
    m-agahi for his; the App's case is not yet measured). The newest must have
    put `head` there. Returns when the head arrived, where the bot soak (B10)
    counts from: a refresh push restarts it. It also closes the API-commit
    forgery B3 cannot: a commit made through the API with the bot's author
    email may verify, but its push is the forger's."""
    bot_id = BOTS[kind][0]
    need(isinstance(activity, list) and 1 <= len(activity) < PAGE,
         "B9: the branch activity is empty or may be truncated")
    for event in activity:
        kind_of = dig(event, "activity_type")
        need(kind_of in PUSHES, f"B9: a {event.get('activity_type')!r} on the branch")
        actor = dig(event, "actor", "id")
        refresh = kind_of == "push" and actor == BOT_ID and dig(event, "after") in refreshes
        need(refresh or (actor == bot_id and dig(event, "after") not in refreshes),
             "B9: a ref update not pushed by the bot")
    need(dig(activity, 0, "after") == head, "B9: the newest ref update is not the head")
    return parse_time(dig(activity, 0, "timestamp"))


def contains(api, owner, name, tip):
    """For B3: whether the base tip `tip` contains commit `sha`, by a compare
    from `sha` to the tip. Anything but identical or ahead with nothing behind
    is no."""
    def on_base(sha):
        compare = api.get(f"repos/{owner}/{name}/compare/{sha}...{tip}")
        return dig(compare, "status") in ("identical", "ahead") and dig(compare, "behind_by") == 0
    return on_base


def evaluate_bot(api, owner, name, n, classes, now):
    """ADR-1106: merge a bot pull request whose `ci / passed` is green on its
    head, which contains the base tip. ADR-1119 (supersedes ADR-1109): a
    BEHIND one is never merged. When it passes every other gate it is
    REFRESHED (update-branch at its head), and a later run merges it once CI is
    green on the new head. Red, pending or conflicting: a skip."""
    meta = api.get(f"repos/{owner}/{name}")
    check_bot_repo(meta, owner, classes)
    pr = api.get(f"repos/{owner}/{name}/pulls/{int(n)}")
    need(dig(pr, "number") == n, "unexpected API shape: another pull request")
    kind, branch = check_bot_pr(pr, meta, owner)
    head = dig(pr, "head", "sha")
    need(isinstance(head, str) and bool(SHA.fullmatch(head)), "unexpected API shape: head sha")
    check_reviews(api.get(f"repos/{owner}/{name}/pulls/{n}/reviews?per_page={PAGE}"),
                  api.graphql_threads(owner, name, n))
    base = dig(pr, "base", "ref")
    tip = dig(api.get(f"repos/{owner}/{name}/git/ref/heads/{urllib.parse.quote(base, safe='/')}"),
              "object", "sha")
    need(isinstance(tip, str) and bool(SHA.fullmatch(tip)), "unexpected API shape: base tip")
    timeline = api.get(f"repos/{owner}/{name}/issues/{n}/timeline?per_page={PAGE}")
    # A full page hides older events: B3's force-push actors must read the
    # whole timeline, or the pull request is a skip.
    need(isinstance(timeline, list) and len(timeline) < PAGE, "B3: the timeline may be truncated")
    subject, refreshes = check_bot_commits(
        api.get(f"repos/{owner}/{name}/pulls/{n}/commits?per_page={PAGE}"), timeline, kind, head,
        base, branch, contains(api, owner, name, tip))
    check_bot_files(api.get(f"repos/{owner}/{name}/pulls/{n}/files?per_page={PAGE}"), pr, kind, branch)
    ref = urllib.parse.quote(f"refs/heads/{branch}", safe="/")
    pushed = check_bot_pushes(api.get(f"repos/{owner}/{name}/activity?ref={ref}&per_page={PAGE}"),
                              kind, head, refreshes)
    compare = fetcher(api)(ci_verdict.compare_url(f"{owner}/{name}", tip, head))
    status, behind = dig(compare, "status"), dig(compare, "behind_by")
    workflow, caller = ci_shape(name)
    lost = None
    try:
        check_ci(api, owner, name, head, now=now, branch=branch, workflow=workflow, caller=caller,
                 precommit=False)
    except Lost as found:  # (c): rerun only after U3, U2 or U1, and B10 admit it
        found.kind = kind
        lost = found
    need(dig(pr, "mergeable") is True, "U3: GitHub does not call it mergeable")
    verdict = {"repo": name, "n": n, "head": head, "title": subject, "body": BOT_MESSAGE,
               "kind": kind, "branch": branch, "reason": f"{kind} (ADR-1106)"}
    up_to_date = status == "ahead" and behind == 0
    if up_to_date:
        need(dig(pr, "base", "sha") == tip, "U2: the pull request's base sha is not the base tip")
    else:
        # BEHIND. U2 does not apply: `base.sha` is the old base by definition.
        need(status == "diverged" and is_int(behind) and behind > 0, f"U1: compare status {status!r}")
    # B10, parity with T5: the head must have sat on the branch this long.
    # Every other gate passed, so the row says when it is due (the follow-up).
    soak = datetime.timedelta(hours=classes["sweep"]["bot_soak_hours"])
    if now - pushed < soak:
        raise NotYet(f"B10: the head is younger than the {classes['sweep']['bot_soak_hours']}h bot soak",
                     pushed + soak)
    if lost is not None:
        raise lost
    if up_to_date:
        return dict(verdict, action="merge")
    # ADR-1119. Every gate passed on this head but U1: bring it up to date. The
    # refresh push is the branch's newest, so B10 restarts from it, and the
    # row is due once CI on the new head and that soak should both be done.
    return dict(verdict, action="refresh",
                reason=f"U: behind base by {behind}; brought up to date (update-branch), "
                       f"merges after CI on the new head",
                due=now + max(datetime.timedelta(minutes=REFRESH_WAIT_MINUTES), soak))


def route(api, owner, name, n, classes, now):
    """A bot pull request is judged by ADR-1106; anything else exactly as before."""
    if bot_kind(api.get(f"repos/{owner}/{name}/pulls/{int(n)}")) is not None:
        return evaluate_bot(api, owner, name, n, classes, now)
    return evaluate(api, owner, name, n, classes, now)


def ci_verdict_text(fetch, repo, commit):
    try:
        return ci_verdict.config_at(fetch, repo, commit)
    except ci_verdict.Refused as exc:
        raise Skip(f"C1: {exc}") from None


def judge(api, owner, name, n, classes, now):
    """`route`, with every failure of any kind turned into a Skip row."""
    try:
        return route(api, owner, name, n, classes, now)
    except NotYet as later:
        return {"repo": name, "n": n, "action": "skip", "reason": str(later), "due": later.at}
    except Lost as lost:  # V1r: a write, so the row carries the head and the run
        row = {"repo": name, "n": n, "head": lost.head, "action": "rerun", "run": lost.run,
               "reason": str(lost), "due": now + datetime.timedelta(minutes=RERUN_WAIT_MINUTES)}
        return dict(row, kind=lost.kind) if lost.kind else row
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
    # ONE ROW PER EXCLUSION, from classes.json (yaadgaar 467). The listing is
    # public-only, so a private excluded repository is never listed and never read.
    exclude = classes["sweep"]["exclude"]
    rows += [{"repo": name, "n": 0, "action": "skip", "reason": f"R2: excluded: {exclude[name]}"}
             for name in sorted(exclude)]
    for name in names:
        if name in exclude:
            continue
        if name in classes["exempt"]:
            rows.append({"repo": name, "n": 0, "action": "skip",
                         "reason": f"R2: exempt: {classes['exempt'][name]}"})
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
    # ADR-1106. EVERY listed public repository, consumer or not, exempt or not;
    # never an excluded or archived one, whose pull requests are not read.
    every = sorted(m["name"] for m in listed if isinstance(m, dict) and isinstance(m.get("name"), str)
                   and fan_out.NAME.fullmatch(m["name"]) and m.get("archived") is not True
                   and m["name"] not in exclude)
    for name in every:
        prs = api.get(f"repos/{owner}/{name}/pulls?state=open&per_page={PAGE}")
        if not isinstance(prs, list):
            rows.append({"repo": name, "n": 0, "action": "skip",
                         "reason": "error: the open pull request listing is not a list"})
            continue
        if len(prs) >= PAGE:
            rows.append({"repo": name, "n": 0, "action": "skip",
                         "reason": f"{len(prs)} open pull requests may be truncated: no bot pull request read"})
            continue
        found = [pr for pr in prs if bot_candidate(pr)]
        # B4. A repin run has opened a NEW pull request, on a new `repin/...`
        # branch named for the newer tags, while an older one stayed open
        # (task-db #122/#125, 2026-10-31). Only
        # the newest repin pull request is judged; an older one is left for a
        # person. Every write is keyed on the number and the head sha anyway.
        repins = [pr["number"] for pr in found if pr["head"]["ref"].startswith("repin/")]
        for pr in found:
            if pr["number"] in repins and pr["number"] != max(repins):
                rows.append({"repo": name, "n": pr["number"], "action": "skip",
                             "reason": f"B4: an older open repin pull request; #{max(repins)} is newer"})
                continue
            rows.append(judge(api, owner, name, pr["number"], classes, now))
    return rows, plan(rows, classes["sweep"]["max_merges"])


def plan(rows, max_merges):
    """The `act` matrix: at most `max_merges` merges, and every refresh. M4.

    Adoption rows are planned first, exactly as before. A bot row (ADR-1106)
    then takes what is left (M4), and at most ONE write per repository per run,
    none in a repository an adoption write already holds (M5). With strict
    checks off a sibling stays mergeable while BEHIND, but a merge makes GitHub
    recompute every sibling's `mergeable` (null for a while: U3) and two bumps
    of one lock file conflict, so the second write waits for the next run,
    which judges it on the new base. `follow_up` asks for that run at once.

    A rerun (V1r) is a write but not a merge: it takes a repository under M5
    and never counts toward M4. A planned rerun keeps its `due` for
    `reruns_due`; a held one is a skip with an M5 reason, due at once."""
    out, merges = [], 0
    for row in rows:
        if row.get("kind"):
            continue
        if row["action"] == "merge":
            if merges >= max_merges:
                row.update(action="skip", reason=f"M4: the cap of {max_merges} merges per run is reached")
                continue
            merges += 1
        if row["action"] in WRITES:
            out.append({"repo": row["repo"], "n": row["n"], "head": row["head"], "action": row["action"]})
    busy = {t["repo"] for t in out}
    for row in rows:
        if not row.get("kind") or row["action"] not in WRITES:
            continue
        if row["repo"] in busy:
            row.update(action="skip", reason="M5: one write per repository per run, and this one has it")
            continue
        if row["action"] == "merge":
            if merges >= max_merges:
                row.update(action="skip", reason=f"M4: the cap of {max_merges} merges per run is reached")
                continue
            merges += 1
        busy.add(row["repo"])
        out.append({"repo": row["repo"], "n": row["n"], "head": row["head"], "action": row["action"],
                    "kind": row["kind"]})
    return out


def hop_input(text):
    """The `hop` input. Empty (the schedule sends none) is 0; anything else must
    be one digit from 0 to MAX_HOPS."""
    if text == "":
        return 0
    if not (isinstance(text, str) and HOP_INPUT.fullmatch(text)) or int(text) > MAX_HOPS:
        raise Refusal(f"hop must be an integer from 0 to {MAX_HOPS}")
    return int(text)


def follow_up(rows, now, hop):
    """ADR-1108. Minutes until the next run should start, or None for no follow-up.

    A row with `due` (B10, a bot refresh, or a V1r rerun still running) is due a minute after
    that time; a row the plan held back (M4, M5) is due at once, one minute,
    after this run's `act`. A PLANNED rerun is not, here: whether it took is
    known only after `act`, so its due goes to `reruns_due` and `after_act`
    (yaadgaar 518 item 5 (a)). Nothing else is: a red, pending or conflicting
    pull request waits for the cron. A run at MAX_HOPS, or one whose soonest
    row is MAX_WAIT_MINUTES or more away, asks for nothing."""
    if hop >= MAX_HOPS:
        return None
    soonest = None
    for row in rows:
        if row.get("action") == "rerun":
            continue
        # Held first: a rerun row the plan held back carries a `due` for after
        # its rerun, but nothing was rerun, so it is due at once.
        if row.get("action") == "skip" and str(row.get("reason", "")).startswith(HELD):
            minutes = 1
        elif isinstance(row.get("due"), datetime.datetime):
            minutes = max(1, math.ceil((row["due"] - now).total_seconds() / 60) + 1)
        else:
            continue
        soonest = minutes if soonest is None else min(soonest, minutes)
    if soonest is None or soonest >= MAX_WAIT_MINUTES:
        return None
    return soonest


def reruns_due(rows, hop):
    """(a) The planned reruns, for the follow-up job: `{repo, run, due}` each,
    `due` as UTC text. None at MAX_HOPS, where no follow-up is asked."""
    if hop >= MAX_HOPS:
        return []
    return [{"repo": row["repo"], "run": row["run"], "due": row["due"].strftime("%Y-%m-%dT%H:%M:%SZ")}
            for row in rows if row.get("action") == "rerun" and isinstance(row.get("due"), datetime.datetime)]


def after_act(minutes, reruns, attempt_of, now):
    """(a) yaadgaar 518 item 5. The follow-up's minutes once `act` is done:
    evaluate's own `minutes` (None: none), or a planned rerun's due, but only
    for a rerun that took, its run now past attempt 1 (`attempt_of`). A rerun
    GitHub refused, or whose leg failed, leaves the run at attempt 1 and asks
    for nothing, so it is not planned again every hop. Never a gate: a wrong
    answer costs time, and the next run judges everything from scratch."""
    took = [{"action": "skip", "reason": "V1r: the rerun took", "due": r["due"]}
            for r in reruns if (lambda a: is_int(a) and a >= 2)(attempt_of(r["repo"], r["run"]))]
    found = [m for m in (minutes, follow_up(took, now, 0)) if m is not None]
    return min(found) if found else None


def follow_up_input(text):
    """The `follow_up` evaluate handed on: empty, or 1 to MAX_WAIT_MINUTES - 1."""
    if text == "":
        return None
    if not (isinstance(text, str) and FOLLOW_INPUT.fullmatch(text)) or int(text) >= MAX_WAIT_MINUTES:
        raise Refusal(f"follow_up must be an integer from 1 to {MAX_WAIT_MINUTES - 1}")
    return int(text)


def reruns_input(text):
    """The `reruns` evaluate handed on, checked strictly: a list of exactly
    `{repo, run, due}`, a repository name, a positive integer, a UTC time."""
    if text == "":
        return []
    try:
        got = json.loads(text)
    except ValueError:
        raise Refusal("reruns is not JSON") from None
    if not isinstance(got, list) or len(got) > fan_out.MAX_CONSUMERS * 2:
        raise Refusal("reruns is not a short list")
    out = []
    for item in got:
        if not (isinstance(item, dict) and set(item) == {"repo", "run", "due"}
                and isinstance(item["repo"], str) and fan_out.NAME.fullmatch(item["repo"])
                and item["repo"] not in (".", "..") and is_int(item["run"]) and item["run"] > 0):
            raise Refusal(f"reruns holds a malformed entry: {str(item)[:100]}")
        try:
            due = parse_time(item["due"])
        except Skip:
            raise Refusal(f"reruns holds a malformed due: {str(item['due'])[:40]}") from None
        out.append(dict(item, due=due))
    return out


def act(reader, writer, owner, name, n, head, action, classes, now, kind=""):
    """M1-M3 for one leg: evaluate again from scratch, then merge or refresh.
    `kind` is the leg's (empty for an adoption leg): its token was minted for
    it, so a verdict of another kind writes nothing."""
    # Evaluate ran before a fan-out may have started; it must not race one (yaadgaar 470).
    need(not fan_out_running(reader, owner), "fan-out running")
    verdict = judge(reader, owner, name, n, classes, now)
    if verdict.get("reason", "").startswith("error:"):
        # A read that failed is not a pull request that stopped qualifying (yaadgaar 469).
        raise Refusal(f"M1: the re-evaluation failed: {verdict['reason']}")
    if verdict["action"] != action:
        raise Skip(f"M1: re-evaluated as {verdict['action']}: {verdict.get('reason', '')}")
    need(verdict.get("kind", "") == kind,
         f"M1: re-evaluated as kind {verdict.get('kind', '')!r}, the leg is {kind!r}")
    need(verdict["head"] == head, "M1: the head moved since evaluate")
    if action == "rerun":
        return act_rerun(reader, writer, owner, name, n, verdict["run"])
    if kind:
        return act_bot(reader, writer, owner, name, n, head, action, verdict)
    if action == "merge":
        try:
            writer.merge_pr(owner, name, n, head, f"{verdict['title']} (#{n})", verdict["body"])
        except Refusal as exc:
            # Only 405/409/422 is GitHub refusing this pull request. Anything
            # else is the token or the call, not the pull request: red the run.
            if REFUSED.search(str(exc)):
                raise Skip(f"M2: the merge was refused: {exc}") from None
            raise Refusal(f"M2: the merge call was denied: {exc}") from None
        after = reader.get(f"repos/{owner}/{name}/pulls/{n}", fresh=True)
        need(dig(after, "merged") is True, "M3: not merged after the merge call")
        return f"merged {owner}/{name}#{n} as {dig(after, 'merge_commit_sha')}"
    # A REFRESH. The branch must still be where evaluate read it.
    ref = reader.get(f"repos/{owner}/{name}/git/ref/heads/{fan_out.BRANCH}", fresh=True)
    need(dig(ref, "object", "sha") == head, "the branch moved since it was read: no reset")
    # ONE REF WRITE (yaadgaar 466). The new commit is built on the tip first and
    # the branch then moves straight to it: a reset to the tip first would make
    # head == base, and GitHub closes the pull request. The move is a
    # compare-and-swap from `head` (yaadgaar 470): a fan-out rewrite that landed
    # after the read above is kept, and this is a skip. Any other failure leaves
    # the branch where it was; a Refusal (an `Unverified` one included) reds the run.
    try:
        writer.rewrite_branch(owner, name, fan_out.BRANCH, verdict["tip"], config_only.CONFIG,
                              verdict["text"], verdict["title"], exists=True, expect=head)
    except fan_out.RefMoved as moved:
        raise Skip(f"the branch moved under the refresh: {moved}") from None
    return f"refreshed {owner}/{name}#{n} onto {verdict['tip']}"


def act_rerun(reader, writer, owner, name, n, run):
    """V1r's one write: rerun the run whose `ci / passed` check GitHub lost. The
    run id is the re-evaluation's, never the plan's. `lost_check` just read it
    at attempt 1, and the per-repository `act` group keeps two legs from both
    reading that; after the call it must be past attempt 1. A rerun GitHub
    refuses as it refuses a merge (405, 409, 422) is a skip; any other failure,
    403 included (a token without `actions: write` answers that), is the token
    or the call, and reds the leg. GitHub may show the new attempt a moment
    after it answers (yaadgaar 518 item 5 (d)), so the attempt is read up to
    RERUN_READS times, RERUN_READ_PAUSE seconds apart, before M3 says no."""
    try:
        writer.rerun_run(owner, name, run)
    except Refusal as exc:
        if REFUSED.search(str(exc)):
            raise Skip(f"M2: the rerun was refused: {exc}") from None
        raise Refusal(f"M2: the rerun call was denied: {exc}") from None
    attempt = None
    for read in range(RERUN_READS):
        if read:
            pause(RERUN_READ_PAUSE)
        after = reader.get(f"repos/{owner}/{name}/actions/runs/{int(run)}", fresh=True)
        attempt = dig(after, "run_attempt")
        if is_int(attempt) and attempt > 1:
            return f"reran {owner}/{name} run {run} for #{n} once (V1r), now attempt {attempt}"
    raise Skip(f"M3: run {run} is still at attempt {attempt} after the rerun ({RERUN_READS} reads)")


def pause(seconds):
    time.sleep(seconds)


def act_bot(reader, writer, owner, name, n, head, action, verdict):
    """ADR-1106's writes, after `act` judged the pull request again from
    scratch (B3 included) on this same head. A merge is the same squash at the
    head sha as an adoption merge (GitHub refuses it if the head moved), then
    the bot's branch is deleted (`delete_branch_on_merge` is off estate-wide).
    A REFRESH (ADR-1119) is one update-branch with `expected_head_sha` the
    judged head, and nothing else: no merge, no delete. GitHub answers it 202
    and makes the merge commit later, so no head move is asserted here; the
    next run judges whatever head is there from scratch."""
    if action == "refresh":
        try:
            writer.update_branch(owner, name, n, head)
        except Refusal as exc:
            if REFUSED.search(str(exc)):
                raise Skip(f"M2: update-branch was refused: {exc}") from None
            raise Refusal(f"M2: the update-branch call was denied: {exc}") from None
        return f"brought {owner}/{name}#{n} up to date from {head} (update-branch); merges after CI"
    need(action == "merge", f"M1: a bot leg merges or refreshes, not {action!r}")
    try:
        writer.merge_pr(owner, name, n, head, f"{verdict['title']} (#{n})", BOT_MESSAGE)
    except Refusal as exc:
        if REFUSED.search(str(exc)):
            raise Skip(f"M2: the merge was refused: {exc}") from None
        raise Refusal(f"M2: the merge call was denied: {exc}") from None
    after = reader.get(f"repos/{owner}/{name}/pulls/{n}", fresh=True)
    need(dig(after, "merged") is True, "M3: not merged after the merge call")
    writer.delete_branch(owner, name, verdict["branch"])
    return f"merged {owner}/{name}#{n} as {dig(after, 'merge_commit_sha')}; deleted {verdict['branch']}"


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

    def update_branch(self, owner, name, n, head):
        """ADR-1119: bring a behind bot pull request up to date, only from the
        head the sweeper judged (GitHub answers 422 when it moved)."""
        self._api("-X", "PUT", f"repos/{fan_out.q(owner)}/{fan_out.q(name)}/pulls/{int(n)}/update-branch",
                  payload={"expected_head_sha": head})

    def rerun_run(self, owner, name, run):
        """V1r: rerun one workflow run, every job (a lost check can be any job's)."""
        self._api("-X", "POST", f"repos/{fan_out.q(owner)}/{fan_out.q(name)}/actions/runs/{int(run)}/rerun")

    def delete_branch(self, owner, name, branch):
        """A merged bot pull request's branch, and no other. Gone already (404,
        or 422 `Reference does not exist`) is done."""
        if not any(pattern.fullmatch(branch) for _, _, pattern in BOTS.values()) or ".." in branch:
            raise Refusal(f"refusing to delete `{branch}`: not a dependabot or repin branch")
        try:
            self._api("-X", "DELETE",
                      f"repos/{fan_out.q(owner)}/{fan_out.q(name)}/git/refs/heads/{branch}",
                      missing_ok=True)
        except Refusal as exc:
            if "HTTP 422" not in str(exc):
                raise


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
    wait_cmd = sub.add_parser("wait")
    wait_cmd.add_argument("--owner", required=True)
    wait_cmd.add_argument("--classes", default="classes.json")
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
    go.add_argument("--action", required=True, choices=("merge", "refresh", "rerun"))
    go.add_argument("--kind", default="", choices=("", *BOTS))
    go.add_argument("--classes", default="classes.json")
    fu = sub.add_parser("follow-up")
    fu.add_argument("--owner", required=True)
    fu.add_argument("--classes", default="classes.json")
    args = parser.parse_args(argv)

    try:
        if args.command == "check-name":
            # An empty `repositories:` beside `owner:` mints for EVERY repository.
            if not fan_out.NAME.fullmatch(args.name) or args.name in (".", ".."):
                raise Refusal("the leg names no single repository")
            return 0
        classes = armed_from(args.classes)
        if args.command == "wait":
            cap = wait_minutes(os.environ.get("WAIT_MINUTES", ""))
            if cap == 0:
                print("no wait asked")
                return 0
            tagged = newest_tag_time(SweepGh(os.environ.get("GH_TOKEN", "")), args.owner)
            seconds = wait_seconds(tagged, classes["sweep"]["soak_hours"], now_utc(), cap)
            if tagged is None:
                print("::warning::the newest tag's time could not be read; waiting the whole cap")
            print(f"waiting {seconds}s (cap {cap} min) for the soak and the consumers' CI")
            time.sleep(seconds)
            return 0
        if args.command == "mode":
            live = env_live(classes)
            fan_out.output("live", "true" if live else "false")
            print(f"live={'true' if live else 'false'}")
            return 0
        if args.command == "evaluate":
            if args.live and not env_live(classes):
                raise Refusal("--live, but the run is not armed")
            hop = hop_input(os.environ.get("HOP", ""))
            reader = SweepGh(os.environ.get("GH_TOKEN", ""))
            now = now_utc()
            rows, todo = sweep(reader, args.owner, classes, now)
            fan_out.summary(report(rows, args.live))
            fan_out.output("plan", json.dumps(todo if args.live else [], separators=(",", ":")))
            minutes = follow_up(rows, now, hop) if args.live else None
            reruns = reruns_due(rows, hop) if args.live else []
            if minutes is not None:
                fan_out.output("follow_up", str(minutes))
            if reruns:
                fan_out.output("reruns", json.dumps(reruns, separators=(",", ":")))
            if minutes is not None or reruns:
                fan_out.output("next_hop", str(hop + 1))
                fan_out.summary(f"\nFollow-up: the next run in {minutes} min, and after each rerun that "
                                f"takes ({len(reruns)} planned) (hop {hop + 1} of {MAX_HOPS}).\n")
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
        if args.command == "follow-up":
            # (a) After `act`. Reads only; the workflow dispatches on `minutes`.
            minutes = follow_up_input(os.environ.get("FOLLOW_UP", ""))
            reruns = reruns_input(os.environ.get("RERUNS", ""))
            nxt = os.environ.get("NEXT_HOP", "")
            if not (HOP_INPUT.fullmatch(nxt) and 1 <= int(nxt) <= MAX_HOPS):
                raise Refusal(f"next_hop must be an integer from 1 to {MAX_HOPS}")
            reader = SweepGh(os.environ.get("GH_TOKEN", ""))

            def attempt_of(repo, run):
                got = reader.get(f"repos/{args.owner}/{repo}/actions/runs/{run}", fresh=True)
                need(dig(got, "id") == run, f"the runs API answered for another run than {run}")
                return dig(got, "run_attempt")

            done = after_act(minutes, reruns, attempt_of, now_utc())
            if done is not None:
                fan_out.output("minutes", str(done))
            message = (f"follow-up: the next run in {done} min (hop {nxt})" if done is not None
                       else "follow-up: nothing is due; no reruns took")
            fan_out.summary(fan_out.clean(message, 500) + "\n")
            print(message)
            return 0
        if not env_live(classes):
            raise Refusal("`act` runs only when the sweep is armed")
        if not (fan_out.NAME.fullmatch(args.repo) and SHA.fullmatch(args.head)):
            raise Refusal("`act` was handed a malformed repository or head")
        if not os.environ.get("SWEEP_WRITE_TOKEN"):
            raise Refusal("`act` has no SWEEP_WRITE_TOKEN")
        reader = SweepGh(os.environ.get("GH_TOKEN", ""))
        writer = SweepGh(os.environ["SWEEP_WRITE_TOKEN"])
        try:
            done = act(reader, writer, args.owner, args.repo, args.n, args.head, args.action,
                       classes, now_utc(), args.kind)
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
