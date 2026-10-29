"""LEDGER 1756 S5, ADR-0982, ADR-0983. What `fan_out.py` asserts.

THE ROUND TRIP IS THE FIRST CLAIM. `classes.json` is MEASURED data: each of the
21 consumers' `.pre-commit-config.yaml`, fetched at the commit named in
`FIXTURES` below, rendered from its class at its OWN current rev, must come back
byte for byte. Where it does not, the difference is existing drift, and this
file names each one exactly rather than skipping it: `DRIFT` lists the five
repositories whose hook ORDER differs from their class, and the test asserts
the rendered block differs from the fixture in that one way and no other.

THE FIXTURES ARE COPIES, NOT REPOSITORIES. Nothing here reaches GitHub: the
fan-out runs against `Fake`, an in-memory client that records every write and
can be told to raise on one. That is how "a dry run makes zero writes" is
proved rather than promised.

THE BODY IS CHECKED BY RUNNING `pr_body.review`, in both of its modes, and
`next_version.derive`, never by restating their rules. Since yaadgaar 442 the
body carries no Changelog bullet, so `review` refuses it as a human's and the
bot exemption is what accepts it.

Run: python3 -m pytest scripts/tests/ -q
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import fan_out  # noqa: E402
import next_version  # noqa: E402
import pr_body  # noqa: E402

GATE = REPO / "scripts" / "fan_out.py"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "fan_out"
CLASSES = fan_out.load_classes((REPO / "classes.json").read_text())
WORKFLOW = REPO / ".github" / "workflows" / "fan-out.yaml"
ACTIONS = "https://github.com/yadgarhq/actions"

# repository -> (class topic suffix, rev, default-branch commit the fixture was
# fetched from on 2026-10-21). The class is the topic S4 set on the repository.
FIXTURES = {
    "argocd": ("base", "v1.20.1", "ea2865168edfa0048f8170cddcf78bdff3ef2dbe"),
    "argocd-verify": ("verify", "v1.20.1", "3a0a50baaedf3d558cad96a0b9b4fa9dba1dc0a7"),
    "chart": ("chart", "v1.31.0", "e7fa50073ffff7fabe95d7cb1f1f54538a1a7e27"),
    "config": ("chart", "v1.20.1", "606937e594e8a30081126b6d9c4b049524001861"),
    "dial": ("rust-helm", "v1.20.1", "8f46d53fac6ac6798ad121a118500ad94c8d3cdc"),
    "docs": ("base", "v1.20.1", "701653e4e95d3356d03e47c2608fe295d9d26359"),
    "estate": ("rust-app", "v1.20.1", "63d14a1bbdd1a11f6697b127f336712e51ad896d"),
    "gateway": ("service", "v1.31.0", "f6931e7d2e4b46da73ce6947cbf648ec0f98e0e1"),
    "iam-db": ("service", "v1.34.0", "95ffa53dc3d59195248d40a678b25824616edb38"),
    "iam": ("service", "v1.31.0", "84f6107ea6a379d066fe8c5db5ddd334c1f0c36d"),
    "lifecycle": ("rust-helm", "v1.20.1", "ea566300019a6f3a19a9c1e2d6437ccbe4264e73"),
    "platform": ("chart", "v1.30.7", "26be6a3b8cebee90778c292c85190af5403b19dc"),
    "project-db": ("service", "v1.34.0", "a2493e1cd51082fa6eccd9d283e46e82d04f7a54"),
    "project": ("service", "v1.34.0", "9e2d2a7f853baced60b0b31fa1760085dd5ecd91"),
    "proto": ("base", "v1.20.1", "25f2882c690eb17cc18576f8f4f5eea5642a84e9"),
    "seeder": ("rust-helm", "v1.34.0", "45c0ff8d4dcd9b817f5f2ea3fd74aac81213a2f9"),
    "store": ("rust-lib", "v1.30.4", "56635adc084d8f12b15b6485ea2b96f13215dfa3"),
    "task-db": ("service", "v1.34.0", "441a0e392d1c8a2bcad9e59f5a98b6927a5388bb"),
    "task": ("service", "v1.34.0", "29ef8d8852835d9a2310dd846061402675b08b07"),
    "telemetry": ("rust-lib", "v1.20.1", "824afb54915eb732869d95418ac616c72a138e1c"),
    "yadgar": ("rust-app", "v1.20.1", "f3fd9a103b48f47ac8c58b0c8841dac63b6f0974"),
}

# THE MEASURED DRIFT, every entry an order difference and nothing else. A
# repository leaving this list (its owner reordered it by hand) or joining it
# (a class edit, or a renderer regression) turns the round trip red.
DRIFT = {
    "chart": ["order differs: helm-lint moves from after hadolint to after actionlint"],
    "iam-db": [
        "order differs: complexity, cargo-clippy-complexity move from after "
        "boot-reads-watched to after cargo-clippy"
    ],
    "project-db": [
        "order differs: complexity, cargo-clippy-complexity move from after "
        "boot-reads-watched to after cargo-clippy"
    ],
    "store": [
        "order differs: no-compiled-in-defaults moves from after no-test-skips "
        "to after observe-coverage"
    ],
    "telemetry": [
        "order differs: no-compiled-in-defaults moves from after cargo-deny "
        "to after observe-coverage"
    ],
}


def fixture(repo):
    return (FIXTURE_DIR / f"{repo}.pre-commit-config.yaml").read_text()


def hooks(repo):
    cls = FIXTURES[repo][0]
    return fan_out.expected_hooks(CLASSES, cls, repo)


def actions_block(text):
    blocks = [b for b in yaml.safe_load(text)["repos"] if b["repo"] == ACTIONS]
    assert len(blocks) == 1
    return blocks[0]


def as_pairs(hook_list):
    return [(h["id"], {k: v for k, v in h.items() if k != "id"}) for h in hook_list]


# --------------------------------------------------------------------------
# the class data
# --------------------------------------------------------------------------


def test_the_fixture_set_is_every_consumer_and_nothing_else():
    on_disk = {p.name.split(".pre-commit-config.yaml")[0] for p in FIXTURE_DIR.iterdir()}
    assert on_disk == set(FIXTURES)
    assert len(FIXTURES) == 21


def test_every_class_names_hooks_this_repository_publishes():
    published = fan_out.manifest_ids((REPO / ".pre-commit-hooks.yaml").read_text())
    for name, cls in CLASSES["classes"].items():
        ids = [h["id"] for h in cls["hooks"]]
        assert ids, name
        assert len(ids) == len(set(ids)), name
        assert set(ids) <= published, (name, set(ids) - published)


def test_manifest_ids_agrees_with_a_real_yaml_parser():
    text = (REPO / ".pre-commit-hooks.yaml").read_text()
    assert fan_out.manifest_ids(text) == {h["id"] for h in yaml.safe_load(text)}


def test_actions_is_exempt_in_the_data_itself():
    assert "actions" in CLASSES["exempt"]


def test_a_class_with_a_duplicate_hook_is_refused():
    data = json.loads((REPO / "classes.json").read_text())
    data["classes"]["base"]["hooks"].append({"id": "gitleaks"})
    with pytest.raises(fan_out.Refusal):
        fan_out.load_classes(json.dumps(data))


def test_an_override_for_a_hook_the_class_lacks_is_refused():
    data = json.loads((REPO / "classes.json").read_text())
    data["overrides"]["telemetry"]["helm-lint"] = {"exclude": "x"}
    classes = fan_out.load_classes(json.dumps(data))
    with pytest.raises(fan_out.Refusal):
        fan_out.expected_hooks(classes, "rust-lib", "telemetry")


def test_the_telemetry_override_is_what_separates_it_from_store():
    store = dict(fan_out.expected_hooks(CLASSES, "rust-lib", "store"))
    telemetry = dict(fan_out.expected_hooks(CLASSES, "rust-lib", "telemetry"))
    assert store["prettier"] == {}
    assert telemetry["prettier"] == {"exclude": "^(chart/templates/|proto/)"}


# --------------------------------------------------------------------------
# the round trip: 21 measured configs at their CURRENT rev
# --------------------------------------------------------------------------


@pytest.mark.parametrize("repo", sorted(FIXTURES))
def test_rendering_at_the_current_rev_reproduces_the_file(repo):
    text = fixture(repo)
    cls, rev, _ = FIXTURES[repo]
    assert fan_out.current_rev(text) == rev
    rendered = fan_out.render(text, hooks(repo), rev)
    assert fan_out.drift(text, hooks(repo)) == DRIFT.get(repo, [])
    if repo in DRIFT:
        assert rendered != text
    else:
        assert rendered == text


@pytest.mark.parametrize("repo", sorted(FIXTURES))
def test_the_rendered_block_is_the_class_and_every_other_block_is_untouched(repo):
    text = fixture(repo)
    rendered = fan_out.render(text, hooks(repo), "v9.9.9")
    before, after = yaml.safe_load(text), yaml.safe_load(rendered)
    others = [b for b in before["repos"] if b["repo"] != ACTIONS]
    assert others == [b for b in after["repos"] if b["repo"] != ACTIONS]
    block = actions_block(rendered)
    assert block["rev"] == "v9.9.9"
    assert as_pairs(block["hooks"]) == hooks(repo)
    # BYTES, not just meaning: everything outside the block is the same text.
    old, new = fan_out.parse(text), fan_out.parse(rendered)
    assert (old.prefix, old.suffix) == (new.prefix, new.suffix)


@pytest.mark.parametrize("repo", sorted(FIXTURES))
def test_a_rev_move_with_no_drift_changes_exactly_one_line(repo):
    if repo in DRIFT:
        return
    text = fixture(repo)
    rendered = fan_out.render(text, hooks(repo), "v1.35.0")
    changed = [
        (a, b)
        for a, b in zip(text.splitlines(), rendered.splitlines(), strict=True)
        if a != b
    ]
    assert changed == [(f"    rev: {FIXTURES[repo][1]}", "    rev: v1.35.0")]


@pytest.mark.parametrize("repo", sorted(FIXTURES))
def test_rendering_is_idempotent(repo):
    once = fan_out.render(fixture(repo), hooks(repo), "v1.35.0")
    assert fan_out.render(once, hooks(repo), "v1.35.0") == once
    assert fan_out.drift(once, hooks(repo)) == []


def test_a_reordered_hook_keeps_the_comment_written_above_it():
    text = fixture("store")
    rendered = fan_out.render(text, hooks("store"), "v1.30.4")
    unit = [u for u in fan_out.parse(text).units if u.id == "no-compiled-in-defaults"][0]
    assert "".join(unit.lines) in rendered


def test_the_next_blocks_leading_comment_stays_with_that_block():
    """gateway's `# LOCAL, because ...` sits above `- repo: local`, not in ours."""
    block = fan_out.parse(fixture("gateway"))
    assert not any("LOCAL, because" in line for u in block.units for line in u.lines)
    assert any("LOCAL, because" in line for line in block.suffix)


