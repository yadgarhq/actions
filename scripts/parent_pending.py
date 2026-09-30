#!/usr/bin/env python3
"""What a parent tag must ALSO release: the Changelog no tag has released yet.

A HUMAN MERGE CAN REACH A PARENT TAG WITHOUT ITS OWN RUN TAGGING IT. The
`version` job stamps the parent's examples on top of the merge and fast-forwards
`main`; if `main` moved first, it defers. When what moved `main` is
`parent_bump.py`'s pin commit, the next tag is the one `parent_bump.py` cuts —
and `next_version.py` derives every later tag from THAT one, so a merge it does
not count is never released, and a `feat!` in it ships as a patch.

THE RULE THAT CLOSES IT, and it is the rule `next_version.py` already follows:
a tag's number is derived from EVERY commit between the previous tag and the
commit it points at. The `version` job reads that range with `git log`; this
reads it with the compare API, then asks the SAME ladder — `next_version.compute`
— with the module's own synthesised bullet added. With both cutters obeying it,
every Changelog entry on `main` is counted by the first tag at or after it,
whichever cutter cuts that tag. Nothing is left for a later merge to pick up,
so nothing depends on a later merge existing.

THE BASELINE IS `repin.greatest`, the tag `parent_pin.parent_version` derives
from, and it must be an ANCESTOR of the commit being tagged. A tag off `main` is
refused: `git describe` would not see it either, and the two cutters would count
different ranges.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import json

from next_version import SEMVER, compute, read
from parent_pin import BULLETS, Refusal, movement, parent_version
from pr_body import BULLET, bump_for


def messages(gh, last, target):
    """Every commit message in `last..target`, oldest first. Truncation refuses."""
    done = gh.call("api", gh.repo("compare", f"{last}...{target}"))
    if done.returncode != 0:
        raise Refusal(
            f"the commits between `{last}` and {target[:7]} could not be listed: "
            f"{' '.join((done.stderr or done.stdout or '').split()) or 'no output'}. "
            "No version was derived."
        )
    try:
        body = json.loads(done.stdout)
        status, total = body["status"], body["total_commits"]
        found = [c["commit"]["message"] for c in body["commits"]]
    except (ValueError, KeyError, TypeError) as error:
        raise Refusal(f"the comparison `{last}...{target[:7]}` is unreadable ({error}).") from error
    if status == "identical":
        return []
    if status != "ahead":
        raise Refusal(
            f"{target[:7]} is `{status}` from `{last}` rather than ahead of it, so "
            f"`{last}` is not on the history being tagged and no range can be "
            "counted from it. A parent tag off `main` is cut by hand, if at all."
        )
    if len(found) != total:
        raise Refusal(
            f"the comparison `{last}...{target[:7]}` returned {len(found)} of "
            f"{total} commits. A range read short is a Changelog entry dropped, "
            "so no version was derived. Cut this one by hand."
        )
    return found


def derive(gh, tags, previous, offered, target):
    """`(next, note, last, entries)` for tagging `target`, pending Changelog included."""
    nxt, note, last = parent_version(tags, previous, offered)
    entries, matches, lenient = read(messages(gh, last, target))
    if not matches:
        return nxt, note, last, []
    if bump_for(matches) != bump_for(lenient):
        raise Refusal(
            f"the Changelog since `{last}` is ambiguous under GitHub's 72-column "
            f"wrap — a {bump_for(matches)} bump read strictly, a "
            f"{bump_for(lenient)} bump read as wrapped — so no version was derived. "
            "The `version` job refuses the same range; cut the tag by hand."
        )
    bullet = BULLETS[movement(previous, offered)]
    nxt, note, _ = compute(
        last, SEMVER.match(last), [bullet] + entries, [BULLET.match(bullet)] + matches
    )
    return nxt, note, last, entries


def tag_message(version, note, module, previous, offered, last, repo, entries=()):
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
    ] + ([
        "",
        f"Also released: {len(entries)} Changelog entries since {last} that no "
        "tag had carried (parent_pending.py).",
        "",
    ] + [f"- {e.lstrip('-* ')}" for e in entries] if entries else []))
