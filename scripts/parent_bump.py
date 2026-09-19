#!/usr/bin/env python3
"""The release-time half of ADR-0722: pin the module, cut the parent version.

`parent_pin.py` DECIDES and this EXECUTES. That file rewrites one module's pin in
a parent `Chart.yaml` and derives the parent's next number through
`next_version.compute`; it was published by #82 and, until this file, was wired
to nothing — `grep -rn parent_pin .github/` answered with nothing at all. A
tested rewriter no workflow calls rewrites nothing, which is the shape ADR-0607
names and the shape ledger 686 measured across a whole suite. This file is the
caller, and `ci-release.yaml`'s `parent` job is the one place it runs.

EVERY RULE STILL LIVES IN `parent_pin`. This module imports `pins`, `pin`,
`parent_version` and `Refusal` and re-implements none of them: the semver
comparison, the equal-version refusal, the downgrade refusal, the
nothing-inspected refusals and the ladder are all read from there. What is here
is the ORDER of the API calls, the concurrency behaviour and the reporting —
none of which `parent_pin` can have an opinion about, because it never touches a
network.

WHAT THE RELEASE PATH ACTUALLY DOES, and why it is four calls rather than a git
clone. The parent lives in another repository, so this reads
`chart/Chart.yaml` through the Contents API, rewrites it in memory, PUTs it back
against the blob sha it read, then creates an annotated tag object and the ref
that points at it. Nothing is cloned and no working tree exists, so there is no
branch to leave behind and no credential written to disk. The two-call tag is
`ci-pr.yaml`'s own idiom, for the reason stated there: `POST /git/refs` alone
makes a LIGHTWEIGHT tag with no message, and the message is the permanent record
of what the version was derived from.

THE TAG IS WHAT PUBLISHES, AND IT ONLY WORKS BECAUSE AN APP CUTS IT. A tag
created with `GITHUB_TOKEN` triggers no workflow, so the parent would be pinned
and never published — inert, with every check green. An App token does trigger
one, and that is measured rather than believed: `yadgarhq/config` carries five
consecutive tag-push runs (`v0.1.1` … `v0.1.5`) whose actor is
`yadgarhq-bot[bot]`, each of them a successful release. ADR-0527's App is the one
this uses, scoped to `chart` alone.

A SECOND MINT RATHER THAN A WIDER FIRST ONE, decided in `ci-release.yaml` and
recorded here because this file is what the token is for. Adding `chart` to the
`argocd` mint would hand the token that pins the cluster's images write access to
the chart repository, and hand this write path the power to rewrite every
`versions/*.yaml`. The same argument the `estate` dispatch already makes for its
own separate mint.

CONCURRENCY IS THE DEFECT THIS FILE IS SHAPED AROUND. Eight modules release
independently and all eight rewrite ONE file, so a blind read-modify-write loses
a pin — and a lost pin is the worst outcome available here, because the parent
then PUBLISHES a set it does not carry and nothing downstream reports it. Two
separate mechanisms answer it, and they answer different halves:

  THE CONTENT. Every attempt re-reads `chart/Chart.yaml` and re-applies the pin
  to whatever it finds. The Contents API refuses a PUT carrying a stale blob sha,
  so the loser of a race gets an error rather than an overwrite, re-reads the
  winner's file — which carries the winner's pin — and writes a file carrying
  BOTH. The rewrite is textual and touches one token, so two pins never conflict
  as text either.

  THE VERSION. Every attempt re-lists the parent's tags, so the loser derives
  from the winner's new tag and gets the NEXT number rather than the same one.
  The remaining window is between the tag list and the ref creation, and it
  closes on `Reference already exists`: the tags are re-listed, the version
  re-derived from `parent_pin.parent_version` and the tag cut again. ADR-0722
  says a module release cuts a whole new parent version, so two module releases
  must produce TWO parent versions — never one merged one — and re-deriving is
  what keeps that true instead of letting the loser exit 0 on a tag that does not
  carry its pin.

A RE-RUN MUST BE ABLE TO FINISH THE JOB, and that is what `recover` is for. The
tag is cut and the module chart is published by the time this runs, so
"release again" is not available as a repair — `ci-release.yaml` spends four
lines forbidding it for the `argocd` write, for the same reason. The documented
repair is **Re-run failed jobs**, and it only works if a second run can tell
"the pin is already committed and published" from "the pin is committed and the
tag never happened". `parent_pin.pin` refuses an equal version outright and its
docstring says what to do instead — "a caller that wants idempotence reads the
pin first with `pins()`" — so that is what this does: it reads the pin in `main`
AND the pin at the newest published tag, and the two together say which of the
three states the parent is in.

WHAT THIS DOES NOT CLASSIFY, said rather than asserted. A failed PUT is retried
whatever its cause, because every attempt re-reads the file first and is
therefore safe to repeat; naming the HTTP status a stale sha produces would be a
claim this repository cannot measure without writing to the parent chart, and a
guessed cause stated confidently is the defect `ci-release.yaml`'s estate step
spends a paragraph correcting. `Reference already exists` IS matched on its text,
because `ci-pr.yaml` already records that string as what the live API puts on
stderr for the tag race it handles.

Usage: parent_bump.py, with OWNER, MODULE, VERSION and REPO in the environment.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import base64
import json
import os
import subprocess
import sys
import time
from collections import namedtuple

from parent_pin import Refusal, parent_version, pin, pins
from repin import greatest, output, summary

# WHERE THE PARENT IS, and both halves are constants rather than inputs. The
# repository name is fixed by D60 — one parent chart for the organisation, in its
# own repository — and the path is fixed by every other repository in the estate
# putting its chart under `chart/`. An input would be a knob no caller could
# answer differently without the parent's `dependencies:` block disagreeing.
PARENT = "chart"
PATH = "chart/Chart.yaml"
BRANCH = "main"

# THREE, THE SAME BOUND THE `argocd` WRITE USES, and for the reason given there:
# a third failure is not proof that no retry could ever clear it, it is the point
# at which reporting beats guessing.
ATTEMPTS = 3

EXISTS = "Reference already exists"

File = namedtuple("File", "sha text")


class Gh:
    """Every call this makes to GitHub, and nothing else reaches the network.

    ONE INJECTED `run` RATHER THAN A MOCKED `urllib`. `gh` already carries the
    token from `GH_TOKEN`, the retries GitHub asks for and the pagination, so
    re-implementing any of that here would be a second HTTP client to keep
    correct. What the injection buys is that the whole of this file is executable
    offline against an argv-dispatching stub, which is the only way a release
    path that cannot be run gets tested at all.
    """

    def __init__(self, owner, run=None, sleep=None):
        self.owner = owner
        self._run = run or (
            lambda args: subprocess.run(args, capture_output=True, text=True)
        )
        self._sleep = sleep or time.sleep

    def repo(self, *parts):
        return "/".join(("repos", self.owner, PARENT) + parts)

    def call(self, *args):
        """The process result, unexamined. Every caller decides what non-zero means."""
        return self._run(["gh"] + [str(a) for a in args])

    def pause(self, seconds):
        self._sleep(seconds)

    def contents(self, ref):
        """The parent's `Chart.yaml` at one ref, as its blob sha and its text."""
        done = self.call("api", f"{self.repo('contents', PATH)}?ref={ref}")
        if done.returncode != 0:
            raise Refusal(
                f"`{PATH}` could not be read from `{self.owner}/{PARENT}` at "
                f"{ref}: {stderr(done)}. Nothing was written."
            )
        try:
            body = json.loads(done.stdout)
            return File(body["sha"], base64.b64decode(body["content"]).decode("utf-8"))
        except (ValueError, KeyError, UnicodeDecodeError) as error:
            raise Refusal(
                f"the Contents API answer for `{PATH}` at {ref} could not be read "
                f"as a base64 file ({error}). Nothing was written."
            ) from error

    def head(self):
        """The commit `main` points at, which is what a recovered tag points at."""
        done = self.call("api", self.repo("commits", BRANCH), "--jq", ".sha")
        if done.returncode != 0:
            raise Refusal(
                f"`{BRANCH}` of `{self.owner}/{PARENT}` could not be read: "
                f"{stderr(done)}."
            )
        return done.stdout.strip()

    def tags(self):
        """Every `v*` tag the parent carries, unordered.

        `git/matching-refs` WITH `--paginate`, NOT `/tags?per_page=100`. The
        parent's cadence is the UNION of eight module cadences by ADR-0722, so it
        accumulates tags faster than any module does — `gateway` alone is at 74 —
        and a first page is a silently truncated list the moment there are more
        than a hundred. A truncated list whose greatest entry is missing derives a
        version that already exists, which is exactly the tag race this file
        handles at the bottom; there is no reason to manufacture one.

        IT IS A PREFIX MATCH AND ANSWERS MORE THAN RELEASES, said because the
        `v` reads like a version filter and is not: a `vendor-drop` tag comes back
        too, and `yadgarhq/actions` already carries a bare `v1` alongside fifty
        plain tags. Nothing here filters them out, because `repin.greatest` drops
        every unorderable entry — so a stray tag cannot become a baseline, and a
        list holding NOTHING orderable reaches `parent_version`'s no-baseline
        refusal rather than deriving from the stray.
        """
        done = self.call(
            "api", "--paginate", self.repo("git", "matching-refs", "tags/v"),
            "--jq", ".[].ref",
        )
        if done.returncode != 0:
            raise Refusal(
                f"the tags of `{self.owner}/{PARENT}` could not be listed: "
                f"{stderr(done)}. No version was derived and nothing was written."
            )
        return [
            line.strip()[len("refs/tags/") :]
            for line in done.stdout.splitlines()
            if line.strip().startswith("refs/tags/")
        ]

    def put(self, text, sha, message):
        """One commit rewriting `Chart.yaml`, or the error that refused it."""
        return self.call(
            "api", "-X", "PUT", self.repo("contents", PATH),
            "-f", f"message={message}",
            "-f", f"content={base64.b64encode(text.encode('utf-8')).decode('ascii')}",
            "-f", f"sha={sha}",
            "-f", f"branch={BRANCH}",
        )

    def tag(self, version, message, commit):
        """The annotated tag OBJECT, whose message is the record of the derivation."""
        done = self.call(
            "api", self.repo("git", "tags"),
            "-f", f"tag=v{version}", "-f", f"message={message}",
            "-f", f"object={commit}", "-f", "type=commit", "--jq", ".sha",
        )
        if done.returncode != 0:
            raise Refusal(
                f"the tag object for `v{version}` could not be created in "
                f"`{self.owner}/{PARENT}`: {stderr(done)}. The pin IS committed; "
                "re-run this job rather than releasing again."
            )
        return done.stdout.strip()

    def ref(self, version, obj):
        return self.call(
            "api", self.repo("git", "refs"),
            "-f", f"ref=refs/tags/v{version}", "-f", f"sha={obj}",
        )


