#!/usr/bin/env python3
"""A new stable Rust opens ONE yadgarhq/actions pull request. ADR-1120 part (b).

ADR-1118 made `versions.yaml` rust-toolchain THE Rust version of the org, the
latest stable release. `rust_propagate.py` carries a new value out to every
consumer. This is the half before it: it notices the new release upstream and
proposes the new value in yadgarhq/actions itself.

THE DETECTION. `static.rust-lang.org/dist/channel-rust-stable.toml` names the
stable release (`[pkg.rust] version`). When it is newer than `versions.yaml`
rust-toolchain at the tip of actions main, AND Docker Hub serves both
`rust:X.Y.Z-bookworm` and `rust:X.Y-bookworm` as the SAME multi-arch index (so
the minor tag the build base names is that exact release), this opens or
refreshes ONE signed App pull request on `rust-release/X.Y.Z` that changes
exactly three lines:

    versions.yaml                          the rust-toolchain entry: `version`
                                           becomes X.Y.Z and `previous` the old one
    containers/rust-build/Containerfile    the `rust:` base: X.Y-bookworm at the
                                           index digest
    containers/estate-runner/Containerfile the rustup default toolchain

Until the image is published, the run says so and writes nothing; the daily
schedule asks again. The sweeper merges the pull request once `ci / passed` is
green (`sweep.py`, kind `rust-release`, B8 by line).

THE GRACE WINDOW. `previous` is what keeps consumer pull requests green between
that merge and each consumer's own rust-bump merge: ci-pr's `rust_parity`
passes a field still at exactly `previous` that the pull request did not move
(`rust_version.py`). It closes itself. When a propagation run finds every Rust
repository current, this opens `rust-release/X.Y.Z-end`, which deletes
`previous` and nothing else. While `previous` is present, no new release is
proposed: one window at a time. A consumer whose rust-bump a person declined
keeps the window open, and the step summary says so.

THE FILES ARE READ AT THE TIP of actions main through the API, never from the
workflow checkout, because `repin_publish.publish` lays them over the tip's
tree: texts from an older checkout would revert whatever main changed since.

NO VERSION LITERAL HERE. `versions_parity.py` counts every site that pins the
Rust version; this file builds its lines from parts, so it adds no site.

DRY UNLESS ARMED. `classes.json` `rust_bump.release_live` arms it, and it ships
false. A dispatch with `dry_run` false does NOT bypass that: live needs the
switch AND main. The first live detection is the first run after a reviewed
pull request sets it true.

Commands:

    rust_release.py mode                 EVENT, DRY_RUN, REF -> `live=true|false`
    rust_release.py run --owner O [--live]   GH_TOKEN (App, actions only), ALL_CURRENT

Tests: python3 -m pytest scripts/tests/ -q
"""

import argparse
import http.client
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request

import fan_out
import repin
import repin_publish
import rust_version
from repin import Refusal

CHANNEL_URL = "https://static.rust-lang.org/dist/channel-rust-stable.toml"
TOKEN_URL = ("https://auth.docker.io/token?service=registry.docker.io"
             "&scope=repository:library/rust:pull")
MANIFESTS = "https://registry-1.docker.io/v2/library/rust/manifests/"
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
TIMEOUT = 30
MAX_CHANNEL = 4 * 1024 * 1024

VERSIONS = "versions.yaml"
RUST_BUILD = "containers/rust-build/Containerfile"
ESTATE_RUNNER = "containers/estate-runner/Containerfile"
FILES = (VERSIONS, RUST_BUILD, ESTATE_RUNNER)
TEMPLATE = ".github/pull_request_template.md"

XYZ = r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}"
END = "-end"
BRANCH = re.compile(rf"rust-release/{XYZ}(?:{END})?")
ENTRY = re.compile(
    rf'  rust-toolchain: \{{ version: "({XYZ})", sites: ([1-9][0-9]{{0,2}})(?:, previous: "({XYZ})")? \}}')