# --------------------------------------------------------------------------
# the renderer on shapes the estate does not carry today
# --------------------------------------------------------------------------

SMALL = """repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: check-yaml

  # Shared.
  - repo: https://github.com/yadgarhq/actions
    rev: v1.20.1
    hooks:
      # why gitleaks
      - id: gitleaks
      - id: helm-lint
      - id: prettier
        exclude: ^old/

  # LOCAL
  - repo: local
    hooks:
      - id: x
        name: x
        entry: x
        language: system
"""


def test_a_missing_hook_is_added_an_extra_dropped_and_options_rewritten():
    want = [("gitleaks", {}), ("prettier", {"exclude": "^(a/|b/)"}), ("actionlint", {})]
    rendered = fan_out.render(SMALL, want, "v1.35.0")
    assert as_pairs(actions_block(rendered)["hooks"]) == want
    assert "      # why gitleaks\n      - id: gitleaks\n" in rendered
    assert "helm-lint" not in rendered
    assert fan_out.drift(SMALL, want) == [
        "missing: actionlint",
        "extra: helm-lint",
        "options differ on prettier: the class sets {'exclude': '^(a/|b/)'}",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        SMALL.replace("  - repo: https://github.com/yadgarhq/actions\n", "  - repo: elsewhere\n"),
        SMALL + "  - repo: https://github.com/yadgarhq/actions\n    rev: v1\n    hooks:\n      - id: a\n",
        SMALL.replace("    rev: v1.20.1\n", "    rev: v1.20.1\n    exclude: x\n"),
        SMALL.replace("      - id: helm-lint\n", "     - id: helm-lint\n"),
        SMALL.replace("        exclude: ^old/\n", "        exclude: |\n          ^old/\n"),
        SMALL.replace("    rev: v1.20.1\n", "    rev: v1.20.1 # pinned\n"),
        SMALL.replace("      - id: helm-lint\n", "      - id: gitleaks\n"),
    ],
    ids=["no-block", "two-blocks", "repo-key", "indent", "block-scalar", "rev-comment", "dup-id"],
)
def test_a_shape_the_renderer_does_not_know_is_refused(broken):
    with pytest.raises(fan_out.Refusal):
        fan_out.render(broken, [("gitleaks", {})], "v1.35.0")


# --------------------------------------------------------------------------
# tags, branch names, the mode
# --------------------------------------------------------------------------


def test_only_a_plain_semver_tag_is_a_target():
    for good in ("v1.35.0", "v10.0.12"):
        assert fan_out.check_tag(good) == good
    for bad in ("v1", "v1.35", "1.35.0", "v1.35.0-rc1", "", "main",
                "v1.35.0\n", " v1.35.0", "v1.35.0 ", "v1.2.3;id", "v1.2.3:x",
                "v" + "1" * 50 + ".0.0", "v1.2.3/../x", None):
        with pytest.raises(fan_out.Refusal):
            fan_out.check_tag(bad)


def test_one_fixed_branch_per_consumer_whatever_the_tag():
    """ADR-0982 says EXACTLY ONE adoption pull request per repository. A
    branch per tag stacked one per release (review of actions#124)."""
    assert fan_out.BRANCH == "actions-adopt"


@pytest.mark.parametrize(
    "event,dry_run,ref,live",
    [
        ("push", "", "refs/tags/v1.35.0", True),
        ("push", "true", "refs/tags/v1.35.0", True),
        ("workflow_dispatch", "true", "refs/heads/main", False),
        ("workflow_dispatch", "false", "refs/heads/main", True),
        ("workflow_dispatch", "false", "refs/heads/feat/x", False),
        ("workflow_dispatch", "", "refs/heads/main", False),
        ("workflow_dispatch", "False", "refs/heads/main", False),
        ("push", "", "refs/heads/main", False),
        ("push", "", "refs/tags/v1", False),
        ("push", "", "refs/tags/v1.35.0-rc1", False),
        ("push", "", "refs/tags/release", False),
        ("workflow_dispatch", "false", "refs/tags/v1.35.0", False),
        ("pull_request", "false", "refs/pull/1/merge", False),
    ],
)
def test_the_mode_is_live_only_on_a_tag_push_or_an_explicit_dispatch_from_main(
    event, dry_run, ref, live
):
    assert fan_out.is_live(event, dry_run, ref, armed=True) is live


@pytest.mark.parametrize(
    "event,dry_run,ref,live",
    [
        ("push", "", "refs/tags/v1.35.0", False),
        ("workflow_dispatch", "false", "refs/heads/main", True),
        ("workflow_dispatch", "true", "refs/heads/main", False),
    ],
)
def test_until_classes_json_arms_it_a_tag_push_is_dry(event, dry_run, ref, live):
    """FIRST-RUN SAFETY (review of actions#124): the tag this merge cuts runs
    DRY. A tag push goes live only after a reviewed PR sets `live: true`; a
    dispatch from main with dry_run=false stays the explicit human path."""
    assert fan_out.is_live(event, dry_run, ref, armed=False) is live


def test_classes_json_carries_a_boolean_live_and_it_is_armed():
    # The S5 flip (ledger 1756): the file ships `"live": true`, so the tag this
    # change cuts is the first live fan-out. Rollback is a pull request that
    # sets it back to false and changes this assertion with it.
    assert CLASSES["live"] is True


@pytest.mark.parametrize("value", ["absent", "false", 0, None, "true", 1])
def test_an_absent_or_non_boolean_live_is_refused(value):
    data = json.loads((REPO / "classes.json").read_text())
    if value == "absent":
        del data["live"]
    else:
        data["live"] = value
    with pytest.raises(fan_out.Refusal):
        fan_out.load_classes(json.dumps(data))


def mode(tmp_path, live_value, event="push", ref="refs/tags/v1.35.0", dry_run=""):
    tmp_path = tmp_path / str(live_value)
    tmp_path.mkdir()
    data = json.loads((REPO / "classes.json").read_text())
    data["live"] = live_value
    classes = tmp_path / "classes.json"
    classes.write_text(json.dumps(data))
    out = tmp_path / "out"
    env = {"EVENT": event, "DRY_RUN": dry_run, "REF": ref,
           "GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, str(GATE), "mode", "--classes", str(classes)],
                          env=env, capture_output=True, text=True, check=False)
    return done, out.read_text() if out.exists() else ""


def test_the_mode_command_keeps_a_tag_push_dry_until_armed(tmp_path):
    assert mode(tmp_path, False)[1] == "live=false\n"
    assert mode(tmp_path, True)[1] == "live=true\n"
    done, written = mode(tmp_path, "yes")
    assert done.returncode != 0 and written == ""


def test_a_tag_push_with_live_false_makes_zero_writes(tmp_path):
    _, written = mode(tmp_path, False)
    live = written.strip() == "live=true"
    gh = Fake(estate(), forbid_writes=True)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=live)
    assert (live, code) == (False, 0)
    assert gh.writes == []


def test_the_mode_command_writes_a_step_output(tmp_path):
    out = tmp_path / "out"
    env = {"EVENT": "workflow_dispatch", "DRY_RUN": "true", "REF": "refs/heads/main",
           "GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, str(GATE), "mode", "--classes",
                           str(REPO / "classes.json")], env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert out.read_text() == "live=false\n"


# --------------------------------------------------------------------------
# the fan-out, against an in-memory GitHub
# --------------------------------------------------------------------------

TEMPLATE = """## What

<!-- what -->

## Why

## Changelog

<!-- bullets -->

## Verification

## Risk
"""

MANIFEST = (REPO / ".pre-commit-hooks.yaml").read_text()