def stderr(done):
    """One line of a process's complaint, for an annotation that has to fit."""
    return " ".join((done.stderr or done.stdout or "").split()) or "no output"


def message_for(module, previous, offered, repo):
    """The commit message, and IT MUST CARRY NO CHANGELOG BULLET.

    THIS IS LOAD-BEARING AND IT IS NOT A STYLE RULE. The push this message lands
    on `main` of the parent runs the parent's own `ci.yaml`, which calls
    `ci-pr.yaml`, whose `version` job derives a release from the Changelog
    bullets in the range. A bullet here would make the parent's own CI cut a
    SECOND tag for one module release, which ADR-0722 forbids — it says a module
    release cuts A new parent version.

    WITH NO BULLET IT STAYS GREEN AND CUTS NOTHING, measured against
    `next_version.py` rather than hoped for. `refuse_entries` is green when every
    commit in the range is bot-authored and the diff ships nothing, and both
    halves hold: `SHIPS` is `Cargo.toml`, `Cargo.lock`, `Containerfile` and
    `Dockerfile`, so `chart/Chart.yaml` is not in it; and the author GitHub
    stamps on a Contents API write by this App is
    `yadgarhq-bot[bot] <...@users.noreply.github.com>`, read off the twelve
    newest commits of `yadgarhq/argocd`, which `pr_body.BOT` matches. The
    synthesis that would ADD a bullet is gated on `ships(files)` and therefore
    does not fire either.
    """
    return "\n".join([
        f"ci: pin {module} {offered} in the parent chart",
        "",
        f"{module} {previous} -> {offered}, released by {repo}.",
        "ADR-0722: the release that publishes a module chart also cuts a new",
        "version of the parent, so the newest published parent always pins the",
        "newest released version of every module.",
        "",
        "No Changelog bullet, deliberately: one here would make this repository",
        "cut a second version for one module release. See parent_bump.py.",
    ])


