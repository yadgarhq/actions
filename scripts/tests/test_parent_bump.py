"""What `parent_bump.py` writes into the parent chart, and what it REFUSES.

THE RELEASE PATH CANNOT BE RUN, WHICH IS THE WHOLE REASON THIS FILE IS LONG.
`ci-release.yaml` is tag-triggered, so the first execution of any change to it is
post-merge in a consumer repository, against a real published module chart and a
real parent. Nothing here can fire one — and the estate's answer to that is
already written down twice: `chart_publicly_pullable.py` takes an injectable
`fetch`, and `ci-release.yaml` says "A SCRIPT RATHER THAN SHELL, because shell in
this file is untestable by anything this repository runs". So `parent_bump.py`
takes an injectable `run`, and every case below executes the real code against a
stub that models the API's own refusals.

THE STUB MODELS THE BLOB SHA RATHER THAN ACCEPTING EVERY WRITE, and that is what
makes the concurrency cases real rather than decorative. A PUT carrying a sha
that is not the one the stub currently holds is REFUSED, the way the Contents API
refuses one, so `test_a_lost_content_race_keeps_both_pins` is a genuine
read-modify-write race and not a rehearsal of one.

THE THREE LOAD-BEARING CASES, named because the rest of the file supports them:

  `test_a_lost_content_race_keeps_both_pins` — the worst outcome available to
  this feature is a LOST PIN, because the parent then publishes a set it does not
  carry and nothing downstream reports it. Two modules rewrite one file; this
  asserts the file that lands carries both.

  `test_two_module_releases_cut_two_parent_versions` — ADR-0722 says a module
  release cuts a NEW parent version, so two releases must produce two. A merged
  one would satisfy "the newest parent pins the newest set" and still break the
  rule.

  `test_the_commit_message_makes_the_parent_derive_nothing` — the pin lands on
  the parent's `main`, which runs the parent's own CI, which derives a release
  from Changelog bullets. This feeds the real commit message to the real
  `next_version.derive` and demands NO version. Without it, one module release
  would cut two parent versions and the second would be invisible here.

Run: python3 -m pytest scripts/tests/ -q
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
from collections import namedtuple

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ci_verdict  # noqa: E402
import next_version  # noqa: E402
import parent_bump  # noqa: E402
import parent_pin  # noqa: E402
import pr_body  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"

# THE AUTHOR GITHUB STAMPS ON A CONTENTS API WRITE BY THIS APP, read off the
# twelve newest commits of `yadgarhq/argocd` on 2026-09-19 rather than guessed.
# Those commits are made by the same App through the same endpoint, so this is
# the string the parent's own `next_version.py` will see in `%an <%ae>`.
BOT = "yadgarhq-bot[bot] <324366854+yadgarhq-bot[bot]@users.noreply.github.com>"

OWNER = "yadgarhq"
REPO = "yadgarhq/gateway"

# THE EIGHT REAL PINS, the same fixture `test_parent_pin.py` uses and for the same
# reason: six of the eight are wrong under a lexical maximum.
PINS = {
    "config": "0.1.5",
    "gateway": "0.9.48",
    "iam": "0.8.39",
    "iam-db": "0.7.40",
    "project": "0.1.17",
    "project-db": "0.3.5",
    "task": "0.5.28",
    "task-db": "0.6.29",
}

HEAD = "apiVersion: v2\nname: yadgar\nversion: 0.1.0\n\n"
PATH = "chart/Chart.yaml"

Done = namedtuple("Done", "returncode stdout stderr")


def chart(pins=None):
    lines = [HEAD, "dependencies:\n"]
    for name, version in (pins or PINS).items():
        lines.append(f"  - name: {name}\n")
        lines.append(f"    version: {version}\n")
        lines.append("    repository: oci://ghcr.io/yadgarhq/charts\n")
    return "".join(lines)


class Api:
    """An argv-dispatching `gh` stub holding commits, trees, one `main` and the tags.

    `main` MOVES ONLY BY FAST-FORWARD unless the caller forces it, which is how
    GitHub answers `PATCH git/refs/heads/main` with `force: false`. That is what
    makes the concurrency cases a real read-modify-write race: a writer who read
    `main` before somebody else landed is refused, re-reads, and writes again.
    """

    def __init__(self, main=None, tags=None, published=None, examples=None,
                 interfere=None, interfere_ref=None):
        extra = dict(examples or {})
        # THE TAGS ARE ANCESTORS OF `main`, oldest first, the way every cut tag
        # in the parent is: `commit-0` is the one commit since the newest of them.
        self.trees, self.parents, self.meta, self.tagged = {}, {}, {}, {}
        previous = None
        for tag in tags if tags is not None else ["v0.1.0"]:
            sha = f"commit-{tag}"
            self.trees[sha] = {
                PATH: published if published is not None else chart(), **extra
            }
            self.parents[sha], self.meta[sha] = previous, f"ci: {tag}"
            self.tagged[tag], previous = sha, sha
        self.trees["commit-0"] = {PATH: main if main is not None else chart(), **extra}
        self.parents["commit-0"] = previous
        self.meta["commit-0"] = "ci: pin a module in the parent chart"
        self.main = "commit-0"
        self.made = 0
        self.objects = 0
        self.object_commit = {}
        self.calls = []
        self.puts = []
        self.refs = []
        self.patches = []
        self.interfere = interfere
        self.interfere_ref = interfere_ref
        self.put_failures = 0
        self.ref_error = None
        self.unreadable = {}
        self.compare_cap = 250
        self.patch_hooks = {}

    # -- helpers ---------------------------------------------------------------

    @property
    def tags(self):
        return list(self.tagged)

    def publish(self, tag, text, extra=None):
        """Another writer lands `text` on `main` and tags it, the way a winner does."""
        self.land({PATH: text, **(extra or {})})
        self.tagged[tag] = self.main

    def resolve(self, ref):
        if ref == "main":
            return self.main
        return self.tagged.get(ref, ref)

    def text(self, ref="main", path=PATH):
        return self.trees[self.resolve(ref)][path]

    def land(self, files, message="ci: pin another module in the parent chart"):
        """Another commit on `main`: a racing module's pin, or a human merge."""
        sha = f"commit-other-{len(self.parents)}"
        self.trees[sha] = {**self.trees[self.main], **files}
        self.parents[sha] = self.main
        self.meta[sha] = message
        self.main = sha

    def compare(self, path):
        base, head = (self.resolve(r) for r in path.split("/compare/", 1)[1].split("..."))
        chain, sha = [], head
        while sha is not None and sha != base:
            chain.append(sha)
            sha = self.parents[sha]
        status = "identical" if not chain else "ahead" if sha == base else "diverged"
        commits = [{"sha": c, "commit": {"message": self.meta[c]}} for c in reversed(chain)]
        commits = commits[: self.compare_cap]
        return Done(0, json.dumps(
            {"status": status, "total_commits": len(chain), "commits": commits}), "")

    def field(self, args, key):
        for index, arg in enumerate(args):
            if arg.startswith(f"{key}=") and args[index - 1] == "-f":
                return arg[len(key) + 1 :]
        return None

    @staticmethod
    def body(args):
        with open(args[args.index("--input") + 1], encoding="utf-8") as fh:
            return json.load(fh)

    def __call__(self, args):
        self.calls.append(list(args))
        path = next(a for a in args if a.startswith("repos/"))
        method = args[args.index("-X") + 1] if "-X" in args else "GET"

        if "/compare/" in path:
            return self.compare(path)
        if "git/matching-refs/tags/v" in path:
            return Done(0, "".join(f"refs/tags/{t}\n" for t in self.tags), "")
        if path.endswith("git/tags"):
            self.objects += 1
            self.object_commit[f"object-{self.objects}"] = self.field(args, "object")
            return Done(0, f"object-{self.objects}\n", "")
        if path.endswith("git/refs"):
            return self.ref(args)
        if path.endswith("git/refs/heads/main"):
            return self.patch(args)
        if path.endswith("commits/main"):
            return Done(0, f"{self.main}\n", "")
        if "/git/commits/" in path:
            return Done(0, "tree-" + path.rsplit("/", 1)[1] + "\n", "")
        if path.endswith("git/trees"):
            data = self.body(args)
            files = dict(self.trees[data["base_tree"][len("tree-") :]])
            for entry in data["tree"]:
                assert (entry["mode"], entry["type"]) == ("100644", "blob")
                files[entry["path"]] = entry["content"]
            key = f"tree-new-{len(self.trees)}"
            self.trees[key[len("tree-") :]] = files
            return Done(0, key + "\n", "")
        if path.endswith("git/commits") and method == "POST":
            data = self.body(args)
            self.made += 1
            sha = f"commit-{self.made}"
            self.trees[sha] = self.trees[data["tree"][len("tree-") :]]
            self.parents[sha] = data["parents"][0]
            self.meta[sha] = data["message"]
            return Done(0, sha + "\n", "")
        if "/contents/" in path and "?ref=" in path:
            name, ref = path.split("/contents/", 1)[1].split("?ref=", 1)
            if name in self.unreadable:
                return Done(1, "", self.unreadable[name])
            files = self.trees.get(self.resolve(ref))
            if files is None or name not in files:
                return Done(1, "", "gh: Not Found (HTTP 404)")
            return Done(
                0,
                json.dumps(
                    {
                        "sha": f"blob-{ref}",
                        "content": base64.b64encode(
                            files[name].encode("utf-8")
                        ).decode("ascii"),
                    }
                ),
                "",
            )
        raise AssertionError(f"the stub was asked something it does not model: {args}")

    def patch(self, args):
        if self.interfere is not None:
            self.interfere(self)
            self.interfere = None
        hook = self.patch_hooks.pop(len(self.patches), None)
        if hook is not None:
            hook(self)
            self.patches.append({"refused": "moved by a hook"})
            return Done(1, "", "gh: Update is not a fast forward (HTTP 422)")
        if self.put_failures:
            self.put_failures -= 1
            return Done(1, "", "gh: refused by the stub (HTTP 500)")
        data = self.body(args)
        self.patches.append(data)
        sha = data["sha"]
        if not data.get("force") and self.parents[sha] != self.main:
            return Done(1, "", "gh: Update is not a fast forward (HTTP 422)")
        parent = self.trees[self.parents[sha]]
        self.main = sha
        self.puts.append(
            {"sha": sha, "text": self.trees[sha][PATH], "message": self.meta[sha],
             "branch": "main",
             "files": sorted(p for p, v in self.trees[sha].items() if parent.get(p) != v)}
        )
        return Done(0, "{}", "")

    def ref(self, args):
        if self.interfere_ref is not None:
            self.interfere_ref(self)
            self.interfere_ref = None
        name = self.field(args, "ref")
        if self.ref_error is not None:
            return Done(1, "", self.ref_error)
        tag = name[len("refs/tags/") :]
        if tag in self.tagged:
            return Done(1, "", "gh: Reference already exists (HTTP 422)")
        obj = self.field(args, "sha")
        self.tagged[tag] = self.object_commit[obj]
        self.refs.append({"ref": name, "sha": obj})
        return Done(0, "{}", "")