ENTRY_KEY = "  rust-toolchain:"
IMAGE = re.compile(r"FROM docker\.io/library/rust:([0-9]{1,4}\.[0-9]{1,4})-bookworm@(sha256:[0-9a-f]{64})")
IMAGE_KEY = "library/rust:"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
FLAG = "--default-toolchain"


def _check_files(files):
    if VERSIONS not in files or not set(files) <= set(FILES):
        raise Refusal(f"a Rust release commit changes {VERSIONS} and only {', '.join(FILES)}; "
                      f"got {sorted(files)}")


LANE = repin_publish.Lane(BRANCH, "rust-release/", _check_files, "rust-bump")


# --------------------------------------------------------------------------
# the lines, built and read
# --------------------------------------------------------------------------


def key(version):
    return rust_version.key(version)


def minor(version):
    return ".".join(version.split(".")[:2])


def entry(version, sites, previous=None):
    """The one versions.yaml line, exactly as `tool_versions.py` reads it."""
    tail = f', previous: "{previous}"' if previous else ""
    return f'{ENTRY_KEY} {{ version: "{version}", sites: {sites}{tail} }}'


def image(version_minor, digest):
    return "FROM docker.io/" + IMAGE_KEY + version_minor + "-bookworm@" + digest


def branch(version, end=False):
    name = f"rust-release/{version}{END if end else ''}"
    if not isinstance(version, str) or not BRANCH.fullmatch(name):
        raise Refusal(f"the Rust version {version!r} is not X.Y.Z")
    return name


def _only(lines, hit, what):
    found = [i for i, line in enumerate(lines) if hit(line)]
    if len(found) != 1:
        raise Refusal(f"{what}: {len(found)} matching lines, not exactly one")
    return found[0]


def read_entry(text):
    """`(index, version, sites, previous)` of the rust-toolchain line. Exactly
    one line starts with the key, and it is the shape this writes."""
    lines = text.split("\n")
    i = _only(lines, lambda line: line.startswith(ENTRY_KEY), f"{VERSIONS} rust-toolchain")
    got = ENTRY.fullmatch(lines[i])
    if not got:
        raise Refusal(f"{VERSIONS}: the rust-toolchain line is not "
                      f'`{ENTRY_KEY} {{ version: "X.Y.Z", sites: N[, previous: "X.Y.Z"] }}`')
    version, sites, previous = got.groups()
    if previous is not None and key(previous) >= key(version):
        raise Refusal(f"{VERSIONS}: previous {previous} is not older than {version}")
    return i, version, sites, previous


def bump(files, new, digest):
    """`{path: new text}` moving the three sites from versions.yaml's version
    to `new`, and setting `previous` to the version it replaces."""
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Refusal(f"the image digest {digest!r} is not sha256 and 64 hex")
    i, old, sites, previous = read_entry(files[VERSIONS])
    if previous is not None:
        raise Refusal(f"{VERSIONS} still carries previous {previous}: the last window is open")
    if key(new) <= key(old):
        raise Refusal(f"{new} is not newer than {VERSIONS} rust-toolchain {old}")
    out = {}
    lines = files[VERSIONS].split("\n")
    lines[i] = entry(new, sites, old)
    out[VERSIONS] = "\n".join(lines)

    lines = files[RUST_BUILD].split("\n")
    j = _only(lines, lambda line: IMAGE_KEY in line, f"{RUST_BUILD} rust base")
    got = IMAGE.fullmatch(lines[j])
    if not got or got.group(1) != minor(old):
        raise Refusal(f"{RUST_BUILD}: the rust base line is not `{minor(old)}-bookworm` at a digest")
    lines[j] = image(minor(new), digest)
    out[RUST_BUILD] = "\n".join(lines)

    lines = files[ESTATE_RUNNER].split("\n")
    was = f"{FLAG} {old} "
    k = _only(lines, lambda line: FLAG in line, f"{ESTATE_RUNNER} {FLAG}")
    if lines[k].count(was) != 1 or lines[k].count(FLAG) != 1:
        raise Refusal(f"{ESTATE_RUNNER}: the {FLAG} line does not name {old} once")
    lines[k] = lines[k].replace(was, f"{FLAG} {new} ")
    out[ESTATE_RUNNER] = "\n".join(lines)
    return out


