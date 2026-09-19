#!/usr/bin/env python3
"""One module's pin in the parent chart, and the parent version that follows.

ADR-0722 rules that the release which publishes a module chart ALSO cuts a new
version of the parent chart, so the newest published parent always pins the
newest released version of every module, and the parent's version is never cut
by hand. This is the mechanism for the writing half of that rule: it rewrites
ONE module's `version:` in a parent `Chart.yaml` and derives the parent's own
next number.

PUBLISHED AND ADOPTED NOWHERE, deliberately, which is ADR-0607's shape —
publishing a mechanism and adopting it are separate acts. `yadgarhq/chart` does
not exist, `ghcr.io/yadgarhq/charts/yadgar` has no tags, and whether creating
that repository needs the operator's ADR-0574 per-module confirmation is
unanswered. No workflow calls this file. Wiring it now would add a path nothing
exercises to a workflow eighteen repositories consume at `@main`.

THE NUMERIC COMPARISON IS THE WHOLE POINT OF THE FILE. ADR-0690: Dependabot
orders git tags LEXICALLY, and at the moment a patch number reached two digits
every proposal it made became a DOWNGRADE titled "bump" — nineteen of them open
at once across seven repositories, and not one internal pin advanced for weeks.
Nothing detected it. The same ordering error was made again on 2026-09-19 while
measuring the published chart tags for the parent-chart plan: a lexical maximum
answers `0.9.9` for `gateway`, whose semver-greatest tag is `0.9.48`, and it was
wrong for SIX of the eight charts. A pin written from a text comparison is a
downgrade that publishes cleanly, resolves cleanly and reports nothing. So this
refuses to write a version that is not strictly greater than the one pinned, by
integers, naming both numbers.

EQUAL IS A REFUSAL, decided rather than left to fall through. A re-offer of the
version already pinned means the caller believes a release happened that did
not, and the parent version derived from it would cut a NEW parent artefact
pinning a byte-identical set. A caller that wants idempotence reads the pin
first with `pins()` — the same reader this uses — rather than relying on a
silent no-op. Every refusal happens before the file is opened for writing, so a
refused run leaves the chart byte-identical.

NOTHING INSPECTED MUST NEVER BE EXIT 0. This estate has measured at least four
checks that passed while examining nothing, so a missing `dependencies:` key, a
key with no entries under it, an empty flow sequence, a module absent from the
block, and an entry shaped in a way this does not understand are each a
REFUSAL. A rewriter that finds no site and exits 0 is the fifth.

THE REWRITE IS TEXTUAL, NOT A YAML ROUND TRIP, and that is a requirement rather
than a shortcut. Re-emitting a parsed document renormalises quoting, key order
and flow style across the whole file, so one version movement would appear in
the release diff as eight entries moving — and `prettier` runs on every
committed YAML file in this estate, so the shapes a real parent carries are a
MIX of single-line and multi-line flow mappings. This rewrites the version token
in place and leaves every other byte alone.

THE LADDER IS `next_version.compute`, NEVER A SECOND COPY. That function already
carries the estate's pre-1.0 branch — under `0.x` a breaking change bumps MINOR
and everything else bumps PATCH — and a second copy of a version rule is the
drift this estate's D-numbered decisions keep refusing. It is consulted through
a SYNTHESISED Changelog bullet, which is the same device `next_version.derive`
already uses for a bot-only range. The parent's baseline is the semver-greatest
`v*` tag via `repin.greatest`, for the same ordering reason as above, and NOT
the `version:` field of the parent's own `Chart.yaml`: `ci-release.yaml`'s chart
job runs `helm package chart --version "$VERSION"` with the tag, so that field
never reaches a published artefact. ADR-0634 settled the same question for a
crate's `version` field.

WHAT THE NUMBERS CANNOT SAY, said out loud. The module's bump KIND is read back
from its two version numbers, because no caller passes one today. Under `0.x` a
`feat:` and a `fix:` release produce the SAME number, so those two are
indistinguishable here and the parent takes the smaller movement — which is
correct for a `0.x` parent, where a minor and a patch module change both give a
parent patch, and would need the caller to state the kind once any parent
reaches `1.0`.

Usage: parent_pin.py <parent Chart.yaml> <module> <version> <parent tag>...

The parent tags are the parent repository's existing `v*` tags. With none, there
is no baseline and this refuses: the first tag of a repository is cut by hand,
which is `next_version.py`'s own rule, and inventing `0.1.0` here would be the
second version rule this file exists to avoid.
"""

