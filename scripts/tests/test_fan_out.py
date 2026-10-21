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

THE BODY IS CHECKED BY RUNNING `pr_body.review`, in both of its modes, as
`test_repin.py` does, never by restating its rules.

Run: python3 -m pytest scripts/tests/ -q
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import fan_out  # noqa: E402
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
        "options differ on prettier: {'exclude': '^old/'} -> {'exclude': '^(a/|b/)'}",
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
    for bad in ("v1", "v1.35", "1.35.0", "v1.35.0-rc1", "", "main"):
        with pytest.raises(fan_out.Refusal):
            fan_out.check_tag(bad)


def test_the_branch_is_derived_from_the_tag_alone():
    assert fan_out.branch_for("v1.35.0") == "actions-adopt/v1.35.0"
    assert fan_out.branch_for("v1.35.0") == fan_out.branch_for("v1.35.0")


@pytest.mark.parametrize(
    "event,dry_run,ref,live",
    [
        ("push", "", "refs/tags/v1.35.0", True),
        ("workflow_dispatch", "true", "refs/heads/main", False),
        ("workflow_dispatch", "false", "refs/heads/main", True),
        ("workflow_dispatch", "false", "refs/heads/feat/x", False),
        ("workflow_dispatch", "", "refs/heads/main", False),
        ("workflow_dispatch", "False", "refs/heads/main", False),
        ("push", "", "refs/heads/main", False),
        ("pull_request", "false", "refs/pull/1/merge", False),
    ],
)
def test_the_mode_is_live_only_on_a_tag_push_or_an_explicit_dispatch_from_main(
    event, dry_run, ref, live
):
    assert fan_out.is_live(event, dry_run, ref) is live


def test_the_mode_command_writes_a_step_output(tmp_path):
    out = tmp_path / "out"
    env = {"EVENT": "workflow_dispatch", "DRY_RUN": "true", "REF": "refs/heads/main",
           "GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, str(GATE), "mode"], env=env,
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

    # reads
    def list_repos(self, owner):
        return [
            {"name": n, "archived": r.get("archived", False), "topics": r.get("topics", []),
             "default_branch": "main"}
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

    def find_prs(self, owner, repo, branch):
        return [dict(p) for p in self.repos[repo].get("prs", []) if p["head"] == branch]

    # writes
    def _write(self, *what):
        if self.forbid_writes:
            raise AssertionError(f"a write in a dry run: {what}")
        self.writes.append(what)

    def create_ref(self, owner, repo, branch, sha):
        self._write("create_ref", repo, branch)
        self.repos[repo]["branches"][branch] = sha

    def reset_ref(self, owner, repo, branch, sha):
        self._write("reset_ref", repo, branch)
        self.repos[repo]["branches"][branch] = sha

    def put_file(self, owner, repo, path, branch, text, blob, message):
        self._write("put_file", repo, branch)
        r = self.repos[repo]
        parent = r["branches"][branch]
        assert blob == f"blob-{parent}-{path}", "stale blob sha"
        self.next_sha += 1
        sha = f"c{self.next_sha}"
        r["commits"][sha] = {**r["commits"][parent], path: text}
        r["branches"][branch] = sha

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
    assert {p["head"] for p in prs} == {"actions-adopt/v1.35.0"}
    for repo in FIXTURES:
        r = gh.repos[repo]
        text = r["commits"][r["branches"]["actions-adopt/v1.35.0"]][".pre-commit-config.yaml"]
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
    assert kinds == ["reset_ref", "put_file"]
    assert len(r["prs"]) == 1
    head = r["commits"][r["branches"]["actions-adopt/v1.35.0"]]
    assert head[".pre-commit-config.yaml"].endswith("# later\n")


def test_a_branch_left_without_a_pull_request_is_reused_not_duplicated():
    gh = Fake(estate())
    gh.repos["argocd"]["branches"]["actions-adopt/v1.35.0"] = "base"
    rows, _ = fan_out.run(gh, "yadgarhq", "v1.35.0", CLASSES, live=True)
    writes = [w for w in gh.writes if w[1] == "argocd"]
    assert [w[0] for w in writes] == ["reset_ref", "put_file", "create_pr"]
    assert by_repo(rows)["argocd"]["status"] == "opened"


def test_a_pull_request_closed_without_merging_is_not_reopened():
    repos = estate()
    repos["docs"]["prs"] = [{"number": 7, "head": "actions-adopt/v1.35.0", "state": "closed",
                             "merged": False, "url": "u", "title": "t", "body": "b"}]
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


def test_the_body_passes_the_template_gate_in_both_modes():
    body = generated_body()
    problems, bump, count = pr_body.review(body, wrapped=False)
    assert problems == []
    assert (bump, count) == ("patch", 1)
    problems, wbump, wcount = pr_body.review(wrap(body), wrapped=True)
    assert problems == []
    assert (wbump, wcount) == (bump, count)


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
    repos = [
        {"name": "actions", "archived": False, "topics": []},
        {"name": "deploy", "archived": True, "topics": ["yadgar-class-base"]},
        {"name": "docs", "archived": False, "topics": ["yadgar-class-base"]},
        {"name": "double", "archived": False,
         "topics": ["yadgar-class-base", "yadgar-class-chart"]},
        {"name": "stray", "archived": False, "topics": ["yadgar-deployable"]},
        {"name": "task", "archived": False,
         "topics": ["yadgar-deployable", "yadgar-class-service"]},
    ]
    assert fan_out.consumers(repos, CLASSES) == ["docs", "task"]


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
