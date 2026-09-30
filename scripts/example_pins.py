#!/usr/bin/env python3
"""The parent chart's examples pin the version the commit is tagged as. ADR-0820.

THE DEFECT. `yadgarhq/chart` carries three example Argo CD Applications an
adopter copies: `example/application.yaml` and `example/kind/application.yaml`
pin the parent chart `yadgar`, and `example/operators-application.yaml` pins
`platform` at the version the parent carries inside it. Nobody who moves those
pins cuts the release. Two machines cut every tag of that repository — the
`version` job in `ci-pr.yaml` after a human merge, and `parent_bump.py` after a
module release — and neither knew the files existed. Tag `v0.3.8` shipped
examples pinning `0.3.5`.

THE RULE, which is the whole of this file: at every tag `vN`, in THE SAME COMMIT
the tag points at, every Application whose `chart:` is `yadgar` has
`targetRevision: N`, and every one whose `chart:` is `platform` has the version
that commit's `chart/Chart.yaml` declares for its `platform` dependency. Both
cutters call `stamp` with the number they are about to tag, commit what it
returns in one fast-forward of `main`, and tag that commit.

WHY `main` AND NEVER A COMMIT OFF IT. `next_version.state` finds the baseline
with `git describe`, which only sees tags reachable from HEAD. A tag on a commit
`main` does not contain would be invisible to the next derivation, which would
count the same Changelog again from the tag before it.

THE REWRITE IS TEXTUAL, NOT A YAML ROUND TRIP, for `parent_pin`'s reason and one
more. Re-emitting a parsed document renormalises the comments, quoting and flow
mappings that make up most of these files; and the `version` job runs the
runner's bare `python3`, where neither PyYAML nor ruamel is promised. So this
finds the `chart: <name>` key, takes the mapping it sits in by indentation, and
rewrites the value token of the ONE `targetRevision:` key in that mapping. Every
other byte stays. Prose that names a version is not rewritten: a comment is the
chart repository's to keep true, and guessing which number in a sentence is the
pin is how a rewriter corrupts documentation.

NOTHING INSPECTED IS NOT A PASS. A file that mentions `chart: yadgar` or
`chart: platform` in a shape this does not understand — a flow mapping, say — is
a refusal, not a skip, because a skip there ships exactly the stale pin this
file exists to prevent. What IS a skip: a file that is absent, and a file that
names neither chart. Every repository in the estate runs the `version` job, and
only one of them has these examples.

Usage (the `version` job): example_pins.py, with REPO, SHA and VERSION in the
environment and the repository checked out at SHA. It writes `target=<sha>` to
`GITHUB_OUTPUT`: SHA itself when nothing needed stamping, the stamp commit when
something did, and EMPTY when `main` moved underneath it — the tag is then left
to the next push, whose range still carries this one's Changelog.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile

from parent_pin import VALUE, Refusal, pins, triple, unquote
from repin import output, summary

CHART_YAML = "chart/Chart.yaml"
EXAMPLES = (
    "example/application.yaml",
    "example/kind/application.yaml",
    "example/operators-application.yaml",
)
PARENT = "yadgar"
PLATFORM = "platform"
BRANCH = "main"

# A KEY AT THE START OF A LINE, optionally opening a sequence entry. Group
# `lead` is the indentation, `dash` the `- ` when there is one; the key's column
# is their combined width.
KEY = re.compile(r"^(?P<lead>[ ]*)(?P<dash>-[ ]+)?(?P<key>[A-Za-z_][\w.-]*):(?P<rest>.*)$")
# ANY MENTION OF A CHART NAME AS A `chart:` VALUE, whatever the shape around it.
# More mentions than block-style sites means a shape this does not rewrite.
MENTION = re.compile(r"""(?:^|[\s,{])chart:[ \t]*["']?([\w.-]+)["']?[ \t]*(?=$|[,}#\s])""", re.M)


def shape(line):
    """`(column of the key, indent, has dash, key)` for a key line, else None."""
    match = KEY.match(line.rstrip("\n"))
    if match is None:
        return None
    lead, dash = len(match.group("lead")), len(match.group("dash") or "")
    return lead + dash, lead, bool(dash), match.group("key")


def is_noise(line):
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def mapping(lines, at):
    """The line indices of the keys that are siblings of the key on line `at`."""
    column, _, dashed, _ = shape(lines[at])
    siblings = [at]
    if not dashed:
        for index in range(at - 1, -1, -1):
            if is_noise(lines[index]):
                continue
            found = shape(lines[index])
            indent = len(lines[index]) - len(lines[index].lstrip(" "))
            if found is not None and found[0] == column:
                siblings.append(index)
                if found[2]:
                    break
                continue
            if indent < column:
                break
    for index in range(at + 1, len(lines)):
        if is_noise(lines[index]):
            continue
        found = shape(lines[index])
        indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if indent < column:
            break
        if found is not None and found[0] == column and not found[2]:
            siblings.append(index)
    return siblings


def rewrite(text, targets):
    """`text` with the pin of every Application for a chart in `targets` moved."""
    lines = text.splitlines(keepends=True)
    sites = {}
    for index, line in enumerate(lines):
        found = shape(line)
        if found is None or found[3] != "chart":
            continue
        raw = re.match(r"[ \t]*" + VALUE, KEY.match(line.rstrip("\n")).group("rest"))
        name, _ = unquote(raw.group(1))
        if name in targets:
            sites.setdefault(name, []).append(index)

    # COMMENTS ARE PROSE, and an example that documents an alternative form in a
    # commented-out block — `yadgarhq/config`'s does — is not a missed site.
    code = "".join(line for line in lines if not line.lstrip().startswith("#"))
    for name in targets:
        mentioned = sum(1 for m in MENTION.finditer(code) if m.group(1) == name)
        if mentioned != len(sites.get(name, [])):
            raise Refusal(
                f"this file names `chart: {name}` {mentioned} time(s) and "
                f"{len(sites.get(name, []))} of them are block-style keys this "
                "can rewrite. The rest are in a shape it does not understand — a "
                "flow mapping, say — and skipping them would ship a stale pin."
            )

    for name, indices in sites.items():
        version = targets[name]
        if version is None:
            raise Refusal(
                f"an Application pins `chart: {name}`, and `{CHART_YAML}` in the "
                f"same commit declares no `{name}` dependency, so there is no "
                "version to pin it at."
            )
        for at in indices:
            pins_here = [
                i for i in mapping(lines, at) if shape(lines[i])[3] == "targetRevision"
            ]
            if len(pins_here) != 1:
                raise Refusal(
                    f"the Application for `chart: {name}` on line {at + 1} carries "
                    f"{len(pins_here)} `targetRevision:` keys beside it. There must "
                    "be exactly one to rewrite."
                )
            lines[pins_here[0]] = moved(lines[pins_here[0]], version)
    return "".join(lines)


def moved(line, version):
    """One `targetRevision:` line with its value token replaced, quotes kept."""
    match = re.match(r"^([ ]*(?:-[ ]+)?targetRevision:[ \t]*)" + VALUE, line)
    token = match.group(2).rstrip()
    _, quote = unquote(token)
    end = match.start(2) + len(token)
    return line[: match.start(2)] + quote + version + quote + line[end:]


def stamp(files, version, chart_text):
    """`{path: new text}` for every example that `version` changes. Absent is None."""
    present = {path: text for path, text in files.items() if text is not None}
    if not present:
        return {}
    if triple(version) is None:
        raise Refusal(
            f"{version!r} is not a plain `MAJOR.MINOR.PATCH` version, and an "
            "Argo CD `targetRevision` for an OCI chart must be one."
        )
    platform = pins(chart_text).get(PLATFORM) if chart_text is not None else None
    targets = {PARENT: version, PLATFORM: platform}
    changed = {}
    for path, text in present.items():
        new = rewrite(text, targets)
        if new != text:
            changed[path] = new
    return changed


def message(version):
    """The stamp commit's message, and like `parent_bump`'s IT CARRIES NO BULLET.

    A Changelog bullet here would make the repository's own `version` job cut a
    second release for this one.
    """
    return "\n".join([
        f"ci: pin the examples at v{version}",
        "",
        f"The commit tagged v{version} carries examples that pin v{version},",
        "and the platform version that commit's chart/Chart.yaml declares.",
        "ADR-0820. No Changelog bullet, deliberately: see example_pins.py.",
    ])


class Api:
    """`gh api` against one repository, through an injectable `run`."""

    def __init__(self, full_name, run=None):
        self.full_name = full_name
        self._run = run or (
            lambda args: subprocess.run(args, capture_output=True, text=True)
        )

    def repo(self, *parts):
        return "/".join(("repos", self.full_name) + parts)

    def call(self, *args):
        return self._run(["gh"] + [str(a) for a in args])


def send(gh, method, path, body):
    """One JSON request. `-f` would send `force: false` as the string "false"."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(body, fh)
    try:
        return gh.call("api", "-X", method, path, "--input", fh.name, "--jq", ".sha")
    finally:
        os.unlink(fh.name)


def complaint(done):
    return " ".join((done.stderr or done.stdout or "").split()) or "no output"


def commit(gh, parent, changes, text):
    """One commit on top of `parent` carrying `changes`, fast-forwarded onto `main`.

    Returns `(sha, None)`, or `(None, why)` when any call failed — including the
    ref update GitHub refuses because `main` is no longer `parent`. `force` is
    FALSE: a forced update would drop whatever landed on `main` meanwhile, which
    in the parent chart is another module's pin.

    THE AUTHOR IS UNMEASURED, said rather than assumed. No `author` is sent, and
    GitHub documents that the commit then takes the authenticated identity — for
    an App installation token, the same `yadgarhq-bot[bot]` that `pr_body.BOT`
    matches on the Contents API writes read off `yadgarhq/argocd`. No commit of
    this shape exists in the estate yet to read it off.
    """
    done = gh.call("api", gh.repo("git", "commits", parent), "--jq", ".tree.sha")
    if done.returncode != 0:
        return None, complaint(done)
    tree = send(gh, "POST", gh.repo("git", "trees"), {
        "base_tree": done.stdout.strip(),
        "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in sorted(changes.items())
        ],
    })
    if tree.returncode != 0:
        return None, complaint(tree)
    made = send(gh, "POST", gh.repo("git", "commits"), {
        "message": text, "tree": tree.stdout.strip(), "parents": [parent],
    })
    if made.returncode != 0:
        return None, complaint(made)
    sha = made.stdout.strip()
    moved_ref = send(
        gh, "PATCH", gh.repo("git", "refs", "heads", BRANCH), {"sha": sha, "force": False}
    )
    if moved_ref.returncode != 0:
        return None, complaint(moved_ref)
    return sha, None


def read(gh, path, ref):
    """`path` at `ref`, or None when it does not exist there. ONLY a 404 is absent.

    Any other failure is a refusal: a transient error read as "no examples" would
    tag a commit whose examples were never stamped, which is the defect itself.
    """
    done = gh.call("api", f"{gh.repo('contents', path)}?ref={ref}")
    if done.returncode != 0:
        if "(HTTP 404)" in complaint(done):
            return None
        raise Refusal(
            f"`{path}` could not be read from `{gh.repo()}` at {ref}: "
            f"{complaint(done)}. Nothing was tagged."
        )
    try:
        return base64.b64decode(json.loads(done.stdout)["content"]).decode("utf-8")
    except (ValueError, KeyError, UnicodeDecodeError) as error:
        raise Refusal(f"`{path}` at {ref} is not a readable file ({error}).") from error


def examples_at(gh, ref):
    return {path: read(gh, path, ref) for path in EXAMPLES}


def head(gh):
    done = gh.call("api", gh.repo("commits", BRANCH), "--jq", ".sha")
    return done.stdout.strip() if done.returncode == 0 else None


def settle(gh, base, version, attempts):
    """The commit to tag `version` on: `base` itself if its examples pin it.

    Otherwise — a lost tag race, or a recovered pin whose examples are behind —
    the examples are re-stamped in a follow-up commit on `main`'s CURRENT head,
    never on a side commit: a tag `main` does not contain is invisible to
    `git describe`. `gh` is `parent_bump.Gh`, which also carries `pause`.
    """
    def stale(ref):
        return stamp(examples_at(gh, ref), version, read(gh, CHART_YAML, ref))

    if not stale(base):
        return base
    error = "no attempt was made"
    for attempt in range(1, attempts + 1):
        now = head(gh)
        if now is None:
            error = f"`{BRANCH}` could not be read"
        else:
            changes = stale(now)
            if not changes:
                return now
            made, error = commit(gh, now, changes, message(version))
            if made is not None:
                return made
        if attempt < attempts:
            gh.pause(attempt * 5)
    raise Refusal(
        f"the examples could not be re-stamped at v{version} in {attempts} "
        f"attempts; the last was refused with {error!r}. The pin IS committed at "
        f"{base[:7]} and nothing was tagged; re-run this job rather than "
        "releasing again."
    )


def on_disk(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def main(run=None):
    env = os.environ
    repo, sha = env.get("REPO", "").strip(), env.get("SHA", "").strip()
    version = env.get("VERSION", "").strip()
    if not repo or not sha or not version:
        print(f"::error::example_pins.py needs REPO, SHA and VERSION; got {repo!r}, {sha!r}, {version!r}.")
        return 2

    changes = stamp({path: on_disk(path) for path in EXAMPLES}, version, on_disk(CHART_YAML))
    if not changes:
        output(target=sha)
        return 0

    gh = Api(repo, run=run)
    made, error = commit(gh, sha, changes, message(version))
    if made is not None:
        output(target=made)
        summary(f"Stamped the examples at `v{version}` in `{made[:7]}`; the tag goes there.")
        return 0

    now = head(gh)
    if now is not None and now != sha:
        # DEFERRED, NOT DROPPED. `main` moved past this merge, so the next push's
        # `version` run derives over a range that still holds this merge's
        # Changelog and stamps and tags on its own HEAD.
        output(target="")
        print(f"::warning::v{version} not tagged here: {BRANCH} moved from {sha[:7]} to {now[:7]} before the examples could be stamped. The run for {now[:7]} derives over this merge's Changelog and tags it.")
        summary(f"_`v{version}` deferred: `{BRANCH}` moved before the examples were stamped. The next run tags it._")
        return 0
    output(target="")
    print(f"::error::v{version} was derived and not tagged: the examples could not be stamped at {sha[:7]}: {error}. The release App must be a bypass actor on the {BRANCH} ruleset.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Refusal as refusal:
        print(f"::error::the examples could not be stamped, so nothing was tagged: {refusal}")
        sys.exit(1)