def bump(api, module="gateway", version="0.9.49", monkeypatch=None, repo=REPO):
    """`main()` itself, with the environment the workflow step sets."""
    env = {"OWNER": OWNER, "MODULE": module, "VERSION": version, "REPO": repo}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return parent_bump.main(run=api, sleep=lambda _s: None)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def pins_of(text):
    import parent_pin

    return parent_pin.pins(text)


# ---------------------------------------------------------------------------
# The happy path, and every fact it is supposed to establish.
# ---------------------------------------------------------------------------


def test_the_pin_is_written_and_the_parent_is_tagged():
    api = Api()
    assert bump(api) == 0
    assert len(api.puts) == 1
    assert api.puts[0]["branch"] == "main"
    assert pins_of(api.puts[0]["text"])["gateway"] == "0.9.49"
    # PRE-1.0: a module patch gives the parent a patch, off `next_version.compute`.
    assert api.refs == [{"ref": "refs/tags/v0.1.1", "sha": "object-1"}]


def test_every_other_pin_is_byte_identical():
    """A rewrite that renormalises the file would show as eight pins moving."""
    api = Api()
    assert bump(api) == 0
    before, after = chart().splitlines(), api.puts[0]["text"].splitlines()
    differing = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert len(differing) == 1
    assert len(before) == len(after)


