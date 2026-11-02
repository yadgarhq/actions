"""What `rust_release.py` asserts (ADR-1120 part b, yaadgaar 527 step 6).

A new stable Rust, once Docker Hub serves its image, opens ONE actions pull
request that moves exactly three lines and sets `previous`; a later one deletes
`previous` once every Rust repository is current. Each green case has a red
twin. The fixtures are built from the REAL files at this checkout, so the
first bot pull request (which changes them) does not red this suite.

Run: python3 -m pytest scripts/tests/ -q
"""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fan_out  # noqa: E402
import repin  # noqa: E402
import rust_release as rr  # noqa: E402
import sweep  # noqa: E402
import test_repin_publish as trp  # noqa: E402
import tool_versions  # noqa: E402
import versions_parity  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REAL = {p: (REPO / p).read_text() for p in rr.FILES}
# The suite must pass in the bot's own pull requests too, where versions.yaml
# carries `previous`: the baseline is the real file with no window open.
_i, _v, _s, _p = rr.read_entry(REAL["versions.yaml"])
_lines = REAL["versions.yaml"].split("\n")
_lines[_i] = rr.entry(_v, _s)
REAL["versions.yaml"] = "\n".join(_lines)
OLD = tool_versions.read(str(REPO / "versions.yaml"))["rust_toolchain"]
MAJOR, MIN, PATCH = (int(x) for x in OLD.split("."))
NEW = f"{MAJOR}.{MIN + 1}.0"
NEWER = f"{MAJOR}.{MIN + 2}.0"
DIGEST = "sha256:" + "c" * 64
TEMPLATE = Path(__file__).resolve().parent / "fixtures" / "pull_request_template.md"


def channel(version):
    return f'manifest-version = "2"\n[pkg.rust]\nversion = "{version} (58cf8f9c3 2026-10-13)"\n'


class Net:
    """Docker Hub and the channel, in dicts. `tags` maps a reference to a digest."""

    def __init__(self, upstream=NEW, tags=None):
        self.upstream = upstream
        self.tags = {f"{NEW}-bookworm": DIGEST, f"{rr.minor(NEW)}-bookworm": DIGEST,
                     DIGEST: DIGEST} if tags is None else tags

    def channel(self):
        return channel(self.upstream)

    def index(self, reference):
        return self.tags.get(reference)


class Fake(trp.Fake):
    def __init__(self, files=None, **kw):
        super().__init__(**kw)
        self.files = dict(REAL if files is None else files)
        self.trees_made = []

    def repo(self):
        return dict(super().repo(), default_branch="main")

    def files_at(self, sha):
        assert sha == self.refs["main"]  # the tip, never a checkout
        return dict(self.files)

    def template_at(self, sha):
        return None

    def make_tree(self, base_tree, files):
        self.trees += 1
        self.trees_made.append(dict(files))
        return "t-" + repr(sorted(files.items()))


def org_template():
    return TEMPLATE.read_text()


def go(fake, net=None, all_current=False, live=True):
    return rr.run(fake, net or Net(), all_current, live, org_template)


def graced(previous=OLD, version=NEW):
    return dict(REAL, **{"versions.yaml": REAL["versions.yaml"].replace(
        rr.entry(OLD, read_sites()), rr.entry(version, read_sites(), previous))})


def read_sites():
    return rr.read_entry(REAL["versions.yaml"])[2]


def changed_lines(before, after):
    a, b = before.split("\n"), after.split("\n")
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b) if x != y]


# ---------------------------------------------------------------------------
# the real files are in the shape the detector reads
# ---------------------------------------------------------------------------


def test_the_real_files_read():
    _, version, sites, previous = rr.read_entry((REPO / "versions.yaml").read_text())
    assert version == OLD and int(sites) >= 1
    assert previous is None or rr.key(previous) < rr.key(version)
    rr.bump(REAL, NEW, DIGEST)  # the Containerfiles are in the shape it moves


def test_the_branch_pattern_is_the_sweepers():
    assert rr.BRANCH.pattern == sweep.BOTS["rust-release"][2].pattern
    assert sweep.BOTS["rust-release"][0] == fan_out.BOT_ID


