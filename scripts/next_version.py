#!/usr/bin/env python3
"""The release a merge implies, and a LOUD refusal where it implies none.

WHY THIS IS A FILE. It was a `python3 - <<'PY'` heredoc in `ci-pr.yaml`'s
`version` job, and `hooks/run_block_size.py` already says what that costs: "a
heredoc is undiffable, untestable and invisible to every linter". That mattered
here more than anywhere else in the repository, because EIGHT branches of it end
in no tag and seven of the eight left the job GREEN — a gate reporting success
while doing nothing, which is this organisation's most-repeated defect class
(ADR-0689, ADR-0691, ledgers 701, 715, 720). Six of those branches now refuse
out loud, and a refusal nobody can test is a refusal nobody should trust.

THE PREDICATE THAT DECIDES GREEN FROM RED, and it needs no per-repository
configuration: a repository that already carries at least one `v*` tag has OPTED
INTO the release model, so a no-tag outcome there is a FAILURE. A repository with
no baseline at all is not on the model, and silence is the correct answer. Five
repositories — `argocd`, `config`, `deploy`, `docs`, `estate` — carry zero tags
deliberately, and two of them are GitOps manifest repositories Argo syncs from
`main`, where a release tag has never been part of the mechanism.

WHAT STAYS GREEN, and neither is an oversight:

  NO BASELINE. Nothing to count from. The first tag of a repository is cut by
  hand; every one after it is derived from the Changelog.

  AN EMPTY RANGE. HEAD already carries the baseline tag, so the release has
  happened. This is checked BEFORE every refusal below, and the order is
  load-bearing: this step runs ahead of the `is HEAD already released` step, so a
  refusal raised on an empty range would pre-empt that step's green and redden
  every re-run of a main-push run — and every LOST TAG RACE, which `ci-pr.yaml`
  documents as benign.

  A BOT MERGE THAT CHANGES NOTHING SHIPPED. See `SHIPS` below.

WHAT REFUSES, each with the measurement that earned it:

  A NON-SEMVER BASELINE. `yadgarhq/yadgar` has cut zero automatic tags EVER,
  because `SEMVER` rejects its `v0.1.0a7` baseline on every merge, permanently,
  green: 14 of its last 15 merges cut nothing, `src/` changes included. The
  refusal NAMES which repair to attempt, because there are two and they are not
  interchangeable — see `refuse_baseline`.

  NO PARSEABLE ENTRIES. Safe to redden only because the synthesis below removes
  the legitimate bot case first.

  AN AMBIGUOUS WRAP. Refusing to guess was already right; staying green about it
  was not. The `::warning::` that used to stand in for a verdict is gone,
  superseded by an exit code rather than dropped.

WHAT THIS CANNOT DO, said here rather than left to be assumed. `version` is not
in the `passed` job's `needs:`, and there is no required check on a push to
`main`, so a refusal here BLOCKS NOTHING. It converts silence into a red run and
a failure notification. That is the whole of the improvement, and overstating it
would be the same mistake as the silence.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import os
import re
import subprocess
import sys
from collections import namedtuple

from pr_body import BULLET, bump_for, commit_entries, is_bot

# A BASELINE THIS REFUSES TO COUNT FROM, rather than one it mangles. `int("0a6")`
# raised, which merely reddened a report once and would now stand between a merge
# and its release. Anything that is not a plain `vMAJOR.MINOR.PATCH` yields no
# version at all.
SEMVER = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")

# WHAT SHIPS, and the set is the whole of Change 1's narrowness. A bot cannot be
# taught the template, so the Changelog gate exempts it — correct, or every
# Dependabot pull request would be blocked — and the derivation then found no
# entries and cut nothing. The question this set answers is not "was a dependency
# updated" but "does the bot's diff change WHAT SHIPS": `Cargo.toml` and
# `Cargo.lock` do, and a `Containerfile` does because the base image is part of
# the artifact. A bumped action SHA under `.github/workflows/` does NOT — it
# changes how the repository is built, not what it produces — and synthesising a
# release for one would be a guess against this job's own rule that writing no
# tag is always the cheaper mistake. Matched on the BASENAME, because `actions`'
# own two bot merges touch `containers/rust-build/Containerfile` and nothing else.
SHIPS = frozenset({"Cargo.toml", "Cargo.lock", "Containerfile", "Dockerfile"})

# ONE ENTRY, NOT ONE PER COMMIT. A grouped bump is a single dependency change
# however many crates moved, and the count reaches the annotated tag's permanent
# message. `fix` rather than `build`, deliberately: both classify as patch, and
# `fix` is what the bullet MEANS to a reader of the release.
SYNTHETIC = (
    "- fix(deps): bot-authored dependency updates, which carry no Changelog "
    "of their own"
)

Verdict = namedtuple("Verdict", "rc nxt note entries lines")


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def read(messages):
    """Both readings of every Changelog bullet in a range, and their text.

    IT ABSORBS MORE FREELY THAN THE GATE DOES, said here rather than left to be
    found. `changelog_entries` absorbs only inside an OPEN entry and only within
    the `## Changelog` section; this has neither notion, because the derivation
    never had one — it reads every line of every commit. So a bullet list under
    `## Why` written with no blank line above it can drop from the lenient count
    too. The direction is safe: a disagreement writes no tag, and across 160 real
    tag windows in eighteen repositories the two readings never disagreed once.

    BOTH READINGS OVER THE WHOLE RANGE, and the range is why the HEAD gate is not
    enough on its own. That gate reads HEAD only, so it refuses an ambiguous
    message on the push that lands it — and the NEXT push, with a clean HEAD,
    still has the ambiguous commit sitting in `last..HEAD`.
    """
    entries, matches, lenient = [], [], []
    for message in messages:
        found, strict, loose = commit_entries(message.splitlines())
        entries += found
        matches += strict
        lenient += loose
    return entries, matches, lenient


def ships(files):
    """Whether the diff touches something that is part of what is released."""
    return any(os.path.basename(name) in SHIPS for name in files)


def bot_only(authors):
    """Whether EVERY commit in the range is bot-authored.

    `all(... for a in [])` is True, so the emptiness is tested explicitly. A
    vacuous truth here would let an empty range satisfy the synthesis condition
    and cut a second tag on a commit that already carries one.
    """
    return bool(authors) and all(is_bot(author) for author in authors)


def refuse_baseline(last, tags):
    """The non-semver refusal, and it names WHICH of two repairs to attempt.

    TWO SHAPES, TWO DIFFERENT ACTIONS. `yadgarhq/yadgar` carries seven tags and
    every one is an alpha, so nothing plain exists to count from and the repair is
    to MINT the first plain tag by hand. `yadgarhq/actions` carries fifty plain
    tags plus a bare `v1`, so a refusal there means a STRAY tag has come nearer to
    HEAD than the plain ones, and the repair is to move or delete it. A refusal
    that cannot tell them apart sends the operator down the wrong path.

    WHY NOT JUST FILTER THE `describe` GLOB. Because `--match 'v[0-9]*.[0-9]*.
    [0-9]*'` MATCHES `v0.1.0a7` — the trailing `*` eats the `a7` — so it is a
    no-op for the one repository it would supposedly help. And where it did work
    it would return an empty baseline, take the no-baseline branch, and report
    GREEN: precision that hides the defect it appears to fix.

    WHY `actions` IS SAFE TODAY AND ONLY BY ACCIDENT. `v1` points 53 commits
    behind `main`, so `describe` returns `v1.22.0` at distance 0. Measured:
    `git describe --tags --abbrev=0` breaks a DISTANCE TIE in favour of the newer
    ANNOTATED tag — a fresh annotated tag at `main` beat `v1.22.0`, while a
    lightweight one lost to it — and `v1` is an annotated tag object. Moving it to
    a release commit would red the repository that publishes this workflow.
    """
    plain = [tag for tag in tags if SEMVER.match(tag)]
    lines = [
        f"`{last}` is the nearest `v*` tag to HEAD and it is not a plain",
        "`vMAJOR.MINOR.PATCH` tag, so there is no baseline this can count from",
        "and nothing was tagged.",
        "",
    ]
    if plain:
        newest = max(plain, key=lambda t: tuple(int(g) for g in SEMVER.match(t).groups()))
        lines += [
            f"This repository DOES carry plain tags — the newest is `{newest}` —",
            f"so `{last}` is a **stray**: move it or delete it rather than cutting",
            "a new one. A stray tag nearer to HEAD than the plain ones wins",
            "`git describe`, which is what silenced this repository's releases.",
        ]
    else:
        lines += [
            "There is **no plain** `vMAJOR.MINOR.PATCH` tag in this repository at",
            "all, so there is nothing to move: cut the first plain one by hand and",
            "every tag after it is derived from the Changelog again.",
        ]
    return lines


def refuse_entries(last, authors, files):
    """No parseable entries: green where nothing ships, red otherwise.

    WITH A GREEN GATE THIS IMPLIES A BOT. The `gate` step ahead of this reddens a
    human-authored HEAD carrying no readable Changelog, and a conformant HEAD
    always yields at least one bullet — so the only author who reaches here is one
    the template exempts. That makes the red below a FAIL-CLOSED guard rather than
    a condition anybody hits today: it fires if the gate's reader and this one
    ever disagree about what a body contains, instead of shipping nothing quietly.
    """
    if bot_only(authors) and not ships(files):
        return 0, [
            f"No parseable Changelog entries since `{last}`, and every commit in",
            "the range is bot-authored and touches nothing that **ships** — no",
            f"{', '.join(sorted(SHIPS))}. Nothing to release, and that is the",
            "right answer rather than a missing one.",
        ]
    return 1, [
        f"No parseable Changelog entries since `{last}`, so no version could be",
        "derived and **nothing was tagged** — on a repository that carries tags",
        "and has therefore opted into the release model. Cut the intended tag by",
        "hand, and check why the message that landed carries no Changelog.",
    ]


def compute(last, base, entries, matches):
    """The version itself, and the ladder is unchanged by this file."""
    bump = bump_for(matches)
    major, minor, patch = (int(g) for g in base.groups())

    # PRE-1.0 IS NOT THE SAME RULE, and getting it wrong publishes a "compatible"
    # version that is not. Under 0.x a breaking change bumps MINOR and a feature
    # bumps PATCH.
    if major == 0:
        nxt = f"0.{minor + 1}.0" if bump == "major" else f"0.{minor}.{patch + 1}"
        note = "pre-1.0: a `%s` change bumps %s" % (
            bump, "minor" if bump == "major" else "patch")
    else:
        nxt = {"major": f"{major + 1}.0.0",
               "minor": f"{major}.{minor + 1}.0",
               "patch": f"{major}.{minor}.{patch + 1}"}[bump]
        note = f"{bump} bump"

    lines = [f"`{last}` → **`v{nxt}`**  ({note})", "",
             f"{len(entries)} entries since `{last}`:", ""]
    lines += [f"- {e.lstrip('-* ')}" for e in entries[:25]]
    if len(entries) > 25:
        lines += ["", f"...and {len(entries) - 25} more."]
    return nxt, note, lines


def derive(last, tags, messages, authors, files):
    """The whole branch table, as a verdict a test can read.

    Every path that cannot be CERTAIN yields an empty version and the tagging
    steps skip. There is no default and no best guess — a tag cannot be withdrawn
    or moved, so writing none is always the cheaper mistake.
    """
    entries, matches, lenient = read(messages)

    # THE SYNTHESIS, and all three conditions are required. A merged dependency
    # fix used to reach a release only if some LATER human merge happened to cut a
    # tag covering its range, which is indistinguishable from working until the
    # day nothing follows. Two are sitting un-released right now.
    if not entries and bot_only(authors) and ships(files):
        entries.append(SYNTHETIC)
        # THE STRINGS AND THE MATCH OBJECTS GO TO DIFFERENT LISTS. `entries`
        # carries text to the tag message; `matches` and `lenient` carry parsed
        # bullets to `bump_for`, which reads `.group`. Both readings get it, so
        # the synthesis can never make a range ambiguous.
        matches.append(BULLET.match(SYNTHETIC))
        lenient.append(BULLET.match(SYNTHETIC))

    out = ["## Next version", ""]
    if not last:
        return Verdict(0, "", "", entries, out + [
            "No `v*` tag yet, so there is **no baseline** to compute from.",
            "The first tag of a repository is cut by hand; every one after it is",
            "derived from the Changelog."])

    if not messages:
        return Verdict(0, "", "", entries, out + [
            f"HEAD is **already** released as `{last}`, so there is nothing to",
            "cut. A re-run of this job, and the loser of a two-merge tag race,",
            "both land here."])

    base = SEMVER.match(last)
    if not base:
        return Verdict(1, "", "", entries, out + refuse_baseline(last, tags))

    if not entries:
        rc, lines = refuse_entries(last, authors, files)
        return Verdict(rc, "", "", entries, out + lines)

    if bump_for(matches) != bump_for(lenient):
        # NO TAG AND NO GUESS. A `- feat!:` the wrap pushed to column 0 is a
        # second entry to one reading and prose to the other, and the two imply
        # different releases. It does not self-heal the way the HEAD gate does —
        # the commit stays in the range — so the way out is a tag cut by hand,
        # which is already how the first tag of every repository is cut.
        return Verdict(1, "", "", entries, out + [
            f"The Changelog since `{last}` is **ambiguous** under GitHub's",
            "72-column wrap, so no version was derived and nothing was tagged.",
            f"Read strictly: {len(matches)} entries implying a",
            f"**{bump_for(matches)}** bump. Read as the wrap explains it:",
            f"{len(lenient)} entries implying a **{bump_for(lenient)}**.",
            "Cut the intended tag by hand."])

    nxt, note, lines = compute(last, base, entries, matches)
    return Verdict(0, nxt, note, entries, out + lines)


def state():
    """What git says, paired so a message can never be read against another's author."""
    last = sh("git", "describe", "--tags", "--abbrev=0", "--match", "v*")
    tags = [t for t in sh("git", "tag", "--list", "v*").splitlines() if t.strip()]
    rng = f"{last}..HEAD" if last else "HEAD"
    # ONE CALL RATHER THAN TWO. Splitting the authors out into their own `git log`
    # pairs them by INDEX, and any commit git chose to omit from one list and not
    # the other would silently shift every author by one.
    log = sh("git", "log", rng, "--format=%an <%ae>%x1f%B%x00")
    messages, authors = [], []
    for record in log.split("\0"):
        if not record.strip():
            continue
        author, _, message = record.partition("\x1f")
        authors.append(author.strip())
        messages.append(message)
    files = []
    if last:
        files = [f for f in sh("git", "diff", "--name-only", rng).splitlines() if f.strip()]
    return last, tags, messages, authors, files


