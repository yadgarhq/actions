#!/usr/bin/env python3
"""Refuse a move of a hook-asserted tool's version while a consumer is behind.

YAADGAAR 231, ADR-0983 STEP 2. `hooks/tool_version.py` refuses a local binary
unless `versions.yaml` AT THE REV A CONSUMER PINS names it, as `version` or in
`accept`. CI installs `version` from `versions.yaml` on actions MAIN (ADR-0981).
So a pull request here that changes `version` of gitleaks, hadolint,
actionlint or cargo-deny reds every consumer whose rev does not yet accept the
new version. ADR-0983 makes the move two steps (the header of `versions.yaml`
says how). This check is the refusal of step 2 while step 1 is not complete.

WHAT IT COMPARES. `versions.yaml` at the merge commit's first parent (the base
tip) against the merge commit itself. A tool whose `version` differs is a
move. A tool that only gains an `accept` entry (step 1), a tool new on the
head, and every tool no hook asserts are not moves. No move: a notice, exit 0,
and no API call at all.

WHAT "BEHIND" MEANS. For each move to version N, a consumer is ADOPTED when
`versions.yaml` at its rev, read from this checkout's tags, has `version` N
or lists N in `accept`. Anything else is BEHIND, and so is every fact that
cannot be read: a rev that is not a plain vX.Y.Z, a tag this repository does
not carry, an unreadable file, a config with no `yadgarhq/actions` block, a
failed API read. Each is a red row that names the repository.

PUBLIC CONSUMERS ARE READ (Max, 2026-10-30, option 3). The listing and each
consumer's `.pre-commit-config.yaml` on its default branch are read with
`GITHUB_TOKEN`, which reaches public repositories only. No credential is
added to a pull request run. A consumer is what `fan_out.consumers` says:
one class topic, not archived, not a fork, not exempt.

PRIVATE CONSUMERS ARE ATTESTED, NOT READ. `GITHUB_TOKEN` cannot see `docs`
or `argocd-verify`. `classes.json` `private_consumers` carries one reviewed
entry for each:

    {"rev": "vX.Y.Z"}    the rev it pins, judged exactly like a public rev
    {"exempt": "why"}    not judged; ADR-0983's "exempted by explicit decision"

THE ATTESTATION IS A REV, NOT A FLAG. An "adopted: true" would be true for
the bump it was written for and silently true for every later one. A rev
cannot go stale that way: an old rev computes as behind, and the pull request
that moves the tool must first update the entry to the rev the private
repository pins now.

WHICH PRIVATE CONSUMERS MUST BE ATTESTED. Every name in `sweep.exclude` that
the public listing did not return, every private repository the listing did
return (a local run with a wider token), and every entry already present.
A required name with no entry is red. An entry for a repository the listing
reads as public is red too: a measurement is never overridden by an
attestation.

WHAT THIS CANNOT SEE. A new private consumer that nobody names in
`classes.json`. Only the fan-out's owner-wide metadata token lists private
repositories, and a pull request run carries no such token.

STANDARD LIBRARY ONLY: the job runs on the runner's `python3` with no
`setup-python` and no PyYAML, like `fan_out.py` and `tool_versions.py`.

Run:   python3 scripts/hook_tools_adopted.py --owner yadgarhq [--base REV --head REV]
Tests: python3 -m pytest scripts/tests/test_hook_tools_adopted.py -q
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT / "hooks"))

import fan_out  # noqa: E402
import tool_version  # noqa: E402
import tool_versions  # noqa: E402
from repin import Refusal  # noqa: E402

# The tools `hooks/tool_version.py` asserts. Read from it, so this cannot drift;
# `test_hook_tools_adopted.py` also holds it equal to `versions_parity.HOOK_ASSERTED`.
HOOK_ASSERTED = tuple(tool_version.TOOLS)
VERSIONS = "versions.yaml"
GIT_TIMEOUT = 60


# --------------------------------------------------------------------------
# versions.yaml
# --------------------------------------------------------------------------


def _read(text):
    """(outputs, accepted) of one `versions.yaml` text, through the one reader."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / VERSIONS
        path.write_text(text, encoding="utf-8")
        try:
            return tool_versions.read(path), tool_versions.accepted(path)
        except tool_versions.ReadError as error:
            raise Refusal(str(error).replace(str(path), VERSIONS)) from error


