#!/usr/bin/env python3
"""Advance a consumer's in-org crate pins to their producers' greatest tags.

WHY THIS EXISTS, AND IT IS A CHANNEL THAT WAS DELIBERATELY CLOSED. Four in-org
crates — `yadgar-telemetry`, `yadgar-lifecycle`, `yadgar-dial`, `yadgar-store` —
are consumed by seven repositories as git dependencies pinned by an exact release
tag (ADR-0526). Dependabot was the only thing that advanced those pins, and
ADR-0690 records what it advanced them to: `"v0.2.9" > "v0.2.10"` on a CHARACTER
comparison, because `9` sorts above `1` at the fifth character, so every proposal
it made once a patch number reached two digits was a DOWNGRADE wearing the word
"bump". Nineteen such pull requests were closed, one confirmed by diff
(`gateway#85` rewrote both the manifest tag and the lockfile's commit sha to the
older release). All seven `dependabot.yaml` files now name the four crates in an
`ignore:` block, which stops the downgrades and leaves NOTHING that advances a
pin: every re-pin since has been a human opening seven pull requests by hand, the
most recent being `gateway#90`.

SO THE ORDERING IS THE ENTIRE POINT OF THIS FILE, and `greatest` parses
`(major, minor, patch)` into integers rather than comparing text. A sort that
happens to agree with semver on the fixture it was tested against is the defect,
not the fix — `scripts/tests/test_repin.py` asserts that its fixture makes the
two orderings DISAGREE.

NOTHING HERE KNOWS THE FOUR CRATE NAMES, and that is deliberate rather than
tidy. The consumer-to-crate map is not symmetric — `yadgar-dial` is in four of
the seven and `yadgar-store` in the other three — so a hardcoded map is a second
place the truth lives and a second place it rots. `git_deps` derives the list
from the manifest, so a repository that gains or drops a crate needs no edit
here, and a fifth crate is covered on the day its first consumer declares it.

IT REFUSES RATHER THAN SKIPS, everywhere it cannot act. A git dependency on this
organisation that is pinned by a revision, by a branch, by a tag this cannot
order, or on a line this cannot rewrite is REFUSED BY NAME and the whole run
goes red. The alternative — quietly leaving it at whatever it holds — is the
"absence read as benign" class `pr_body.py`'s own comments say this estate has
shipped four times, and it is worse here than elsewhere: the failure is
invisible, because a repository whose pin never moves looks exactly like a
repository whose pins are current.

PARSING AND REWRITING ARE TWO DIFFERENT JOBS, and only one of them may touch
the file. A consumer's `Cargo.toml` is mostly COMMENT — `gateway`'s runs to
paragraphs of reasoning per dependency — and no TOML writer preserves that. So
`tomllib` FINDS the dependencies and a line-oriented substitution MOVES the tag,
touching one line per bump and nothing else.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import json
import os
import re
import sys
import tomllib
from collections import namedtuple

ORG = "yadgarhq"

# THE ONE ORDERABLE SHAPE, and it is the same regex the `version` job in
# `ci-pr.yaml` refuses a baseline with. `yadgarhq/yadgar` carries `v0.1.0a6`
# (ADR-0498's prerelease) and `int("0a6")` raises, so anything that is not a
# plain `vMAJOR.MINOR.PATCH` is not a candidate rather than a crash.
SEMVER = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")

# https://github.com/yadgarhq/store.git  and  git@github.com:yadgarhq/store.git
GIT_URL = re.compile(
    rf"^(?:https://github\.com/|git@github\.com:){ORG}/"
    r"(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)

HEADING = re.compile(r"^[ \t]*(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)

REQUIRED = ("What", "Why", "Changelog", "Verification", "Risk")

# THE SQUASH SUBJECT IS PERMANENT HISTORY AND IT MUST NOT EXCEED THE WRAP COLUMN.
# GitHub hard-wraps a pull request BODY at 72 columns on its way into the squash
# commit but does NOT wrap the SUBJECT, and `pr_body.looks_wrapped` judges the
# message AS A WHOLE: one over-long multi-word line makes it read every line as
# author-typed, so it reassembles nothing and every Changelog bullet the wrap
# broke reaches the annotated tag TRUNCATED. A published tag never moves in this
# organisation, so that truncation can never be corrected.
#
# 64 RATHER THAN 72, and the difference is the suffix GitHub appends. The squash
# subject is the pull request title plus ` (#NNN)`, which is 8 characters at a
# four-digit number — so 64 is the widest title that still lands at or under
# `pr_body.WRAP` once the number is on it. The estate's own near-miss is the
# measurement: `actions#78` carried a 77-character title, which would have made
# an 85-column subject and silently truncated all five of its bullets.
SUBJECT_BUDGET = 64

Dep = namedtuple("Dep", "name producer tag")
Bump = namedtuple("Bump", "dep tag")


class Refusal(Exception):
    """A repository this cannot advance, named rather than silently skipped."""


def semver(tag):
    """`(major, minor, patch)` for a plain `vX.Y.Z` tag, else None."""
    match = SEMVER.match((tag or "").strip())
    return tuple(int(g) for g in match.groups()) if match else None


def greatest(tags):
    """The semver-greatest plain `vX.Y.Z` tag in `tags`, or None.

    ORDERED BY PARSED INTEGERS, NEVER BY TEXT. This is the whole reason the job
    this belongs to exists: a lexical sort of `["v0.2.9", "v0.2.10"]` answers
    `v0.2.9`, which is ADR-0690's downgrade. Anything unorderable is dropped
    rather than guessed at, so a producer carrying `v0.1.0a6` alongside plain
    tags still yields the greatest plain one.
    """
    orderable = [t for t in tags or () if semver(t)]
    return max(orderable, key=semver) if orderable else None


def _specs(node, key=None):
    """Every dependency spec in a parsed manifest, with the name it is under.

    RECURSIVE RATHER THAN A LIST OF TABLE NAMES. `[dependencies]`,
    `[dev-dependencies]`, `[build-dependencies]`, `[workspace.dependencies]` and
    `[target.'cfg(unix)'.dependencies]` are all real shapes, and a hardcoded list
    of them is a list that silently misses the sixth. A dict carrying a `git`
    string IS a git dependency wherever it sits, so the walk asks that instead.
    """
    if isinstance(node, dict):
        if isinstance(node.get("git"), str):
            yield key, node
            return
        for name, value in node.items():
            yield from _specs(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _specs(value, key)


def git_deps(text):
    """Every in-org git dependency in a manifest, as `Dep`s.

    A foreign git dependency and a registry dependency are not ours and are
    skipped in silence. Anything that names this ORGANISATION and cannot be
    turned into a `Dep` is refused by name — see the module docstring.
    """
    found = []
    for name, spec in _specs(tomllib.loads(text)):
        url = spec["git"].strip()
        match = GIT_URL.match(url)
        if not match:
            if ORG in url:
                raise Refusal(
                    f"`{name}` names `{ORG}` in its git url `{url}`, which this "
                    "cannot resolve to a producer repository. Re-pin it by hand, "
                    "or fix the url to the "
                    f"`https://github.com/{ORG}/<repo>.git` form every other "
                    "consumer uses."
                )
            continue
        producer = match.group("repo")
        tag = spec.get("tag")
        if not tag:
            held = ", ".join(f"`{k}`" for k in sorted(spec) if k != "git") or "nothing"
            raise Refusal(
                f"`{name}` is an in-org git dependency on `{ORG}/{producer}` with "
                f"no `tag =` pin (it carries {held}). ADR-0526: an in-org crate "
                "is pinned by a PUBLISHED TAG, never by a revision and never by "
                "a branch, because a published tag never moves in this "
                "organisation. Nothing here can order what it holds, so nothing "
                "is proposed for this repository until that pin is a tag."
            )
        if not semver(tag):
            raise Refusal(
                f"`{name}` is pinned at `{tag}`, which is not a plain "
                "`vMAJOR.MINOR.PATCH` tag, so this cannot tell whether it is "
                "behind — and reading it as current would be the silent wrong "
                "state this refuses. Re-pin it by hand."
            )
        found.append(Dep(name, producer, tag))
    return found


def plan(deps, tags):
    """The `Bump`s needed to bring every pin to its producer's greatest tag.

    `tags` maps a producer repository name to its published tag names. A
    producer with no orderable tag, and a pin AHEAD of every tag its producer
    publishes, are both refused: the first is a question this cannot answer and
    the second is a manifest naming a release that does not exist.
    """
    bumps, seen = [], set()
    for dep in deps:
        best = greatest(tags.get(dep.producer) or ())
        if not best:
            raise Refusal(
                f"`{ORG}/{dep.producer}` publishes no plain "
                "`vMAJOR.MINOR.PATCH` tag, so there is nothing to compare "
                f"`{dep.name}`'s pin `{dep.tag}` against. Cut a plain tag "
                "there, or re-pin by hand."
            )
        if semver(dep.tag) > semver(best):
            raise Refusal(
                f"`{dep.name}` is pinned at `{dep.tag}`, which is AHEAD of "
                f"`{best}`, the greatest tag `{ORG}/{dep.producer}` publishes. "
                "A pin naming a release that does not exist cannot be advanced "
                "and must not be read as current."
            )
        # ONE BUMP PER (CRATE, TAG), NEVER ONE PER DECLARATION. A crate declared
        # in both `[dependencies]` and `[dev-dependencies]` parses as two deps at
        # the same tag, and `pin_lines` already moves every line that carries it.
        # Without this the second copy's bump finds no remaining `v0.2.9` to
        # replace and refuses a manifest that is already correct.
        if dep.tag != best and (dep.name, dep.tag) not in seen:
            seen.add((dep.name, dep.tag))
            bumps.append(Bump(dep, best))
    return bumps


def as_json(bumps):
    """A plan, in the shape the workflow hands back to `apply`."""
    return [
        {
            "name": b.dep.name,
            "producer": b.dep.producer,
            "from": b.dep.tag,
            "to": b.tag,
        }
        for b in sorted(bumps, key=lambda b: b.dep.name)
    ]


def from_json(rows):
    return [Bump(Dep(r["name"], r["producer"], r["from"]), r["to"]) for r in rows]


def pin_lines(lines, dep):
    """Every line carrying this dependency's declaration AND its `tag =`.

    ONE LINE IS THE SHAPE EVERY CONSUMER USES, measured across all seven:
    `yadgar-store = { git = "...", tag = "v0.2.13" }`. A crate declared in two
    tables gives two lines and both move. A declaration whose `tag =` sits on a
    continuation line gives NONE, and that refuses rather than skipping — the
    alternative is re-serialising the manifest, which destroys the paragraphs of
    reasoning these files carry per dependency.
    """
    declaration = re.compile(
        rf"^\s*(?:{re.escape(dep.name)}|\"{re.escape(dep.name)}\"|"
        rf"'{re.escape(dep.name)}')\s*=\s*\{{"
        rf".*\btag\s*=\s*\"{re.escape(dep.tag)}\""
    )
    hits = [i for i, line in enumerate(lines) if declaration.search(line)]
    if not hits:
        raise Refusal(
            f"`{dep.name}` parses as pinned at `{dep.tag}`, but no single line of "
            "`Cargo.toml` carries both its declaration and that tag — a "
            "multi-line inline table does this. Rewriting it would mean "
            "re-serialising the manifest and losing its comments, so this "
            "refuses instead. Move the pin onto the declaration's own line, or "
            "re-pin by hand."
        )
    return hits


def rewrite(text, bumps):
    """The manifest with every bumped tag moved, and nothing else touched."""
    lines = text.splitlines(keepends=True)
    pin = re.compile(r'(\btag\s*=\s*")([^"]+)(")')
    for bump in bumps:
        for index in pin_lines(lines, bump.dep):
            replaced, count = pin.subn(
                lambda m: m.group(1) + bump.tag + m.group(3), lines[index], count=1
            )
            if count != 1:
                raise Refusal(
                    f"`{bump.dep.name}`'s pin was found and then could not be "
                    f"replaced on line {index + 1}. Re-pin by hand."
                )
            lines[index] = replaced
    return "".join(lines)


def branch(bumps):
    """A branch name decided entirely by WHAT is being bumped.

    IDEMPOTENCE RESTS ON THIS. Two runs of the same bump must reach the same
    branch, so the second can see the first's pull request and open nothing. The
    `yadgar-` prefix is dropped because every crate carries it and the ref has a
    length budget; sorting makes it independent of the order the manifest
    happened to declare them in.
    """
    parts = [
        f"{b.dep.name.removeprefix('yadgar-')}-{b.tag}"
        for b in sorted(bumps, key=lambda b: b.dep.name)
    ]
    return "repin/" + "_".join(parts)


def title(bumps):
    """The pull request title, which becomes the consumer's squash subject.

    IT NAMES THE CRATES WHILE THEY FIT AND COUNTS THEM WHEN THEY DO NOT. The
    earlier form appended " to their semver-greatest tags", which put a three-
    crate subject at 84 columns with the number on it — over
    `SUBJECT_BUDGET` and over `pr_body.WRAP`. That phrase says what the `## What`
    section already says at length, so the subject drops it rather than truncating
    somewhere a reader cannot predict.

    THE FALLBACK IS NOT DECORATION. Nothing here knows the four crate names, so a
    fifth crate with a long one must not be able to blow the budget — the failure
    would be a truncated tag message in somebody else's repository, months from
    now. When the names do not fit, the count does.
    """
    ordered = sorted(bumps, key=lambda b: b.dep.name)
    named = "chore(deps): re-pin " + ", ".join(
        b.dep.name.removeprefix("yadgar-") for b in ordered
    )
    if len(named) <= SUBJECT_BUDGET:
        return named
    return f"chore(deps): re-pin {len(ordered)} in-org crate pins"


def headings(text):
    """`(hashes, title)` for every markdown heading, comments stripped first.

    ANY LEVEL, matching `pr_body.sections`, which splits on `#` through `######`
    — so a body that answered under a different level than the template asked
    would leave the required section collecting nothing.
    """
    stripped = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    return [(m.group(1), m.group(2).strip()) for m in HEADING.finditer(stripped)]


def body(template, bumps, repository):
    """The pull request body, built from the TARGET repository's own template.

    THE HEADINGS COME FROM THE TEMPLATE RATHER THAN FROM A LIST HERE, because
    the seven templates have drifted across generations and a body that carries
    four of five sections fails `ci / template` on the one it dropped. A heading
    this has no answer for is still emitted, with a sentence under it, so a
    template that grows a section does not produce an empty one.

    NOTHING OUTSIDE `## Changelog` MAY LOOK LIKE A BULLET. `commit_entries`
    reads every line of a commit message with no notion of sections, so a
    `- feat: ...` line under `## What` would become a derivation entry and could
    move the release. The lists below open with a backticked crate name, which
    matches no Conventional Commits type — the same form `gateway#90` used.
    """
    found = headings(template)
    titles = [title for _, title in found]
    missing = [h for h in REQUIRED if h not in titles]
    if missing:
        raise Refusal(
            "the target repository's `.github/pull_request_template.md` is "
            f"missing {', '.join('`## ' + m + '`' for m in missing)}, so no body "
            "built from it can pass `ci / template`. Fix the template first."
        )

    ordered = sorted(bumps, key=lambda b: b.dep.name)
    moves = [f"- `{b.dep.name}`: `{b.dep.tag}` -> `{b.tag}`" for b in ordered]
    crates = ", ".join(f"`{b.dep.name}`" for b in ordered)

    filled = {
        "What": [
            f"The scheduled in-org crate re-pin for `{repository}`. Every pin "
            "below now equals the semver-greatest plain `vMAJOR.MINOR.PATCH` "
            "tag its producer repository publishes:",
            "",
            *moves,
            "",
            "No other line of `Cargo.toml` changed. `Cargo.lock` carries the "
            "re-resolution of those crates and nothing else.",
        ],
        "Why": [
            "ADR-0690: Dependabot orders these git tags LEXICALLY rather than by "
            "semver, so `v0.2.9` sorts above `v0.2.10` and every proposal it "
            "made once a patch number reached two digits was a downgrade. "
            "Nineteen such pull requests were closed, and the four crate names "
            "now sit in an `ignore:` block in every consumer's "
            "`dependabot.yaml` — which left NOTHING advancing an in-org pin. "
            "Each round was a human opening seven pull requests by hand.",
            "",
            "This is that round, opened by the `repin` workflow in "
            "`yadgarhq/actions`, which orders tags by parsed integers. A bot "
            "proposes and a human merges: ADR-0531 rejects automation where it "
            "MANUFACTURES A REVIEW, and this manufactures none.",
            "",
            "ADR-0526 requires an in-org crate to be pinned by a published tag, "
            "never a bare revision. ADR-0634 confirms the producer's own "
            "`version` field is vestigial, so the tag is the only number that "
            "decides which code this builds against.",
        ],
        "Changelog": [
            f"- chore(deps): bump {b.dep.name} from {b.dep.tag} to {b.tag}"
            for b in ordered
        ],
        "Verification": [
            "`ci / passed` on this pull request IS the verification. It runs "
            "`cargo test --all-features`, the feature-off build, `clippy -D "
            "warnings` and this repository's own pre-commit gates against the "
            "new pins. Nothing was verified before the pull request was opened.",
            "",
            f"The lockfile was re-resolved with a package-scoped `cargo update "
            f"-p` per crate ({crates}), never a bare `cargo update`, so no "
            "unrelated package moved. Read the `Cargo.lock` diff before merging: "
            "a transitive dependency of a bumped crate can still move, and that "
            "is the half of this change the crate names do not show.",
            "",
            "NOT DONE HERE, and no check can do it: nobody has read the "
            "producers' diffs between the old tag and the new one. A "
            "behavioural change inside a bumped crate reaches `main` if this "
            "repository's suite does not exercise it.",
        ],
        "Risk": [
            "Low, and reversible one line per crate. This adopts "
            "already-published tags of in-org crates; undoing it is restoring "
            "the old tag in `Cargo.toml` plus `cargo update -p <crate>`. A "
            "published tag never moves in this organisation, so the old pin "
            "still names the same commit it always did.",
            "",
            "D80: this change renders no resource, names no cloud and names no "
            "ingress implementation, so it is correct under a different "
            "operator, ingress or cloud. It adds no trust boundary of its own "
            "and no control here rests on an environment default.",
            "",
            "The residual is not nothing, and it belongs in this section rather "
            "than in a claim that it is absent: `yadgar-dial` and "
            "`yadgar-store` both carry TLS configuration, so a bumped tag CAN "
            "move a security property, and this pull request's checks do not "
            "read the producers' diffs. A reviewer taking a bump of either "
            "crate should read its release range.",
        ],
    }

    spare = (
        "Nothing specific to an automated dependency pin; see the sections "
        "above."
    )
    out = []
    for hashes, title in found:
        out += [f"{hashes} {title}", ""]
        out += filled.get(title, [spare])
        out += [""]
    return "\n".join(out).rstrip() + "\n"


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
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def do_producers():
    """The producer repositories whose tags the workflow has to list.

    A SEPARATE SUBCOMMAND BECAUSE THE SET IS DERIVED, not configured. The
    workflow cannot know which repositories to ask about until the manifest has
    been read, and reading the manifest is this module's job.
    """
    for producer in sorted({d.producer for d in git_deps(read("Cargo.toml"))}):
        print(producer)
    return 0


def read_tags(directory, producers):
    """One file per producer, one tag per line, as the workflow's `gh api` writes.

    A DIRECTORY RATHER THAN ONE JSON DOCUMENT, so the workflow needs no `jq`
    accumulation loop to build it — the fragile half of a shell step that has to
    merge N API responses. A producer with no file is refused rather than read as
    a repository that publishes nothing, because those two mean opposite things:
    the first is a listing that never ran.
    """
    tags = {}
    for producer in producers:
        path = os.path.join(directory, producer)
        if not os.path.isfile(path):
            raise Refusal(
                f"no tag listing was written for `{ORG}/{producer}`, so this "
                "cannot say whether its consumers are behind. The listing step "
                "did not run for it."
            )
        tags[producer] = read(path).split()
    return tags


def do_plan(tags_dir, plan_path, body_path):
    """Read the manifest and the tag listings; write the plan, body and outputs."""
    deps = git_deps(read("Cargo.toml"))
    if not deps:
        summary(
            "### Re-pin\n\nNo in-org git dependency in `Cargo.toml`, so there is "
            "nothing to re-pin here."
        )
        output(stale="false")
        return 0

    tags = read_tags(tags_dir, sorted({d.producer for d in deps}))
    bumps = plan(deps, tags)

    held = "\n".join(
        f"| `{d.name}` | `{d.tag}` | `{greatest(tags.get(d.producer) or ()) }` |"
        for d in sorted(deps, key=lambda d: d.name)
    )
    table = "| crate | pinned | greatest published |\n| --- | --- | --- |\n" + held

    if not bumps:
        summary(
            f"### Re-pin\n\nEvery in-org pin already equals its producer's "
            f"semver-greatest tag. Nothing to do.\n\n{table}"
        )
        output(stale="false")
        return 0

    with open(plan_path, "w", encoding="utf-8") as handle:
        json.dump(as_json(bumps), handle, indent=2)
    with open(body_path, "w", encoding="utf-8") as handle:
        handle.write(
            body(
                read(".github/pull_request_template.md"),
                bumps,
                os.environ.get("GITHUB_REPOSITORY", "this repository"),
            )
        )

    head = branch(bumps)
    output(
        stale="true",
        branch=head,
        crates=" ".join(f"-p {b.dep.name}" for b in as_json_order(bumps)),
        title=title(bumps),
    )
    summary(f"### Re-pin\n\n{len(bumps)} pin(s) behind. Head branch `{head}`.\n\n{table}")
    return 0


def as_json_order(bumps):
    return sorted(bumps, key=lambda b: b.dep.name)


def do_apply(plan_path):
    """Move every tag the plan names, refusing a manifest that has moved."""
    rows = json.loads(read(plan_path))
    if not rows:
        print("::error::the plan is empty, so `apply` has nothing to do")
        return 1
    bumps = from_json(rows)
    text = read("Cargo.toml")
    held = {d.name: d.tag for d in git_deps(text)}
    for bump in bumps:
        if held.get(bump.dep.name) != bump.dep.tag:
            print(
                f"::error::the plan expects `{bump.dep.name}` at "
                f"`{bump.dep.tag}` and `Cargo.toml` holds "
                f"`{held.get(bump.dep.name)}`. The manifest moved under this "
                "run; nothing was written."
            )
            return 1
    with open("Cargo.toml", "w", encoding="utf-8") as handle:
        handle.write(rewrite(text, bumps))
    for bump in as_json_order(bumps):
        print(f"{bump.dep.name}: {bump.dep.tag} -> {bump.tag}")
    return 0


def main(argv):
    if len(argv) == 2 and argv[1] == "producers":
        return do_producers()
    if len(argv) == 5 and argv[1] == "plan":
        return do_plan(argv[2], argv[3], argv[4])
    if len(argv) == 3 and argv[1] == "apply":
        return do_apply(argv[2])
    print(
        "::error::usage: repin.py producers"
        " | repin.py plan <tags-dir> <plan.json> <body.md>"
        " | repin.py apply <plan.json>"
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Refusal as refusal:
        # A REFUSAL IS THE OUTPUT, not a traceback. Every one of them names the
        # dependency and what a human has to do about it, and a traceback would
        # bury that under a stack nobody reads.
        print(f"::error::{refusal}")
        summary(f"### Re-pin refused\n\n{refusal}")
        sys.exit(1)
