#!/usr/bin/env python3
"""One bot pull request per consumer moving its Rust version fields. ADR-1120.

`versions.yaml` rust-toolchain on actions main is THE Rust version of the org
(ADR-1118), and ADR-1120 makes every Rust version field equal it: the
`rust-toolchain.toml` channel, every `Cargo.toml` `rust-version` and every
literal workflow `toolchain:` (`rust_version.py` defines the fields and the
rewrite). When a consumer's default branch differs, this opens or refreshes
ONE pull request there, and the sweeper merges it once `ci / passed` is green
(ADR-1106; `sweep.BOTS["rust"]`, B8's line check).

THE COMMIT IS `repin_publish.publish`, on a lane of its own (`LANE`): a commit
through the git data API with NO author and NO committer, so GitHub signs it
as the App with `web-flow` as committer, checked before and after the ref
moves; the branch created, or moved by compare-and-swap; one pull request per
branch; every other open pull request the App opened on a `rust-bump/` head
closed as superseded, its branch kept. The parent is the consumer's default tip
read through the API, so the commit is never behind when it is made.

ONE BRANCH PER VERSION, `rust-bump/X.Y.Z`. The sweeper deletes a bot branch
after the merge, and a `branch_deletion` on a name fails its B9 if that name
is proposed again; a version names a new branch each time. A pull request a
person CLOSED is never proposed again for that version (`declined`, a
warning). One that MERGED while the default branch differs again is an error
row: something moved the fields back.

WHICH REPOSITORIES. Every public repository of the owner that is live, not a
fork and not in `classes.json` `sweep.exclude`, with a `Cargo.toml` at the root
of its default branch. Listed with `GITHUB_TOKEN`, which sees public
repositories only: the sweeper merges public ones only (R1), so a private one
would carry a pull request nobody merges. The write token is then minted for
exactly those names; an empty list is a refusal, because an empty
`repositories:` beside `owner:` mints for every repository.

DRY UNTIL ARMED. `classes.json` `"rust_bump": {"live": true}` arms the schedule
and the push trigger; it ships false. A `workflow_dispatch` from main with
`dry_run` false is the explicit human path. A dry run reads with a read-only
token and writes nothing.

Commands:

    rust_propagate.py mode                 EVENT, DRY_RUN, REF -> `live=true|false`
    rust_propagate.py enumerate --owner O --out F    GITHUB_TOKEN -> `repos=a,b,c`
    rust_propagate.py run --owner O --repos F [--live]   GH_TOKEN is the App token

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import base64
import json
import os
import re
import sys

import fan_out
import repin
import repin_publish
import rust_version
import tool_versions
from repin import Refusal

BRANCH = re.compile(r"rust-bump/[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
TEMPLATE = ".github/pull_request_template.md"
MAX_FILES = 200  # Rust version files one repository may carry, then a refusal


def _check_files(files):
    bad = sorted(p for p in files if rust_version.kind_of(p) is None)
    if not files or bad:
        raise Refusal(f"a Rust version bump commits Rust version files only; got {sorted(files)}")


LANE = repin_publish.Lane(BRANCH, "rust-bump/", _check_files, "rust-bump")


def branch(version):
    name = f"rust-bump/{version}"
    if not isinstance(version, str) or not BRANCH.fullmatch(name):
        raise Refusal(f"the Rust version {version!r} is not X.Y.Z")
    return name


def title(version):
    return f"chore(deps): bump the Rust version to {version} (ADR-1120)"


def body(template, version, repository, paths):
    """From the template's own headings, as `repin.body` builds it. Nothing
    outside `## Changelog` looks like a bullet: `commit_entries` reads every
    line of a squash message."""
    found = repin.headings(template)
    missing = [h for h in repin.REQUIRED if h not in [t for _, t in found]]
    if missing:
        raise Refusal(f"the pull request template is missing {', '.join(missing)}; "
                      "no body built from it can pass `ci / template`")
    listed = ", ".join(f"`{p}`" for p in paths)
    filled = {
        "What": [f"Moves every Rust version field of `{repository}` to `{version}`, the "
                 f"`rust-toolchain` value in yadgarhq/actions `versions.yaml` on main. "
                 f"Files: {listed}. Only `channel`, `rust-version` and literal workflow "
                 "`toolchain:` lines change."],
        "Why": ["ADR-1118: one Rust version org-wide, the latest stable. ADR-1120: the MSRV "
                "equals it, and a new value in `versions.yaml` opens this pull request "
                "automatically (the `rust-bump` workflow in yadgarhq/actions). The sweeper "
                "merges it once `ci / passed` is green (ADR-1106)."],
        "Changelog": [f"- chore(deps): bump the Rust version to {version} (ADR-1120)"],
        "Verification": ["`ci / passed` on this pull request is the verification: the build, "
                         "the tests and clippy run on the new toolchain. Nothing was run before "
                         "it was opened."],
        "Risk": ["A newer MSRV enables clippy lints gated on it, so this can go red under "
                 "`-D warnings`; a bot cannot fix that, and a person must. Undo by reverting "
                 "the one commit. D80: this renders no resource and touches no trust "
                 "boundary."],
    }
    out = []
    for hashes, heading in found:
        out += [f"{hashes} {heading}", ""]
        out += filled.get(heading, ["Nothing specific to an automated Rust version bump."])
        out += [""]
    return "\n".join(out).rstrip() + "\n"


def one(client, repository, version, live, org_template):
    """One consumer. Returns a row; raises Refusal for an error row."""
    br = branch(version)
    meta = client.repo()
    tip = client.branch_sha(meta["default_branch"])
    files = client.rust_files(tip)
    row = {"repo": repository, "status": "", "url": "", "paths": [], "closed": []}
    if "Cargo.toml" not in files:
        return dict(row, status="not rust")
    try:
        moved = rust_version.changes(files, version)
    except rust_version.Refusal as refusal:
        raise Refusal(str(refusal)) from None
    if not moved:
        return dict(row, status="current")
    row["paths"] = sorted(moved)
    state = repin_publish.decide(client, br)
    if state == "merged":
        raise Refusal(f"the pull request on `{br}` merged, yet the default branch differs from "
                      f"{version} again (drift came back). Fix it by hand.")
    if state == "suppressed":
        print(f"::warning::{repository}: a person closed the pull request on `{br}`, so it is "
              "not proposed again. Reopen it, or move the fields by hand.")
        return dict(row, status="declined")
    if not live:
        open_pr = any(p["state"] == "open" for p in client.pulls(br))
        return dict(row, status="would refresh" if open_pr else "would open")
    template = client.template_at(tip) or org_template()
    got = repin_publish.publish(client, br, title(version),
                                body(template, version, repository, row["paths"]), tip,
                                {p: t.encode() for p, t in moved.items()}, lane=LANE)
    return dict(row, status=got["status"], url=got["url"], closed=got["closed"])


def candidates(repos, classes, owner):
    """Public, live, not a fork, not excluded. A listing missing `private`
    reads as private (fail closed)."""
    exclude = classes.get("sweep", {}).get("exclude", {})
    return sorted(m["name"] for m in repos
                  if fan_out.ineligible(m, owner) is None and m.get("private", True) is False
                  and m["name"] not in exclude)


def enumerate_rust(lister, owner, classes):
    repos = lister.list_repos(owner)
    by_name = {m["name"]: m for m in repos if isinstance(m, dict) and "name" in m}
    names = [n for n in candidates(repos, classes, owner)
             if lister.read_file(owner, n, "Cargo.toml", by_name[n]["default_branch"]) is not None]
    if not names:
        raise Refusal("no public repository carries a Cargo.toml; an empty `repositories:` "
                      "beside `owner:` mints for every repository, so this is a refusal")
    if len(names) > fan_out.MAX_CONSUMERS:
        raise Refusal(f"{len(names)} repositories, over the cap of {fan_out.MAX_CONSUMERS}")
    return names


def is_live(event, dry_run, ref, armed_):
    if event in ("schedule", "push"):
        return bool(armed_) and ref == "refs/heads/main"
    if event == "workflow_dispatch":
        return dry_run == "false" and ref == "refs/heads/main"
    return False


def armed(classes):
    section = classes.get("rust_bump")
    if not isinstance(section, dict) or not isinstance(section.get("live"), bool):
        raise Refusal("classes.json: `rust_bump.live` must be true or false")
    return section["live"]


def read_version(path="versions.yaml"):
    try:
        version = tool_versions.read(path)["rust_toolchain"]
    except (tool_versions.ReadError, KeyError) as exc:
        raise Refusal(f"{path}: no rust-toolchain version ({exc})") from None
    branch(version)
    return version


# --------------------------------------------------------------------------
# GitHub, through `gh api` with the App's token (GH_TOKEN)
# --------------------------------------------------------------------------


class Gh(repin_publish.Gh):
    """`repin_publish.Gh` on the rust lane, plus the reads this needs."""

    def __init__(self, owner, repo):
        super().__init__(owner, repo, lane=LANE)

    def rust_files(self, sha):
        got = self._json(f"{self.at}/git/trees/{repin_publish.q(sha, '')}?recursive=1")
        if got.get("truncated") is not False:
            raise Refusal(f"the tree of {sha} is truncated (or unreadable); refused")
        wanted = [e for e in got["tree"]
                  if e.get("type") == "blob" and rust_version.kind_of(e.get("path", ""))]
        if len(wanted) > MAX_FILES:
            raise Refusal(f"{len(wanted)} Rust version files, over the cap of {MAX_FILES}")
        out = {}
        for entry in wanted:
            if entry.get("size", rust_version.MAX_BYTES + 1) > rust_version.MAX_BYTES:
                raise Refusal(f"{entry['path']} is over {rust_version.MAX_BYTES} bytes")
            blob = self._json(f"{self.at}/git/blobs/{repin_publish.q(entry['sha'], '')}")
            out[entry["path"]] = base64.b64decode(blob["content"]).decode("utf-8")
        return out

    def template_at(self, sha):
        got = self.read_file(self.owner, self.name, TEMPLATE, sha)
        return got[0] if got else None


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def report(rows, version, live):
    lines = [f"## rust-bump propagation to `{version}` ({'LIVE' if live else 'dry run'})", "",
             "| repository | status | files | pull request |", "|---|---|---|---|"]
    for row in rows:
        files = ", ".join(f"`{p}`" for p in row.get("paths", []))
        lines.append(f"| {row['repo']} | {row['status']} | {files} | {row.get('url', '')} |"
                     + (f" {fan_out.clean(row['note'])}" if row.get("note") else ""))
    repin_publish.summary("\n".join(lines))


def do_run(args):
    version = read_version(args.versions)
    names = [n for n in load(args.repos) if fan_out.NAME.fullmatch(n)]
    rows = []
    for name in names:
        try:
            rows.append(one(Gh(args.owner, name), f"{args.owner}/{name}", version, args.live,
                            repin.org_template))
        except fan_out.Unverified:
            raise
        except Refusal as refusal:
            rows.append({"repo": f"{args.owner}/{name}", "status": "error",
                         "note": str(refusal)[:300]})
            print(f"::error::{args.owner}/{name}: {fan_out.annotation(str(refusal))}")
    report(rows, version, args.live)
    return 1 if any(r["status"] == "error" for r in rows) else 0


def main(argv):
    parser = argparse.ArgumentParser(prog="rust_propagate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    mode = sub.add_parser("mode")
    mode.add_argument("--classes", default="classes.json")
    enum = sub.add_parser("enumerate")
    enum.add_argument("--owner", required=True)
    enum.add_argument("--classes", default="classes.json")
    enum.add_argument("--out", required=True)
    go = sub.add_parser("run")
    go.add_argument("--owner", required=True)
    go.add_argument("--repos", required=True)
    go.add_argument("--live", action="store_true")
    go.add_argument("--versions", default="versions.yaml")
    args = parser.parse_args(argv)
    try:
        if args.command == "mode":
            live = is_live(os.environ.get("EVENT", ""), os.environ.get("DRY_RUN", ""),
                           os.environ.get("REF", ""), armed(load(args.classes)))
            fan_out.output("live", "true" if live else "false")
            print(f"live={'true' if live else 'false'}")
            return 0
        if args.command == "enumerate":
            names = enumerate_rust(fan_out.Gh(), args.owner, load(args.classes))
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(names, handle)
            fan_out.output("repos", ",".join(names))
            print(f"{len(names)} Rust repositories: {', '.join(names)}")
            return 0
        return do_run(args)
    except Refusal as refusal:
        print(f"::error::{fan_out.annotation(str(refusal))}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
