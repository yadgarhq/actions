#!/usr/bin/env python3
"""The template contract, in one place, for the two things that read a body.

A pull request body in this organisation is not commentary. Every repository
squash-merges with `PR_BODY` as the commit message, so the body IS the permanent
history, and the `version` job derives the release from the Conventional Commits
bullets in it. That makes the body a BUILD INPUT, and this file is the one
definition of what a valid one looks like.

WHY IT IS A FILE RATHER THAN A THIRD INLINE COPY. Ledger 630: a body validated
by `ci / template` on a pull request could be edited afterwards and merged with
no re-check, because `on: pull_request` without `types:` subscribes to `opened`,
`synchronize` and `reopened` and NOT to `edited`. Closing that adds a second
reader of the body — the push-to-main assertion in the `version` job — and a
second reader that disagrees with the first is worse than the gap. So the
`version` job's assertion and its derivation share this module.

ONE REGEX IS NOT ONE VERDICT, and ledger 687 is the correction. Sharing the
bullet pattern stops the two readers disagreeing about what a bullet LOOKS like;
it does not stop them disagreeing about what a body MEANS, because they read
different text — one reads what the author typed and the other reads what the
wrap made of it. So the wrapped reader now derives BOTH readings of the message
in front of it and refuses when they imply different releases, rather than
picking one and cutting a tag nobody can withdraw.

THE TWO MODES, and the reason there are two. GitHub HARD-WRAPS the pull request
body at about 72 columns when it writes the squash commit message. Measured, not
assumed: `yadgarhq/store` pull request 15 carries four one-line Changelog
bullets and commit `2198a4f` carries the same four wrapped across eleven lines.
So "every line under `## Changelog` is a bullet" is the right rule BEFORE the
merge and an impossible one after it. In wrapped mode a line that does not open
an entry is read as the continuation of the entry above it, and is a problem
only when there is no entry above it to continue.

WHAT WRAPPED MODE CANNOT SEE, said here rather than discovered later: prose
appended directly under a bullet with no blank line between them is exactly what
a wrapped continuation looks like, and no parser can tell them apart. The
pull-request-time check, reading the unwrapped body, can and does. So wrapped
mode ACCEPTS bodies the template REJECTS, which is why the step that runs it is
named for the Changelog it can read rather than for the template.

WHERE IT CAN TELL THEM APART IT DOES, on arithmetic rather than on a guess: see
`is_wrap_continuation`. A greedy wrap at a known column says exactly which lines
it could have produced, and that is enough to stop `- W for warnings` mid-
sentence reading as a malformed bullet and to notice when `- feat!:` mid-
sentence would turn a reviewed patch into a major.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import os
import re
import sys

# Conventional Commits. The set is closed: a type outside it does not parse, and
# a bullet nobody can parse is a release nobody can version.
TYPES = "build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test"
BULLET = re.compile(
    rf"^\s*[-*]\s+(?P<type>{TYPES})(?P<scope>\([^)]+\))?(?P<bang>!)?:\s+(?P<desc>\S.*)$"
)

# WHAT OPENS AN ENTRY, and the trailing space is load-bearing. A bullet wrapped
# mid-flight can put `-D warnings` at the start of a continuation line; requiring
# whitespace after the marker keeps that a continuation rather than a malformed
# bullet.
STARTS_ENTRY = re.compile(r"^\s*[-*][ \t]")

# THE COLUMN GITHUB WRAPS AT, measured rather than assumed and the same fixture
# the tests use: `yadgarhq/store` pull request 15 against commit `2198a4f`, whose
# longest line is 72 CHARACTERS and 74 bytes. Characters, so an em-dash costs
# one. Every wrap in it is greedy and breaks on a word boundary.
WRAP = 72

REQUIRED = ("What", "Why", "Changelog", "Verification", "Risk")


def looks_wrapped(lines, width=WRAP):
    """Whether this text could be what the wrap emitted, judged as a whole.

    WRAPPEDNESS IS A PROPERTY OF THE MESSAGE, not of one line, and deciding it
    once is what stops the arithmetic below misfiring on a message the wrap
    never touched. A repository whose squash message is not the pull request
    body reaches history unwrapped, and its bullets are single lines far wider
    than the column. `yadgarhq/iam-db` and `yadgarhq/task-db` both carry a
    nine-entry Changelog of exactly that shape.

    A GREEDY WRAP CAN STILL EMIT AN OVER-LONG LINE, and only one way: a single
    token wider than the column, which has nowhere to break. A URL or a long
    path does it. So an over-long line is evidence of an unwrapped message only
    when it holds more than one word. Both halves are measured over 605 real
    commit messages — 105 over-long single-token lines, which are wrap output,
    against 4,520 over-long multi-word lines, which are not.

    THE MEASUREMENT THAT DECIDED IT. Judging line by line instead, six real
    messages in `deploy`, `docs`, `iam`, `project-db` and `telemetry` have an
    unwrapped line of exactly 71 or 72 columns immediately above a genuine
    bullet, and the arithmetic below absorbs that bullet. None of the six
    changed a bump, but six of the seven near misses absorbed a `feat` — the
    class that decides one. Deciding wrappedness first removes all six.
    """
    for line in lines:
        if not line.strip() or len(line) <= width:
            continue
        if len(line.split()) == 1:
            continue
        return False
    return True


def is_wrap_continuation(previous, line, width=WRAP):
    """Whether the wrap alone explains `line` beginning where it does.

    LEDGER 687(a) AND (c), AND IT IS ARITHMETIC RATHER THAN A GUESS. GitHub
    wraps greedily on word boundaries and starts continuations at column 0, so a
    line is a possible continuation only when its first word did not FIT on the
    line above. For a line opening `- ` that first word is one character, so the
    line above has to be 71 or 72 columns already — anything shorter had room
    for the dash, and the author must have typed the break. An over-long single
    token above it satisfies that trivially, which is the right answer: the wrap
    had to break after it.

    ONLY ASK THIS OF A MESSAGE `looks_wrapped` HAS ALREADY ACCEPTED. On one it
    has not, the arithmetic is being applied to line breaks a person typed, and
    it will absorb bullets that are really bullets.

    Nothing precedes the first line of a paragraph, so it is never anybody's
    continuation. That is the invariant that keeps the two readings comparable:
    the lenient one can never empty a Changelog the strict one reads.
    """
    if not (previous or "").strip():
        return False
    words = line.split()
    if not words:
        return False
    return len(previous) + 1 + len(words[0]) > width


def commit_entries(lines):
    """Every Changelog bullet in one commit message: its text, and both readings.

    THE DERIVATION'S READER, lifted out of the `version` job's heredoc so the
    text that reaches an annotated tag is written where it can be tested. That
    job already imported `looks_wrapped` and `is_wrap_continuation`, and already
    decided wrappedness once per commit — but it used them only to decide WHICH
    BULLETS COUNT, never to reassemble what a bullet SAYS. So a bullet the wrap
    broke across two lines reached the tag as its first line alone: the
    continuation matched no `BULLET`, was skipped by the loop, and the tail was
    gone. An annotated tag's message is the permanent record of a release and
    the `v*` ruleset forbids moving or deleting one, so that truncation could
    never be corrected — only a new tag could.

    JOINING IS THE WHOLE OF THE CHANGE, and the invariant that makes it safe is
    that a continuation line never matched `BULLET` in the first place, so it
    was never counted. `entries`, `matches` and `lenient` hold exactly what they
    held before, one element per bullet; only the TEXT of an entry grows. No
    version this derives can move.

    IT ABSORBS MORE FREELY THAN `changelog_entries` DOES, and that is inherited
    rather than new: this reads every line of every commit with no notion of the
    `## Changelog` section. `is_wrap_continuation` is what keeps it honest. A
    greedy wrap fills every line but the last, so the line after a bullet's
    final continuation is short, and a following heading or paragraph fails the
    arithmetic and is not absorbed. A blank line closes the entry outright.

    ONLY A MESSAGE `looks_wrapped` ACCEPTS is joined at all. One the wrap never
    touched carries whole bullets on single lines, and joining into those would
    append text the author deliberately put on a line of its own.
    """
    absorb = looks_wrapped(lines)
    entries, matches, lenient = [], [], []
    previous, open_entry = None, False
    for line in lines:
        if not line.strip():
            previous, open_entry = None, False
            continue
        match = BULLET.match(line)
        if not match:
            if absorb and open_entry and is_wrap_continuation(previous, line):
                entries[-1] += " " + line.strip()
            else:
                open_entry = False
            previous = line
            continue
        entries.append(line.strip())
        matches.append(match)
        # THE AMBIGUITY READING, unchanged. A `- ` line the wrap alone explains
        # is a second entry to the strict reading and a continuation to the
        # lenient one, and when the two imply different bumps the caller cuts no
        # tag at all.
        if not (absorb and is_wrap_continuation(previous, line)):
            lenient.append(match)
        previous, open_entry = line, True
    return entries, matches, lenient


# A BOT IDENTITY, in the three shapes this is handed one. GitHub appends `[bot]`
# to the login, so it ends the NAME (`dependabot[bot]`), precedes the `@` of the
# noreply ADDRESS, and precedes the `<` of the `%an <%ae>` form a commit author
# arrives in. Matching a bare `bot` substring instead would exempt anybody on a
# robotics team.
BOT = re.compile(r"\[bot\]\s*(<|@|$)")


def is_bot(author):
    """Whether a commit author is a bot, matching `template`'s own exemption.

    A bot writes its own body and cannot be taught the template. The
    pull-request check exempts `user.type == 'Bot'`; a push to `main` has only
    the commit author to go on.
    """
    return bool(BOT.search((author or "").strip()))


def sections(text):
    """Headings to their lines, with HTML comments stripped first.

    ANY heading level splits, `#` through `######`, which is the behaviour the
    pull request template already relies on and warns about: a `###` inside a
    required section starts a new section, so that section collects nothing and
    reads as empty. Sub-structure belongs in bold lead-ins.
    """
    stripped = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    found, current = {}, None
    for line in stripped.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1).strip()
            found[current] = []
        elif current:
            found[current].append(line)
    return found


def bump_for(matches):
    """The highest bump implied by a run of parsed bullets."""
    kinds = set()
    for m in matches:
        if m.group("bang"):
            kinds.add("major")
        elif m.group("type") == "feat":
            kinds.add("minor")
        else:
            kinds.add("patch")
    return "major" if "major" in kinds else "minor" if "minor" in kinds else "patch"


def changelog_entries(lines, wrapped, absorb=False):
    """Split a `## Changelog` section into (offending lines, parsed bullets).

    `absorb` is the LENIENT reading of a wrapped body: a dash line the wrap
    alone explains continues the entry above it instead of opening one. The
    strict reading — every dash line opens an entry — is the same call with
    `absorb=False`, and `review` below runs both and compares what they imply.
    """
    bad, matches = [], []
    if not wrapped:
        for line in (l for l in lines if l.strip()):
            match = BULLET.match(line)
            (matches if match else bad).append(match or line.strip())
        return bad, matches

    open_entry, previous = False, None
    for line in lines:
        if not line.strip():
            open_entry, previous = False, None
            continue
        if STARTS_ENTRY.match(line):
            if absorb and open_entry and is_wrap_continuation(previous, line):
                previous = line
                continue
            match = BULLET.match(line)
            (matches if match else bad).append(match or line.strip())
            open_entry = True
        elif not open_entry:
            bad.append(line.strip())
        previous = line
    return bad, matches


def ambiguity(strict, lenient):
    """The refusal, naming both readings and the line they part company on.

    LEDGER 687(c). This is the one divergence that cannot be lived with: read
    strictly, a `- feat!:` the wrap pushed to column 0 is a second entry and the
    tag cut from this message is a MAJOR; read as the wrap explains it, the same
    two lines are one patch. Nobody can withdraw or move a tag once it is cut,
    and the commit it was derived from is immutable, so the cheaper mistake is
    to cut nothing and say why.

    IT REFUSES ON THE BUMP AND NOT ON THE COUNT, and that is measured. Across
    288 real wrapped Changelog sections in eighteen repositories the two
    readings disagree about the COUNT twelve times and about the BUMP zero
    times. Refusing on the count would redden four merges in a hundred for a
    disagreement that implies the same release.
    """
    kept = [m.group(0) for m in lenient]
    parted = [m.group(0).strip() for m in strict if m.group(0) not in kept]
    return (
        "the wrap makes this Changelog ambiguous, so no version is derived from "
        f"it. Read strictly it is {len(strict)} entries implying a "
        f"**{bump_for(strict)}** bump; read as GitHub's {WRAP}-column wrap "
        f"explains it, {len(lenient)} entries implying a "
        f"**{bump_for(lenient)}**. The line they part company on: "
        + "; ".join(f"`{p[:60]}`" for p in parted[:4])
        + ". Cut the tag by hand if the strict reading is the intended one."
    )


def review(text, wrapped):
    """Every problem with a body, plus the bump and entry count it implies."""
    found = sections(text)
    problems = []
    for heading in REQUIRED:
        if heading not in found:
            problems.append(f"missing section `## {heading}`")
        elif not any(l.strip() for l in found[heading]):
            problems.append(f"`## {heading}` is empty (comments are not an answer)")

    # A RED THAT NAMES ITS CAUSE. Five `missing section` lines describe a
    # symptom. When a COMMIT MESSAGE carries none of the sections, the usual
    # cause is not a body somebody wrote badly — it is a repository that does
    # not squash-merge with the body at all, so the text this reads was never
    # the text `ci / template` checked. Gated on `wrapped` because that is the
    # discriminator: an unwrapped body is a pull request body, where the setting
    # cannot be the reason.
    if wrapped and len(problems) == len(REQUIRED):
        problems.insert(
            0,
            "this commit message carries none of the template's sections, which "
            "usually means the repository does not squash-merge with the pull "
            "request body — check `squash_merge_commit_message`",
        )

    bump, count = None, 0
    lines = found.get("Changelog", [])
    if any(l.strip() for l in lines):
        # BOTH READINGS OF THE SAME TEXT, because in wrapped mode they can
        # differ and only one of them can be released. A pull request body is
        # kept verbatim — what the author typed is what it says — and so is a
        # commit message the wrap plainly never touched.
        absorb = wrapped and looks_wrapped((text or "").splitlines())
        bad, lenient = changelog_entries(lines, wrapped, absorb=absorb)
        _, strict = changelog_entries(lines, wrapped)
        if bad:
            problems.append(
                "every Changelog entry must be a Conventional Commits bullet, "
                "e.g. `- fix: a metadata leak in the audit relay`. Offending: "
                + "; ".join(f"`{b[:60]}`" for b in bad[:4])
            )
        elif wrapped and bump_for(strict) != bump_for(lenient):
            problems.append(ambiguity(strict, lenient))
        else:
            bump, count = bump_for(strict), len(strict)
    return problems, bump, count


def main():
    subject = os.environ.get("SUBJECT") or "pull request body"
    wrapped = os.environ.get("WRAPPED", "").lower() == "true"
    author = os.environ.get("AUTHOR") or ""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")

    # THE ONE EXEMPTION, and it NAMES the author rather than reporting a bare
    # pass. A bot writes its own body and cannot be taught the template — the
    # pull-request check exempts `user.type == 'Bot'` for dependabot, and
    # `ci-release.yaml` writes a one-line commit per release into `yadgarhq/
    # argocd` as the release App. A skip nobody can attribute is the thing this
    # gate exists to refuse, so the attribution is the output.
    if is_bot(author):
        finding = f"{subject} is bot-authored ({author}), which the template exempts."
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"### Template\n\n{finding}\n")
        print(finding)
        return 0

    problems, bump, count = review(os.environ.get("BODY") or "", wrapped)

    if problems:
        lines = [f"### {subject} does not follow the template", ""]
        lines += [f"- {p}" for p in problems]
        lines += ["", "The template is in `.github/pull_request_template.md`."]
        if summary:
            with open(summary, "a") as fh:
                fh.write("\n".join(lines) + "\n")
        print("::error::" + "; ".join(problems))
        return 1

    # A FINDING, never a bare "ok". This gate exists because a check that
    # reports success when it parsed nothing is the failure mode, so it says
    # what it parsed and what that implies.
    finding = (
        f"{subject} follows the template: "
        f"{count} Changelog {'entry' if count == 1 else 'entries'}, "
        f"implying a **{bump}** bump."
    )
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### Template\n\n{finding}\n")
    print(finding)
    return 0


if __name__ == "__main__":
    sys.exit(main())