class Fake:
    """GitHub, as far as `fan_out` touches it. Every write is recorded."""

    def __init__(self, repos, forbid_writes=False, actions_tags=("v1.34.0", "v1.35.0")):
        self.repos = copy.deepcopy(repos)
        self.forbid_writes = forbid_writes
        self.writes = []
        self.reads = []
        self.actions_tags = list(actions_tags)
        self.next_pr = 100
        self.next_sha = 0
        self.moves = []

    # reads
    def list_repos(self, owner):
        return [
            {"name": n, "archived": r.get("archived", False), "topics": r.get("topics", []),
             "default_branch": "main", "fork": r.get("fork", False),
             "disabled": False, "owner": r.get("owner", owner)}
            for n, r in self.repos.items()
        ]

    def read_file(self, owner, repo, path, ref):
        self.reads.append((repo, path, ref))
        if repo == "actions":
            return (MANIFEST, "m") if ref in self.actions_tags else None
        r = self.repos[repo]
        sha = r["branches"].get(ref, ref if ref in r["commits"] else None)
        if sha is None:
            return None
        text = r["commits"][sha].get(path)
        return None if text is None else (text, f"blob-{sha}-{path}")

    def tags(self, owner, repo):
        return list(self.actions_tags)

    def branch_sha(self, owner, repo, branch):
        return self.repos[repo]["branches"].get(branch)

    def branch_commits(self, owner, repo, base, head):
        meta, found, sha = self.repos[repo].get("meta", {}), [], head
        while sha != base and sha in meta:
            found.append({"author": meta[sha]["author"], "message": meta[sha]["message"]})
            sha = meta[sha]["parent"]
        return found

    def find_prs(self, owner, repo, branch):
        return [dict(p) for p in self.repos[repo].get("prs", []) if p["head"] == branch]

    # writes
    def _write(self, *what):
        if self.forbid_writes:
            raise AssertionError(f"a write in a dry run: {what}")
        self.writes.append(what)

    def _move(self, repo, branch, sha):
        """Every ref write lands here, as GitHub would see it: a pull request
        whose head becomes its base is CLOSED (yaadgaar 466)."""
        r = self.repos[repo]
        self.moves.append((repo, branch, sha, r["branches"]["main"]))
        r["branches"][branch] = sha
        if sha == r["branches"]["main"]:
            for p in r.get("prs", []):
                if p["head"] == branch and p["state"] == "open":
                    p["state"] = "closed"

    def rewrite_branch(self, owner, repo, branch, parent, path, text, message, exists):
        self._write("rewrite_branch", repo, branch, parent, exists)
        r = self.repos[repo]
        assert (branch in r["branches"]) == exists, "exists disagrees with the branch"
        self.next_sha += 1
        sha = f"c{self.next_sha}"
        r["commits"][sha] = {**r["commits"][parent], path: text}
        r.setdefault("meta", {})[sha] = {"parent": parent, "author": fan_out.BOT,
                                          "message": message}
        self._move(repo, branch, sha)

    def create_pr(self, owner, repo, base, branch, title, body):
        self._write("create_pr", repo, branch)
        self.next_pr += 1
        url = f"https://github.com/{owner}/{repo}/pull/{self.next_pr}"
        self.repos[repo].setdefault("prs", []).append(
            {"number": self.next_pr, "head": branch, "state": "open", "merged": False,
             "url": url, "title": title, "body": body})
        return url

    def update_pr(self, owner, repo, number, title, body):
        self._write("update_pr", repo, number)
        for p in self.repos[repo]["prs"]:
            if p["number"] == number:
                p["title"], p["body"] = title, body


def consumer(repo, topics=None, template=TEMPLATE):
    files = {".pre-commit-config.yaml": fixture(repo)}
    if template is not None:
        files[".github/pull_request_template.md"] = template
    cls = FIXTURES[repo][0]
    return {
        "topics": topics if topics is not None else [f"yadgar-class-{cls}"],
        "branches": {"main": "base"},
        "commits": {"base": files},
    }


def estate():
    repos = {r: consumer(r) for r in FIXTURES}
    repos["actions"] = {"topics": [], "branches": {}, "commits": {}}
    repos["deploy"] = {"archived": True, "topics": [], "branches": {}, "commits": {}}
    repos["gateway"]["topics"].append("yadgar-deployable")
    return repos


def by_repo(rows):
    return {r["repo"]: r for r in rows}


def test_enumeration_skips_archived_exempts_actions_and_errors_on_zero_or_two_topics():
    repos = estate()
    repos["stray"] = {"topics": ["yadgar-deployable"], "branches": {}, "commits": {}}
    repos["double"] = {"topics": ["yadgar-class-base", "yadgar-class-chart"],
                       "branches": {}, "commits": {}}
    repos["unknown"] = {"topics": ["yadgar-class-nope"], "branches": {}, "commits": {}}
    gh = Fake(repos)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=False)
    got = by_repo(rows)
    assert got["deploy"]["status"] == "archived"
    assert got["actions"]["status"] == "exempt"
    for bad in ("stray", "double", "unknown"):
        assert got[bad]["status"] == "error", bad
    assert code == 1
    touched = {repo for repo, _, _ in gh.reads}
    assert not touched & {"stray", "double", "unknown", "deploy"}
    assert gh.writes == []


def test_a_dry_run_makes_zero_writes_and_proposes_one_pull_request_per_consumer():
    gh = Fake(estate(), forbid_writes=True)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=False)
    assert code == 0
    consumers = [r for r in rows if r["repo"] in FIXTURES]
    assert len(consumers) == 21
    assert all(r["status"] == "would open" for r in consumers)
    assert all(r["target"] == "v1.35.0" for r in consumers)
    assert all(r["diff"] for r in consumers)


def test_a_live_run_opens_one_pull_request_per_consumer_and_a_rerun_opens_none():
    gh = Fake(estate())
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert code == 0
    assert [r["status"] for r in rows if r["repo"] in FIXTURES] == ["opened"] * 21
    prs = [p for r in gh.repos.values() for p in r.get("prs", [])]
    assert len(prs) == 21
    assert {p["head"] for p in prs} == {"actions-adopt"}
    for repo in FIXTURES:
        r = gh.repos[repo]
        text = r["commits"][r["branches"]["actions-adopt"]][".pre-commit-config.yaml"]
        assert fan_out.current_rev(text) == "v1.35.0"
        assert fan_out.drift(text, hooks(repo)) == []

    gh.writes.clear()
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert code == 0
    assert [r["status"] for r in rows if r["repo"] in FIXTURES] == ["unchanged"] * 21
    assert gh.writes == []
    assert len([p for r in gh.repos.values() for p in r.get("prs", [])]) == 21


def test_a_rerun_after_main_moved_updates_the_same_branch_and_pull_request():
    gh = Fake(estate())
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    r = gh.repos["docs"]
    r["commits"]["base2"] = {**r["commits"]["base"],
                             ".pre-commit-config.yaml": fixture("docs") + "# later\n"}
    r["branches"]["main"] = "base2"
    gh.writes.clear()
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "updated"
    kinds = [w[0] for w in gh.writes]
    assert kinds == ["rewrite_branch"]
    assert len(r["prs"]) == 1
    head = r["commits"][r["branches"]["actions-adopt"]]
    assert head[".pre-commit-config.yaml"].endswith("# later\n")


def moved_to_a_new_tag():
    """An open adoption pull request for v1.34.0, re-run for v1.35.0 after `main` moved.

    Both the branch and the title/body change, which is the shape that started
    a `synchronize` and an `edited` run on one head sha (yaadgaar 440).
    """
    gh = Fake(estate())
    fan_out.run(gh, "yadgarhq", "v1.34.0", CLASSES, live=True)
    r = gh.repos["docs"]
    r["commits"]["base2"] = {**r["commits"]["base"],
                             ".pre-commit-config.yaml": fixture("docs") + "# later\n"}
    r["branches"]["main"] = "base2"
    gh.writes.clear()
    gh.moves.clear()
    return gh, r


def test_the_pull_request_is_edited_before_its_branch_moves():
    """YAADGAAR 440 / ADR-1033: `update_pr` comes BEFORE the branch moves.

    An `edited` event's payload carries the head at edit time. Edited first, the
    text-only run lands on the OLD head, which already carries a verdict, and
    the new head gets only the full `synchronize` run. Edited after, both runs
    start on the new head and the text-only one finds no verdict to carry.
    """
    gh, r = moved_to_a_new_tag()
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "updated"
    kinds = [w[0] for w in gh.writes if w[1] == "docs"]
    assert kinds == ["update_pr", "rewrite_branch"]
    assert fan_out.target_of(r["prs"][0]["title"]) == "v1.35.0"


def test_a_foreign_commit_refuses_before_the_pull_request_is_edited():
    """Edited first must not mean edited regardless: the reset refusal still writes nothing."""
    gh, r = moved_to_a_new_tag()
    head = r["branches"]["actions-adopt"]
    r["commits"]["h1"] = {**r["commits"][head], "fix.txt": "a human fix"}
    r["meta"]["h1"] = {"parent": head, "author": "m-agahi", "message": "fix: hand fix"}
    r["branches"]["actions-adopt"] = "h1"
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "error"
    assert not [w for w in gh.writes if w[1] == "docs"]
    assert fan_out.target_of(r["prs"][0]["title"]) == "v1.34.0"
    assert code == 1


def test_a_branch_left_without_a_pull_request_is_reused_not_duplicated():
    gh = Fake(estate())
    gh.repos["argocd"]["branches"]["actions-adopt"] = "base"
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    writes = [w for w in gh.writes if w[1] == "argocd"]
    assert [w[0] for w in writes] == ["rewrite_branch", "create_pr"]
    assert by_repo(rows)["argocd"]["status"] == "opened"


def test_a_pull_request_closed_without_merging_is_not_reopened():
    repos = estate()
    repos["docs"]["prs"] = [{"number": 7, "head": "actions-adopt", "state": "closed",
                             "merged": False, "url": "u", "body": "b",
                             "title": "chore(deps): bump yadgarhq/actions hooks from "
                                      "v1.20.1 to v1.35.0"}]
    gh = Fake(repos)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "declined"
    assert not [w for w in gh.writes if w[1] == "docs"]
    assert code == 0


def test_a_consumer_already_at_the_tag_is_adopted_and_untouched():
    repos = estate()
    text = fan_out.render(fixture("argocd"), hooks("argocd"), "v1.35.0")
    repos["argocd"]["commits"]["base"][".pre-commit-config.yaml"] = text
    gh = Fake(repos)
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["argocd"]["status"] == "adopted"
    assert not [w for w in gh.writes if w[1] == "argocd"]


def test_a_consumer_ahead_of_the_target_is_never_downgraded():
    gh = Fake(estate(), actions_tags=("v1.30.5",))
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.30.5", CLASSES, live=True)
    got = by_repo(rows)
    assert got["task"]["status"] == "ahead"
    assert got["argocd"]["status"] == "opened"
    assert not [w for w in gh.writes if w[1] == "task"]


