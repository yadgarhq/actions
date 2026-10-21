#!/usr/bin/env python3
"""An `actions` release, proposed to every consumer. LEDGER 1756 S5.

ADR-0982: when `yadgarhq/actions` cuts a release tag, this finds every consumer
by its ONE `yadgar-class-<name>` topic, renders that consumer's
`yadgarhq/actions` block of `.pre-commit-config.yaml` from its class at the new
tag, and opens or updates exactly one adoption pull request per repository. It
PROPOSES AND NEVER MERGES (ADR-0690, ADR-0531): a bot opening a pull request a
human reads manufactures no review; a bot merging its own would. ADR-0983: the
report it writes says which consumers carry the tag and which do not.

THE BLOCK IS EDITED AS TEXT, NOT RE-SERIALISED. A consumer's block carries the
review history of every hook it adopted, as comments between the entries. A
renderer that rebuilt the block from data would delete that history in every
pull request it opened, and could not reproduce a single real file. So:

    - only the `yadgarhq/actions` block is touched. Lines outside it come back
      byte for byte, and a comment at the block's own indentation or less just
      before the NEXT `- repo:` belongs to that next block;
    - each run of comment lines belongs to the `- id:` that follows it, and
      moves with it when the class reorders hooks;
    - when the ids, order and keys already equal the class, the `rev:` value
      is the only byte that changes;
    - a shape this does not know is refused, never guessed at.

`classes.json` at the repository root is the class data. `scripts/tests/
test_fan_out.py` renders the 21 measured consumer configs at their own current
rev and gets each one back exactly, except five named order drifts.

STANDARD LIBRARY ONLY, so it runs on the runner's system `python3` with no
`setup-python` step and no PyYAML (versions.yaml stays the one place a version
is set). That is why the class data is JSON and the hook manifest is read with
one anchored pattern, which the tests hold equal to `yaml.safe_load`.

Commands:

    fan_out.py mode                 EVENT, DRY_RUN, REF -> `live=true|false`
    fan_out.py run --owner O [--tag vX.Y.Z] [--live] [--classes classes.json]

A run with no `--live` reads and reports and writes NOTHING. The workflow also
mints a read-only token for that mode, so a dry run cannot write even if this
file had a bug.

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import base64
import difflib
import json
import os
import re
import html
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

from repin import REQUIRED, Refusal, greatest, semver

ACTIONS_URL = "https://github.com/yadgarhq/actions"
ACTIONS_REPO = "actions"
CONFIG = ".pre-commit-config.yaml"
TEMPLATE = ".github/pull_request_template.md"
MANIFEST = ".pre-commit-hooks.yaml"
# ONE FIXED BRANCH PER CONSUMER (review of actions#124). A branch per tag
# stacked one conflicting pull request per release, against ADR-0982's
# "exactly one". The open pull request on this branch is moved to the newest
# tag instead.
BRANCH = "actions-adopt"
# The App every generated commit is authored by, and the subjects it writes.
# A commit on BRANCH that is not both is somebody's work, and is never reset.
BOT = "yadgarhq-bot[bot]"
GENERATED = re.compile(r"chore\(deps\): (bump|reorder|regenerate) yadgarhq/actions hooks ")
MAX_BRANCH_COMMITS = 50
DEPLOYABLE = "yadgar-deployable"

REPO_LINE = f"  - repo: {ACTIONS_URL}"
REV = re.compile(r"^    rev: ([^\s#]+)$")
HEADER_COMMENT = re.compile(r"^    #")
HOOKS_LINE = "    hooks:"
HOOK_ID = re.compile(r"^      - id: ([A-Za-z0-9_.-]+)$")
HOOK_COMMENT = re.compile(r"^      #")
OPTION = re.compile(r"^        ([A-Za-z_]+): (\S.*)$")
DEEPER = re.compile(r"^        ")
NEXT_ITEM = re.compile(r"^  - ")
OUTSIDE_COMMENT = re.compile(r"^ {0,2}#")
PLAIN = re.compile(r"[A-Za-z0-9^$()|/._*+?\\][A-Za-z0-9^$()|/._*+?\\-]*")
MANIFEST_ID = re.compile(r"^- id: (\S+)\s*$", re.M)

# SECURITY REVIEW (ledger 1756 S5). Everything read from a consumer, and the
# `tag` input, is untrusted. These bound what one run can be made to do.
#
# A TAG IS FULLMATCHED, never `match`ed: `$` also matches before a trailing
# newline, and `repin.semver` strips whitespace first, so `"v1.2.3\n"` and
# `" v1.2.3"` both parsed there. Digits are capped so no tag is huge.
TAG = re.compile(r"v[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}")
# GitHub's own repository-name alphabet. A name outside it is never put in a URL.
NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
MAX_CONSUMERS = 64  # repositories (and so pull requests) one run may touch
MAX_PAGES = 10  # x100 repositories or tags listed, then a refusal, not a loop
MAX_BYTES = 256 * 1024  # a .pre-commit-config.yaml larger is refused unread
MAX_LINES = 4000  # bounds every per-line pass, the LCS included
MAX_HOOKS = 100  # bounds the LCS table: MAX_HOOKS squared cells at most
MAX_TEMPLATE_BYTES = 64 * 1024
GH_TIMEOUT = 60  # seconds per `gh` call; nothing here retries


# --------------------------------------------------------------------------
# the class data
# --------------------------------------------------------------------------


def load_classes(text):
    """`classes.json`, checked. A malformed class is refused, not half-used."""
    data = json.loads(text)
    for key in ("topic_prefix", "exempt", "classes", "overrides"):
        if key not in data:
            raise Refusal(f"classes.json: missing `{key}`")
    # FIRST-RUN SAFETY (review of actions#124). `live` arms a TAG PUSH; until a
    # reviewed pull request sets it true, every tag runs dry. Exactly a JSON
    # boolean: a string "false" is truthy, so anything else is refused.
    if not isinstance(data.get("live"), bool):
        raise Refusal("classes.json: `live` must be true or false")
    for name, cls in data["classes"].items():
        ids = [h.get("id") for h in cls.get("hooks", [])]
        if not ids or not all(isinstance(i, str) and i for i in ids):
            raise Refusal(f"classes.json: class `{name}` has no hooks or a hook with no id")
        if len(ids) != len(set(ids)):
            raise Refusal(f"classes.json: class `{name}` names a hook twice")
    return data


def expected_hooks(classes, cls, repo):
    """`[(id, {key: value})]` for `repo` of class `cls`, overrides applied."""
    if cls not in classes["classes"]:
        raise Refusal(f"no class `{cls}` in classes.json")
    hooks = [
        (h["id"], {k: v for k, v in h.items() if k != "id"})
        for h in classes["classes"][cls]["hooks"]
    ]
    override = classes["overrides"].get(repo, {})
    ids = [i for i, _ in hooks]
    unknown = sorted(set(override) - set(ids))
    if unknown:
        raise Refusal(f"classes.json: override for `{repo}` names {unknown}, not in `{cls}`")
    return [(i, dict(override.get(i, opts))) for i, opts in hooks]


def class_of(topics, prefix):
    return [t[len(prefix):] for t in topics if t.startswith(prefix)]


def manifest_ids(text):
    """The hook ids `.pre-commit-hooks.yaml` publishes: its top-level `- id:`s."""
    return set(MANIFEST_ID.findall(text))


# --------------------------------------------------------------------------
# the block, as text
# --------------------------------------------------------------------------


@dataclass
class Unit:
    id: str
    lead: list
    id_line: str
    opt_lines: list
    opts: dict

    @property
    def lines(self):
        return self.lead + [self.id_line] + self.opt_lines


@dataclass
class Block:
    prefix: list
    header: list
    rev_at: int
    units: list
    tail: list
    suffix: list = field(default_factory=list)

    @property
    def rev(self):
        return REV.match(self.header[self.rev_at].rstrip("\n")).group(1)


def scalar(raw, where):
    raw = raw.strip()
    if raw[:1] in "|>&*!{%@`":
        raise Refusal(f"{where}: a value of the shape `{raw}` is not one this renders")
    if raw.startswith('"'):
        return json.loads(raw)
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise Refusal(f"{where}: unterminated quoted value")
        return raw[1:-1].replace("''", "'")
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise Refusal(f"{where}: a flow list must close on its own line")
        inner = raw[1:-1].strip()
        return [scalar(item, where) for item in inner.split(",")] if inner else []
    if " #" in raw:
        raise Refusal(f"{where}: a trailing comment on a value is not one this renders")
    return raw


def options(lines, where):
    found = {}
    for line in lines:
        s = line.rstrip("\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        m = OPTION.match(s)
        if not m:
            raise Refusal(f"{where}: unexpected line `{s}`")
        if m.group(1) in found:
            raise Refusal(f"{where}: `{m.group(1)}` twice")
        found[m.group(1)] = scalar(m.group(2), where)
    return found


def parse(text):
    """Split a config into the `yadgarhq/actions` block and what surrounds it."""
    if len(text.encode()) > MAX_BYTES:
        raise Refusal(f"{CONFIG} is over {MAX_BYTES} bytes; refused unread")
    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_LINES:
        raise Refusal(f"{CONFIG} is over {MAX_LINES} lines; refused unread")
    starts = [i for i, l in enumerate(lines) if ACTIONS_URL in l and "repo:" in l]
    if len(starts) != 1 or lines[starts[0]].rstrip("\n") != REPO_LINE:
        raise Refusal(
            f"{CONFIG} must carry exactly one `{REPO_LINE.strip()}` entry at two "
            f"spaces' indentation; found {len(starts)}"
        )
    start = starts[0]
    end = start + 1
    while end < len(lines):
        s = lines[end]
        if NEXT_ITEM.match(s) or (s.strip() and not s.startswith(" ") and not s.startswith("#")):
            break
        end += 1
    while end - 1 > start and (not lines[end - 1].strip() or OUTSIDE_COMMENT.match(lines[end - 1])):
        end -= 1
    block = lines[start:end]

    header, rev_at, i = [block[0]], None, 1
    while i < len(block) and block[i].rstrip("\n") != HOOKS_LINE:
        s = block[i].rstrip("\n")
        if REV.match(s):
            if rev_at is not None:
                raise Refusal(f"{CONFIG}: two `rev:` lines in the yadgarhq/actions block")
            rev_at = len(header)
        elif s.strip() and not HEADER_COMMENT.match(s):
            raise Refusal(f"{CONFIG}: unexpected line in the yadgarhq/actions block: `{s}`")
        header.append(block[i])
        i += 1
    if rev_at is None or i == len(block):
        raise Refusal(f"{CONFIG}: the yadgarhq/actions block needs one plain `rev:` and `hooks:`")
    header.append(block[i])

    units, pending, seen = [], [], set()
    for line in block[i + 1:]:
        s = line.rstrip("\n")
        m = HOOK_ID.match(s)
        if m:
            if m.group(1) in seen:
                raise Refusal(f"{CONFIG}: hook `{m.group(1)}` listed twice")
            seen.add(m.group(1))
            if len(seen) > MAX_HOOKS:
                raise Refusal(f"{CONFIG}: more than {MAX_HOOKS} hooks in the yadgarhq/actions block")
            units.append(Unit(m.group(1), pending, line, [], {}))
            pending = []
        elif not s.strip() or HOOK_COMMENT.match(s):
            pending.append(line)
        elif DEEPER.match(s) and units and not pending:
            units[-1].opt_lines.append(line)
        else:
            raise Refusal(f"{CONFIG}: unexpected line in the yadgarhq/actions hooks: `{s}`")
    for unit in units:
        unit.opts = options(unit.opt_lines, f"{CONFIG} hook `{unit.id}`")
    return Block(lines[:start], header, rev_at, units, pending, lines[end:])


def current_rev(text):
    return parse(text).rev


def plain_or_quoted(value):
    if isinstance(value, list):
        return "[" + ", ".join(plain_or_quoted(v) for v in value) + "]"
    value = str(value)
    return value if PLAIN.fullmatch(value) else json.dumps(value)


def option_lines(opts):
    return [f"        {k}: {plain_or_quoted(v)}\n" for k, v in opts.items()]


def render(text, hooks, rev):
    """`text` with its yadgarhq/actions block at `rev` and exactly `hooks`."""
    block = parse(text)
    header = list(block.header)
    header[block.rev_at] = f"    rev: {rev}\n"
    by_id = {u.id: u for u in block.units}
    out = []
    for hook_id, opts in hooks:
        unit = by_id.get(hook_id)
        if unit and unit.opts == opts:
            out += unit.lines
        elif unit:
            out += unit.lead + [unit.id_line] + option_lines(opts)
        else:
            out += [f"      - id: {hook_id}\n"] + option_lines(opts)
    return "".join(block.prefix + header + out + block.tail + block.suffix)


def lcs(a, b):
    """One longest common subsequence of `a` and `b`, as a set of items."""
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            table[i][j] = (
                table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
            )
    keep, i, j = set(), 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            keep.add(a[i])
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return keep


def drift(text, hooks):
    """How the block differs from `hooks`, one sentence per difference."""
    units = parse(text).units
    have = [u.id for u in units]
    want = [i for i, _ in hooks]
    opts = {u.id: u.opts for u in units}
    found = [f"missing: {i}" for i in want if i not in have]
    found += [f"extra: {i}" for i in have if i not in want]
    common_have = [i for i in have if i in want]
    common_want = [i for i in want if i in have]
    if common_have != common_want:
        kept = lcs(common_have, common_want)
        groups = []
        for i in common_want:
            if i in kept:
                continue
            k = common_want.index(i)
            if groups and common_want[k - 1] == groups[-1][-1]:
                groups[-1].append(i)
            else:
                groups.append([i])
        for group in groups:
            k = common_have.index(group[0])
            was = common_have[k - 1] if k else "the start"
            w = common_want.index(group[0])
            now = common_want[w - 1] if w else "the start"
            verb = "moves" if len(group) == 1 else "move"
            found.append(
                f"order differs: {', '.join(group)} {verb} from after {was} to after {now}"
            )
    for hook_id, want_opts in hooks:
        if hook_id in opts and opts[hook_id] != want_opts:
            # ONLY the class's value: the consumer's is untrusted text, and this
            # sentence reaches a pull request body and the job summary.
            found.append(f"options differ on {hook_id}: the class sets {want_opts!r}")
    return found


# --------------------------------------------------------------------------
# names, the mode, the body
# --------------------------------------------------------------------------


def check_tag(tag):
    """A plain `vX.Y.Z`, or a refusal. A moving `v1` would pin consumers to it."""
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise Refusal(f"{tag!r} is not a plain vMAJOR.MINOR.PATCH tag")
    return tag


def title_for(current, tag, changes):
    """The title, which also targets the tag: `target_of` reads it back."""
    if current != tag:
        return f"chore(deps): bump yadgarhq/actions hooks from {current} to {tag}"
    if changes and all(c.startswith("order differs:") for c in changes):
        return f"chore(deps): reorder yadgarhq/actions hooks ({tag})"
    return f"chore(deps): regenerate yadgarhq/actions hooks ({tag})"


def target_of(title):
    """The tag a generated title moves to (its last tag), or None."""
    found = TAG.findall((title or "")[:300])
    return found[-1] if found else None


def is_live(event, dry_run, ref, armed):
    """Writes happen on a tag push once `classes.json` is armed, or on a
    dispatch from `main` that says so (the explicit human path)."""
    if event == "push":
        return (armed and ref.startswith("refs/tags/")
                and bool(TAG.fullmatch(ref[len("refs/tags/"):])))
    if event == "workflow_dispatch":
        return dry_run == "false" and ref == "refs/heads/main"
    return False


def git_status(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, timeout=GH_TIMEOUT,
                              check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 127


def tag_on_main(tag, git=git_status):
    """A live run adopts only a tag `main` contains.

    Any repository writer can push a `v*` tag at a commit off `main`. The tag
    is checked before git sees it, and the check is `merge-base --is-ancestor`
    against the fetched `main`, so a tag cut anywhere else writes nothing.
    """
    check_tag(tag)
    if git("merge-base", "--is-ancestor", f"refs/tags/{tag}", "refs/remotes/origin/main"):
        raise Refusal(f"`{tag}` is not on main; a live fan-out adopts only a tag main carries")
    return True


def strip_comments(text):
    """`<!-- ... -->` removed, in one linear pass (no backtracking regex)."""
    out, i = [], 0
    while True:
        j = text.find("<!--", i)
        k = text.find("-->", j + 4) if j >= 0 else -1
        if j < 0 or k < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        i = k + 3


def headings(text):
    """`(hashes, title)` per markdown heading, comments stripped, linear time."""
    found = []
    for line in strip_comments(text or "").splitlines():
        rest = line.lstrip(" \t")
        hashes = len(rest) - len(rest.lstrip("#"))
        if not 1 <= hashes <= 6 or rest[hashes:hashes + 1] not in (" ", "\t"):
            continue
        title = rest[hashes:].strip(" \t")
        if title:
            found.append(("#" * hashes, title))
    return found


def body(template, repository, cls, current, tag, changes, deployable):
    """The adoption pull request body, from the CONSUMER's own template.

    NOTHING OUTSIDE `## Changelog` MAY LOOK LIKE A CONVENTIONAL COMMITS BULLET
    (see `repin.body`): the lists below open with a backticked phrase.
    """
    if len((template or "").encode()) > MAX_TEMPLATE_BYTES:
        raise Refusal(f"{TEMPLATE} is over {MAX_TEMPLATE_BYTES} bytes")
    found = headings(template)
    titles = [t for _, t in found]
    missing = [h for h in REQUIRED if h not in titles]
    if missing:
        raise Refusal(
            f"{TEMPLATE} is missing {', '.join('`## ' + m + '`' for m in missing)}, "
            "so no body built from it can pass `ci / template`"
        )
    moved = current != tag
    compare = f"https://github.com/yadgarhq/actions/compare/{current}...{tag}"
    what = [
        (f"Moves the `yadgarhq/actions` hook block of `{repository}` from `{current}` "
         f"to `{tag}`." if moved else
         f"Keeps the `yadgarhq/actions` hook block of `{repository}` at `{tag}`.")
        + f" The block is regenerated from the `{cls}` class (topic "
        f"`yadgar-class-{cls}`), as `classes.json` in `yadgarhq/actions` defines it.",
        "",
    ]
    if changes:
        what += [
            "The block also returns to its class definition. Before this change "
            "it differed from the class in:",
            "",
            *[f"- `{c}`" for c in changes],
            "",
            "A reordered hook keeps the comment written above it.",
        ]
    else:
        what += [
            "The hook ids, their order and their keys already match the class, "
            "so the `rev:` line is the only change.",
        ]
    what += ["", f"No other block of `{CONFIG}` changed."]
    risk = [
        "Merging cuts a patch release of this repository. The `version` job "
        "derives one from the Changelog bullet above, as it does for any bullet.",
    ]
    if deployable:
        risk += [
            "",
            f"This repository carries `{DEPLOYABLE}`, so that release enters the "
            "parent chart's pin chain like any other release.",
        ]
    risk += [
        "",
        "A newer hook can red a check that passed at the old rev. That is the gate "
        "working: fix the cause, and never widen the gate to make it pass.",
        "",
        (f"Reversible: restore `rev: {current}`. A published tag never moves, so the "
         if moved else "Reversible: revert this pull request. A published tag never moves, so the ")
        +
        "old rev still names the same hooks.",
        "",
        "D80: this changes developer tooling only. It renders no resource, names no "
        "cloud and no ingress implementation, and adds no trust boundary.",
    ]
    filled = {
        "What": what,
        "Why": [
            "ADR-0982: an `actions` release opens one adoption pull request per "
            "consumer, found by its class topic. ADR-0561 rules that a release "
            "fires them; ADR-0635 rules that a hook set is generated from its "
            "class. Before this, every rev bump was a hand edit and nothing "
            "detected drift.",
            "",
            "Opened by the `fan-out` workflow in `yadgarhq/actions`, as the "
            "`yadgarhq-bot` App. It proposes and never merges (ADR-0690, "
            "ADR-0531). ADR-0983: the run's report says which consumers carry "
            f"`{tag}`.",
        ],
        "Changelog": [f"- {title_for(current, tag, changes)}"],
        "Verification": [
            "`ci / passed` on this pull request runs this repository's pre-commit "
            "at the new rev; that run IS the verification. Nothing was run before "
            "the pull request was opened. A repository with no CI needs "
            "`pre-commit run --all-files` on this branch before the merge.",
            "",
            (f"The release range to read: {compare}" if moved
             else "The rev does not move, so there is no release range to read."),
        ],
        "Risk": risk,
    }
    spare = "Nothing specific to an automated hook adoption; see the sections above."
    out = []
    for hashes, title in found:
        out += [f"{hashes} {title}", ""]
        out += filled.get(title, [spare])
        out += [""]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# the fan-out
# --------------------------------------------------------------------------


def unified(old, new, repo):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        f"a/{repo}/{CONFIG}", f"b/{repo}/{CONFIG}"))


def one(client, owner, meta, cls, tag, classes, live, row):
    name, base = meta["name"], meta["default_branch"]
    base_sha = client.branch_sha(owner, name, base)
    got = client.read_file(owner, name, CONFIG, base_sha)
    if got is None:
        raise Refusal(f"no {CONFIG} on `{base}`")
    text, blob = got
    current = parse(text).rev
    if not TAG.fullmatch(current):
        # The value is untrusted and is NOT echoed: it would reach the summary.
        raise Refusal("its yadgarhq/actions rev is not a plain vX.Y.Z tag")
    row["current"] = current
    if semver(current) > semver(tag):
        row["status"] = "ahead"
        return
    hooks = expected_hooks(classes, cls, name)
    new = render(text, hooks, tag)
    if new == text:
        row["status"] = "adopted"
        return
    changes = drift(text, hooks)
    patch = unified(text, new, name)
    plus = sum(1 for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in patch.splitlines() if l.startswith("-") and not l.startswith("---"))
    row.update(diff=f"+{plus} -{minus}", patch=patch, changes=changes)
    template = client.read_file(owner, name, TEMPLATE, base_sha)
    if template is None:
        raise Refusal(f"no {TEMPLATE}, so no body can be built for it")
    title = title_for(current, tag, changes)
    text_body = body(template[0], f"{owner}/{name}", cls, current, tag, changes,
                     DEPLOYABLE in meta["topics"])
    branch = BRANCH
    prs = client.find_prs(owner, name, branch)
    open_pr = next((p for p in prs if p["state"] == "open"), None)
    if open_pr is not None:
        moving_to = target_of(open_pr["title"])
        if moving_to and semver(moving_to) > semver(tag):
            # The open pull request already proposes a newer tag; never move it down.
            row.update(status="ahead", url=open_pr["url"])
            return
    for_this_tag = [p for p in prs if p["state"] != "open" and target_of(p["title"]) == tag]
    if open_pr is None and any(p["merged"] for p in for_this_tag):
        raise Refusal(
            f"the adoption pull request for {tag} merged, yet `{base}` is not the class "
            "at that tag (drift came back). Fix it by hand."
        )
    if open_pr is None and for_this_tag:
        row.update(status="declined", url=for_this_tag[0]["url"])
        print(f"::warning::{owner}/{name}: the adoption pull request for {tag} was closed "
              "without merging, so it is not proposed again. Reopen it, or move the rev by hand.")
        return
    if not live:
        row.update(status="would update" if open_pr else "would open",
                   url=open_pr["url"] if open_pr else "")
        return

    wrote = False
    head = client.branch_sha(owner, name, branch)
    on_branch = client.read_file(owner, name, CONFIG, head) if head else None
    if head is None:
        client.create_ref(owner, name, branch, base_sha)
    if on_branch is None or on_branch[0] != new:
        if head is not None:
            foreign = [c for c in client.branch_commits(owner, name, base_sha, head)
                       if c["author"] != BOT or not GENERATED.match(c["message"])]
            if foreign:
                raise Refusal(
                    f"`{branch}` carries {len(foreign)} commit(s) not made by the fan-out; "
                    "refusing to reset them away. Merge or move that work, then re-run."
                )
            client.reset_ref(owner, name, branch, base_sha)
        client.put_file(owner, name, CONFIG, branch, new, blob, title)
        wrote = True
    if open_pr is None:
        row.update(status="opened",
                   url=client.create_pr(owner, name, base, branch, title, text_body))
        return
    if (open_pr["title"], open_pr["body"]) != (title, text_body):
        client.update_pr(owner, name, open_pr["number"], title, text_body)
        wrote = True
    row.update(status="updated" if wrote else "unchanged", url=open_pr["url"])


def ineligible(meta, owner):
    """Why `meta` may never be written, or None. A listing missing a key fails closed."""
    if not NAME.fullmatch(str(meta.get("name", ""))):
        return "error", "a repository name outside GitHub's alphabet"
    if meta.get("owner") != owner:
        return "error", f"owned by {meta.get('owner')!r}, not {owner}"
    if meta.get("archived", True):
        return "archived", ""
    if meta.get("fork", True) or meta.get("disabled", True):
        return "ineligible", "a fork or a disabled repository"
    return None


def consumers(repos, classes, owner):
    """The names the write token is scoped to: in `owner`, live, not a fork,
    not exempt, one class topic. More than MAX_CONSUMERS refuses the run."""
    names = sorted(
        m["name"] for m in repos
        if ineligible(m, owner) is None and m["name"] not in classes["exempt"]
        and len(class_of(m["topics"], classes["topic_prefix"])) == 1
    )
    if len(names) > MAX_CONSUMERS:
        raise Refusal(f"{len(names)} consumers, over the cap of {MAX_CONSUMERS}")
    return names


def run(client, owner, tag, classes, live, manifest_text=None):
    """Every repository of `owner`, one row each, and the exit code.

    `manifest_text` is `.pre-commit-hooks.yaml` at `tag`, read by the caller
    from its own checkout (the workflow's token is not scoped to `actions`);
    without it the client reads it from GitHub.
    """
    check_tag(tag)
    if manifest_text is None:
        manifest = client.read_file(owner, ACTIONS_REPO, MANIFEST, tag)
        if manifest is None:
            raise Refusal(f"yadgarhq/actions carries no {MANIFEST} at `{tag}`")
        manifest_text = manifest[0]
    published = manifest_ids(manifest_text)
    for name, cls in classes["classes"].items():
        absent = [h["id"] for h in cls["hooks"] if h["id"] not in published]
        if absent:
            raise Refusal(f"class `{name}` names {absent}, which `{tag}` does not publish")
    rows = []
    listed = client.list_repos(owner)
    consumers(listed, classes, owner)  # the cap, before anything is read
    for meta in sorted(listed, key=lambda m: str(m.get("name"))):
        row = {"repo": meta["name"], "class": "", "current": "", "target": tag,
               "status": "", "url": "", "diff": "", "note": "", "patch": "", "changes": []}
        rows.append(row)
        why = ineligible(meta, owner)
        if why:
            row.update(status=why[0], note=why[1])
            continue
        if meta["name"] in classes["exempt"]:
            row["status"] = "exempt"
            continue
        names = class_of(meta["topics"], classes["topic_prefix"])
        if len(names) != 1:
            row.update(status="error", note=f"{len(names)} class topics, need exactly one")
            continue
        row["class"] = names[0]
        if names[0] not in classes["classes"]:
            row.update(status="error", note=f"no class `{names[0]}` in classes.json")
            continue
        try:
            one(client, owner, meta, names[0], tag, classes, live, row)
        except Refusal as refusal:
            row.update(status="error", note=str(refusal))
    return rows, 1 if any(r["status"] == "error" for r in rows) else 0


def clean(text, limit=300):
    """Consumer-derived text made inert for a markdown table cell."""
    text = " ".join(str(text).split())
    for bad, good in (("|", "/"), ("`", "'"), ("::", ": :"), ("<", "&lt;"), (">", "&gt;")):
        text = text.replace(bad, good)
    return text[:limit]


def annotation(text):
    """A message safe to print after `::error::` (one line, no command)."""
    return " ".join(str(text).split()).replace("::", ": :")[:500]


def report(rows, tag, live):
    consumers = [r for r in rows if r["status"] not in ("archived", "exempt")]
    at = [r for r in consumers if r["status"] == "adopted"]
    out = [
        f"## actions fan-out to `{tag}` — {'LIVE' if live else 'DRY RUN (no writes)'}",
        "",
        f"{len(at)} of {len(consumers)} consumers carry `{tag}` (ADR-0983 adoption "
        "completeness).",
        "",
        "| repository | class | current rev | target rev | status | pull request | diff | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        notes = clean("; ".join(r["changes"]) or r["note"])
        out.append(
            f"| `{r['repo']}` | {r['class'] or '—'} | {r['current'] or '—'} | {r['target']} "
            f"| {r['status']} | {r['url'] or '—'} | {r['diff'] or '—'} | {notes or '—'} |"
        )
    for r in rows:
        if r["patch"]:
            # <pre> with every character escaped: a patch line holding ``` would
            # otherwise close a markdown fence and inject into the summary.
            out += ["", f"<details><summary><code>{r['repo']}</code></summary>", "",
                    "<pre>", html.escape(r["patch"].rstrip("\n")).replace("::", ":&#58;"),
                    "</pre>", "",
                    "</details>"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# GitHub, through `gh api`
# --------------------------------------------------------------------------


def q(part, safe="/"):
    """One URL path or query part, percent-encoded."""
    return urllib.parse.quote(str(part), safe=safe)


class Gh:
    """The real client. Each method is one REST call through `gh api`.

    `repos`, when given, is the listing the enumeration step already took with
    its own token, so this token never needs to list the organisation.
    """

    def __init__(self, repos=None):
        self.repos = repos

    def _api(self, *args, payload=None, missing_ok=False):
        argv = ["gh", "api", *args] + (["--input", "-"] if payload is not None else [])
        try:
            done = subprocess.run(
                argv, input=json.dumps(payload) if payload is not None else None,
                capture_output=True, text=True, check=False, timeout=GH_TIMEOUT)
        except subprocess.TimeoutExpired as expired:
            raise Refusal(f"gh api timed out after {GH_TIMEOUT}s") from expired
        if done.returncode != 0:
            if missing_ok and "HTTP 404" in done.stderr:
                return None
            raise Refusal(f"gh api {' '.join(args)}: {done.stderr.strip()[:300]}")
        return done.stdout

    def list_repos(self, owner):
        if self.repos is not None:
            return self.repos
        found = []
        for page in self._pages(f"orgs/{q(owner)}/repos"):
            found += [{"name": r["name"], "archived": r["archived"],
                       "disabled": r.get("disabled", True), "fork": r["fork"],
                       "topics": r.get("topics", []), "default_branch": r["default_branch"],
                       "owner": r["owner"]["login"]} for r in page]
        return found

    def _pages(self, path):
        """Each page of a listing, MAX_PAGES at most, then a refusal."""
        for number in range(1, MAX_PAGES + 1):
            page = json.loads(self._api(f"{path}?per_page=100&page={number}"))
            yield page
            if len(page) < 100:
                return
        raise Refusal(f"{path} lists more than {MAX_PAGES * 100}; refused, not paged further")

    def read_file(self, owner, repo, path, ref):
        out = self._api(f"repos/{q(owner)}/{q(repo)}/contents/{path}?ref={q(ref, '')}",
                        missing_ok=True)
        if out is None:
            return None
        got = json.loads(out)
        if got.get("type") != "file" or got.get("size", MAX_BYTES + 1) > MAX_BYTES:
            raise Refusal(f"{path} is not a file of at most {MAX_BYTES} bytes")
        return base64.b64decode(got["content"]).decode(), got["sha"]

    def tags(self, owner, repo):
        return [t["name"] for page in self._pages(f"repos/{q(owner)}/{q(repo)}/tags")
                for t in page]

    def branch_sha(self, owner, repo, branch):
        out = self._api(f"repos/{q(owner)}/{q(repo)}/git/ref/heads/{q(branch)}",
                        missing_ok=True)
        return None if out is None else json.loads(out)["object"]["sha"]

    def branch_commits(self, owner, repo, base, head):
        """Commits on `head` that `base` lacks, as `{author, message}`."""
        got = json.loads(self._api(
            f"repos/{q(owner)}/{q(repo)}/compare/{q(base, '')}...{q(head, '')}?per_page=100"))
        if got.get("total_commits", MAX_BRANCH_COMMITS + 1) > MAX_BRANCH_COMMITS:
            raise Refusal(f"`{BRANCH}` is over {MAX_BRANCH_COMMITS} commits ahead; refused")
        return [{"author": (c.get("author") or {}).get("login"),
                 "message": c["commit"]["message"]} for c in got["commits"]]

    def find_prs(self, owner, repo, branch):
        out = self._api(f"repos/{q(owner)}/{q(repo)}/pulls?head={q(owner + ':' + branch, '')}"
                        "&state=all&per_page=10")
        return [{"number": p["number"], "state": p["state"], "merged": bool(p["merged_at"]),
                 "url": p["html_url"], "title": p["title"], "body": p["body"] or ""}
                for p in json.loads(out)]

    def create_ref(self, owner, repo, branch, sha):
        self._api("-X", "POST", f"repos/{q(owner)}/{q(repo)}/git/refs",
                  payload={"ref": f"refs/heads/{branch}", "sha": sha})

    def reset_ref(self, owner, repo, branch, sha):
        if branch != BRANCH:
            raise Refusal(f"refusing to force-move `{branch}`: only `{BRANCH}` is ours")
        self._api("-X", "PATCH", f"repos/{q(owner)}/{q(repo)}/git/refs/heads/{q(branch)}",
                  payload={"sha": sha, "force": True})

    def put_file(self, owner, repo, path, branch, text, blob, message):
        self._api("-X", "PUT", f"repos/{q(owner)}/{q(repo)}/contents/{path}", payload={
            "message": message, "branch": branch, "sha": blob,
            "content": base64.b64encode(text.encode()).decode()})

    def create_pr(self, owner, repo, base, branch, title, body_text):
        out = self._api("-X", "POST", f"repos/{q(owner)}/{q(repo)}/pulls", payload={
            "base": base, "head": branch, "title": title, "body": body_text})
        return json.loads(out)["html_url"]

    def update_pr(self, owner, repo, number, title, body_text):
        self._api("-X", "PATCH", f"repos/{q(owner)}/{q(repo)}/pulls/{int(number)}",
                  payload={"title": title, "body": body_text})


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def summary(text):
    """The report goes to the step summary and NOWHERE ELSE (review of
    actions#124). It carries consumer diff context, and the runner reads any
    stdout line that starts, after leading whitespace, with `::` as a
    workflow command."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mode_cmd = sub.add_parser("mode")
    mode_cmd.add_argument("--classes", default="classes.json")
    sub.add_parser("greatest", help="the greatest plain vX.Y.Z tag on stdin")
    check = sub.add_parser("check-tag")
    check.add_argument("tag")
    on_main = sub.add_parser("on-main")
    on_main.add_argument("--tag", required=True)
    on_main.add_argument("--report-only", action="store_true",
                         help="dry mode: say whether the tag is on main, never fail")
    enum = sub.add_parser("enumerate")
    enum.add_argument("--owner", required=True)
    enum.add_argument("--classes", default="classes.json")
    enum.add_argument("--out", required=True)
    go = sub.add_parser("run")
    go.add_argument("--repos", default="", help="the listing `enumerate` wrote")
    go.add_argument("--manifest", default="", help=f"{MANIFEST} at the tag")
    go.add_argument("--owner", required=True)
    go.add_argument("--tag", default="")
    go.add_argument("--live", action="store_true")
    go.add_argument("--classes", default="classes.json")
    args = parser.parse_args(argv)

    if args.command in ("check-tag", "on-main"):
        try:
            check_tag(args.tag)
            if args.command == "on-main":
                tag_on_main(args.tag)
                print(f"{args.tag} is on main")
        except Refusal as refusal:
            if args.command == "on-main" and args.report_only and TAG.fullmatch(args.tag):
                print(f"{args.tag} is not on main (report only: a live run would refuse it)")
                return 0
            print(f"::error::{annotation(refusal)}", file=sys.stderr)
            return 1
        return 0
    if args.command == "greatest":
        tag = greatest(sys.stdin.read().split())
        if not tag:
            print("::error::no plain vX.Y.Z tag", file=sys.stderr)
            return 1
        print(tag)
        return 0
    if args.command == "mode":
        try:
            with open(args.classes, encoding="utf-8") as handle:
                armed = load_classes(handle.read())["live"]
        except (OSError, ValueError, Refusal) as refusal:
            print(f"::error::{annotation(refusal)}", file=sys.stderr)
            return 1
        live = is_live(os.environ.get("EVENT", ""), os.environ.get("DRY_RUN", ""),
                       os.environ.get("REF", ""), armed)
        output("live", "true" if live else "false")
        print(f"live={'true' if live else 'false'}")
        return 0

    try:
        with open(args.classes, encoding="utf-8") as handle:
            classes = load_classes(handle.read())
        if args.command == "enumerate":
            repos = Gh().list_repos(args.owner)
            names = consumers(repos, classes, args.owner)
            if not names:
                # An empty `repositories:` beside `owner:` mints for EVERY
                # repository, so no consumer is a refusal, never a pass.
                raise Refusal("no repository carries exactly one class topic")
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(repos, handle)
            output("repos", ",".join(names))
            print(f"{len(names)} consumers: {', '.join(names)}")
            return 0
        repos = None
        if args.repos:
            with open(args.repos, encoding="utf-8") as handle:
                repos = json.load(handle)
        client = Gh(repos)
        manifest = None
        if args.manifest:
            with open(args.manifest, encoding="utf-8") as handle:
                manifest = handle.read()
        tag = args.tag or greatest(client.tags(args.owner, ACTIONS_REPO))
        if not tag:
            raise Refusal("yadgarhq/actions carries no plain vX.Y.Z tag")
        rows, code = run(client, args.owner, tag, classes, args.live, manifest)
    except Refusal as refusal:
        print(f"::error::{annotation(refusal)}", file=sys.stderr)
        return 1
    summary(report(rows, tag, args.live))
    print(f"{len(rows)} repositories reported to the step summary ({'live' if args.live else 'dry'}).")
    for r in rows:
        if r["status"] == "error":
            print(f"::error::{annotation(args.owner + '/' + r['repo'] + ': ' + r['note'])}",
                  file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