def test_the_tag_points_at_the_commit_that_carries_the_pin():
    """A tag on any other commit publishes a parent without this release in it."""
    api = Api()
    assert bump(api) == 0
    obj = next(c for c in api.calls if c[2].endswith("git/tags"))
    assert "object=commit-1" in obj


def test_the_tag_is_annotated_and_records_what_it_came_from():
    api = Api()
    assert bump(api) == 0
    message = next(
        a[len("message=") :]
        for c in api.calls
        if c[2].endswith("git/tags")
        for a in c
        if a.startswith("message=")
    )
    assert message.startswith("v0.1.1\n")
    assert "gateway 0.9.48 -> 0.9.49" in message
    assert "v0.1.0" in message


def test_a_module_breaking_bump_makes_the_parent_minor_not_major():
    """The ladder is `next_version.compute`, not a second copy of the arithmetic.

    Every patch-on-patch case is the same number under the estate's pre-1.0
    ladder and under ordinary semver, so only this row can tell a reuse from a
    reimplementation. `test_parent_pin.py` pins it for the deciding function; this
    pins that the release path actually reaches that function.
    """
    api = Api()
    assert bump(api, module="gateway", version="0.10.0") == 0
    assert api.refs[0]["ref"] == "refs/tags/v0.2.0"


def test_the_tags_are_listed_unpaginated_and_by_matching_refs():
    """A first page is a truncated list, and a truncated list derives a stale number.

    The parent's cadence is the union of eight module cadences by ADR-0722, so it
    passes a hundred tags sooner than any module does. `gateway` alone carries 74.
    """
    api = Api()
    assert bump(api) == 0
    listing = next(
        c for c in api.calls if any("git/matching-refs/tags/v" in a for a in c)
    )
    assert "--paginate" in listing


# ---------------------------------------------------------------------------
# The parent's own CI must not cut a second version. ADR-0722 says ONE.
# ---------------------------------------------------------------------------


def test_the_commit_message_makes_the_parent_derive_nothing():
    """The real message through the real derivation, demanding no version.

    The pin lands on the parent's `main`, and the parent's `ci.yaml` calls
    `ci-pr.yaml`, whose `version` job runs `next_version.derive` over the range.
    A Changelog bullet in this message would cut a SECOND parent version for one
    module release. `refuse_entries` is green for a bot-authored range that ships
    nothing, so both halves are asserted here rather than in prose.
    """
    api = Api()
    assert bump(api) == 0
    message = api.puts[0]["message"]
    verdict = next_version.derive(
        "v0.1.0", ["v0.1.0"], [message], [BOT], ["chart/Chart.yaml"]
    )
    assert verdict.rc == 0, "\n".join(verdict.lines)
    assert verdict.nxt == ""