def test_a_tag_actions_does_not_carry_is_refused_before_anything_is_written():
    gh = Fake(estate())
    with pytest.raises(fan_out.Refusal):
        fan_out.run(gh, "yadgarhq", "v1.99.0", CLASSES, live=True)
    assert gh.writes == []


def test_a_class_hook_missing_at_the_target_tag_refuses_the_whole_run():
    gh = Fake(estate())
    gh.read_file_orig = gh.read_file

    def without_chart_baseline(owner, repo, path, ref):
        if repo == "actions":
            text = MANIFEST.replace("- id: chart-baseline\n", "- id: chart-baseline-gone\n")
            return text, "m"
        return gh.read_file_orig(owner, repo, path, ref)

    gh.read_file = without_chart_baseline
    with pytest.raises(fan_out.Refusal, match="chart-baseline"):
        fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert gh.writes == []


def test_a_consumer_without_the_template_is_an_error_and_untouched():
    repos = estate()
    repos["docs"] = consumer("docs", template=None)
    gh = Fake(repos)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "error"
    assert not [w for w in gh.writes if w[1] == "docs"]
    assert code == 1


# --------------------------------------------------------------------------
# the pull request body
# --------------------------------------------------------------------------


def wrap(text, width=pr_body.WRAP):
    out = []
    for line in text.splitlines():
        if len(line) <= width:
            out.append(line)
            continue
        current = ""
        for word in line.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                out.append(current)
                current = word
            else:
                current = candidate
        out.append(current)
    return "\n".join(out)


def generated_body(repo="iam-db", template=TEMPLATE, deployable=True):
    text = fixture(repo)
    return fan_out.body(template, f"yadgarhq/{repo}", FIXTURES[repo][0],
                        fan_out.current_rev(text), "v1.35.0",
                        fan_out.drift(text, hooks(repo)), deployable)


def test_the_body_fills_every_heading_of_the_consumers_template_in_its_order():
    body = generated_body()
    assert [h for h in body.splitlines() if h.startswith("## ")] == [
        "## What", "## Why", "## Changelog", "## Verification", "## Risk"]


# --------------------------------------------------------------------------
# a merged adoption cuts no release (yaadgaar 442)
# --------------------------------------------------------------------------
#
# THE RULING (2026-10-27, "No release + sweeper"): merging a hook bump cuts no
# tag. In the v1.37.0 fan-out 17 of 21 merges cut one, and the ten tags in
# deployable repositories pushed ten bot pin commits onto chart's `main`,
# leaving every open chart pull request BEHIND. The tag came from the `- chore(deps):`
# bullet the body carried, which `bump_for` reads as a patch like any bullet.
#
# NO NEW CLASSIFICATION. `next_version.derive` already answers "no release" for
# a range that carries no Changelog bullet, is bot-authored throughout and
# touches nothing in `next_version.SHIPS`. An adoption writes only
# `.pre-commit-config.yaml`, so the body carrying no bullet is the whole change.
#
# THE PRICE, stated: no Changelog both passes the human template and releases
# nothing, because every bullet is at least a patch. So this body relies on the
# bot exemption at both readers — `ci / template` skips a `Bot` pull request,
# and the merge-time gate exempts a `[bot]` commit author. All 21 v1.37.0
# merges were authored `yadgarhq-bot[bot]` and all 20 with a `template` check
# reported it `skipped`. `review` itself is unchanged.

FAN_OUT_BOT = "yadgarhq-bot[bot] <324366854+yadgarhq-bot[bot]@users.noreply.github.com>"
HUMAN = "Max Agahi <64974579+m-agahi@users.noreply.github.com>"
ADOPTED = [fan_out.CONFIG]


def squash(body, title="chore(deps): bump yadgarhq/actions hooks from v1.34.0 to v1.35.0"):
    """The commit message GitHub writes: the title plus ` (#N)`, the body wrapped."""
    return f"{title} (#180)\n\n{wrap(body)}\n"


def human_body(changelog):
    return (
        f"## What\n\na thing\n\n## Why\n\na reason\n\n## Changelog\n\n{changelog}\n\n"
        "## Verification\n\nran it\n\n## Risk\n\nnone\n"
    )


@pytest.mark.parametrize("repo,deployable", [("iam-db", True), ("docs", False)])
def test_a_merged_adoption_cuts_no_release(repo, deployable):
    body = generated_body(repo, deployable=deployable)
    for message in (squash(body), f"title (#180)\n\n{body}"):
        verdict = next_version.derive("v1.4.0", ["v1.4.0"], [message], [True], ADOPTED)
        assert (verdict.rc, verdict.nxt) == (0, ""), verdict.lines


@pytest.mark.parametrize("repo", ["iam-db", "docs"])
def test_no_line_of_the_body_reads_as_a_changelog_bullet(repo):
    # `commit_entries` is not scoped to `## Changelog`: it reads every line of
    # the message, so a bullet-shaped line anywhere would still release.
    body = generated_body(repo)
    for text in (body, wrap(body)):
        assert not [l for l in text.splitlines() if pr_body.BULLET.match(l)]
        assert pr_body.commit_entries(text.splitlines()) == ([], [], [])


def test_the_body_still_answers_every_heading():
    found = pr_body.sections(generated_body())
    for heading in pr_body.REQUIRED:
        assert any(line.strip() for line in found[heading]), heading


def test_the_body_says_it_cuts_no_release():
    body = generated_body()
    assert "cuts a patch release" not in body
    assert "no release" in " ".join(pr_body.sections(body)["Changelog"]).lower()
    risk = " ".join(pr_body.sections(body)["Risk"])
    assert "Merging cuts no release of this repository." in risk
    assert "pin chain like any other release" not in risk


def test_a_deployable_body_says_nothing_enters_the_pin_chain():
    risk = " ".join(pr_body.sections(generated_body("iam-db"))["Risk"])
    assert ("This repository carries `yadgar-deployable`, and with no release "
            "nothing enters the parent chart's pin chain.") in risk


def test_the_body_says_a_commit_added_to_the_branch_lands_unreleased():
    risk = " ".join(pr_body.sections(generated_body())["Risk"])
    assert ("A commit a person adds to this branch lands with no release, because "
            "the squash keeps the bot as author. Put such a fix in its own pull "
            "request.") in risk
    assert "fix the cause in its own pull request" in risk


def confirmed_by_the_api(monkeypatch):
    """The commits API's answer for a squash-merged adoption, for any sha.

    Shape read off `GET /repos/yadgarhq/docs/commits/2a370c8` on 2026-10-28:
    verified, author id 324366854 (`Bot`), committer `web-flow` (19864447).
    """
    monkeypatch.setattr(pr_body, "fetch_commit", lambda repo, sha, token: {
        "sha": sha,
        "commit": {"verification": {"verified": True}},
        "author": {"id": 324366854, "type": "Bot"},
        "committer": {"id": 19864447, "type": "User"},
    })


def test_the_merge_time_gate_exempts_the_bot_author(monkeypatch, capsys):
    confirmed_by_the_api(monkeypatch)
    monkeypatch.setenv("BODY", squash(generated_body()))
    monkeypatch.setenv("WRAPPED", "true")
    monkeypatch.setenv("AUTHOR", FAN_OUT_BOT)
    monkeypatch.setenv("SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_REPOSITORY", "yadgarhq/docs")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert pr_body.main() == 0
    assert "bot-authored" in capsys.readouterr().out


def test_the_human_template_still_refuses_a_body_with_no_bullet():
    # The exemption is the only reason the body above is accepted. Read as a
    # human's, it is refused in both modes, which is the template unchanged.
    body = generated_body()
    problems, _, _ = pr_body.review(body, wrapped=False)
    assert problems and "Conventional Commits bullet" in problems[0]
    problems, _, _ = pr_body.review(wrap(body), wrapped=True)
    assert problems


@pytest.mark.parametrize("changelog,expected", [
    ("- fix: a metadata leak in the audit relay", "1.4.1"),
    ("- feat: partial results when one provider is unhealthy", "1.5.0"),
    ("- chore(deps): bump yadgarhq/actions hooks from v1.34.0 to v1.35.0", "1.4.1"),
])
def test_a_human_bullet_still_releases(changelog, expected):
    verdict = next_version.derive("v1.4.0", ["v1.4.0"], [squash(human_body(changelog), "t")],
                                  [False], ["src/main.rs"])
    assert (verdict.rc, verdict.nxt) == (0, expected)


def test_the_adoption_body_under_a_human_author_fails_closed():
    # Not reachable today (every adoption merge is bot-authored); if it ever is,
    # the job reds rather than tagging or staying silently green.
    verdict = next_version.derive("v1.4.0", ["v1.4.0"], [squash(generated_body())],
                                  [False], ADOPTED)
    assert (verdict.rc, verdict.nxt) == (1, "")


def test_a_real_adoption_merge_cuts_no_tag_end_to_end(tmp_path, monkeypatch):
    # IN-PROCESS, because the bot verdict now comes from the commits API and a
    # subprocess cannot be handed a stub without an injection point a caller
    # could also use. The git history is still real.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)

    def git(*args):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
                        "-c", "user.email=t@t", *args],
                       cwd=tmp_path, check=True, capture_output=True, text=True, env=env)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "root")
    git("tag", "-a", "v1.4.0", "-m", "v1.4.0")
    (tmp_path / fan_out.CONFIG).write_text("repos: []\n")
    git("add", "--", fan_out.CONFIG)
    git("commit", "-q", f"--author={FAN_OUT_BOT}", "-m", squash(generated_body()))
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    out.touch()
    summary.touch()
    for name, value in dict(GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary),
                            RUNNER_TEMP=str(tmp_path), GITHUB_REPOSITORY="yadgarhq/docs",
                            GH_TOKEN="t").items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    confirmed_by_the_api(monkeypatch)
    assert next_version.main() == 0
    assert out.read_text() == "next=\n"
    assert not (tmp_path / "tag_message.txt").exists()