def test_the_lane_never_shares_another_flows_prefix():
    for other in ("repin/", "rust-bump/"):
        assert not rr.LANE.prefix.startswith(other)
    assert not rr.BRANCH.fullmatch(f"rust-bump/{NEW}")


@pytest.mark.parametrize("bad", [f"rust-release/{NEW}-x", f"rust-release/{rr.minor(NEW)}",
                                 "rust-release/stable", f"rust-release/{NEW}/x", f"rust-release/{NEW}-end-end"])
def test_a_branch_that_is_not_a_version_is_not_the_lane(bad):
    assert not rr.BRANCH.fullmatch(bad)


def test_the_lane_commits_only_the_three_files_and_always_versions_yaml():
    rr.LANE.check_files({p: b"" for p in rr.FILES})
    rr.LANE.check_files({"versions.yaml": b""})
    for bad in ({rr.RUST_BUILD: b""}, {"versions.yaml": b"", "README.md": b""}, {}):
        with pytest.raises(repin.Refusal):
            rr.LANE.check_files(bad)


def test_this_source_adds_no_rust_version_site():
    """versions_parity counts the sites; the detector builds its lines from parts."""
    assert versions_parity.check(REPO) == []
    text = (REPO / "scripts" / "rust_release.py").read_text()
    assert OLD not in text and f"rust:{rr.minor(OLD)}" not in text


# ---------------------------------------------------------------------------
# the bump: three lines, exactly
# ---------------------------------------------------------------------------


def test_the_bump_moves_exactly_three_lines():
    moved = rr.bump(REAL, NEW, DIGEST)
    assert sorted(moved) == sorted(rr.FILES)
    sites = read_sites()
    assert changed_lines(REAL["versions.yaml"], moved["versions.yaml"]) == [
        (rr.entry(OLD, sites), rr.entry(NEW, sites, OLD))]
    [(was, now)] = changed_lines(REAL[rr.RUST_BUILD], moved[rr.RUST_BUILD])
    assert rr.IMAGE.fullmatch(was).group(1) == rr.minor(OLD)
    assert now == rr.image(rr.minor(NEW), DIGEST)
    [(was, now)] = changed_lines(REAL[rr.ESTATE_RUNNER], moved[rr.ESTATE_RUNNER])
    assert now == was.replace(f"{rr.FLAG} {OLD} ", f"{rr.FLAG} {NEW} ")