def test_the_author_github_stamps_is_one_pr_body_reads_as_a_bot():
    """If it were not, the parent's CI would redden on every module release.

    `refuse_entries` returns rc=1 for a non-bot range with no Changelog, and its
    message says "Cut the intended tag by hand" — advice that would be wrong.
    """
    assert pr_body.is_bot(BOT)


def test_the_commit_message_carries_no_bullet_at_all():
    """The property the derivation test depends on, asserted directly too.

    A bullet that `BULLET` happens not to parse today would pass the test above
    and become a version the day `pr_body.TYPES` grows.
    """
    api = Api()
    assert bump(api) == 0
    for line in api.puts[0]["message"].splitlines():
        assert not pr_body.STARTS_ENTRY.match(line), line


def test_chart_yaml_is_not_a_file_the_estate_calls_shipping():
    """`SHIPS` is what would ADD a synthetic bullet to a bot-only range."""
    assert not next_version.ships(["chart/Chart.yaml"])


# ---------------------------------------------------------------------------
# Concurrency. Eight modules, one file, and a lost pin is the worst outcome.
# ---------------------------------------------------------------------------


def test_a_lost_content_race_keeps_both_pins():
    """The loser re-reads the winner's file and writes one carrying BOTH pins."""

    def winner(api):
        api.land({PATH: chart({**PINS, "task": "0.5.29"})})

    api = Api(interfere=winner)
    assert bump(api) == 0
    landed = pins_of(api.text())
    assert landed["task"] == "0.5.29", "the winner's pin was overwritten"
    assert landed["gateway"] == "0.9.49", "this release's pin was not written"


def test_a_lost_content_race_is_refused_before_it_is_retried():
    """The first PUT must FAIL rather than land, or the race proves nothing."""

    def winner(api):
        api.land({})

    api = Api(interfere=winner)
    assert bump(api) == 0
    assert len(api.puts) == 1
    assert len(api.patches) == 2


def test_a_lost_tag_race_cuts_the_next_version_rather_than_exiting_zero():
    """The loser's pin must not end up under a tag that does not carry it.

    THE WINNER ARRIVES AFTER THE TAG LIST IS READ, which is the only window the
    re-derivation exists for. A winner who arrives earlier is handled by the
    listing itself and proves nothing about `Reference already exists`.
    """

    def winner(api):
        api.publish("v0.1.1", parent_pin.pin(api.text(), "task", "0.5.29")[0])

    api = Api(interfere_ref=winner)
    assert bump(api) == 0
    # v0.1.1 was refused, the tags were re-listed, and the NEXT number was cut —
    # on a second tag object, which is what makes this the retry and not the
    # first pass.
    assert api.refs == [{"ref": "refs/tags/v0.1.2", "sha": "object-2"}]
    assert api.objects == 2
    assert pins_of(api.text("v0.1.2"))["gateway"] == "0.9.49"


def test_two_module_releases_cut_two_parent_versions():
    """ADR-0722: a module release cuts a NEW parent version, so two means two."""
    api = Api()
    assert bump(api, module="gateway", version="0.9.49") == 0
    assert bump(api, module="task", version="0.5.29") == 0
    assert [r["ref"] for r in api.refs] == [
        "refs/tags/v0.1.1",
        "refs/tags/v0.1.2",
    ]
    landed = pins_of(api.text())
    assert (landed["gateway"], landed["task"]) == ("0.9.49", "0.5.29")


# ---------------------------------------------------------------------------
# ADR-0820: the commit a parent tag points at pins that tag in its examples.
# ---------------------------------------------------------------------------


def application(chart_name, version):
    return (
        "# An example an adopter copies.\n"
        "apiVersion: argoproj.io/v1alpha1\n"
        "kind: Application\n"
        "spec:\n"
        "  source:\n"
        "    repoURL: ghcr.io/yadgarhq/charts\n"
        f"    chart: {chart_name}\n"
        "    # the pin\n"
        f"    targetRevision: {version}\n"
    )


WITH_PLATFORM = {**PINS, "platform": "0.1.19"}

EXAMPLES = {
    "example/application.yaml": application("yadgar", "0.0.9"),
    "example/kind/application.yaml": application("yadgar", "0.0.9"),
    "example/operators-application.yaml": application("platform", "0.1.18"),
}


def revision(api, ref, path):
    import re

    return re.search(r"targetRevision: (\S+)", api.text(ref, path)).group(1)


def with_examples(**kwargs):
    return Api(main=chart(WITH_PLATFORM), published=chart(WITH_PLATFORM),
               examples=EXAMPLES, **kwargs)


def test_the_pin_and_the_examples_land_in_one_commit():
    api = with_examples()
    assert bump(api) == 0
    assert len(api.puts) == 1
    assert api.puts[0]["files"] == sorted([PATH, *EXAMPLES])


def test_the_tagged_commit_pins_the_tag_in_its_examples():
    api = with_examples()
    assert bump(api) == 0
    assert api.tagged["v0.1.1"] == api.puts[0]["sha"]
    assert revision(api, "v0.1.1", "example/application.yaml") == "0.1.1"
    assert revision(api, "v0.1.1", "example/kind/application.yaml") == "0.1.1"
    assert revision(api, "v0.1.1", "example/operators-application.yaml") == "0.1.19"