def main():
    last, tags, messages, authors, files = state()
    verdict = derive(last, tags, messages, authors, files)
    text = "\n".join(verdict.lines)

    if verdict.nxt:
        # THE TAG MESSAGE IS WRITTEN HERE because this is the only place that
        # knows what the number was derived FROM, and the ruleset makes an
        # annotated tag's message the permanent record of it. Every entry, not the
        # summary's first 25 — a truncated record of a release cannot be
        # un-truncated later.
        message = [f"v{verdict.nxt}", "",
                   f"{verdict.note.replace('`', '')}, from {len(verdict.entries)} "
                   f"Changelog entries since {last}.", ""]
        message += [f"- {e.lstrip('-* ')}" for e in verdict.entries]
        with open(f"{os.environ['RUNNER_TEMP']}/tag_message.txt", "w") as fh:
            fh.write("\n".join(message) + "\n")

    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"next={verdict.nxt}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
        fh.write(text + "\n")
    print(text)
    if verdict.rc:
        # THE ANNOTATION IS THE POINT. Every one of these refusals used to write a
        # step summary on a job whose conclusion was success, and a step summary
        # nobody opens is how a dropped release becomes invisible.
        print("::error::" + " ".join(verdict.lines[2:]))
    return verdict.rc


if __name__ == "__main__":
    sys.exit(main())