def test_the_bumped_tree_passes_versions_parity_and_the_reader(tmp_path):
    import shutil
    for name in ("scripts", "hooks", "containers", ".github/workflows"):
        shutil.copytree(REPO / name, tmp_path / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests", ".pytest_cache"))
    for name in (".pre-commit-config.yaml", ".pre-commit-hooks.yaml", "versions.yaml"):
        shutil.copy(REPO / name, tmp_path / name)
    for path, text in rr.bump(REAL, NEW, DIGEST).items():
        (tmp_path / path).write_text(text)
    assert versions_parity.check(tmp_path) == []
    got = tool_versions.read(str(tmp_path / "versions.yaml"))
    assert (got["rust_toolchain"], got["rust_toolchain_previous"]) == (NEW, OLD)


@pytest.mark.parametrize("new", [OLD, f"{MAJOR}.{MIN - 1}.0", f"{MAJOR}.{MIN}.{max(PATCH - 1, 0)}"])
def test_no_downgrade_and_no_sideways_move(new):
    with pytest.raises(rr.Refusal):
        rr.bump(REAL, new, DIGEST)


@pytest.mark.parametrize("digest", ["sha256:" + "c" * 63, "C" * 64, "sha256:" + "C" * 64, "", None])
def test_a_digest_that_is_not_64_hex_is_refused(digest):
    with pytest.raises(rr.Refusal):
        rr.bump(REAL, NEW, digest)


def test_a_bump_while_the_window_is_open_is_refused():
    with pytest.raises(rr.Refusal, match="window"):
        rr.bump(graced(), NEWER, DIGEST)


@pytest.mark.parametrize("path,old,new", [
    ("versions.yaml", "  rust-toolchain: {", "  rust-toolchain:  {"),
    (rr.RUST_BUILD, "-bookworm@", "-slim-bookworm@"),
    (rr.ESTATE_RUNNER, f"{rr.FLAG} {OLD} ", f"{rr.FLAG} {OLD}x "),
])
def test_a_site_in_another_shape_is_refused(path, old, new):
    files = dict(REAL, **{path: REAL[path].replace(old, new, 1)})
    with pytest.raises(rr.Refusal):
        rr.bump(files, NEW, DIGEST)


@pytest.mark.parametrize("path,extra", [
    ("versions.yaml", '  rust-toolchain: { version: "0.0.1", sites: 1 }'),
    (rr.RUST_BUILD, "FROM docker.io/" + rr.IMAGE_KEY + "1.0-bookworm@sha256:" + "d" * 64),
    (rr.ESTATE_RUNNER, f"RUN true {rr.FLAG} {OLD} x"),
])
def test_a_second_site_is_refused(path, extra):
    files = dict(REAL, **{path: REAL[path] + extra + "\n"})
    with pytest.raises(rr.Refusal):
        rr.bump(files, NEW, DIGEST)


def test_the_end_deletes_previous_and_nothing_else():
    moved = rr.end(graced())
    assert list(moved) == ["versions.yaml"]
    assert moved["versions.yaml"] == REAL["versions.yaml"].replace(rr.entry(OLD, read_sites()),
                                                                   rr.entry(NEW, read_sites()))


def test_there_is_no_end_without_a_window():
    with pytest.raises(rr.Refusal):
        rr.end(REAL)


# ---------------------------------------------------------------------------
# the channel and the registry
# ---------------------------------------------------------------------------


def test_stable_reads_pkg_rust_version():
    assert rr.stable(channel(NEW)) == NEW


@pytest.mark.parametrize("text", ["", "[pkg.rust]\n", '[pkg.rust]\nversion = "1.100"\n',
                                  '[pkg.rust]\nversion = "nightly"\n', "[pkg\n",
                                  '[pkg.cargo]\nversion = "1.100.0"\n'])
def test_a_channel_without_an_x_y_z_rust_is_refused(text):
    with pytest.raises(rr.Refusal):
        rr.stable(text)


def test_resolve_needs_both_tags_on_one_index():
    assert rr.resolve(Net(), NEW) == (DIGEST, None)
    digest, why = rr.resolve(Net(tags={}), NEW)
    assert digest is None and "not on Docker Hub" in why
    digest, why = rr.resolve(Net(tags={f"{NEW}-bookworm": DIGEST}), NEW)
    assert digest is None
    digest, why = rr.resolve(Net(tags={f"{NEW}-bookworm": DIGEST,
                                       f"{rr.minor(NEW)}-bookworm": "sha256:" + "d" * 64}), NEW)
    assert digest is None and "not yet" in why


class Answer:
    def __init__(self, headers, body=b""):
        self.headers = headers
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def opener(index_type, digest=DIGEST, code=None):
    calls = []

    def open_(request, timeout=None):
        calls.append(request)
        if request.full_url == rr.TOKEN_URL:
            return Answer({}, json.dumps({"token": "t"}).encode())
        if code is not None:
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO())
        return Answer({"Content-Type": index_type, "Docker-Content-Digest": digest})
    return open_, calls


def test_the_registry_asks_for_an_index_and_returns_its_digest():
    open_, calls = opener(rr.INDEX_TYPES[0])
    assert rr.Net(open_).index(f"{NEW}-bookworm") == DIGEST
    head = calls[-1]
    assert head.get_method() == "HEAD"
    assert head.full_url == rr.MANIFESTS + f"{NEW}-bookworm"
    assert all(t in head.get_header("Accept") for t in rr.INDEX_TYPES)


def test_a_missing_tag_is_none_and_any_other_error_is_a_refusal():
    assert rr.Net(opener("x", code=404)[0]).index("t") is None
    with pytest.raises(rr.Refusal):
        rr.Net(opener("x", code=500)[0]).index("t")