def test_a_platform_release_moves_the_operators_example_to_the_new_pin():
    """The platform pin is read from the tree being committed, not from `main`."""
    api = with_examples()
    assert bump(api, module="platform", version="0.1.20") == 0
    assert len(api.puts) == 1, "the first commit must already carry the new pin"
    assert revision(api, "v0.1.1", "example/operators-application.yaml") == "0.1.20"
    assert pins_of(api.text("v0.1.1"))["platform"] == "0.1.20"


def test_a_lost_tag_race_restamps_before_it_tags_the_next_number():
    """The pushed commit pins the number that was lost. Tagging N+1 there is the bug."""

    def winner(api):
        api.publish("v0.1.1", parent_pin.pin(api.text(), "task", "0.5.29")[0])

    api = with_examples(interfere_ref=winner)
    assert bump(api) == 0
    assert [r["ref"] for r in api.refs] == ["refs/tags/v0.1.2"]
    tagged = api.tagged["v0.1.2"]
    assert revision(api, tagged, "example/application.yaml") == "0.1.2"
    assert pins_of(api.text(tagged))["gateway"] == "0.9.49"
    assert tagged == api.main, "the restamp is a fast-forward of main, not a side commit"
    assert len(api.puts) == 2
    assert api.puts[1]["files"] == sorted(["example/application.yaml",
                                           "example/kind/application.yaml"])
    verdict = next_version.derive(
        "v0.1.0", ["v0.1.0"], [api.puts[1]["message"]], [BOT], api.puts[1]["files"]
    )
    assert verdict.rc == 0 and verdict.nxt == "", "\n".join(verdict.lines)


def test_a_restamp_after_main_moved_builds_on_the_new_head():
    """The winner's pin commit landed after ours: the restamp must keep it."""

    def winner(api):
        api.land({PATH: chart({**WITH_PLATFORM, "gateway": "0.9.49", "task": "0.5.29"})})
        api.publish("v0.1.1", api.text(), EXAMPLES)

    api = with_examples(interfere_ref=winner)
    assert bump(api) == 0
    tagged = api.tagged["v0.1.2"]
    assert tagged == api.main
    assert pins_of(api.text(tagged))["task"] == "0.5.29"
    assert revision(api, tagged, "example/application.yaml") == "0.1.2"


def test_a_recovered_tag_restamps_stale_examples_first():
    """Pin committed, never tagged, examples behind: the repair finishes all three."""
    api = Api(main=chart({**WITH_PLATFORM, "gateway": "0.9.49"}),
              published=chart(WITH_PLATFORM), examples=EXAMPLES)
    assert bump(api) == 0
    assert revision(api, "v0.1.1", "example/application.yaml") == "0.1.1"
    assert api.tagged["v0.1.1"] == api.main


def test_an_example_that_cannot_be_read_is_refused_rather_than_skipped():
    """Only a 404 is "absent". A 5xx read as absent ships a stale pin under a tag."""
    api = with_examples()
    api.unreadable["example/kind/application.yaml"] = "gh: Server Error (HTTP 502)"
    text = refused(api)
    assert "example/kind/application.yaml" in text
    assert api.puts == [] and api.refs == []


def test_a_parent_without_examples_gets_one_commit_touching_only_chart_yaml():
    """The parent before chart#17 merges, and any parent that never has examples."""
    api = Api()
    assert bump(api) == 0
    assert [p["files"] for p in api.puts] == [[PATH]]


def test_the_combined_commit_derives_nothing_in_the_parent():
    api = with_examples()
    assert bump(api) == 0
    verdict = next_version.derive(
        "v0.1.0", ["v0.1.0"], [api.puts[0]["message"]], [BOT], api.puts[0]["files"]
    )
    assert verdict.rc == 0 and verdict.nxt == "", "\n".join(verdict.lines)
    for line in api.puts[0]["message"].splitlines():
        assert not pr_body.STARTS_ENTRY.match(line), line


# ---------------------------------------------------------------------------
# A human merge the `version` job deferred must not be swallowed by this tag.
# ---------------------------------------------------------------------------

HUMAN_BREAKING = "\n".join([
    "feat!: drop the by-name arm of the parent's values (#40)",
    "",
    "## What", "", "A change.", "", "## Why", "", "A reason.", "",
    "## Changelog", "",
    "- feat!: drop the by-name arm of the parent's values",
    "",
    "## Verification", "", "Ran it.", "", "## Risk", "", "None.",
])


def tag_message_of(api, version):
    obj = next(r["sha"] for r in api.refs if r["ref"] == f"refs/tags/v{version}")
    number = int(obj.split("-")[1])
    call = [c for c in api.calls if c[2].endswith("git/tags")][number - 1]
    return next(a[len("message="):] for a in call if a.startswith("message="))