def tag_message(version, note, module, previous, offered, last, repo):
    """The annotated tag's message — the permanent record of the derivation.

    `ci-pr.yaml` writes one for the same reason: the ruleset makes an annotated
    tag's message permanent, and this is the only place that knows what the
    number was derived FROM.
    """
    return "\n".join([
        f"v{version}",
        "",
        f"{note.replace('`', '')}, derived from {last} by ADR-0722's rule that a",
        "module release cuts a new parent version.",
        "",
        f"{module} {previous} -> {offered}, released by {repo}.",
    ])


def cut(gh, commit, previous, offered, module, repo):
    """The parent's next version, tagged, re-deriving when it loses a race.

    THE RE-DERIVATION IS THE POINT, not the retry. Two modules that read the tag
    list before either tagged derive the SAME number, and the loser's ref
    creation is refused. Exiting 0 there would leave its pin committed under a
    tag that does not carry it — a published parent claiming a set it does not
    have, which is this file's worst outcome. So the loser re-lists the tags,
    asks `parent_pin.parent_version` again, and cuts the number after the
    winner's.
    """
    error = "no attempt was made"
    for attempt in range(1, ATTEMPTS + 1):
        nxt, note, last = parent_version(gh.tags(), previous, offered)
        obj = gh.tag(
            nxt, tag_message(nxt, note, module, previous, offered, last, repo), commit
        )
        done = gh.ref(nxt, obj)
        if done.returncode == 0:
            return nxt, note, last
        error = stderr(done)
        if EXISTS not in error:
            raise Refusal(
                f"`refs/tags/v{nxt}` could not be created in "
                f"`{gh.owner}/{PARENT}`: {error}. The pin IS committed at "
                f"{commit[:7]}; re-run this job rather than releasing again."
            )
        if attempt < ATTEMPTS:
            gh.pause(attempt * 5)
    raise Refusal(
        f"{ATTEMPTS} parent versions in a row already existed — the last was "
        f"refused with {error!r} — so this release's pin is committed at "
        f"{commit[:7]} and carries no tag. That means {ATTEMPTS} other module "
        "releases tagged the parent while this one was deriving. Re-run this job: "
        "it re-reads the pin and cuts the version that is missing."
    )