@pytest.mark.parametrize("kind,digest", [
    ("application/vnd.oci.image.manifest.v1+json", DIGEST),
    ("application/vnd.docker.distribution.manifest.v2+json", DIGEST),
    (rr.INDEX_TYPES[0], "sha256:short"),
    (rr.INDEX_TYPES[0], ""),
])
def test_a_single_arch_manifest_or_a_bad_digest_is_refused(kind, digest):
    with pytest.raises(rr.Refusal):
        rr.Net(opener(kind, digest)[0]).index("t")


def test_the_detector_reports_a_broken_upstream_read_as_an_error(monkeypatch, capsys):
    """An answer cut short (http.client) is an error row, not a traceback."""
    import http.client

    class Broken(rr.Net):
        def channel(self):
            raise http.client.IncompleteRead(b"x")

    monkeypatch.setattr(rr, "Net", Broken)
    monkeypatch.setattr(rr, "Gh", lambda owner: Fake())
    assert rr.main(["run", "--owner", "o"]) == 1
    assert "upstream read failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------


def test_a_new_stable_with_its_image_opens_one_signed_pull_request():
    fake = Fake()
    row = go(fake)
    assert (row["action"], row["version"], row["status"]) == ("bump", NEW, "opened")
    assert fake.kinds() == ["commit", "create_ref", "create_pr"]
    assert fake.trees_made == [{p: t.encode() for p, t in rr.bump(REAL, NEW, DIGEST).items()}]
    _, _, parent, message = fake.writes[0]
    assert parent == trp.BASE
    assert message == rr.title("bump", NEW)
    assert fake.refs[f"rust-release/{NEW}"]


def test_the_body_carries_every_template_section():
    text = rr.body(org_template(), "bump", NEW, OLD, DIGEST)
    for heading in repin.REQUIRED:
        assert f"## {heading}" in text
    assert DIGEST in text and "ADR-1120" in text


def test_no_image_yet_writes_nothing():
    fake = Fake()
    row = go(fake, Net(tags={}))
    assert row["status"] == "image not ready" and fake.writes == []


def test_a_current_version_writes_nothing():
    fake = Fake()
    assert go(fake, Net(upstream=OLD))["action"] == "current"
    assert go(fake, Net(upstream=f"{MAJOR}.{MIN - 1}.0"))["action"] == "current"
    assert fake.writes == []


def test_an_open_window_waits_even_for_a_newer_release():
    fake = Fake(files=graced())
    row = go(fake, Net(upstream=NEWER), all_current=False)
    assert row["action"] == "wait" and fake.writes == []


def test_the_window_ends_when_every_repository_is_current():
    fake = Fake(files=graced())
    row = go(fake, Net(upstream=NEWER), all_current=True)
    assert (row["action"], row["status"]) == ("end", "opened")
    assert fake.trees_made == [{p: t.encode() for p, t in rr.end(graced()).items()}]
    assert fake.refs[f"rust-release/{NEW}-end"]
    assert fake.writes[0][3] == rr.title("end", NEW)


def test_all_current_without_a_window_is_still_a_detection():
    fake = Fake()
    assert go(fake, all_current=True)["action"] == "bump"


def test_a_dry_run_writes_nothing():
    fake = Fake()
    assert go(fake, live=False)["status"] == "would open"
    assert fake.writes == []


def test_a_declined_bump_is_not_proposed_again():
    fake = Fake(prs=[trp.pr(7, f"rust-release/{NEW}", state="closed")])
    assert go(fake)["status"] == "declined"
    assert fake.writes == []


def test_a_merged_bump_with_versions_yaml_behind_is_an_error():
    fake = Fake(prs=[trp.pr(7, f"rust-release/{NEW}", state="closed", merged=True)])
    with pytest.raises(rr.Refusal, match="merged"):
        go(fake)


# ---------------------------------------------------------------------------
# armed or dry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event,dry,ref,armed,live", [
    ("schedule", "", "refs/heads/main", True, True),
    ("push", "", "refs/heads/main", True, True),
    ("workflow_dispatch", "false", "refs/heads/main", True, True),
    ("workflow_dispatch", "true", "refs/heads/main", True, False),
    # unarmed: no trigger is live, a dispatch with dry_run false included
    ("schedule", "", "refs/heads/main", False, False),
    ("workflow_dispatch", "false", "refs/heads/main", False, False),
    ("push", "", "refs/heads/feature", True, False),
    ("workflow_dispatch", "false", "refs/heads/feature", True, False),
    ("pull_request", "false", "refs/heads/main", True, False),
])
def test_live_or_dry(event, dry, ref, armed, live):
    assert rr.is_live(event, dry, ref, armed) is live