def test_a_deferred_human_merge_is_released_by_the_parent_tag():
    """The M/P/Y interleaving, and the case where no Y ever comes.

    M (`feat!`) lands; this job's pin commit P fast-forwards before M's own
    `version` run can stamp, so that run defers. P's tag is the first tag at or
    after M, so it MUST carry M's Changelog — under `0.x` a `feat!` is a minor
    bump, not the patch a module release alone would give. Were it cut as a
    patch, the next merge Y would derive from it and M would never be released.
    """
    api = Api(tags=["v0.3.8"])
    api.land({"chart/values.yaml": "x: 1\n"}, message=HUMAN_BREAKING)
    assert bump(api) == 0
    assert [r["ref"] for r in api.refs] == ["refs/tags/v0.4.0"]
    assert api.tagged["v0.4.0"] == api.main
    message = tag_message_of(api, "0.4.0")
    assert "feat!: drop the by-name arm of the parent's values" in message
    # Y, afterwards, derives from the tag that already released M.
    verdict = next_version.derive(
        "v0.4.0", ["v0.3.8", "v0.4.0"],
        ["fix: y\n\n## Changelog\n\n- fix: a later merge\n"],
        ["Max <max@example.com>"], ["chart/values.yaml"],
    )
    assert verdict.nxt == "0.4.1"


def test_a_merge_its_own_run_already_tagged_is_not_counted_twice():
    api = Api(tags=["v0.3.8"])
    api.land({"chart/values.yaml": "x: 1\n"}, message=HUMAN_BREAKING)
    api.tagged["v0.4.0"] = api.main
    assert bump(api) == 0
    assert [r["ref"] for r in api.refs] == ["refs/tags/v0.4.1"]
    assert "feat!" not in tag_message_of(api, "0.4.1")


def test_a_lost_tag_race_rederives_over_the_winners_range_too():
    """A human merge landing during the race is folded into the re-derived number."""

    def winner(api):
        api.publish("v0.1.1", parent_pin.pin(api.text(), "task", "0.5.29")[0])
        api.land({"chart/values.yaml": "x: 1\n"}, message=HUMAN_BREAKING)

    api = Api(interfere_ref=winner)
    assert bump(api) == 0
    assert [r["ref"] for r in api.refs] == ["refs/tags/v0.2.0"]
    assert api.tagged["v0.2.0"] == api.main


def test_a_merge_landing_during_the_restamp_is_counted_before_the_tag():
    """The re-stamp lands on a newer head, so the number is derived again over it.

    The lost race moves this job onto the winner's head; while it re-stamps, a
    human `feat!` merge lands and refuses the fast-forward. The re-stamp must
    build on THAT head, and the tag it cuts must count the merge — `v0.2.0`,
    not the patch derived before the merge existed.
    """

    def winner(api):
        api.publish("v0.1.1", parent_pin.pin(api.text(), "task", "0.5.29")[0])

    api = with_examples(interfere_ref=winner)
    api.patch_hooks[1] = lambda a: a.land(
        {"chart/values.yaml": "x: 1\n"}, message=HUMAN_BREAKING
    )
    assert bump(api) == 0
    assert [r["ref"] for r in api.refs] == ["refs/tags/v0.2.0"]
    tagged = api.tagged["v0.2.0"]
    assert tagged == api.main
    assert revision(api, tagged, "example/application.yaml") == "0.2.0"
    assert api.text(tagged, "chart/values.yaml") == "x: 1\n"
    assert "feat!: drop the by-name arm" in tag_message_of(api, "0.2.0")


def test_a_truncated_comparison_is_refused_rather_than_read_as_complete():
    api = Api(tags=["v0.3.8"])
    api.land({"chart/values.yaml": "x: 1\n"}, message=HUMAN_BREAKING)
    api.compare_cap = 1
    text = refused(api)
    assert "commits" in text
    assert api.refs == []


def test_a_tag_off_main_is_refused_rather_than_derived_from():
    api = Api(tags=["v0.3.8"])
    api.parents["commit-0"] = None
    text = refused(api)
    assert "diverged" in text
    assert api.puts == [] and api.refs == []


# ---------------------------------------------------------------------------
# A re-run has to be able to finish the job. Releasing again is not the repair.
# ---------------------------------------------------------------------------


def test_a_rerun_of_a_finished_bump_writes_nothing_and_stays_green():
    api = Api(main=chart({**PINS, "gateway": "0.9.49"}),
              tags=["v0.1.0", "v0.1.1"],
              published=chart({**PINS, "gateway": "0.9.49"}))
    assert bump(api) == 0
    assert api.puts == []
    assert api.refs == []


def test_a_pin_committed_but_never_tagged_gets_the_missing_tag():
    """The half-finished state a failed tag leaves, and the repair that clears it.

    `parent_pin.pin` refuses an equal version outright, so without this branch a
    re-run would be red forever and the documented repair would not work.
    """
    api = Api(main=chart({**PINS, "gateway": "0.9.49"}), tags=["v0.1.0"])
    assert bump(api) == 0
    assert api.puts == [], "the pin was already there and must not be rewritten"
    assert api.refs == [{"ref": "refs/tags/v0.1.1", "sha": "object-1"}]


