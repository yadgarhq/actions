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
`version` job's assertion and its derivation share this module; the two agree
because there is only one of them.

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
pull-request-time check, reading the unwrapped body, can and does.

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

REQUIRED = ("What", "Why", "Changelog", "Verification", "Risk")


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


def changelog_entries(lines, wrapped):
    """Split a `## Changelog` section into (offending lines, parsed bullets)."""
    bad, matches = [], []
    if not wrapped:
        for line in (l for l in lines if l.strip()):
            match = BULLET.match(line)
            (matches if match else bad).append(match or line.strip())
        return bad, matches

    open_entry = False
    for line in lines:
        if not line.strip():
            open_entry = False
            continue
        if STARTS_ENTRY.match(line):
            match = BULLET.match(line)
            (matches if match else bad).append(match or line.strip())
            open_entry = True
        elif not open_entry:
            bad.append(line.strip())
    return bad, matches


def review(text, wrapped):
    """Every problem with a body, plus the bump and entry count it implies."""
    found = sections(text)
    problems = []
    for heading in REQUIRED:
        if heading not in found:
            problems.append(f"missing section `## {heading}`")
        elif not any(l.strip() for l in found[heading]):
            problems.append(f"`## {heading}` is empty (comments are not an answer)")

    bump, count = None, 0
    lines = found.get("Changelog", [])
    if any(l.strip() for l in lines):
        bad, matches = changelog_entries(lines, wrapped)
        if bad:
            problems.append(
                "every Changelog entry must be a Conventional Commits bullet, "
                "e.g. `- fix: a metadata leak in the audit relay`. Offending: "
                + "; ".join(f"`{b[:60]}`" for b in bad[:4])
            )
        else:
            bump, count = bump_for(matches), len(matches)
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