def recover(gh, module, offered, repo):
    """`main` already pins this version. Decide whether anything published it.

    THREE STATES AND THIS TELLS THEM APART, because a re-run has to. The pin in
    `main` matching the offer means either a previous run of this job finished —
    in which case there is nothing to do and saying so is the right answer — or
    it committed and never tagged, in which case the parent's newest published
    version does NOT carry this module's release and the repair is to cut the
    version that is missing. The discriminator is the pin as the newest published
    TAG carries it, which is one extra read and no guessing.
    """
    last = greatest(gh.tags())
    if last is None:
        raise Refusal(
            f"`{module}` is already pinned at {offered} in `{BRANCH}` of "
            f"`{gh.owner}/{PARENT}`, and the parent carries no orderable `v*` "
            "tag at all, so there is nothing to compare it against and no "
            "baseline to derive from. The first tag of a repository is cut by "
            "hand — `next_version.py` says so."
        )

    published = pins(gh.contents(last).text).get(module)
    if published == offered:
        return None, (
            f"`{module}` is already pinned at **{offered}** in `{BRANCH}` and "
            f"`{last}` publishes that pin, so this release has already been "
            "taken into the parent. Nothing to write and nothing to tag — this "
            "is what a re-run of a run that already succeeded looks like."
        )
    if published is None:
        raise Refusal(
            f"`{module}` is pinned at {offered} in `{BRANCH}` of "
            f"`{gh.owner}/{PARENT}` but is not in `dependencies:` at `{last}`, "
            "so what the published parent says about this module cannot be read "
            "and no version movement can be derived from it."
        )

    commit = gh.head()
    nxt, note, base = cut(gh, commit, published, offered, module, repo)
    return nxt, (
        f"`{module}` was ALREADY pinned at **{offered}** in `{BRANCH}`, and "
        f"`{last}` still published {published} — so a previous run committed the "
        "pin and never cut the version. The pin is untouched and the missing "
        f"version is cut: the parent moves `{base}` → **`v{nxt}`** ({note}) at "
        f"`{commit[:7]}`."
    )