def test_the_recovered_tag_points_at_main_rather_than_at_nothing():
    api = Api(main=chart({**PINS, "gateway": "0.9.49"}), tags=["v0.1.0"])
    assert bump(api) == 0
    obj = next(c for c in api.calls if c[2].endswith("git/tags"))
    assert "object=commit-0" in obj


# ---------------------------------------------------------------------------
# The refusals. Every one of them leaves the parent byte-identical.
# ---------------------------------------------------------------------------


def refused(api, **kwargs):
    with pytest.raises(parent_bump.Refusal) as caught:
        bump(api, **kwargs)
    return str(caught.value)


def test_a_downgrade_is_refused_and_nothing_is_written():
    """ADR-0690's defect, at the release path rather than only in the rewriter."""
    api = Api()
    text = refused(api, module="gateway", version="0.9.9")
    assert "OLDER" in text
    assert api.puts == [] and api.refs == []


def test_a_module_the_parent_does_not_declare_is_refused():
    api = Api()
    text = refused(api, module="telemetry", version="0.1.0")
    assert "not in `dependencies:`" in text
    assert api.puts == []


def test_a_parent_with_no_tag_is_refused_rather_than_invented():
    api = Api(tags=[])
    text = refused(api)
    assert "no orderable" in text
    assert api.puts == [], "a refused derivation must not leave a pin behind"


def test_a_stray_v_tag_is_not_mistaken_for_a_baseline():
    """`git/matching-refs/tags/v` is a PREFIX match, so it answers more than releases.

    The endpoint returns every ref whose name starts with `tags/v` — a
    `vendor-drop` or a `version-freeze` tag comes back alongside `v0.1.0`.
    `repin.greatest` drops anything unorderable, so a stray tag cannot corrupt
    the derived number; what needs pinning is the other half, that a list which is
    NON-EMPTY and holds nothing orderable still reaches the no-baseline refusal
    instead of deriving from the stray. `yadgarhq/actions` already carries a bare
    `v1` alongside fifty plain tags, so a `v`-prefixed non-release is a real shape
    in this estate rather than a hypothetical one.
    """
    api = Api(tags=["vendor-drop"])
    text = refused(api)
    assert "no orderable" in text
    assert api.puts == [] and api.refs == []


def test_a_write_refused_three_times_names_the_ruleset():
    api = Api()
    api.put_failures = 3
    text = refused(api)
    assert "bypass actor" in text
    assert "argocd" in text


def test_a_tag_refused_for_any_other_reason_says_the_pin_is_committed():
    """Releasing again is not the repair, and the annotation has to say so."""
    api = Api()
    api.ref_error = "gh: Resource not accessible by integration (HTTP 403)"
    text = refused(api)
    assert "IS committed" in text


def test_three_lost_tag_races_report_rather_than_loop_forever():
    api = Api()
    api.ref_error = "gh: Reference already exists (HTTP 422)"
    text = refused(api)
    assert "carries no tag" in text
    assert len(api.puts) == 1


def test_an_unreadable_parent_is_refused_before_anything_is_written():
    api = Api()
    api.unreadable[PATH] = "gh: Server Error (HTTP 502)"
    text = refused(api)
    assert "could not be read" in text
    assert api.puts == []


def test_a_missing_environment_exits_two_rather_than_guessing(monkeypatch):
    monkeypatch.delenv("MODULE", raising=False)
    monkeypatch.setenv("OWNER", OWNER)
    monkeypatch.setenv("VERSION", "0.9.49")
    api = Api()
    assert parent_bump.main(run=api, sleep=lambda _s: None) == 2
    assert api.calls == []


def test_a_leading_v_on_the_version_is_stripped_rather_than_refused():
    """Callers pass `github.ref_name`. `detect` already strips it; belt and braces."""
    api = Api()
    assert bump(api, version="v0.9.49") == 0
    assert pins_of(api.text())["gateway"] == "0.9.49"


# ---------------------------------------------------------------------------
# The workflow. A tested script the workflow does not call runs nowhere.
# ---------------------------------------------------------------------------


def workflow():
    return yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))


def steps(job):
    return {s.get("id") or s.get("name"): s for s in workflow()["jobs"][job]["steps"]}


def test_the_release_actually_calls_the_script():
    block = steps("parent")["bump"]["run"]
    assert "parent_bump.py" in block
    assert "<<'PY'" not in block
    assert "set -euo pipefail" in block