def moves(base_text, head_text):
    """{tool: (old, new)} for each hook-asserted tool whose `version` changed."""
    base, _ = _read(base_text)
    head, _ = _read(head_text)
    found = {}
    for tool in HOOK_ASSERTED:
        key = tool.replace("-", "_")
        if key in base and key in head and base[key] != head[key]:
            found[tool] = (base[key], head[key])
    return found


def accepts(text, tool, version):
    outputs, accepted = _read(text)
    return outputs.get(tool.replace("-", "_")) == version or version in accepted.get(tool, ())


# --------------------------------------------------------------------------
# the consumers
# --------------------------------------------------------------------------


def load_private(classes):
    """`private_consumers`, checked. A malformed entry is refused, not half-used."""
    entries = classes.get("private_consumers")
    if not isinstance(entries, dict):
        raise Refusal("classes.json: missing `private_consumers`")
    for name, entry in entries.items():
        where = f"classes.json: private_consumers.{name}"
        if not fan_out.NAME.fullmatch(str(name)):
            raise Refusal(f"{where}: not a repository name")
        if not isinstance(entry, dict) or len(entry) != 1:
            raise Refusal(f"{where}: exactly one of `rev` or `exempt`")
        if "rev" in entry:
            if not isinstance(entry["rev"], str) or not fan_out.TAG.fullmatch(entry["rev"]):
                raise Refusal(f"{where}: `rev` must be a plain vX.Y.Z")
        elif "exempt" in entry:
            if not isinstance(entry["exempt"], str) or not entry["exempt"].strip():
                raise Refusal(f"{where}: `exempt` must be a non-empty reason")
        else:
            raise Refusal(f"{where}: exactly one of `rev` or `exempt`")
    return entries


def row(repo, source, rev="", ok=False, why=""):
    return {"repo": repo, "source": source, "rev": rev, "ok": ok, "why": why}


def public_rows(client, owner, classes):
    """(rows with each public consumer's rev, every listed name, private names)."""
    listed = client.list_repos(owner)
    public = [m for m in listed if m.get("private") is False]
    private = {m["name"] for m in listed if m.get("private") is not False
               and fan_out.ineligible(m, owner) is None and m["name"] not in classes["exempt"]
               and len(fan_out.class_of(m.get("topics", []), classes["topic_prefix"])) == 1}
    rows = []
    for name in fan_out.consumers(public, classes, owner):
        branch = next(m["default_branch"] for m in public if m["name"] == name)
        try:
            text = client.config(owner, name, branch)
            if text is None:
                raise Refusal(f"no {fan_out.CONFIG} on `{branch}`")
            rows.append(row(name, "public", fan_out.current_rev(text)))
        except Refusal as refusal:
            rows.append(row(name, "public", why=f"cannot read its rev: {refusal}"))
    return rows, {m.get("name") for m in public}, private


def private_rows(classes, public_names, private_listed):
    entries = load_private(classes)
    required = (set(classes["sweep"]["exclude"]) - public_names) | private_listed | set(entries)
    rows = []
    for name in sorted(required):
        entry = entries.get(name)
        if name in public_names:
            rows.append(row(name, "attested", why=(
                "the public listing reads this repository; delete its "
                "`private_consumers` entry, a measurement wins")))
        elif entry is None:
            rows.append(row(name, "attested", why=(
                "private, so it cannot be read here, and classes.json "
                "`private_consumers` attests no rev or exemption for it")))
        elif "exempt" in entry:
            rows.append(row(name, "exempt", ok=True, why=entry["exempt"]))
        else:
            rows.append(row(name, "attested", entry["rev"]))
    return rows


def judge(r, found, show):
    """Fill `ok` and `why` of a row that carries a rev and no error yet."""
    rev = r["rev"]
    if not fan_out.TAG.fullmatch(rev):
        r["why"] = f"rev {rev!r} is not a plain vX.Y.Z tag"
        return
    text = show(rev)
    if text is None:
        r["why"] = f"yadgarhq/actions carries no {VERSIONS} at `{rev}`"
        return
    try:
        behind = [f"{tool} {new}" for tool, (_, new) in found.items()
                  if not accepts(text, tool, new)]
    except Refusal as refusal:
        r["why"] = f"cannot read {VERSIONS} at `{rev}`: {refusal}"
        return
    r["ok"] = not behind
    r["why"] = f"behind: `{rev}` does not accept {', '.join(behind)}" if behind else "adopted"


def run(base_text, head_text, classes, client, owner, show):
    """(ok, rows, moves). Reads nothing remote unless a hook-asserted tool moved."""
    found = moves(base_text, head_text)
    if not found:
        return True, [], found
    rows, public_names, private_listed = public_rows(client, owner, classes)
    rows += private_rows(classes, public_names, private_listed)
    for r in rows:
        if r["rev"] and not r["why"]:
            judge(r, found, show)
    return all(r["ok"] for r in rows), rows, found