def bump(gh, module, offered, repo):
    """Pin the module and cut the parent version, re-reading on every attempt."""
    error = "no attempt was made"
    for attempt in range(1, ATTEMPTS + 1):
        current = gh.contents(BRANCH)
        # THE READ BEFORE THE REWRITE, which is what `parent_pin.pin`'s docstring
        # asks a caller that wants idempotence to do. It refuses an equal version
        # rather than no-op'ing, on purpose, so this asks first.
        if pins(current.text).get(module) == offered:
            return recover(gh, module, offered, repo)

        rewritten, previous = pin(current.text, module, offered)
        # BOTH DERIVATIONS BEFORE EITHER WRITE, which is `parent_pin.main`'s own
        # rule and the whole reason the parent's version is computed twice here.
        # `parent_version` refuses a parent that carries no orderable `v*` tag —
        # the first tag of a repository is cut by hand — and refuses two versions
        # it cannot order against each other. Deriving only AFTER the commit would
        # leave those refusals behind a pin nothing ever tags, which is the
        # half-finished state `recover` exists to clean up rather than to create.
        # The answer is thrown away: `cut` re-derives from a freshly listed set,
        # because between here and the tag another module may have moved the
        # parent, and the number that gets tagged has to be the later one.
        parent_version(gh.tags(), previous, offered)
        done = gh.put(
            rewritten, current.sha, message_for(module, previous, offered, repo)
        )
        if done.returncode == 0:
            commit = json.loads(done.stdout)["commit"]["sha"]
            nxt, note, last = cut(gh, commit, previous, offered, module, repo)
            return nxt, (
                f"`{module}` {previous} → **{offered}** in "
                f"`{gh.owner}/{PARENT}`'s `{PATH}`, compared as integers. Every "
                "other dependency is byte-identical.\n\n"
                f"The parent moves `{last}` → **`v{nxt}`** ({note}), derived "
                "from the estate's own ladder in `next_version.compute`, and "
                f"tagged at `{commit[:7]}`. That tag is what publishes the "
                "parent."
            )
        error = stderr(done)
        if attempt < ATTEMPTS:
            gh.pause(attempt * 5)
    raise Refusal(
        f"`{PATH}` in `{gh.owner}/{PARENT}` could not be written in {ATTEMPTS} "
        f"attempts; the last was refused with {error!r}. Every attempt re-read "
        "the file first, so a stale blob sha is not what is left — the usual "
        f"cause is the `{BRANCH}` ruleset on `{gh.owner}/{PARENT}`, where the "
        "release App has to be a bypass actor, exactly as it is on "
        f"`{gh.owner}/argocd`."
    )


def failed(module, offered, owner, note):
    """What a reader has to know, on the one path they will read it on."""
    return "\n".join([
        "### Published, but the parent chart does NOT pin it",
        "",
        f"`{module} {offered}` is released and pullable, and the parent chart in "
        f"`{owner}/{PARENT}` was NOT re-pinned: {note}",
        "",
        "ADR-0722 is what makes that fatal rather than untidy: an adopter "
        "installs the PARENT at one version, so a module release the parent "
        "never pins is a release no adopter can reach. The newest published "
        "parent now claims a set that is one module behind, and nothing "
        "downstream reports it.",
        "",
        f"RELEASING AGAIN IS NOT THE REPAIR. `v{offered}` is cut and permanent "
        "— the `release-tags` ruleset forbids deletion, update and "
        "non-fast-forward — and the module chart is already pushed. Once the "
        "cause is cleared, use **Re-run failed jobs** on this run: this job "
        "re-reads the pin and finishes whichever half is missing.",
    ])


def main(run=None, sleep=None):
    env = os.environ
    owner = env.get("OWNER", "").strip()
    module = env.get("MODULE", "").strip()
    offered = env.get("VERSION", "").strip().lstrip("v")
    repo = env.get("REPO", "").strip() or "an unnamed repository"

    if not owner or not module or not offered:
        print(
            "::error::parent_bump.py needs OWNER, MODULE and VERSION in the "
            f"environment; got {owner!r}, {module!r} and {offered!r}. Nothing "
            "was written."
        )
        return 2

    gh = Gh(owner, run=run, sleep=sleep)
    nxt, note = bump(gh, module, offered, repo)
    output(module=module, pin=offered, parent=f"v{nxt}" if nxt else "")
    summary("\n".join(["## The parent chart", "", note]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Refusal as refusal:
        # THE ANNOTATION AND THE SUMMARY, BOTH, and neither is the other's
        # substitute. The annotation is what appears on the run without opening
        # anything; the summary is where the repair fits. A step that writes only
        # a summary is how a dropped release becomes invisible, and one that
        # writes only an annotation tells nobody what to do.
        summary(
            failed(
                os.environ.get("MODULE", "this module"),
                os.environ.get("VERSION", "").lstrip("v"),
                os.environ.get("OWNER", "the organisation"),
                str(refusal),
            )
        )
        print(f"::error::the parent chart was not re-pinned: {refusal}")
        sys.exit(1)
