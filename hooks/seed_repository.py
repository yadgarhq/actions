#!/usr/bin/env python3
"""ADR-0705 — merge-time feedback on an organisation's own seed declaration.

THIS IS THE OPTIONAL HALF OF RULE 3, AND SAYING SO IS PART OF THE ARTEFACT. ADR-0705
moves the D37 gates SERVER-SIDE: "the seeder and the chart refuse invalid front-matter,
an invalid schema and any `prompts/ask/` definition unconditionally, and upstream
additionally publishes an OPTIONAL reusable workflow and pre-commit hook an organisation
may reference for merge-time feedback." The refusal that matters is the seeder's,
because CI shipped into somebody else's repository is deletable by whoever owns that
repository — which is the whole reason the guarantee moved. This file is the
convenience: a red pull request instead of a red sync. An organisation that deletes it
loses the early warning and nothing else.

SO NOTHING HERE IS THE ONLY THING BETWEEN A BAD INPUT AND A CLUSTER, and that is a
design constraint rather than a disclaimer. Every check below is one an organisation
can run on its OWN tree with nothing but python3 — no chart, no registry, no running
service. A check needing any of those would be a check whose absence lets something
through, and it belongs to the seeder.

WHAT AN ORGANISATION'S REPOSITORY HOLDS, which is what this judges (D34, D36, ADR-0705):

    prompts/agents/<pattern>.md    org and team level agent prompts (D34)
    content/wiki/<slug>.md
    content/memories/<id>.md
    prompts/ask/                   REFUSED, unconditionally

TWO HOOKS, ONE FILE, for the reason `chart_baseline.py` gives about its own split: an
organisation declaring only wiki content references one half and not the other, and
that choice is a line in its own `.pre-commit-config.yaml` rather than an exemption
buried in this gate.

  `--part seed-no-ask-prompt`  — no path under `prompts/ask/` exists.
  `--part seed-front-matter`   — every content file carries usable front-matter.

WHY THE `ask` PATH CHECK NEEDS NO SEEDER AT ALL. D33 gives the `ask` system prompt no
override: no user, team or org layer, and no merging. ADR-0705 rejected
"publishing CI the organisation is required to reference" precisely because the
reference is deletable and "the D33 guarantee would end at the first organisation that
removed it". A path is a path, so this half is exact at merge time and stays exact —
it just is not the guarantee.

WHY FRONT-MATTER IS THE OTHER HALF. D35 requires "deterministic identity derived from
the source file, so re-seeding updates the existing record in place. Without it, every
upgrade duplicates the help corpus." D36 makes it mechanical: "Front-matter is
mandatory and lint-enforced." So a content file with no front-matter, or front-matter
nobody can read, is the defect — and it is visible in the file itself, which is what
makes it checkable here.

THE FOUR NAMED KEYS ARE `content/memories/` ONLY, AND THE RECORD NAMES NO OTHERS.
D36's layout block annotates exactly one line:

    content/memories/<id>.md    # front-matter: id, tags, anchor, visibility

It annotates neither `content/wiki/<slug>.md` nor `prompts/agents/<pattern>.md`. So
those two roots are required to carry front-matter and are required to carry NO
particular key. Inventing keys for them would ship a guess into somebody else's
repository as a false refusal, and `test_a_wiki_page_needs_no_named_field` is what
fails if anybody does.

A TENSION IN THE RECORD, DATED 2026-09-19, AND WHERE TO CUT IF IT RESOLVES THE OTHER
WAY. D36 lists `id` among a memory's front-matter keys. D42 says seeded records use
"UUIDv5 over a fixed namespace and the source file path" — identity derived from the
PATH, which makes a front-matter `id` redundant rather than load-bearing. Both are
accepted decisions and only the seeder's author can settle it. `id` is required here
because D36 states it flatly and this gate follows the record rather than an inference;
if the seeder rules it out, delete `"id"` from `MEMORY_KEYS` and the one loop iteration
in `test_a_memory_missing_a_named_key_is_refused` goes with it. That is the whole cost,
and it is stated here so nobody has to rediscover the argument.

DELIBERATELY NOT A YAML PARSER. `language: script` provisions nothing, so an adopter
runs this on whatever python3 their machine carries and PyYAML may not be there — the
same constraint `chart_publicly_pullable.py` works under, and `test_the_gate_imports_
only_the_standard_library` pins it. The scanner below reduces a front-matter block to
top-level `key: value` pairs, treats an INDENTED line as belonging to the key above it
so a block list is accepted, and REFUSES anything it cannot reduce. Refusing is the
fail-safe direction: the seeder has a real parser and is the authority, so a merge-time
hook that guessed would pass input the authority then rejects — which is worse than a
hook that says "this gate cannot read line 4".

WHAT IT REFUSES, and every one of these has a case in `scripts/tests/test_seed_repository.py`
that demands it:

- any path under `prompts/ask/`, at any depth, of any extension, and the bare
  directory too. ANCHORED AT THE DECLARATION'S OWN ROOT, never any directory anywhere
  that happens to be called that: `docs/prompts/ask/` is not an override, because
  nothing reads it, and refusing a repository for a path no component looks at is a
  false refusal. An organisation whose declaration sits in a subdirectory points
  `--root` — or the workflow's `content-root` — at that subdirectory, which re-anchors
  the rule where the declaration is. — git cannot commit an empty directory, so that shape reaches the hook
  rather than CI, which is what a pre-commit hook is for. Matched CASE-INSENSITIVELY:
  `prompts/Ask/` is `prompts/ask/` on a case-insensitive filesystem, so Linux and macOS
  would otherwise disagree about whether the same commit carries an override.
- a content file with no front-matter, front-matter that is never closed, a block that
  declares nothing, a line the scanner cannot reduce, a memory missing one of D36's
  four keys, or a memory declaring one of them with no value.
- NOTHING TO INSPECT, on both halves. A tree with none of the three roots is not a
  seed declaration and this gate has no business judging it; roots that exist holding
  zero `*.md` is the shape a renamed directory produces, and it is the shape
  `one_source_per_knob.py` shipped as "0 knobs across 0 files". Five checks in this
  estate have reported success having inspected nothing. `chart_baseline.py` refuses a
  tree with no `Chart.yaml` and `boot_reads_watched.py` refuses one with no
  `src/rotate.rs`, for the same reason and in the same words: a green tick must not
  mean "examined nothing".

  THAT IS NOT A JUDGEMENT ON AN EMPTY ORGANISATION. D34 says an organisation declaring
  no org or team content "simply has no org or team prompts, and resolution falls
  through to system. That is a complete and usable state, not a degraded one." Such an
  organisation does not reference these hooks, and the refusal message says so rather
  than telling them to invent content.

WHAT IT DOES NOT CHECK, said here rather than left to be found. Each of these is
ADR-0705 work that cannot be done outside the seeder or the chart, so attempting it
here would produce a second, weaker authority that disagrees with the first.

- AN INVALID VALUES SCHEMA, which is the third refusal in rule 3 and the one this pair
  does not implement. `chart/values.schema.json` does not exist on
  `yadgarhq/config@main` — checked 2026-09-19, where `chart/` holds `Chart.yaml`,
  `config/`, `templates/` and `values.yaml` and nothing else — so there is no schema to
  validate against and inventing one would publish a rule nobody decided. Even once it
  lands this hook cannot do it: helm is what enforces `values.schema.json`, at template
  time, and there is no JSON Schema validator in the standard library. The seam is in
  `.github/workflows/seed-declaration.yaml`, which has helm available and names the
  unblocking condition in the step that is not yet there.
- WHETHER RE-SEEDING ACTUALLY UPDATES IN PLACE. That is D35's real property and only a
  re-seed demonstrates it. This half checks that front-matter exists and is readable,
  which is the precondition, not the property.
- THE DECLARED MINIMUM SERVICE VERSION D36 asks to be "checked at load". At load is the
  seeder. Merge time can check that a field parses; it cannot know what version the
  organisation's cluster runs.
- THE 1 MiB ConfigMap CEILING. ADR-0705 puts that guard in the chart, which renders the
  ConfigMaps and therefore knows their real encoded size. A merge-time copy with its own
  accounting would disagree with the authority sooner or later, and a guard that
  disagrees with the thing it is approximating is worse than no guard.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import argparse
import os
import re
import sys

# The three roots an organisation declares (D34, D36, ADR-0705). ANY one of them makes
# a tree a seed declaration: D34 allows an organisation to declare wiki content and no
# prompts, so requiring all three would refuse a legitimate shape.
ROOTS = ("prompts/agents", "content/wiki", "content/memories")

# The root that may never exist (D33). Compared case-insensitively — see the docstring.
FORBIDDEN = "prompts/ask"

# The one directory the walk skips. See `forbidden_paths` for why it is the only one.
GIT = ".git"

# The root whose front-matter keys the record names, and the keys it names. D36's
# layout block annotates this line and no other. See the docstring for the D36/D42
# tension over `id` and for exactly what to delete if it resolves the other way.
MEMORY_ROOT = "content/memories"
MEMORY_KEYS = ("id", "tags", "anchor", "visibility")

# The extension this gate reads. A tree of some other extension is a corpus this gate
# is not reading, which is why the floor below refuses it rather than passing it.
SUFFIX = ".md"

# A floor rather than a count. Zero content files is a check that inspected nothing.
MINIMUM_CONTENT_FILES = 1

DELIMITER = "---"

# A top-level mapping line: `key:`, or `key: value`. Deliberately narrow — the scanner
# refuses what this does not match rather than guessing at it.
MAPPING = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_.-]*):(?P<value>.*)$")


def relative(path, root):
    """A forward-slash path relative to the tree, for messages and for matching."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def declared_roots(root):
    """Which of the three roots this tree actually has."""
    return [name for name in ROOTS if os.path.isdir(os.path.join(root, name))]