def test_the_step_is_given_the_chart_scoped_token_and_the_release_facts():
    env = steps("parent")["bump"]["env"]
    assert env["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert env["MODULE"] == "${{ needs.detect.outputs.chartname }}"
    assert env["VERSION"] == "${{ needs.detect.outputs.version }}"
    assert env["OWNER"] == "${{ github.repository_owner }}"


def test_the_pin_name_comes_from_the_chart_and_not_the_repository():
    """`yadgarhq/chart`'s chart is named `yadgar`, so the two are NOT the same fact.

    The parent's `dependencies:` entries are CHART names. Deriving the name from
    `github.repository` would be right for eight repositories by coincidence and
    wrong for the first one whose chart is named differently — which already
    exists.
    """
    detect = steps("detect")["d"]["run"]
    assert "chartname=" in detect
    assert "Chart.yaml" in detect
    assert workflow()["jobs"]["detect"]["outputs"]["chartname"]


def test_the_chart_token_is_its_own_mint_and_not_a_wider_one():
    """The separation ruling: this mint must not be able to write anywhere else.

    Widening it beyond `chart` would hand the token that pins the parent chart
    write access to a repository it has no business touching. `deployment`'s
    own mint, scoped to `estate`, makes the same call for the same reason.
    """
    mint = steps("parent")["app"]
    assert mint["with"]["repositories"] == "chart"
    assert mint["with"]["permission-contents"] == "write"
    assert steps("deployment")["estate_app"]["with"]["repositories"] == "estate"


def test_the_mint_is_the_same_pinned_action_as_every_other_one():
    """Two pins for one action in one file would be a smell of its own."""
    used = {
        step["uses"]
        for job in workflow()["jobs"].values()
        for step in job["steps"]
        if "create-github-app-token" in str(step.get("uses", ""))
    }
    assert len(used) == 1


def cred(client_id, app_key):
    """The `cred` step's SHIPPED shell, executed rather than read."""
    step = steps("parent")["cred"]
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        capture_output=True, text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLIENT_ID": client_id, "APP_KEY": app_key,
            "GITHUB_OUTPUT": "/dev/null", "GITHUB_STEP_SUMMARY": "/dev/null",
        },
    )


def test_a_module_release_with_no_release_app_reddens():
    """A published module chart the parent will never pin is a FAILURE, not a skip.

    Ledger 580's ruling on `ci-pr.yaml`'s own credential check, applied to the
    same shape: work was done that cannot be delivered. The `deployment` job's
    italic note is too quiet for this one, because ADR-0722 makes the parent the
    only thing an adopter installs.
    """
    assert cred("", "").returncode == 1
    assert "::error::" in cred("", "").stdout


def test_half_a_credential_reddens_too():
    assert cred("Iv1.abc", "").returncode == 1
    assert cred("", "-----BEGIN-----").returncode == 1


def test_a_configured_release_app_passes():
    assert cred("Iv1.abc", "-----BEGIN-----").returncode == 0


# The condition, evaluated rather than matched as a string — `ci-release.yaml`'s
# own chart gate is tested this way in `test_ci_verdict.py`, for the reason given
# there: a string assertion reds on a revert and passes on every other predicate
# with the same defect.
def resolver(chart_result, repository):
    values = {
        "needs.chart.result": chart_result,
        "github.repository": repository,
    }

    def resolve(ref, expr):
        if ref not in values:
            raise ci_verdict.Refused(f"{ref} is not supplied by this test ({expr!r})")
        return values[ref]

    return resolve


def condition():
    return workflow()["jobs"]["parent"]["if"]


@pytest.mark.parametrize(
    "result,repository,runs",
    [
        ("success", "yadgarhq/gateway", True),
        ("success", "yadgarhq/config", True),
        # THE PARENT DOES NOT PIN ITSELF. Its chart is `yadgar` and is not in its
        # own `dependencies:`, so the script would refuse and every parent release
        # would be red — the feature failing on its own output.
        ("success", "yadgarhq/chart", False),
        # A CHART THAT DID NOT PUBLISH MUST NOT BE PINNED. `helm package -u`
        # resolves the pin from the registry, so pinning a version that was never
        # pushed makes the NEXT parent release fail instead of this one.
        ("failure", "yadgarhq/gateway", False),
        ("cancelled", "yadgarhq/gateway", False),
        ("skipped", "yadgarhq/gateway", False),
    ],
)
def test_the_parent_is_bumped_only_by_a_module_that_published_a_chart(
    result, repository, runs
):
    assert ci_verdict.evaluate(condition(), resolver(result, repository)) is runs


# WHAT THIS TEST CANNOT SEE, NAMED RATHER THAN LEFT FOR THE NEXT READER TO
# ASSUME. `ci_verdict.evaluate` reports the TRUTH VALUE of `if:`'s text — it
# says nothing about whether GitHub would admit the job AT ALL, which is a
# question this parametrization answered `True` for `("success",
# "yadgarhq/config")` under BOTH the pre- and post-fix condition, and is
# exactly the gap that let `parent` skip in production with every clause here
# reading true. `test_ci_release_skip_propagation.py` is where that question
# is asked.


def test_the_job_is_bounded_so_a_wedged_read_cannot_hold_a_runner():
    """Every job here inherits GitHub's 360-minute default without one."""
    assert workflow()["jobs"]["parent"]["timeout-minutes"] <= 10


def test_the_job_needs_the_chart_job_rather_than_the_image_job():
    """`yadgarhq/config` is a parent module with NO `Containerfile`.

    Hanging this off the `deployment` job — which requires `needs.image.result ==
    'success'` — would silently never bump the parent for `config`, which released
    five times on the day this was written.
    """
    needs = workflow()["jobs"]["parent"]["needs"]
    assert "chart" in needs and "detect" in needs
    assert "image" not in needs