def test_a_real_adoption_merge_the_api_cannot_confirm_fails_closed(tmp_path):
    # The same history as a subprocess with no token: the bot claim cannot be
    # checked, the range reads as a person's, and a person's range with no
    # Changelog reds the job rather than staying silently green.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "GH_TOKEN"}

    def git(*args):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t",
                        "-c", "user.email=t@t", *args],
                       cwd=tmp_path, check=True, capture_output=True, text=True, env=env)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "root")
    git("tag", "-a", "v1.4.0", "-m", "v1.4.0")
    (tmp_path / fan_out.CONFIG).write_text("repos: []\n")
    git("add", "--", fan_out.CONFIG)
    git("commit", "-q", f"--author={FAN_OUT_BOT}", "-m", squash(generated_body()))
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    out.touch()
    summary.touch()
    env.update(GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary),
               RUNNER_TEMP=str(tmp_path), GITHUB_REPOSITORY="yadgarhq/docs")
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "next_version.py")],
                          cwd=tmp_path, env=env, capture_output=True, text=True)
    assert done.returncode == 1, done.stdout + done.stderr
    assert out.read_text() == "next=\n"
    assert not (tmp_path / "tag_message.txt").exists()


def test_the_body_names_the_drift_it_fixes_and_the_release_range():
    body = generated_body()
    assert "complexity, cargo-clippy-complexity move" in body
    assert "https://github.com/yadgarhq/actions/compare/v1.34.0...v1.35.0" in body
    assert "yadgar-deployable" in body
    assert "yadgar-deployable" not in generated_body("docs", deployable=False)


def test_a_template_missing_a_required_heading_is_refused():
    with pytest.raises(fan_out.Refusal):
        generated_body(template=TEMPLATE.replace("## Risk\n", ""))


def test_a_heading_the_body_has_no_answer_for_still_gets_a_sentence():
    body = generated_body(template=TEMPLATE + "\n## Screenshots\n")
    section = pr_body.sections(body)["Screenshots"]
    assert any(line.strip() for line in section)


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_the_report_lists_every_consumer_and_the_adoption_count():
    gh = Fake(estate(), forbid_writes=True)
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=False)
    report = fan_out.report(rows, "v1.35.0", live=False)
    for repo in FIXTURES:
        assert f"| `{repo}` |" in report
    assert "0 of 21 consumers carry `v1.35.0`" in report
    assert "DRY RUN" in report


# --------------------------------------------------------------------------
# the workflow
# --------------------------------------------------------------------------


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_fires_on_a_plain_semver_tag_and_dispatch_defaults_to_dry():
    on = workflow()[True]
    assert on["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert set(on) == {"push", "workflow_dispatch"}


def test_two_tokens_a_metadata_only_lister_and_a_consumer_scoped_writer():
    steps = [s for j in workflow()["jobs"].values() for s in j["steps"]]
    mints = {s["id"]: s["with"] for s in steps if "create-github-app-token" in s.get("uses", "")}
    assert set(mints) == {"lister", "app"}
    lister, app = mints["lister"], mints["app"]
    # THE LISTER: every repository, metadata read, nothing else.
    assert lister["owner"] == "${{ github.repository_owner }}"
    assert "repositories" not in lister
    assert {k: v for k, v in lister.items() if k.startswith("permission-")} == {
        "permission-metadata": "read"}
    # THE WRITER: only the repositories the lister's step found.
    assert app["owner"] == "${{ github.repository_owner }}"
    assert app["repositories"] == "${{ steps.enumerate.outputs.repos }}"
    assert sorted(k for k in app if k.startswith("permission-")) == [
        "permission-contents", "permission-pull-requests"]
    for key in ("permission-contents", "permission-pull-requests"):
        assert app[key] == "${{ steps.mode.outputs.live == 'true' && 'write' || 'read' }}"


def test_the_writer_is_scoped_to_consumers_only():
    base = {"fork": False, "disabled": False, "owner": "yadgarhq"}
    repos = [{**base, **r} for r in [
        {"name": "actions", "archived": False, "topics": []},
        {"name": "deploy", "archived": True, "topics": ["yadgar-class-base"]},
        {"name": "docs", "archived": False, "topics": ["yadgar-class-base"]},
        {"name": "double", "archived": False,
         "topics": ["yadgar-class-base", "yadgar-class-chart"]},
        {"name": "stray", "archived": False, "topics": ["yadgar-deployable"]},
        {"name": "task", "archived": False,
         "topics": ["yadgar-deployable", "yadgar-class-service"]},
        {"name": "forked", "archived": False, "fork": True, "topics": ["yadgar-class-base"]},
        {"name": "off", "archived": False, "disabled": True, "topics": ["yadgar-class-base"]},
        {"name": "foreign", "archived": False, "owner": "someone",
         "topics": ["yadgar-class-base"]},
        {"name": "bad name!", "archived": False, "topics": ["yadgar-class-base"]},
    ]]
    assert fan_out.consumers(repos, CLASSES, "yadgarhq") == ["docs", "task"]


def test_the_greatest_command_orders_by_number_not_text():
    done = subprocess.run([sys.executable, str(GATE), "greatest"],
                          input="v1\nv1.9.0\nv1.10.0\nv1.35.0-rc1\n",
                          capture_output=True, text=True, check=False)
    assert (done.returncode, done.stdout) == (0, "v1.10.0\n")


def test_a_run_reads_the_manifest_it_is_handed_rather_than_github():
    gh = Fake(estate(), forbid_writes=True, actions_tags=())
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=False,
                             manifest_text=MANIFEST)
    assert code == 0
    assert not [r for r in gh.reads if r[0] == "actions"]


def test_nothing_in_the_workflow_or_script_can_merge():
    for text in (WORKFLOW.read_text(), GATE.read_text()):
        assert "pr merge" not in text
        assert "/merge" not in text
        assert "auto-merge" not in text.replace("never auto-merge", "")


# --------------------------------------------------------------------------
# security review, ledger 1756 S5: AUTHORIZATION
# --------------------------------------------------------------------------


def test_a_live_tag_must_be_on_main_and_git_never_sees_a_bad_tag():
    calls = []

    def git(*args):
        calls.append(args)
        return 0 if args[-2] == "refs/tags/v1.35.0" else 1

    assert fan_out.tag_on_main("v1.35.0", git) is True
    assert calls == [("merge-base", "--is-ancestor", "refs/tags/v1.35.0",
                      "refs/remotes/origin/main")]
    with pytest.raises(fan_out.Refusal):
        fan_out.tag_on_main("v1.36.0", git)
    calls.clear()
    with pytest.raises(fan_out.Refusal):
        fan_out.tag_on_main("v1.35.0;rm -rf /", git)
    assert calls == []


def test_the_check_tag_command_refuses_a_tag_with_a_newline():
    for tag, code in (("v1.35.0", 0), ("v1.35.0\nx", 1), ("--help", 1)):
        done = subprocess.run([sys.executable, str(GATE), "check-tag", "--", tag],
                              capture_output=True, text=True, check=False)
        assert done.returncode == code, (tag, done.stderr)


def test_more_consumers_than_the_cap_refuses_the_whole_run():
    repos = [{"name": f"r{i}", "archived": False, "fork": False, "disabled": False,
              "owner": "yadgarhq", "topics": ["yadgar-class-base"]}
             for i in range(fan_out.MAX_CONSUMERS + 1)]
    with pytest.raises(fan_out.Refusal):
        fan_out.consumers(repos, CLASSES, "yadgarhq")


def test_a_fork_or_foreign_repository_is_never_touched():
    repos = estate()
    repos["forked"] = {**consumer("docs"), "fork": True}
    repos["foreign"] = {**consumer("docs"), "owner": "someone"}
    gh = Fake(repos)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    got = by_repo(rows)
    assert got["forked"]["status"] == "ineligible"
    assert got["foreign"]["status"] == "error"
    assert not [w for w in gh.writes if w[1] in ("forked", "foreign")]
    assert not [r for r in gh.reads if r[0] in ("forked", "foreign")]


def test_the_body_never_carries_a_value_read_from_the_consumer():
    evil = "^x/ @everyone [click](https://evil.example)"
    repos = estate()
    text = fixture("chart").replace("exclude: ^chart/templates/", f"exclude: {json.dumps(evil)}")
    repos["chart"]["commits"]["base"][".pre-commit-config.yaml"] = text
    gh = Fake(repos)
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    pr = gh.repos["chart"]["prs"][0]
    assert "evil" not in pr["body"] and "@everyone" not in pr["body"]
    assert "evil" not in pr["title"]


def test_report_and_annotations_neutralise_text_from_a_consumer():
    rows = [{"repo": "x", "class": "base", "current": "", "target": "v1.35.0",
             "status": "error", "url": "", "diff": "",
             "note": "bad `line` | col\n::set-env name=X::y", "patch": "", "changes": []}]
    out = fan_out.report(rows, "v1.35.0", live=False)
    line = [l for l in out.splitlines() if l.startswith("| `x`")][0]
    assert line.count("|") == 9
    assert "::set-env" not in out and "`line`" not in out
    assert "\n" not in fan_out.annotation("a\nb::c") and "::" not in fan_out.annotation("a::b")


def test_the_lister_permission_check_fails_when_any_permission_is_added():
    """ADR-0987: the one owner-wide token holds metadata read and NOTHING else."""
    flow = workflow()
    assert fan_out_lister_violations(flow) == []
    for extra in ("permission-contents", "permission-pull-requests", "permission-administration"):
        mutated = copy.deepcopy(flow)
        lister = [s for s in mutated["jobs"]["fan-out"]["steps"] if s.get("id") == "lister"][0]
        lister["with"][extra] = "read"
        assert fan_out_lister_violations(mutated) != [], extra
    mutated = copy.deepcopy(flow)
    lister = [s for s in mutated["jobs"]["fan-out"]["steps"] if s.get("id") == "lister"][0]
    lister["with"]["permission-metadata"] = "write"
    assert fan_out_lister_violations(mutated) != []