def end(files):
    """`{versions.yaml: text}` with `previous` deleted from the entry, and
    nothing else."""
    i, version, sites, previous = read_entry(files[VERSIONS])
    if previous is None:
        raise Refusal(f"{VERSIONS} carries no previous; there is no window to end")
    lines = files[VERSIONS].split("\n")
    lines[i] = entry(version, sites)
    return {VERSIONS: "\n".join(lines)}


# --------------------------------------------------------------------------
# upstream: the channel and the registry
# --------------------------------------------------------------------------


def stable(text):
    """`[pkg.rust] version` of channel-rust-stable.toml, as X.Y.Z."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise Refusal(f"the stable channel is not TOML: {exc}") from None
    rust = (data.get("pkg") or {}).get("rust") if isinstance(data.get("pkg"), dict) else None
    value = rust.get("version") if isinstance(rust, dict) else None
    head = value.split(" ", 1)[0] if isinstance(value, str) else None
    if not head or not re.fullmatch(XYZ, head):
        raise Refusal(f"the stable channel names no X.Y.Z rust version: {value!r}")
    return head


class Net:
    """The two upstreams, through urllib. Tests replace `open`."""

    def __init__(self, opener=urllib.request.urlopen):
        self.open = opener
        self._token = None

    def channel(self):
        with self.open(urllib.request.Request(CHANNEL_URL), timeout=TIMEOUT) as answer:
            body = answer.read(MAX_CHANNEL + 1)
        if len(body) > MAX_CHANNEL:
            raise Refusal("the stable channel is larger than expected")
        return body.decode("utf-8")

    def token(self):
        if self._token is None:
            with self.open(urllib.request.Request(TOKEN_URL), timeout=TIMEOUT) as answer:
                got = json.loads(answer.read(65536))
            if not isinstance(got, dict) or not isinstance(got.get("token"), str):
                raise Refusal("Docker Hub gave no pull token")
            self._token = got["token"]
        return self._token

    def index(self, reference):
        """The index digest Docker Hub serves for `library/rust:<reference>` (a
        tag, or a digest), or None when it serves nothing there. Anything that
        is not an image index is a refusal: a single-arch digest would lock
        every build to one architecture."""
        request = urllib.request.Request(
            MANIFESTS + reference, method="HEAD",
            headers={"Authorization": f"Bearer {self.token()}", "Accept": ", ".join(INDEX_TYPES)})
        try:
            with self.open(request, timeout=TIMEOUT) as answer:
                kind = (answer.headers.get("Content-Type") or "").split(";")[0].strip()
                digest = answer.headers.get("Docker-Content-Digest") or ""
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise Refusal(f"Docker Hub answered {exc.code} for rust:{reference}") from None
        if kind not in INDEX_TYPES:
            raise Refusal(f"rust:{reference} is {kind!r}, not a multi-arch image index")
        if not DIGEST.fullmatch(digest):
            raise Refusal(f"rust:{reference} came with no sha256 digest")
        return digest


def resolve(net, version):
    """`(digest, None)` once `rust:X.Y.Z-bookworm` and `rust:X.Y-bookworm` are
    one index; else `(None, why)`."""
    exact = net.index(f"{version}-bookworm")
    if exact is None:
        return None, f"rust:{version}-bookworm is not on Docker Hub yet"
    loose = net.index(f"{minor(version)}-bookworm")
    if loose != exact:
        return None, (f"rust:{minor(version)}-bookworm is not yet the {version} index "
                      f"({loose} != {exact})")
    return exact, None


# --------------------------------------------------------------------------
# one run
# --------------------------------------------------------------------------


def decide(versions_text, upstream, all_current):
    """`(action, version, why)`: `end`, `wait`, `bump` or `current`."""
    _, version, _, previous = read_entry(versions_text)
    if previous is not None:
        if all_current:
            return "end", version, f"every Rust repository is at {version}; the window from {previous} ends"
        return "wait", version, (f"the window from {previous} to {version} is open: a Rust repository "
                                 "is not current yet (a declined rust-bump keeps it open)")
    if key(upstream) > key(version):
        return "bump", upstream, f"stable Rust {upstream} is newer than {version}"
    return "current", version, f"versions.yaml is at {version}; stable is {upstream}"


def title(action, version):
    if action == "end":
        return f"chore(deps): end the Rust {version} grace window (ADR-1120)"
    return f"chore(deps): bump the org Rust version to {version} (ADR-1120)"


def body(template, action, version, old, digest):
    found = repin.headings(template)
    missing = [h for h in repin.REQUIRED if h not in [t for _, t in found]]
    if missing:
        raise Refusal(f"the pull request template is missing {', '.join(missing)}; "
                      "no body built from it can pass `ci / template`")
    if action == "end":
        what = (f"Deletes `previous: \"{old}\"` from the `versions.yaml` rust-toolchain entry. "
                "That one line is the whole change.")
        why = ("A propagation run found every public Rust repository at "
               f"`{version}`, so the grace window (ADR-1120) is no longer needed: from this "
               f"merge on, `rust_parity` again refuses any field that is not `{version}`.")
        risk = ("A consumer that drifted back to the old version after the propagation run "
                "reds `ci / passed` again, which is the rule before the window. Undo by "
                "reverting the one commit.")
    else:
        what = (f"Moves the org Rust version from `{old}` to `{version}` (stable on "
                "static.rust-lang.org): the `versions.yaml` rust-toolchain entry, which also "
                f"gains `previous: \"{old}\"`; the `rust-build` base to "
                f"`rust:{minor(version)}-bookworm@{digest}`, the multi-arch index Docker Hub "
                f"serves for `rust:{version}-bookworm`; and the estate runner's rustup default "
                "toolchain. Three lines.")
        why = ("ADR-1118: one Rust version org-wide, the latest stable. ADR-1120: a new stable "
               "release opens this pull request automatically. Once it merges, the `rust-bump` "
               "workflow opens one pull request per Rust repository; `previous` keeps their other "
               "pull requests green until each one merges.")
        risk = ("CI and both images move to a new compiler. New clippy lints can red a consumer's "
                "rust-bump pull request; a person fixes those. Undo by reverting the one commit "
                "before the consumers merge their bumps.")
    filled = {
        "What": [what],
        "Why": [why],
        "Changelog": [f"- {title(action, version)}"],
        "Verification": ["`ci / passed` on this pull request: `versions_parity` holds the three "
                         "sites to one value and the suite runs on it. Nothing was run before it "
                         "was opened."],
        "Risk": [risk + " D80: this renders no resource and touches no trust boundary."],
    }
    out = []
    for hashes, heading in found:
        out += [f"{hashes} {heading}", ""]
        out += filled.get(heading, ["Nothing specific to an automated Rust release bump."])
        out += [""]
    return "\n".join(out).rstrip() + "\n"


def run(client, net, all_current, live, org_template):
    """One run against actions main. Returns a row; raises Refusal for an error."""
    meta = client.repo()
    tip = client.branch_sha(meta["default_branch"])
    files = client.files_at(tip)
    upstream = stable(net.channel())
    action, version, why = decide(files[VERSIONS], upstream, all_current)
    row = {"action": action, "version": version, "why": why, "status": "", "url": ""}
    if action in ("wait", "current"):
        return dict(row, status="nothing to do")
    _, old, _, previous = read_entry(files[VERSIONS])
    digest = None
    if action == "bump":
        digest, note = resolve(net, version)
        if digest is None:
            return dict(row, status="image not ready", why=note)
        moved = bump(files, version, digest)
    else:
        moved, old = end(files), previous
    br = branch(version, end=action == "end")
    state = repin_publish.decide(client, br)
    if state == "merged":
        raise Refusal(f"the pull request on `{br}` merged, yet versions.yaml says otherwise. "
                      "Fix it by hand.")
    if state == "suppressed":
        print(f"::warning::a person closed the pull request on `{br}`, so it is not proposed "
              "again. Reopen it, or make the change by hand.")
        return dict(row, status="declined")
    if not live:
        open_pr = any(p["state"] == "open" for p in client.pulls(br))
        return dict(row, status="would refresh" if open_pr else "would open")
    template = client.template_at(tip) or org_template()
    got = repin_publish.publish(client, br, title(action, version),
                                body(template, action, version, old, digest), tip,
                                {p: t.encode() for p, t in moved.items()}, lane=LANE)
    return dict(row, status=got["status"], url=got["url"])


def is_live(event, dry_run, ref, armed_):
    """Live needs the switch, main, and a trigger that is not a dry dispatch."""
    if not armed_ or ref != "refs/heads/main":
        return False
    if event in ("schedule", "push"):
        return True
    return event == "workflow_dispatch" and dry_run == "false"


def armed(classes):
    section = classes.get("rust_bump")
    if not isinstance(section, dict) or not isinstance(section.get("release_live"), bool):
        raise Refusal("classes.json: `rust_bump.release_live` must be true or false")
    return section["release_live"]


class Gh(repin_publish.Gh):
    """`repin_publish.Gh` on the rust-release lane, plus the reads this needs."""

    def __init__(self, owner):
        super().__init__(owner, fan_out.ACTIONS_REPO, lane=LANE)

    def files_at(self, sha):
        out = {}
        for path in FILES:
            got = self.read_file(self.owner, self.name, path, sha)
            if got is None:
                raise Refusal(f"{path} is missing at {sha}")
            out[path] = got[0]
        return out

    def template_at(self, sha):
        got = self.read_file(self.owner, self.name, TEMPLATE, sha)
        return got[0] if got else None


def report(row, live):
    lines = [f"## Rust release detector ({'LIVE' if live else 'dry run'})", "",
             f"- action: `{row['action']}` at `{row['version']}`",
             f"- status: {row['status']}", f"- why: {fan_out.clean(row['why'])}"]
    if row.get("url"):
        lines.append(f"- pull request: {row['url']}")
    repin_publish.summary("\n".join(lines))


def main(argv):
    parser = argparse.ArgumentParser(prog="rust_release.py")
    sub = parser.add_subparsers(dest="command", required=True)
    mode = sub.add_parser("mode")
    mode.add_argument("--classes", default="classes.json")
    go = sub.add_parser("run")
    go.add_argument("--owner", required=True)
    go.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "mode":
            with open(args.classes, encoding="utf-8") as handle:
                live = is_live(os.environ.get("EVENT", ""), os.environ.get("DRY_RUN", ""),
                               os.environ.get("REF", ""), armed(json.load(handle)))
            fan_out.output("live", "true" if live else "false")
            print(f"live={'true' if live else 'false'}")
            return 0
        row = run(Gh(args.owner), Net(), os.environ.get("ALL_CURRENT") == "true", args.live,
                  repin.org_template)
        report(row, args.live)
        print(f"{row['action']} {row['version']}: {row['status']} ({row['why']})")
        return 0
    except Refusal as refusal:
        print(f"::error::{fan_out.annotation(str(refusal))}")
        return 1
    except (OSError, ValueError, http.client.HTTPException) as exc:  # an upstream that did not answer, or answered garbage
        print(f"::error::{fan_out.annotation(f'upstream read failed: {exc}')}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