import re
import sys
from collections import namedtuple

from next_version import SEMVER, compute
from pr_body import BULLET
from repin import greatest, output, summary

KEY = "dependencies:"

# A PLAIN `MAJOR.MINOR.PATCH` AND NOTHING ELSE. A range (`^0.9.0`), a prerelease
# (`0.9.48-rc1`) or a tag (`v0.9.48`) cannot be ordered against a release, and
# `yadgarhq/yadgar` carries `v0.1.0a6`, so an unorderable string is a real shape
# in this estate rather than a hypothetical one. Unorderable is refused, never
# guessed at and never stripped into shape.
PLAIN = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")

# WHERE A KEY MAY OPEN. Block style puts it at the start of a line; a flow
# mapping puts it after `{` or after a comma. The value stops at a comma, a
# closing brace, a comment or the end of the line, and a quoted value is taken
# whole so its quotes can be put back exactly as they were.
VALUE = r"(\"[^\"]*\"|'[^']*'|[^,}\n#]*)"

# THE BULLETS THE LADDER IS ASKED THROUGH. `pr_body.bump_for` reads `!` and the
# `feat` type off a parsed bullet, so these three are the only inputs needed to
# get the estate's own answer for each rung.
BULLETS = {
    "major": "- feat!: a module release the parent chart takes",
    "minor": "- feat: a module release the parent chart takes",
    "patch": "- fix: a module release the parent chart takes",
}

Entry = namedtuple("Entry", "name version line start end")


class Refusal(Exception):
    """A parent chart this will not rewrite, named rather than silently skipped."""


def triple(version):
    """`(major, minor, patch)` for a plain `X.Y.Z`, else None.

    SEPARATE FROM `repin.semver` ON PURPOSE. That one reads a git TAG and
    requires the `v`, and a chart version must not carry one — so the two cannot
    share a regex without one of them accepting a string it must refuse. What
    they do share is the only thing that matters here: the comparison is on
    parsed INTEGERS, never on text.
    """
    match = PLAIN.match((version or "").strip())
    return tuple(int(group) for group in match.groups()) if match else None


def sites(line, key):
    """Every `<key>:` in one line, as the match whose group 1 is the value."""
    pattern = re.compile(r"(?:^|(?<=[\s,{]))" + re.escape(key) + r":[ \t]*" + VALUE)
    return list(pattern.finditer(line))


def unquote(raw):
    """The value without the quotes it may carry, and the quote character."""
    text = raw.rstrip()
    if text[:1] in ("'", '"') and text[-1:] == text[:1] and len(text) > 1:
        return text[1:-1], text[0]
    return text, ""