def fan_out_lister_violations(flow):
    found = []
    for job in flow["jobs"].values():
        for step in job["steps"]:
            w = step.get("with", {})
            if "create-github-app-token" not in step.get("uses", ""):
                continue
            if "owner" in w and "repositories" not in w:
                perms = {k: v for k, v in w.items() if k.startswith("permission-")}
                if step.get("id") != "lister" or perms != {"permission-metadata": "read"}:
                    found.append((step.get("id"), perms))
    return found


def test_exactly_one_zizmor_ignore_and_it_is_the_listers_owner_line():
    lines = WORKFLOW.read_text().splitlines()
    ignores = [i for i, l in enumerate(lines) if "zizmor: ignore" in l]
    assert len(ignores) == 1
    line = lines[ignores[0]]
    assert line.strip().startswith("owner: ${{ github.repository_owner }}")
    assert line.rstrip().endswith("# zizmor: ignore[github-app]")
    window = "\n".join(lines[max(0, ignores[0] - 25):ignores[0]])
    assert "id: lister" in window and "ADR-0987" in window


def test_the_workflow_validates_the_tag_before_git_reads_it():
    text = WORKFLOW.read_text()
    assert text.index("fan_out.py check-tag") < text.index("git show \"refs/tags/")
    assert "fan_out.py on-main" in text


# --------------------------------------------------------------------------
# security review, ledger 1756 S5: DENIAL OF SERVICE
# --------------------------------------------------------------------------


def test_an_oversized_config_is_an_error_row_not_a_hang():
    repos = estate()
    big = fixture("docs") + "#" * (fan_out.MAX_BYTES + 1) + "\n"
    repos["docs"]["commits"]["base"][".pre-commit-config.yaml"] = big
    many = fixture("proto") + "#\n" * (fan_out.MAX_LINES + 1)
    repos["proto"]["commits"]["base"][".pre-commit-config.yaml"] = many
    gh = Fake(repos)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "error"
    assert by_repo(rows)["proto"]["status"] == "error"
    assert code == 1


def test_too_many_hooks_are_refused():
    text = SMALL.replace("      - id: gitleaks\n", "".join(
        f"      - id: h{i}\n" for i in range(fan_out.MAX_HOOKS + 1)))
    with pytest.raises(fan_out.Refusal):
        fan_out.parse(text)


def test_adversarial_templates_parse_in_linear_time():
    import time
    hostile = [
        "## What\n## Why\n## Changelog\n## Verification\n## Risk\n" + "<!--" * 15000,
        "## What\n## Why\n## Changelog\n## Verification\n## Risk\n#" + " \t" * 30000 + "x\n",
    ]
    for template in hostile:
        assert len(template.encode()) <= fan_out.MAX_TEMPLATE_BYTES
        started = time.monotonic()
        fan_out.headings(template)
        assert time.monotonic() - started < 0.5


def test_headings_agree_with_the_template_reader_on_real_templates():
    for path in sorted((FIXTURE_DIR.parent.parent.parent.parent).glob("*pull_request_template.md")):
        text = path.read_text()
        assert [t for _, t in fan_out.headings(text)] == list(pr_body.sections(text))


def test_pagination_is_bounded(monkeypatch):
    calls = []

    def endless(self, *args, payload=None, missing_ok=False):
        calls.append(args)
        return json.dumps([{"name": f"r{len(calls)}-{i}", "archived": False, "disabled": False,
                            "fork": False, "topics": [], "default_branch": "main",
                            "owner": {"login": "yadgarhq"}} for i in range(100)])

    monkeypatch.setattr(fan_out.Gh, "_api", endless)
    with pytest.raises(fan_out.Refusal):
        fan_out.Gh().list_repos("yadgarhq")
    assert len(calls) == fan_out.MAX_PAGES


def test_every_gh_call_carries_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(fan_out.subprocess, "run", fake_run)
    fan_out.Gh().find_prs("yadgarhq", "docs", "actions-adopt")
    assert 0 < seen["timeout"] <= 120


def test_a_gh_call_that_hangs_is_a_refusal(monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(fan_out.subprocess, "run", hang)
    with pytest.raises(fan_out.Refusal):
        fan_out.Gh().branch_sha("yadgarhq", "docs", "main")


def test_path_parts_are_quoted_into_the_api_url(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"object": {"sha": "s"}}', "")

    monkeypatch.setattr(fan_out.subprocess, "run", fake_run)
    fan_out.Gh().branch_sha("yadgarhq", "docs", "a&b#c?d")
    assert "a%26b%23c%3Fd" in seen[0][-1]



# --------------------------------------------------------------------------
# review of actions#124
# --------------------------------------------------------------------------


def test_two_releases_leave_exactly_one_open_pull_request_per_consumer():
    gh = Fake(estate(), actions_tags=("v1.35.0", "v1.36.0"))
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    rows, code = fan_out.run(gh, "yadgarhq", "v1.36.0", CLASSES, live=True)
    assert code == 0
    for repo in FIXTURES:
        r = gh.repos[repo]
        prs = [p for p in r["prs"] if p["state"] == "open"]
        assert len(prs) == 1 and len(r["prs"]) == 1, repo
        assert prs[0]["title"].endswith("v1.36.0"), prs[0]["title"]
        text = r["commits"][r["branches"]["actions-adopt"]][".pre-commit-config.yaml"]
        assert fan_out.current_rev(text) == "v1.36.0"
    assert {by_repo(rows)[r]["status"] for r in FIXTURES} == {"updated"}


def test_an_older_tag_never_retargets_a_newer_open_pull_request_down():
    gh = Fake(estate(), actions_tags=("v1.35.0", "v1.36.0"))
    fan_out.run(gh, "yadgarhq", "v1.36.0", CLASSES, live=True)
    gh.writes.clear()
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert {by_repo(rows)[r]["status"] for r in FIXTURES} == {"ahead"}
    assert gh.writes == []


def test_a_closed_pull_request_for_an_older_tag_does_not_block_a_newer_one():
    repos = estate()
    repos["docs"]["prs"] = [{"number": 7, "head": "actions-adopt", "state": "closed",
                             "merged": False, "url": "u", "body": "b",
                             "title": "chore(deps): bump yadgarhq/actions hooks from "
                                      "v1.20.1 to v1.34.0"}]
    gh = Fake(repos)
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "opened"


def test_a_human_commit_on_the_adoption_branch_is_never_reset_away():
    gh = Fake(estate())
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    r = gh.repos["docs"]
    head = r["branches"]["actions-adopt"]
    r["commits"]["h1"] = {**r["commits"][head], "fix.txt": "a human fix"}
    r["meta"]["h1"] = {"parent": head, "author": "m-agahi", "message": "fix: hand fix"}
    r["branches"]["actions-adopt"] = "h1"
    r["commits"]["base2"] = {**r["commits"]["base"],
                             ".pre-commit-config.yaml": fixture("docs") + "# later\n"}
    r["branches"]["main"] = "base2"
    gh.writes.clear()
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    row = by_repo(rows)["docs"]
    assert row["status"] == "error" and "not made by" in row["note"]
    assert not [w for w in gh.writes if w[1] == "docs"]
    assert r["branches"]["actions-adopt"] == "h1"
    assert code == 1


def test_a_bot_commit_with_a_foreign_message_also_blocks_the_reset():
    gh = Fake(estate())
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    r = gh.repos["docs"]
    head = r["branches"]["actions-adopt"]
    r["commits"]["h1"] = dict(r["commits"][head])
    r["meta"]["h1"] = {"parent": head, "author": fan_out.BOT, "message": "fix: something else"}
    r["branches"]["actions-adopt"] = "h1"
    r["commits"]["base2"] = {**r["commits"]["base"],
                             ".pre-commit-config.yaml": fixture("docs") + "# later\n"}
    r["branches"]["main"] = "base2"
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert by_repo(rows)["docs"]["status"] == "error"


INJECT = """repos:
  - repo: local
    hooks:
      - id: x
        name: x
        language: system
        entry: |
          ::add-mask::v1.35.0
          ::stop-commands::tok
  - repo: https://github.com/yadgarhq/actions
    rev: v1.20.1
    hooks:
      - id: gitleaks
"""


def test_consumer_text_can_never_become_a_workflow_command(tmp_path, capsys, monkeypatch):
    """The repro from the review: a config line that, trimmed, starts with
    `::`. The report goes ONLY to the step summary, and `::` in a patch is
    neutralised there too."""
    new = fan_out.render(INJECT, [("gitleaks", {})], "v1.35.0")
    row = {"repo": "r", "class": "base", "current": "v1.20.1", "target": "v1.35.0",
           "status": "would open", "url": "", "diff": "+1 -1", "note": "",
           "patch": fan_out.unified(INJECT, new, "r"), "changes": []}
    report = fan_out.report([row], "v1.35.0", False)
    assert "::" not in report
    summary_file = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    fan_out.summary(report)
    assert capsys.readouterr().out == ""
    assert summary_file.read_text() == report


def test_without_a_summary_file_nothing_is_printed(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    fan_out.summary("::add-mask::x\n")
    assert capsys.readouterr().out == ""


def test_an_unvalidated_rev_never_reaches_the_report():
    repos = estate()
    text = fixture("docs").replace("rev: v1.20.1", "rev: ::evil|`x`")
    repos["docs"]["commits"]["base"][".pre-commit-config.yaml"] = text
    gh = Fake(repos, forbid_writes=True)
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=False)
    row = by_repo(rows)["docs"]
    assert row["status"] == "error" and row["current"] == ""
    assert "evil" not in fan_out.report(rows, "v1.35.0", False)


def test_an_order_only_drift_at_the_same_rev_is_titled_a_reorder():
    assert fan_out.title_for("v1.35.0", "v1.35.0", ["order differs: a moves from after b to after c"]) \
        == "chore(deps): reorder yadgarhq/actions hooks (v1.35.0)"
    assert fan_out.title_for("v1.20.1", "v1.35.0", []) \
        == "chore(deps): bump yadgarhq/actions hooks from v1.20.1 to v1.35.0"
    assert fan_out.title_for("v1.35.0", "v1.35.0", ["missing: x"]) \
        == "chore(deps): regenerate yadgarhq/actions hooks (v1.35.0)"


def test_the_on_main_report_only_mode_reports_and_never_fails(tmp_path):
    env = {"PATH": os.environ["PATH"], "GIT_DIR": str(tmp_path / "no-repo"),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path)}
    report_only = subprocess.run([sys.executable, str(GATE), "on-main", "--tag", "v1.35.0",
                                  "--report-only"], env=env, capture_output=True, text=True,
                                 check=False)
    assert report_only.returncode == 0
    assert "not on main" in report_only.stdout
    strict = subprocess.run([sys.executable, str(GATE), "on-main", "--tag", "v1.35.0"],
                            env=env, capture_output=True, text=True, check=False)
    assert strict.returncode == 1