def forbidden_paths(root):
    """Every path under a case-insensitive `prompts/ask/`, plus the directory itself.

    Walked rather than globbed, so a nested override is found at any depth, and so a
    directory carrying nothing is still reported — git cannot commit an empty
    directory, so that shape only ever reaches a pre-commit hook.
    """
    found = []
    for current, directories, files in os.walk(root):
        here = relative(current, root)
        if here.lower() == FORBIDDEN or here.lower().startswith(FORBIDDEN + "/"):
            found.append(here)
            found.extend(sorted(f"{here}/{name}" for name in files))
            continue
        # `.git` IS PRUNED, FOR SPEED AND FOR NOTHING ELSE. With `--root .` the walk
        # would otherwise descend the whole object store. It changes no verdict: the
        # rule above is ANCHORED at the tree's own root — `prompts/ask`, not any
        # directory anywhere that happens to be called that — so nothing under `.git`
        # was ever going to match. `test_the_rule_is_anchored_at_the_content_root`
        # pins the anchoring, which is the property this prune relies on.
        directories[:] = sorted(name for name in directories if name != GIT)
    return sorted(set(found))


def content_files(root):
    """Every `*.md` under the declared roots, relative and sorted."""
    found = []
    for name in declared_roots(root):
        for current, directories, files in os.walk(os.path.join(root, name)):
            directories.sort()
            for filename in sorted(files):
                if filename.endswith(SUFFIX):
                    found.append(relative(os.path.join(current, filename), root))
    return sorted(set(found))