def test_armed_reads_a_json_boolean_only():
    assert rr.armed({"rust_bump": {"live": True, "release_live": True}}) is True
    assert rr.armed({"rust_bump": {"live": True, "release_live": False}}) is False
    for bad in ({}, {"rust_bump": {"live": True}}, {"rust_bump": {"release_live": "true"}},
                {"rust_bump": {"release_live": 1}}):
        with pytest.raises(rr.Refusal):
            rr.armed(bad)


def test_classes_json_ships_the_detector_armed():
    # Armed by ADR-1120 rollout step 6, after dry dispatch 41428639897 on main
    # showed all 14 repositories current and "nothing to do".
    data = json.loads((REPO / "classes.json").read_text())
    assert rr.armed(data) is True


def test_the_gh_writes_only_rust_release_branches():
    class Recorder(rr.Gh):
        def __init__(self):
            super().__init__("o")

        def _api(self, *args, payload=None, missing_ok=False):
            return json.dumps({})

    Recorder().create_ref(f"rust-release/{NEW}", "f" * 40)
    for bad in (f"rust-bump/{NEW}", "main", "repin/x"):
        with pytest.raises(repin.Refusal):
            Recorder().create_ref(bad, "f" * 40)


# ---------------------------------------------------------------------------
# the workflow: the upstream job's tokens name actions alone
# ---------------------------------------------------------------------------

import yaml  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "rust-bump.yaml"


def _mints(job):
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"][job]["steps"]
    return [s for s in steps if str(s.get("uses", "")).startswith("actions/create-github-app-token@")]


def test_every_mint_in_rust_bump_names_its_repositories():
    """An empty `repositories:` beside `owner:` mints for every repository."""
    for job in ("propagate", "upstream"):
        mints = _mints(job)
        assert mints
        for mint in mints:
            assert str(mint["with"].get("repositories", "")).strip(), (job, mint["id"])


def test_the_upstream_mints_name_exactly_the_actions_repository():
    by = {m["id"]: m for m in _mints("upstream")}
    assert set(by) == {"app-read", "app-write"}
    for mint in by.values():
        assert mint["with"]["repositories"] == fan_out.ACTIONS_REPO
        assert "permission-workflows" not in mint["with"]
    assert by["app-read"]["with"]["permission-contents"] == "read"
    assert by["app-write"]["if"] == "steps.mode.outputs.live == 'true'"


def test_the_upstream_job_is_armed_by_its_own_switch_and_reads_all_current():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["upstream"]
    assert job["needs"] == "propagate"
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("scripts/rust_release.py mode" in r for r in runs)
    [go] = [s for s in job["steps"] if "rust_release.py" in s.get("run", "") and "run --owner" in s["run"]]
    assert go["env"]["ALL_CURRENT"] == "${{ needs.propagate.outputs.all_current }}"


def test_files_at_reads_the_three_files_at_the_tip():
    import base64

    class Recorder(rr.Gh):
        def __init__(self):
            super().__init__("o")
            self.calls = []

        def _api(self, *args, payload=None, missing_ok=False):
            self.calls.append(args[-1])
            return json.dumps({"type": "file", "size": 3, "sha": "s",
                               "content": base64.b64encode(b"abc").decode()})

    gh = Recorder()
    assert gh.files_at("a" * 40) == {p: "abc" for p in rr.FILES}
    assert gh.calls == [f"repos/o/actions/contents/{p}?ref={'a' * 40}" for p in rr.FILES]


def test_a_missing_file_at_the_tip_is_a_refusal():
    class Missing(rr.Gh):
        def __init__(self):
            super().__init__("o")

        def _api(self, *args, payload=None, missing_ok=False):
            return None

    with pytest.raises(rr.Refusal, match="missing"):
        Missing().files_at("a" * 40)