def test_the_workflow_runs_on_main_in_dry_mode_as_report_only():
    text = WORKFLOW.read_text()
    assert 'on-main --tag "$tag" --report-only' in text
    assert "--classes" not in text or "mode" in text


# --------------------------------------------------------------------------
# the branch moves ONCE, never through its base (yaadgaar 466)
# --------------------------------------------------------------------------
# v1.39.0 (run 40736970261) reset `actions-adopt` to the base tip and then put
# the file. While head equalled base GitHub CLOSED the pull request, 10 of 21
# times, and a closed adoption pull request reads as "declined" forever.


def test_no_ref_write_ever_points_the_branch_at_its_base():
    gh, r = moved_to_a_new_tag()
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    fresh = Fake(estate())
    fresh.repos["argocd"]["branches"]["actions-adopt"] = "base"
    fan_out.run(fresh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    moves = gh.moves + fresh.moves
    assert moves
    assert [m for m in moves if m[2] == m[3]] == []


def test_an_open_adoption_pull_request_stays_open_through_a_rewrite():
    gh, r = moved_to_a_new_tag()
    rows, code = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert code == 0
    assert by_repo(rows)["docs"]["status"] == "updated"
    assert [p["state"] for p in r["prs"]] == ["open"]


def test_the_one_ref_write_lands_a_commit_whose_parent_is_the_base_tip():
    gh, r = moved_to_a_new_tag()
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    docs = [m for m in gh.moves if m[0] == "docs"]
    assert len(docs) == 1
    _, branch, sha, _ = docs[0]
    assert branch == "actions-adopt"
    assert r["meta"][sha]["parent"] == "base2"
    assert fan_out.current_rev(r["commits"][sha][".pre-commit-config.yaml"]) == "v1.35.0"


def test_a_new_branch_is_created_at_the_new_commit_not_at_the_base():
    gh = Fake(estate())
    fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    docs = [w for w in gh.writes if w[1] == "docs"]
    assert docs[0] == ("rewrite_branch", "docs", "actions-adopt", "base", False)
    assert [m[2] for m in gh.moves if m[0] == "docs"] != ["base"]


def test_an_unverified_commit_stops_the_whole_fan_out():
    """Signing breaks for every repository at once, so the first refusal ends the run."""
    gh = Fake(estate())

    def unsigned(*args, **kwargs):
        gh.writes.append(("rewrite_branch", args[1]))
        raise fan_out.Unverified("not verified")

    gh.rewrite_branch = unsigned
    with pytest.raises(fan_out.Unverified):
        fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    assert len([w for w in gh.writes if w[0] == "rewrite_branch"]) == 1


BASE = "b" * 40
OLD = "0" * 40
NEW = "c" * 40
BOT_EMAIL = "324366854+yadgarhq-bot[bot]@users.noreply.github.com"


class GitHub(fan_out.Gh):
    """`fan_out.Gh` with `_api` answered by a tiny GitHub: refs, commit objects,
    and one open pull request that GitHub CLOSES whenever its head equals its base.
    `signed` and `committer_id` decide what the commits it creates look like."""

    def __init__(self, signed=True, committer_id=19864447, branch=OLD):
        super().__init__()
        self.refs = {"main": BASE} | ({"actions-adopt": branch} if branch else {})
        self.objects = {BASE: {"tree": "tree-base", "parents": []},
                        OLD: {"tree": "tree-old", "parents": [BASE]}}
        self.pr = "open"
        self.signed, self.committer_id = signed, committer_id
        self.calls = []
        self.ref_writes = []

    race = None  # a sha the fan-out moves the branch to just before a compare-and-swap
    graphql_answer = None  # a GraphQL answer to give instead of performing the swap

    def _api(self, *args, payload=None, missing_ok=False):
        method, path = (args[1], args[2]) if args[0] == "-X" else ("GET", args[0])
        self.calls.append((method, path, payload))
        if path == "graphql":
            return self._swap(payload)
        if path == "repos/yadgarhq/docs":
            return json.dumps({"name": "docs", "node_id": "R_docs"})
        prefix = "repos/yadgarhq/docs/"
        assert path.startswith(prefix), path
        path = path[len(prefix):]
        if method == "GET" and path.startswith("git/commits/"):
            sha = path.rsplit("/", 1)[1]
            return json.dumps({"sha": sha, "tree": {"sha": self.objects[sha]["tree"]}})
        if method == "POST" and path == "git/blobs":
            return json.dumps({"sha": "blob-new"})
        if method == "POST" and path == "git/trees":
            return json.dumps({"sha": "tree-new"})
        if method == "POST" and path == "git/commits":
            self.objects[NEW] = {"tree": payload["tree"], "parents": payload["parents"]}
            return json.dumps({"sha": NEW, "verification": {"verified": self.signed},
                               "author": {"email": BOT_EMAIL, "name": "yadgarhq-bot[bot]"},
                               "committer": {"email": "noreply@github.com", "name": "GitHub"}})
        if method in ("PATCH", "POST") and path.startswith("git/refs"):
            name = (path.split("git/refs/heads/", 1)[1] if method == "PATCH"
                    else payload["ref"].removeprefix("refs/heads/"))
            self.ref_writes.append((method, name, payload["sha"]))
            self.refs[name] = payload["sha"]
            if self.refs[name] == self.refs["main"]:
                self.pr = "closed"
            return json.dumps({"object": {"sha": payload["sha"]}})
        if method == "GET" and path.startswith("git/ref/heads/"):
            name = path.removeprefix("git/ref/heads/")
            if name not in self.refs:
                if missing_ok:
                    return None
                raise fan_out.Refusal("gh: Not Found (HTTP 404)")
            return json.dumps({"object": {"sha": self.refs[name]}})
        if method == "GET" and path.startswith("commits/"):
            return json.dumps({
                "sha": path.split("/", 1)[1], "commit": {"verification": {"verified": self.signed}},
                "author": {"id": 324366854, "type": "Bot", "login": "yadgarhq-bot[bot]"},
                "committer": {"id": self.committer_id, "login": "web-flow"}})
        raise AssertionError(f"unexpected call {method} {path}")


    def _swap(self, payload):
        """GraphQL `updateRefs`, as GitHub runs it: `beforeOid` must be the ref's value."""
        assert "updateRefs" in payload["query"]
        if self.race is not None:
            self.refs["actions-adopt"] = self.race
        if self.graphql_answer is not None:
            return json.dumps(self.graphql_answer)
        variables = payload["variables"]
        assert variables["repo"] == "R_docs"
        (update,) = variables["updates"]
        name = update["name"].removeprefix("refs/heads/")
        if self.refs.get(name) != update["beforeOid"]:
            raise fan_out.Refusal("gh: GraphQL: the ref was not at the expected value (updateRefs)")
        self.ref_writes.append(("CAS", name, update["afterOid"], update["force"]))
        self.refs[name] = update["afterOid"]
        return json.dumps({"data": {"updateRefs": {"clientMutationId": None}}})


def rewrite(gh, exists=True, branch="actions-adopt", **kwargs):
    gh.rewrite_branch("yadgarhq", "docs", branch, BASE, ".pre-commit-config.yaml",
                      "repos: []\n", "chore(deps): bump yadgarhq/actions hooks from v1 to v2",
                      exists, **kwargs)


# YAADGAAR 470. The sweeper's refresh passes `expect`, the head it evaluated.
# The ref then moves by compare-and-swap, never by a forced PATCH, so a fan-out
# rewrite that lands between the sweeper's read and its write is never overwritten.


def test_an_expected_head_moves_the_ref_by_compare_and_swap_only():
    gh = GitHub()
    rewrite(gh, expect=OLD)
    assert gh.ref_writes == [("CAS", "actions-adopt", NEW, True)]
    assert [m for m, _, _ in gh.calls if m == "PATCH"] == []
    assert gh.refs["actions-adopt"] == NEW
    assert gh.calls[-1][:2] == ("GET", "repos/yadgarhq/docs/commits/" + NEW)


def test_a_ref_the_fan_out_moved_is_never_overwritten():
    gh = GitHub()
    gh.race = "e" * 40
    with pytest.raises(fan_out.RefMoved):
        rewrite(gh, expect=OLD)
    assert gh.refs["actions-adopt"] == "e" * 40
    assert gh.ref_writes == []


@pytest.mark.parametrize("answer", [
    {"errors": [{"message": "something else"}]},
    {"data": {"updateRefs": None}, "errors": [{"message": "x"}]},
    {"data": {}},
    {"data": {"updateRefs": {"clientMutationId": None}}, "errors": [{"message": "partial"}]},
])
def test_a_failed_swap_on_a_ref_that_did_not_move_is_a_refusal_not_a_move(answer):
    gh = GitHub()
    gh.graphql_answer = answer
    with pytest.raises(fan_out.Refusal) as raised:
        rewrite(gh, expect=OLD)
    assert not isinstance(raised.value, fan_out.RefMoved)
    assert gh.refs["actions-adopt"] == OLD


def test_a_swap_whose_answer_was_lost_but_landed_is_checked_like_any_other():
    gh = GitHub()
    gh.graphql_answer = {"errors": [{"message": "timeout"}]}
    gh.race = NEW  # the swap did land; only its answer was lost
    rewrite(gh, expect=OLD)
    assert gh.calls[-1][:2] == ("GET", "repos/yadgarhq/docs/commits/" + NEW)


def test_rewrite_branch_moves_the_ref_once_to_a_new_commit_on_the_base_tip():
    gh = GitHub()
    rewrite(gh)
    assert gh.ref_writes == [("PATCH", "actions-adopt", NEW)]
    assert gh.objects[NEW]["parents"] == [BASE]
    assert gh.pr == "open"
    trees = [p for m, path, p in gh.calls if path.endswith("git/trees")]
    assert trees == [{"base_tree": "tree-base", "tree": [
        {"path": ".pre-commit-config.yaml", "mode": "100644", "type": "blob",
         "sha": "blob-new"}]}]
    made = [p for m, path, p in gh.calls if m == "POST" and path.endswith("git/commits")]
    assert made == [{"message": "chore(deps): bump yadgarhq/actions hooks from v1 to v2",
                     "tree": "tree-new", "parents": [BASE]}]
    blobs = [p for m, path, p in gh.calls if path.endswith("git/blobs")]
    assert blobs == [{"content": "cmVwb3M6IFtdCg==", "encoding": "base64"}]


def test_the_ref_write_is_the_last_write_and_the_commit_is_read_back_after_it():
    gh = GitHub()
    rewrite(gh)
    assert [m for m, _, _ in gh.calls if m != "GET"] == ["POST", "POST", "POST", "PATCH"]
    assert gh.calls[-1][:2] == ("GET", "repos/yadgarhq/docs/commits/" + NEW)


def test_rewrite_branch_creates_a_missing_branch_at_the_new_commit():
    gh = GitHub(branch=None)
    rewrite(gh, exists=False)
    assert gh.ref_writes == [("POST", "actions-adopt", NEW)]


def test_an_unsigned_commit_is_refused_before_the_ref_moves():
    gh = GitHub(signed=False)
    with pytest.raises(fan_out.Unverified):
        rewrite(gh)
    assert gh.ref_writes == []
    assert gh.refs["actions-adopt"] == OLD


def test_a_commit_the_commits_api_disowns_is_refused_after_the_move():
    gh = GitHub(committer_id=12345)
    with pytest.raises(fan_out.Unverified, match="web-flow"):
        rewrite(gh)


def test_rewrite_branch_refuses_any_other_branch_before_any_call():
    gh = GitHub()
    with pytest.raises(fan_out.Refusal):
        rewrite(gh, branch="main")
    assert gh.calls == []


@pytest.mark.parametrize("answer, why", [
    ({"verification": {"verified": True}, "author": {"email": "x@y"},
      "committer": {"email": "noreply@github.com"}}, "author"),
    ({"verification": {"verified": True}, "author": {"email": BOT_EMAIL},
      "committer": {"email": "me@example.com"}}, "committer"),
    ({"author": {"email": BOT_EMAIL}, "committer": {"email": "noreply@github.com"}},
     "verified"),
    ({"verification": {"verified": "true"}, "author": {"email": BOT_EMAIL},
      "committer": {"email": "noreply@github.com"}}, "verified"),
])
def test_the_created_commit_is_checked_field_by_field(answer, why):
    good = {"verification": {"verified": True}, "author": {"email": BOT_EMAIL},
            "committer": {"email": "noreply@github.com"}}
    fan_out.check_created(good)
    with pytest.raises(fan_out.Unverified, match=why):
        fan_out.check_created(answer)


@pytest.mark.parametrize("change, why", [
    ({"sha": "d" * 40}, "another sha"),
    ({"commit": {"verification": {"verified": False}}}, "verified"),
    ({"author": {"id": 324366854, "type": "User"}}, "App"),
    ({"author": {"id": 1, "type": "Bot"}}, "App"),
    ({"committer": {"id": 1}}, "web-flow"),
])
def test_the_moved_commit_is_checked_like_sweep_k2(change, why):
    good = {"sha": NEW, "commit": {"verification": {"verified": True}},
            "author": {"id": 324366854, "type": "Bot"}, "committer": {"id": 19864447}}
    fan_out.check_landed(good, NEW)
    with pytest.raises(fan_out.Unverified, match=why):
        fan_out.check_landed(good | change, NEW)


def test_each_repository_outcome_is_one_inert_stdout_line():
    rows = [{"repo": "docs", "status": "updated", "url": "https://github.com/yadgarhq/docs/pull/1",
             "note": "", "patch": "::add-mask::x", "changes": ["::stop-commands::t"]},
            {"repo": "::evil\n::error::x", "status": "error", "url": "", "note": "::error::boom",
             "patch": "", "changes": []},
            {"repo": ":::", "status": "error", "url": "", "note": "", "patch": "", "changes": []}]
    lines = fan_out.outcomes(rows, "yadgarhq")
    assert lines[0] == ("fan-out yadgarhq/docs: updated "
                        "https://github.com/yadgarhq/docs/pull/1")
    assert len(lines) == 3
    for line in lines:
        assert "\n" not in line
        assert "::" not in line
        assert "add-mask" not in line and "stop-commands" not in line and "boom" not in line


# --------------------------------------------------------------------------
# yaadgaar 468, ADR-1073: the fan-out triggers the sweep, and never merges
# --------------------------------------------------------------------------

DISPATCH = ('gh api -X POST "repos/${REPO}/actions/workflows/sweep.yaml/dispatches"\n'
            '-f ref=main -f "inputs[dry_run]=false" -f "inputs[wait_minutes]=45"')


def dispatch_violations(flow):
    """Every way the dispatch job could do more than trigger sweep.yaml on main."""
    found = []
    jobs = flow["jobs"]
    if set(jobs) != {"fan-out", "dispatch-sweep"}:
        found.append(("jobs", sorted(jobs)))
    if jobs["fan-out"].get("permissions") != {"contents": "read"}:
        found.append(("fan-out permissions", jobs["fan-out"].get("permissions")))
    if jobs["fan-out"].get("outputs") != {"live": "${{ steps.mode.outputs.live }}"}:
        found.append(("fan-out outputs", jobs["fan-out"].get("outputs")))
    job = jobs.get("dispatch-sweep", {})
    if job.get("permissions") != {"actions": "write"}:
        found.append(("dispatch permissions", job.get("permissions")))
    if job.get("needs") != "fan-out":
        found.append(("needs", job.get("needs")))
    if job.get("if") != "github.event_name == 'push' && needs.fan-out.outputs.live == 'true'":
        found.append(("if", job.get("if")))
    steps = job.get("steps", [])
    if len(steps) != 1 or "uses" in steps[0]:
        found.append(("steps", steps))
        return found
    step = steps[0]
    if step.get("env") != {"GH_TOKEN": "${{ github.token }}", "REPO": "${{ github.repository }}"}:
        found.append(("env", step.get("env")))
    if step.get("run", "").strip() != DISPATCH.replace("\n", " "):
        found.append(("run", step.get("run")))
    if "secrets." in json.dumps(job):
        found.append(("secrets", job))
    return found


def test_the_fan_out_dispatches_only_sweep_yaml_on_main_with_github_token():
    flow = workflow()
    assert dispatch_violations(flow) == []
    job = flow["jobs"]["dispatch-sweep"]
    assert job["timeout-minutes"] <= 5
    # The cap it sends is one sweep.yaml accepts.
    import sweep
    assert sweep.wait_minutes("45") == 45


@pytest.mark.parametrize("mutate", [
    lambda f: f["jobs"]["dispatch-sweep"]["permissions"].update({"contents": "write"}),
    lambda f: f["jobs"]["dispatch-sweep"]["permissions"].update({"pull-requests": "write"}),
    lambda f: f["jobs"]["fan-out"]["permissions"].update({"actions": "write"}),
    lambda f: f["jobs"]["fan-out"]["permissions"].update({"contents": "write"}),
    lambda f: f["jobs"]["dispatch-sweep"].update({"if": "needs.fan-out.outputs.live == 'true'"}),
    lambda f: f["jobs"]["dispatch-sweep"].update({"if": "always()"}),
    lambda f: f["jobs"]["dispatch-sweep"]["steps"][0].update(
        run=f["jobs"]["dispatch-sweep"]["steps"][0]["run"].replace("sweep.yaml", "fan-out.yaml")),
    lambda f: f["jobs"]["dispatch-sweep"]["steps"][0].update(
        run=f["jobs"]["dispatch-sweep"]["steps"][0]["run"].replace("ref=main", "ref=v1")),
    lambda f: f["jobs"]["dispatch-sweep"]["steps"][0].update(
        run=f["jobs"]["dispatch-sweep"]["steps"][0]["run"].replace("dry_run]=false", "dry_run]=true")),
    lambda f: f["jobs"]["dispatch-sweep"]["steps"][0]["env"].update(
        GH_TOKEN="${{ secrets.RELEASE_APP_PRIVATE_KEY }}"),
    lambda f: f["jobs"]["dispatch-sweep"]["steps"].insert(0, {
        "uses": "actions/create-github-app-token@" + "a" * 40}),
    lambda f: f["jobs"].update({"other": {"permissions": {}, "steps": []}}),
])
def test_mutation_any_widening_of_the_dispatch_reddens(mutate):
    flow = copy.deepcopy(workflow())
    mutate(flow)
    assert dispatch_violations(flow) != []