def scan_front_matter(text):
    """`(keys, problem)` for one file's front-matter.

    `keys` maps a top-level key to whether it carries a value — either on its own line
    or as indented lines beneath it. `problem` is a sentence naming what could not be
    read, and is None when the block reduced cleanly.

    NOT A YAML PARSER, and the docstring at the top of this file says why refusing is
    the fail-safe direction.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != DELIMITER:
        return {}, (
            "carries no front-matter block. D35 derives a seeded record's identity "
            "from the source file, so a file without it either duplicates on every "
            "re-seed or cannot be updated in place — see D36, where front-matter is "
            "mandatory and lint-enforced."
        )

    # THE MISSING CLOSE IS DIAGNOSED BEFORE THE SCAN, not after it. An unclosed block
    # is followed by ordinary prose, and prose is not a `key: value` — so a scanner
    # reading top-to-bottom reaches the body first and blames line 4 for not being a
    # mapping, when what the author has to fix is the delimiter. The accurate message
    # is the one somebody acts on.
    if DELIMITER not in [line.strip() for line in lines[1:]]:
        return {}, (
            "opens a front-matter block that is never closed. A `---` on its own line "
            "ends it."
        )

    keys = {}
    last = None
    for number, line in enumerate(lines[1:], start=2):
        if line.strip() == DELIMITER:
            if not keys:
                return {}, (
                    "opens a front-matter block that declares nothing. The "
                    "delimiters alone are not front-matter."
                )
            return keys, None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            if last is None:
                return {}, (
                    f"line {number} is indented and there is no key above it for it "
                    f"to belong to: `{line.strip()}`"
                )
            keys[last] = True
            continue
        match = MAPPING.match(line)
        if not match:
            return {}, (
                f"line {number} is not a top-level `key: value` and this gate does "
                f"not guess at it: `{line.strip()}`. The seeder has the real parser; "
                f"a merge-time hook that assumed would pass input the seeder rejects."
            )
        last = match.group("key")
        keys[last] = bool(match.group("value").strip())

    # Unreachable: the closing delimiter was established above, so the loop returns on
    # it. Kept as a refusal rather than a fall-through to 0 — see the estate's five
    # checks that reported success having inspected nothing.
    return {}, "front-matter could not be read"  # pragma: no cover


def judge_content(root, paths):
    """`[(path, message)]` for every content file this gate cannot accept."""
    findings = []
    for path in paths:
        try:
            text = open(os.path.join(root, path), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError) as error:
            findings.append((path, f"could not be read as UTF-8 text: {error}"))
            continue
        keys, problem = scan_front_matter(text)
        if problem:
            findings.append((path, problem))
            continue
        if not path.startswith(MEMORY_ROOT + "/"):
            continue
        for key in MEMORY_KEYS:
            if key not in keys:
                findings.append(
                    (
                        path,
                        f"declares no `{key}`. D36 names id, tags, anchor and "
                        f"visibility as a memory's front-matter, and D35 needs them "
                        f"to re-seed the record in place rather than duplicating it.",
                    )
                )
            elif not keys[key]:
                findings.append(
                    (
                        path,
                        f"declares `{key}` with no value. A key present and empty is "
                        f"not the key D36 asks for, and leaves the seeder to invent "
                        f"one.",
                    )
                )
    return findings


def refuse_empty_tree(part, root, shown):
    """The layout precondition, and it is the same sentence for both halves."""
    print(
        f"{part}: {shown} holds none of the three roots an organisation's seed "
        f"declaration has — {', '.join(f'{name}/' for name in ROOTS)} — so this gate "
        f"examined nothing. A "
        f"green tick must not mean that. If this repository genuinely declares no org "
        f"or team content, that is a complete state under D34 and it should not "
        f"reference this hook; the seeder and the chart refuse bad input server-side "
        f"either way (ADR-0705), and this hook is only the early warning."
    )


def run_ask(part, root, shown, report):
    """No path under `prompts/ask/` exists (D33)."""
    offenders = forbidden_paths(root)
    if report:
        print(f"{part}: {len(offenders)} path(s) under `{FORBIDDEN}/`")
        for path in offenders:
            print(f"  {path}")

    # THE OFFENCE OUTRANKS THE FLOOR, and the order is pinned by a test. A tree
    # carrying `prompts/ask/` and nothing else trips both; refusing it as "nothing to
    # inspect" would tell an organisation to add content when what it has to do is
    # delete a file.
    if offenders:
        for path in offenders:
            print(f"{shown}/{path}: refused.")
        print(
            f"{part}: the `ask` system prompt has NO override — no user, team or org "
            f"layer and no merging of layers (D33). ADR-0705 rejected making upstream "
            f"CI a requirement precisely because a seed repository able to define this "
            f"prompt \"would end that guarantee at the first organisation that tried\". "
            f"Delete the path above. An organisation that wants the prompt different "
            f"opens a pull request against upstream, where a change to how every "
            f"installation answers is reviewed. The seeder refuses this "
            f"unconditionally, so keeping the file only moves the red from here to the "
            f"sync."
        )
        return 1

    roots = declared_roots(root)
    if not roots:
        refuse_empty_tree(part, root, shown)
        return 1

    print(
        f"{part}: no `{FORBIDDEN}/` in {shown}, over a declaration holding "
        f"{', '.join(f'{name}/' for name in roots)}."
    )
    return 0


def run_front_matter(part, root, shown, report):
    """Every content file carries front-matter this gate can read (D35, D36)."""
    roots = declared_roots(root)
    if not roots:
        refuse_empty_tree(part, root, shown)
        return 1

    paths = content_files(root)
    findings = judge_content(root, paths)

    if report:
        bad = {path for path, _ in findings}
        print(f"{part}: per file")
        for path in paths:
            print(f"  {path}: {'FAIL' if path in bad else 'PASS'}")

    for path, message in findings:
        print(f"{shown}/{path}: {message}")

    if len(paths) < MINIMUM_CONTENT_FILES:
        print(
            f"{part}: {', '.join(f'{name}/' for name in roots)} present in {shown} and "
            f"{len(paths)} content file(s) matching `*{SUFFIX}` under them, which is "
            f"below the floor of {MINIMUM_CONTENT_FILES}. The layout is there and there "
            f"is nothing in it — the shape a renamed directory produces, and the shape "
            f"a gate reporting \"0 knobs across 0 files\" shipped in this estate. A "
            f"green tick must not mean this gate examined nothing."
        )
        return 1
    if findings:
        print(
            f"{part}: {len(findings)} finding(s) over {len(paths)} content file(s). "
            f"Seeded identity is derived deterministically from the source file (D35, "
            f"D42), so a file the seeder cannot read front-matter from either "
            f"duplicates on every upgrade or cannot be updated in place. The seeder "
            f"refuses these unconditionally (ADR-0705); this hook only says so earlier."
        )
        return 1

    print(
        f"{part}: {len(paths)} content file(s) under "
        f"{', '.join(f'{name}/' for name in roots)}, every one carrying front-matter "
        f"this gate could read."
    )
    return 0


PARTS = {
    "seed-no-ask-prompt": run_ask,
    "seed-front-matter": run_front_matter,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ADR-0705 — merge-time feedback on an organisation's own seed "
            "declaration. OPTIONAL by design: the seeder and the chart refuse bad "
            "input server-side and unconditionally, because CI shipped into somebody "
            "else's repository is deletable by whoever owns it. This is the early "
            "warning, never the guarantee."
        )
    )
    parser.add_argument(
        "--part",
        required=True,
        choices=sorted(PARTS),
        help=(
            "which published half to run. REQUIRED and with no default: a default "
            "would let a bare invocation mean one half without saying so."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="the tree to judge (default: the working directory)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "list every path this half judged, as well as the findings. This is how "
            "an organisation measures its own tree before referencing the hook "
            "(ADR-0607)."
        ),
    )
    parser.add_argument(
        "filenames",
        nargs="*",
        help=(
            "ignored. The subject is the whole declaration, so both hooks run with "
            "`pass_filenames: false` — a `files:` filter would leave a declaration "
            "moved elsewhere unjudged, which is the hole `certificate_usages.py` "
            "measured."
        ),
    )
    arguments = parser.parse_args(argv)

    root = arguments.root
    if not os.path.isdir(root):
        print(f"{arguments.part}: --root {root} is not a directory")
        return 1

    # The path as the caller wrote it, so a message names something they can open.
    shown = root.rstrip("/") or root
    return PARTS[arguments.part](arguments.part, root, shown, arguments.report)


if __name__ == "__main__":
    sys.exit(main())
