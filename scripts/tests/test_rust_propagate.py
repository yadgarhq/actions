"""What `rust_propagate.py` asserts (ADR-1120).

When `versions.yaml` rust-toolchain on actions main differs from a consumer's
Rust version fields, ONE bot pull request per consumer moves them, as a commit
GitHub signs, on a branch named for the version. Each property the sweeper
reads (B3, B7, B8, B9) is held against a fake GitHub, and each green case has
a red twin.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fan_out  # noqa: E402
import repin  # noqa: E402
import repin_publish as rp  # noqa: E402
import rust_propagate as rprop  # noqa: E402
import sweep  # noqa: E402
import test_repin_publish as trp  # noqa: E402

V = "1.99.1"
TEMPLATE = Path(__file__).resolve().parent / "fixtures" / "pull_request_template.md"
TOOLCHAIN = f'[toolchain]\nchannel = "{V}"\ncomponents = ["rustfmt", "clippy"]\n'
CARGO = '[package]\nname = "dial"\nrust-version = "1.90"\n'
SMOKE = ("jobs:\n  s:\n    steps:\n      - uses: dtolnay/rust-toolchain@683b # v1\n"
         "        with:\n          toolchain: stable\n")


class Fake(trp.Fake):
    """`test_repin_publish.Fake`, with the files a consumer's tip carries."""

    def __init__(self, files=None, template=None, **kw):
        super().__init__(**kw)
        self.files = {"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO} if files is None else files
        self.template = template
        self.trees_made = []

    def rust_files(self, sha):
        assert sha == self.refs["main"]
        return {p: t for p, t in self.files.items() if rprop.rust_version.kind_of(p)}

    def template_at(self, sha):
        return self.template

    def make_tree(self, base_tree, files):
        self.trees += 1
        self.trees_made.append(dict(files))
        return "t-" + repr(sorted(files.items()))


def org_template():
    return TEMPLATE.read_text()


def one(fake, live=True, version=V):
    return rprop.one(fake, "o/r", version, live, org_template)


# ---------------------------------------------------------------------------
# the branch, the title and the lane the sweeper admits
# ---------------------------------------------------------------------------


def test_the_branch_is_named_for_the_version():
    assert rprop.branch(V) == "rust-bump/1.99.1"


def test_the_branch_pattern_is_the_sweepers():
    assert rprop.BRANCH.pattern == sweep.BOTS["rust"][2].pattern
    assert sweep.BOTS["rust"][0] == fan_out.BOT_ID


def test_the_lane_never_shares_the_repin_prefix():
    """`repin_publish.supersede` closes every open `repin/` pull request the
    App opened; a shared prefix would let each flow close the other's."""
    assert not rprop.LANE.prefix.startswith("repin/")
    assert not rprop.BRANCH.fullmatch("repin/x")


def test_a_branch_that_is_not_a_version_is_refused():
    for bad in ("1.99", "stable", "1.99.1/x", "../main"):
        with pytest.raises(rprop.Refusal):
            rprop.branch(bad)


def test_the_lane_commits_only_rust_version_files():
    rprop.LANE.check_files({"Cargo.toml": b"", "rust-toolchain.toml": b"",
                            ".github/workflows/smoke.yaml": b""})
    for bad in ({"Cargo.lock": b""}, {"src/main.rs": b""}, {}):
        with pytest.raises(repin.Refusal):
            rprop.LANE.check_files(bad)


# ---------------------------------------------------------------------------
# one consumer
# ---------------------------------------------------------------------------


def test_a_consumer_at_1_90_gets_one_signed_commit_and_a_pull_request():
    fake = Fake()
    row = one(fake)
    assert row["status"] == "opened"
    assert row["paths"] == ["Cargo.toml"]
    assert fake.kinds() == ["commit", "create_ref", "create_pr"]
    assert fake.trees_made == [{"Cargo.toml": CARGO.replace("1.90", V).encode()}]
    _, _, parent, message = fake.writes[0]
    assert parent == trp.BASE  # the default tip, read through the API
    assert message == rprop.title(V)  # B7
    assert fake.refs["rust-bump/1.99.1"]


def test_the_workflow_literal_moves_in_the_same_commit():
    fake = Fake(files={"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO.replace("1.90", V),
                       ".github/workflows/smoke.yaml": SMOKE})
    row = one(fake)
    assert row["paths"] == [".github/workflows/smoke.yaml"]


def test_a_current_consumer_writes_nothing():
    fake = Fake(files={"rust-toolchain.toml": TOOLCHAIN, "Cargo.toml": CARGO.replace("1.90", V)})
    assert one(fake)["status"] == "current"
    assert fake.writes == []


def test_a_repository_without_cargo_toml_is_not_rust():
    fake = Fake(files={})
    assert one(fake)["status"] == "not rust"
    assert fake.writes == []


def test_dry_run_reads_and_writes_nothing():
    fake = Fake()
    row = one(fake, live=False)
    assert row["status"] == "would open"
    assert fake.writes == []


def test_dry_run_with_an_open_pull_request_says_would_refresh():
    fake = Fake(prs=[trp.pr(7, "rust-bump/1.99.1")])
    assert one(fake, live=False)["status"] == "would refresh"


def test_a_declined_bump_is_not_proposed_again():
    fake = Fake(prs=[trp.pr(7, "rust-bump/1.99.1", state="closed")])
    row = one(fake)
    assert row["status"] == "declined"
    assert fake.writes == []


def test_a_merged_bump_whose_fields_drifted_back_is_an_error():
    fake = Fake(prs=[trp.pr(7, "rust-bump/1.99.1", state="closed", merged=True)])
    with pytest.raises(rprop.Refusal, match="drift"):
        one(fake)


def test_a_field_that_cannot_be_rewritten_is_refused_before_any_write():
    fake = Fake(files={"rust-toolchain.toml": TOOLCHAIN,
                       "Cargo.toml": '[package]\nname = "x"\n[metadata.x]\nrust-version = "1"\n'})
    with pytest.raises(rprop.Refusal):
        one(fake)
    assert fake.writes == []


def test_a_newer_version_supersedes_the_older_open_bump():
    old = "rust-bump/1.98.0"
    fake = Fake(prs=[trp.pr(7, old)], refs={old: "c" * 40})
    fake.commits["c" * 40] = trp.landed("c" * 40, "t-old", trp.BASE)
    row = one(fake)
    assert row["closed"] == [7]
    assert old in fake.refs  # kept: a deletion would fail B9 later


def test_a_repin_pull_request_is_never_superseded_by_a_rust_bump():
    fake = Fake(prs=[trp.pr(7, "repin/dial-v0.2.29")])
    assert one(fake)["closed"] == []


def test_the_body_carries_every_template_section_and_one_changelog_bullet():
    body = rprop.body(org_template(), V, "o/r", ["Cargo.toml"])
    for heading in repin.REQUIRED:
        assert f"## {heading}" in body
    changelog = body.split("## Changelog", 1)[1].split("## ", 1)[0]
    bullets = [line for line in changelog.splitlines() if line.strip()]
    assert bullets == [f"- chore(deps): bump the Rust version to {V} (ADR-1120)"]
    outside = body.replace(changelog, "")
    assert not any(line.startswith("- ") for line in outside.splitlines())


def test_the_consumers_own_template_wins():
    fake = Fake(template="## What\n\n## Why\n\n## Changelog\n\n## Verification\n\n## Risk\n\n## Extra\n")
    one(fake)
    assert "## Extra" in fake.prs[-1]["body"]


# ---------------------------------------------------------------------------
# which repositories, and live or dry
# ---------------------------------------------------------------------------


def meta(name, private=False, archived=False, fork=False):
    return {"name": name, "archived": archived, "disabled": False, "fork": fork,
            "private": private, "topics": [], "default_branch": "main", "owner": "yadgarhq"}


CLASSES = {"sweep": {"exclude": {"docs": "private"}}, "exempt": {}}


def test_candidates_are_public_live_and_not_excluded():
    repos = [meta("dial"), meta("docs"), meta("secret", private=True), meta("old", archived=True),
             meta("forked", fork=True), meta("bad name")]
    assert rprop.candidates(repos, CLASSES, "yadgarhq") == ["dial"]


def test_a_listing_that_omits_private_reads_as_private():
    repo = meta("dial")
    del repo["private"]
    assert rprop.candidates([repo], CLASSES, "yadgarhq") == []


def test_enumerate_keeps_repositories_with_cargo_toml_and_refuses_none():
    class Lister:
        def list_repos(self, owner):
            return [meta("dial"), meta("chart")]

        def read_file(self, owner, repo, path, ref):
            assert path == "Cargo.toml" and ref == "main"
            return ("[package]\n", "sha") if repo == "dial" else None

    assert rprop.enumerate_rust(Lister(), "yadgarhq", CLASSES) == ["dial"]

    class Empty(Lister):
        def read_file(self, owner, repo, path, ref):
            return None

    with pytest.raises(rprop.Refusal, match="mints for every"):
        rprop.enumerate_rust(Empty(), "yadgarhq", CLASSES)


@pytest.mark.parametrize("event,dry,ref,armed,live", [
    ("schedule", "", "refs/heads/main", True, True),
    ("schedule", "", "refs/heads/main", False, False),
    ("push", "", "refs/heads/main", True, True),
    ("push", "", "refs/heads/feature", True, False),
    ("workflow_dispatch", "false", "refs/heads/main", False, True),
    ("workflow_dispatch", "true", "refs/heads/main", True, False),
    ("workflow_dispatch", "false", "refs/heads/feature", True, False),
    ("pull_request", "false", "refs/heads/main", True, False),
])
def test_live_or_dry(event, dry, ref, armed, live):
    assert rprop.is_live(event, dry, ref, armed) is live


def test_armed_reads_a_json_boolean_only():
    assert rprop.armed({"rust_bump": {"live": True}}) is True
    assert rprop.armed({"rust_bump": {"live": False}}) is False
    for bad in ({}, {"rust_bump": {"live": "true"}}, {"rust_bump": True}):
        with pytest.raises(rprop.Refusal):
            rprop.armed(bad)


def test_classes_json_ships_dry():
    data = json.loads((Path(__file__).resolve().parents[2] / "classes.json").read_text())
    assert rprop.armed(data) is False


def test_the_version_is_read_from_versions_yaml():
    path = Path(__file__).resolve().parents[2] / "versions.yaml"
    got = rprop.read_version(str(path))
    assert rprop.rust_version.VERSION.fullmatch(got)


# ---------------------------------------------------------------------------
# the real client: what it reads from GitHub
# ---------------------------------------------------------------------------


class Recorder(rprop.Gh):
    def __init__(self, answers):
        super().__init__("o", "r")
        self.calls = []
        self.answers = list(answers)

    def _api(self, *args, payload=None, missing_ok=False):
        self.calls.append(args)
        answer = self.answers.pop(0)
        return None if answer is None else json.dumps(answer)


def test_rust_files_reads_the_tree_then_only_rust_version_blobs():
    import base64
    tree = {"truncated": False, "tree": [
        {"path": "Cargo.toml", "type": "blob", "sha": "b1", "size": 10},
        {"path": "src/main.rs", "type": "blob", "sha": "b2", "size": 10},
        {"path": ".github/workflows/ci.yaml", "type": "blob", "sha": "b3", "size": 10},
        {"path": "crates", "type": "tree", "sha": "t1"}]}
    blob = {"encoding": "base64", "content": base64.b64encode(b"x").decode(), "size": 1}
    gh = Recorder([tree, blob, blob])
    assert gh.rust_files("a" * 40) == {"Cargo.toml": "x", ".github/workflows/ci.yaml": "x"}
    assert gh.calls[0][-1] == f"repos/o/r/git/trees/{'a' * 40}?recursive=1"
    assert [c[-1] for c in gh.calls[1:]] == ["repos/o/r/git/blobs/b1", "repos/o/r/git/blobs/b3"]


def test_a_truncated_tree_is_refused():
    gh = Recorder([{"truncated": True, "tree": []}])
    with pytest.raises(rprop.Refusal, match="truncated"):
        gh.rust_files("a" * 40)


def test_the_gh_writes_only_rust_bump_branches():
    gh = Recorder([{}])
    gh.create_ref("rust-bump/1.99.1", "f" * 40)
    with pytest.raises(repin.Refusal):
        Recorder([]).create_ref("repin/dial-v1.0.0", "f" * 40)
    with pytest.raises(repin.Refusal):
        Recorder([]).create_ref("main", "f" * 40)