def block(lines):
    """The line indices of the `dependencies:` block, or a refusal.

    A COLUMN-ZERO KEY, because that is where Chart.yaml's is. An indented
    `dependencies:` belongs to something else — a nested document, a comment
    example — and taking it would rewrite a pin nobody asked about.
    """
    heads = [i for i, line in enumerate(lines) if line.startswith(KEY)]
    if not heads:
        raise Refusal(
            "this chart has no top-level `dependencies:` key, so there is no pin "
            "to rewrite. A parent chart declares every module as a dependency; a "
            "chart with none is not the parent."
        )
    if len(heads) > 1:
        raise Refusal(
            f"this chart carries {len(heads)} top-level `dependencies:` keys, so "
            "which block holds the pin is ambiguous. One of them is dead and a "
            "rewrite of either could be the one nothing reads."
        )

    head = heads[0]
    inline = lines[head][len(KEY) :].strip()
    if inline and not inline.startswith("#"):
        if inline in ("[]", "[ ]"):
            raise Refusal(
                "`dependencies: []` declares no module, so there is no pin to "
                "rewrite and nothing to be written into."
            )
        raise Refusal(
            f"`dependencies:` carries the value {inline!r} on its own line. This "
            "rewrites block style and flow mappings one entry at a time; a whole "
            "sequence on the key line is a shape it does not understand, and "
            "guessing at it would write a pin nobody can review."
        )

    body = []
    for index in range(head + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            body.append(index)
            continue
        # A LIST ITEM MAY SIT AT COLUMN ZERO under its key — YAML allows it and
        # some of this estate's manifests are written that way — so `-` at column
        # zero continues the block while any other unindented key ends it.
        if line[0] not in " \t" and not line.startswith("-"):
            break
        body.append(index)
    return head, body


def entries(lines):
    """Every dependency entry in the block, refusing any shape not understood."""
    _, body = block(lines)

    spans, opened = [], False
    for index in body:
        line = lines[index]
        if line.lstrip().startswith("-"):
            spans.append([index])
            opened = True
        elif opened:
            spans[-1].append(index)
        elif line.strip():
            raise Refusal(
                f"line {index + 1} of this chart sits under `dependencies:` "
                f"without opening an entry: {line.strip()!r}. This rewrites a "
                "sequence of dependency entries and nothing else."
            )

    if not spans:
        raise Refusal(
            "the `dependencies:` key has no entries under it. A lookup that finds "
            "nothing is not a rewrite with nothing to do — it is a parent chart "
            "that pins no module, and exiting 0 on it would publish that silence."
        )

    found = []
    for span in spans:
        names = [(i, m) for i in span for m in sites(lines[i], "name")]
        versions = [(i, m) for i in span for m in sites(lines[i], "version")]
        first = lines[span[0]].strip()
        if len(names) != 1:
            raise Refusal(
                f"the entry opening {first!r} carries {len(names)} `name:` keys. "
                "A nested sequence inside an entry — `tags:`, for one — opens a "
                "`-` line this reads as an entry of its own, and a rewriter that "
                "guessed at it would write a pin into a shape nobody reviewed."
            )
        if len(versions) != 1:
            raise Refusal(
                f"the entry opening {first!r} carries {len(versions)} `version:` "
                "keys, so the pin to rewrite is not identifiable. A Helm "
                "dependency has exactly one."
            )
        index, match = versions[0]
        value, _ = unquote(match.group(1))
        found.append(
            Entry(
                name=unquote(names[0][1].group(1))[0],
                version=value,
                line=index,
                start=match.start(1),
                end=match.start(1) + len(match.group(1).rstrip()),
            )
        )

    seen = [entry.name for entry in found]
    doubled = sorted({name for name in seen if seen.count(name) > 1})
    if doubled:
        raise Refusal(
            f"{', '.join(repr(name) for name in doubled)} appears twice in "
            "`dependencies:`, so a rewrite would move one pin and leave the other "
            "behind — and which one Helm resolves is not this file's to decide."
        )
    return found


def pins(text):
    """`{module: version}` as the chart pins them, for a caller and for a test."""
    lines = text.splitlines(keepends=True)
    return {entry.name: entry.version for entry in entries(lines)}


def pin(text, module, offered):
    """The chart with ONE module's version rewritten, and the version replaced.

    Every refusal is raised before a byte is changed, and the only bytes that
    change are the version token itself.
    """
    if triple(offered) is None:
        raise Refusal(
            f"{offered!r} is not a plain `MAJOR.MINOR.PATCH` version. A `v` "
            "prefix is what a git TAG carries and a Helm chart version must not, "
            "so this refuses rather than stripping it: a stripped prefix hides "
            "the caller's mistake and publishes a parent whose pin still "
            "resolves, while a refusal names it at the producer."
        )

    lines = text.splitlines(keepends=True)
    found = entries(lines)

    matching = [entry for entry in found if entry.name == module]
    if not matching:
        raise Refusal(
            f"{module!r} is not in `dependencies:`, which holds "
            f"{', '.join(repr(entry.name) for entry in found)}. This rewrites a "
            "pin that exists; it does not add one. A module the parent has never "
            "declared needs a reviewed edit to the chart, not a release-time "
            "write."
        )
    entry = matching[0]

    current = triple(entry.version)
    if current is None:
        raise Refusal(
            f"{module!r} is pinned at {entry.version!r}, which is not a plain "
            "`MAJOR.MINOR.PATCH` version and therefore cannot be ordered against "
            f"{offered!r}. A range or a prerelease pin is a reviewed decision and "
            "this will not overwrite one."
        )

    if triple(offered) == current:
        raise Refusal(
            f"{module!r} is already pinned at {entry.version}, and {offered} was "
            "offered. Writing it would cut a new parent version pinning a "
            "byte-identical set of modules, so the offer is refused rather than "
            "absorbed: a caller that wants idempotence reads the pin first."
        )

    if triple(offered) < current:
        raise Refusal(
            f"{module!r} is pinned at {entry.version} and {offered} is OLDER. "
            f"Refused. Compared as integers, {offered} < {entry.version}; as text "
            f"it can read the other way — {offered!r} > {entry.version!r} is "
            f"{offered > entry.version} — which is ADR-0690's defect, where a "
            "lexical ordering produced nineteen downgrade pull requests titled "
            "'bump' and no downstream check reported one."
        )

    line = lines[entry.line]
    _, quote = unquote(sites(line, "version")[0].group(1))
    lines[entry.line] = (
        line[: entry.start] + quote + offered + quote + line[entry.end :]
    )
    return "".join(lines), entry.version


def ladder(current, bump):
    """The estate's own next version for `current`, read off `next_version`.

    NOT ARITHMETIC OF ITS OWN. `compute` holds the pre-1.0 branch and is asked
    through a synthesised Changelog bullet, which is the device
    `next_version.derive` already uses for a bot-only commit range.
    """
    base = SEMVER.match(f"v{current}")
    if base is None:
        raise Refusal(f"{current!r} is not a version the estate's ladder can read.")
    bullet = BULLETS[bump]
    match = BULLET.match(bullet)
    if match is None:
        # NOT A CRASH. Without this, a change to `pr_body.BULLET` or its type list
        # would reach `bump_for` as `None.group`, and an AttributeError in a
        # release path is a defect that reads like an outage.
        raise Refusal(
            f"the synthesised bullet {bullet!r} no longer parses as a Changelog "
            "entry, so the estate's ladder cannot be consulted and this will not "
            "guess at a version. `pr_body.BULLET` has changed."
        )
    nxt, note, _ = compute(f"v{current}", base, [bullet], [match])
    return nxt, note


def movement(old, new):
    """Which rung of the ladder took `old` to `new`.

    THE LADDER IS PROBED BEFORE IT IS INVERTED. A single release is recognised by
    asking `compute` for each rung and comparing, so the common case needs no
    rule here at all. A GAP — several module releases since the parent last
    moved, which ADR-0722 makes possible whenever a parent bump failed or was
    never wired — cannot be recognised that way, so it is classified by the
    widest field that moved. That fallback is an inverse reading of the same
    ladder rather than a second copy of it: under `0.x` a minor movement IS a
    breaking change, and above it a minor movement is a feature.
    """
    before, after = triple(old), triple(new)
    if before is None or after is None:
        raise Refusal(f"{old!r} and {new!r} cannot both be ordered as versions.")
    for bump in ("patch", "minor", "major"):
        if ladder(old, bump)[0] == new:
            return bump
    if before[0] != after[0]:
        return "major"
    if before[1] != after[1]:
        return "major" if before[0] == 0 else "minor"
    return "patch"


def parent_version(tags, old, new):
    """The parent's next version, its note, and the tag it was derived from."""
    last = greatest(tags)
    if last is None:
        raise Refusal(
            "the parent chart carries no orderable `v*` tag, so there is no "
            "baseline to derive from. The first tag of a repository is cut by "
            "hand — `next_version.py` says so and this does not invent `0.1.0`, "
            "which would be the second version rule this file exists to avoid."
        )
    nxt, note = ladder(last[1:], movement(old, new))
    return nxt, note, last


def main(argv):
    if len(argv) < 5:
        print(
            "::error::usage: parent_pin.py <parent Chart.yaml> <module> <version>"
            " <parent tag>..."
        )
        return 2

    path, module, offered, tags = argv[1], argv[2], argv[3], argv[4:]
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise Refusal(f"{path} could not be read: {error}") from error

    # BOTH DERIVATIONS BEFORE EITHER WRITE. A parent with no baseline must not
    # leave a rewritten pin behind for a release that then cuts no version.
    rewritten, previous = pin(text, module, offered)
    nxt, note, last = parent_version(tags, previous, offered)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(rewritten)

    output(pin=offered, previous=previous, parent=nxt, baseline=last)
    summary(
        "\n".join(
            [
                "## Parent chart re-pinned",
                "",
                f"`{module}` {previous} → **{offered}** in `{path}`, compared as "
                "integers. Every other dependency is byte-identical.",
                "",
                f"The parent moves `{last}` → **`v{nxt}`** ({note}), derived from "
                "the estate's own ladder in `next_version.compute`.",
            ]
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main(sys.argv))
    except Refusal as refusal:
        # THE ANNOTATION IS THE OUTPUT, not a traceback. A refusal here names both
        # versions and what a human has to do about it, and a stack would bury it.
        print(f"::error::{refusal}")
        sys.exit(1)