def report(ok, rows, found):
    head = ", ".join(f"{t} {old} -> {new}" for t, (old, new) in found.items())
    if not found:
        return "## Hook-asserted tools (ADR-0983)\n\nNo hook-asserted tool version moves.\n"
    behind = [r for r in rows if not r["ok"]]
    out = [
        "## Hook-asserted tools (ADR-0983)",
        "",
        f"This pull request moves {head}.",
        "",
        (f"{len(rows)} consumers accept it." if ok else
         f"{len(behind)} of {len(rows)} consumers are behind. Release the new version in "
         "`accept` first and let every adoption pull request merge (step 1), or update "
         "`private_consumers` in classes.json."),
        "",
        "| repository | source | rev | verdict |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(f"| `{fan_out.clean(r['repo'], 100)}` | {r['source']} | "
                   f"{fan_out.clean(r['rev'], 60) or '—'} | {fan_out.clean(r['why'])} |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# git and GitHub
# --------------------------------------------------------------------------


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=False, timeout=GIT_TIMEOUT, env=env)


def git_show(cwd, rev, env=None):
    """`versions.yaml` at tag `rev`, or None when this checkout has no such tag or file."""
    done = _git(cwd, "show", f"refs/tags/{rev}:{VERSIONS}", env=env)
    return done.stdout if done.returncode == 0 else None


def sides(cwd, env=None):
    """(base, head) `versions.yaml` texts: the merge commit's first parent, and itself."""
    done = _git(cwd, "rev-list", "--parents", "-n", "1", "HEAD", env=env)
    commits = done.stdout.split()
    if done.returncode != 0 or len(commits) != 3:
        raise Refusal("HEAD is not the pull request's merge commit (two parents); "
                      "pass --base and --head")
    return _show_at(cwd, commits[1], env), _show_at(cwd, commits[0], env)


def _show_at(cwd, commit, env=None):
    done = _git(cwd, "show", f"{commit}:{VERSIONS}", env=env)
    if done.returncode != 0:
        raise Refusal(f"no {VERSIONS} at {commit}: {done.stderr.strip()[:200]}")
    return done.stdout


class Public(fan_out.Gh):
    """The listing, with `private`; and one config on a default branch."""

    def list_repos(self, owner):
        found = []
        for page in self._pages(f"orgs/{fan_out.q(owner)}/repos"):
            found += [{"name": r["name"], "archived": r["archived"], "fork": r["fork"],
                       "disabled": r.get("disabled", True), "private": r.get("private", True),
                       "topics": r.get("topics", []), "default_branch": r["default_branch"],
                       "owner": r["owner"]["login"]} for r in page]
        return found

    def config(self, owner, repo, ref):
        got = self.read_file(owner, repo, fan_out.CONFIG, ref)
        return None if got is None else got[0]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner", required=True)
    parser.add_argument("--classes", default=str(ROOT / "classes.json"))
    parser.add_argument("--base", default="", help="a commit; default: HEAD's first parent")
    parser.add_argument("--head", default="", help="a commit; default: HEAD")
    args = parser.parse_args(argv)
    cwd = ROOT
    try:
        if bool(args.base) != bool(args.head):
            raise Refusal("pass both --base and --head, or neither")
        if args.base:
            base, head = _show_at(cwd, args.base), _show_at(cwd, args.head)
        else:
            base, head = sides(cwd)
        with open(args.classes, encoding="utf-8") as handle:
            classes = fan_out.load_classes(handle.read())
        ok, rows, found = run(base, head, classes, Public(), args.owner,
                              lambda rev: git_show(cwd, rev))
    except (OSError, ValueError, Refusal) as refusal:
        print(f"::error::{fan_out.annotation(refusal)}", file=sys.stderr)
        return 1
    fan_out.summary(report(ok, rows, found))
    if not found:
        print("::notice::no hook-asserted tool version moves; nothing to check (ADR-0983)")
        return 0
    for r in rows:
        if not r["ok"]:
            print(f"::error::{fan_out.annotation(args.owner + '/' + r['repo'] + ': ' + r['why'])}",
                  file=sys.stderr)
    print(f"{sum(r['ok'] for r in rows)} of {len(rows)} consumers accept "
          + ", ".join(f"{t} {n}" for t, (_, n) in found.items()))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
