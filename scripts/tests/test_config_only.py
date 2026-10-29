"""Yaadgaar 442: a hook rev bump runs detect, precommit and passed, and nothing else.

Three layers, each with its own red cases:

1. `config_only.classify`, the one definition of the class.
2. `detect`'s step `c`, run VERBATIM out of `ci-pr.yaml` against a real git
   merge commit built in `tmp_path`, the way `actions/checkout` hands one to a
   pull request run.
3. `ci_verdict.make_config_only_resolver`, which re-derives the value from a
   stubbed GitHub API before `ci / passed` accepts one skip.

Every green case sits beside red ones on the same fixture, so a classifier
that said `true` to everything fails here at once.

Run: python3 -m pytest scripts/tests/ -q
"""

import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"
sys.path.insert(0, str(ROOT / "scripts"))

import ci_verdict  # noqa: E402
import config_only  # noqa: E402

# The shape of a real consumer's file (yadgarhq/store at the v1.36.1 pin):
# another remote block above, the actions block, a local block below.
BASE_CONFIG = """\
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
  # Pinned to a tag; the fan-out moves it.

  - repo: https://github.com/yadgarhq/actions
    rev: v1.36.1
    hooks:
      - id: gitleaks
      - id: prettier
  - repo: local
    hooks:
      - id: fmt
        name: fmt
        entry: cargo fmt
        language: system
"""

ACTIONS_REV = "    rev: v1.36.1"


def bumped(text=BASE_CONFIG, to="v1.37.0", old=ACTIONS_REV):
    assert text.count(old) == 1
    return text.replace(old, f"    rev: {to}")


# ---------------------------------------------------------------------------
# 1. The class
# ---------------------------------------------------------------------------


def test_a_forward_bump_of_the_actions_rev_is_in_the_class():
    assert config_only.classify(BASE_CONFIG, bumped()) == ("v1.36.1", "v1.37.0")


def test_versions_compare_as_numbers_not_as_text():
    """ADR-0690's 19 downgrades: `v1.10.0` is newer than `v1.9.0`, and text says otherwise."""
    nine = bumped(to="v1.9.0")
    ten = bumped(to="v1.10.0")
    assert config_only.classify(nine, ten) == ("v1.9.0", "v1.10.0")
    with pytest.raises(config_only.NotConfigOnly, match="forward"):
        config_only.classify(ten, nine)


@pytest.mark.parametrize("to", ["v1.36.0", "v0.99.99", "v1.35.9"])
def test_a_downgrade_is_not_in_the_class(to):
    with pytest.raises(config_only.NotConfigOnly, match="forward"):
        config_only.classify(BASE_CONFIG, bumped(to=to))


def test_an_unchanged_file_is_not_in_the_class():
    with pytest.raises(config_only.NotConfigOnly, match="0 lines differ"):
        config_only.classify(BASE_CONFIG, BASE_CONFIG)


def test_a_rev_line_and_a_hook_id_line_are_not_in_the_class():
    new = bumped().replace("      - id: prettier", "      - id: prettier-x")
    with pytest.raises(config_only.NotConfigOnly, match="2 lines differ"):
        config_only.classify(BASE_CONFIG, new)


def test_reordered_hook_ids_are_not_in_the_class():
    """chart#57's shape: same ids, new order, plus the bump."""
    new = bumped().replace(
        "      - id: gitleaks\n      - id: prettier",
        "      - id: prettier\n      - id: gitleaks",
    )
    with pytest.raises(config_only.NotConfigOnly):
        config_only.classify(BASE_CONFIG, new)


def test_an_added_line_is_not_in_the_class():
    new = bumped().replace("      - id: prettier\n", "      - id: prettier\n      - id: extra\n")
    with pytest.raises(config_only.NotConfigOnly, match="gained or lost"):
        config_only.classify(BASE_CONFIG, new)


def test_a_bump_in_another_repos_block_is_not_in_the_class():
    new = bumped(old="    rev: v5.0.0", to="v5.1.0")
    with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
        config_only.classify(BASE_CONFIG, new)


def test_a_block_whose_url_is_not_exactly_the_actions_url_is_not_in_the_class():
    for url in (
        "https://github.com/yadgarhq/actions-fork",
        "https://github.com/yadgarhq/actions.git",
        "https://github.com/yadgarhq/actions  # pinned",
        "https://github.com/someone/actions",
    ):
        old = BASE_CONFIG.replace("https://github.com/yadgarhq/actions", url)
        with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
            config_only.classify(old, bumped(old))


def test_the_nearest_block_header_decides():
    """An actions header further up does not cover a rev line under another header."""
    old = BASE_CONFIG.replace(
        "  - repo: https://github.com/yadgarhq/actions\n    rev: v1.36.1",
        "  - repo: https://github.com/yadgarhq/actions\n  - repo: https://x/y\n    rev: v1.36.1",
    )
    new = old.replace("  - repo: https://x/y\n    rev: v1.36.1", "  - repo: https://x/y\n    rev: v1.37.0")
    with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
        config_only.classify(old, new)


def test_a_comment_or_blank_line_between_header_and_rev_stays_in_the_block():
    old = BASE_CONFIG.replace(
        "  - repo: https://github.com/yadgarhq/actions\n    rev: v1.36.1",
        "  - repo: https://github.com/yadgarhq/actions\n  # moved by the fan-out\n\n    rev: v1.36.1",
    )
    assert config_only.classify(old, bumped(old)) == ("v1.36.1", "v1.37.0")


EVIL_AFTER_ACTIONS = {
    # A sequence entry whose FIRST key is not `repo:`. Its `repo:` and `rev:`
    # sit at four spaces, so only the four-space stop in `block_of` keeps the
    # walk from climbing past `  - hooks:` into the actions block above.
    "a hooks-first entry": "  - hooks: [{id: e}]\n    repo: https://github.com/evil/x\n    rev: v2.0.0\n",
    # The bare `-` form: the entry's keys start on the next line.
    "a bare - entry": "  -\n    repo: https://github.com/evil/x\n    rev: v2.0.0\n",
}


@pytest.mark.parametrize("name", sorted(EVIL_AFTER_ACTIONS))
def test_a_rev_in_an_entry_after_the_actions_block_is_not_in_the_class(name):
    old = BASE_CONFIG.replace("  - repo: local\n", EVIL_AFTER_ACTIONS[name] + "  - repo: local\n")
    new = bumped(old, to="v2.1.0", old="    rev: v2.0.0")
    with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
        config_only.classify(old, new)


def test_a_rev_line_outside_any_block_is_not_in_the_class():
    old = "    rev: v1.0.0\n"
    with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
        config_only.classify(old, "    rev: v1.1.0\n")


def test_leaving_the_block_stops_the_walk():
    """A top-level key between the actions header and the rev line ends the block."""
    old = BASE_CONFIG.replace(
        "  - repo: https://github.com/yadgarhq/actions\n    rev: v1.36.1",
        "  - repo: https://github.com/yadgarhq/actions\nci:\n    rev: v1.36.1",
    )
    with pytest.raises(config_only.NotConfigOnly, match="yadgarhq/actions block"):
        config_only.classify(old, bumped(old))


@pytest.mark.parametrize(
    "line",
    [
        "    rev: v1.37.0-rc1",
        "    rev: v1.37",
        "    rev: v01.37.0",
        "    rev: 1.37.0",
        "    rev: v1.37.0 # comment",
        "    rev: v1.37.0\r",
        "     rev: v1.37.0",
        "    rev: 'v1.37.0'",
        "    rev: v1.37.0 ",
        "    rev: deadbeef",
    ],
)
def test_only_an_exact_vX_Y_Z_line_is_a_rev_line(line):
    new = BASE_CONFIG.replace(ACTIONS_REV, line)
    with pytest.raises(config_only.NotConfigOnly, match="is not a"):
        config_only.classify(BASE_CONFIG, new)


def test_a_newline_after_the_rev_is_not_absorbed_by_the_match():
    """`fullmatch`, not `match(...$)`: Python's `$` also matches before a final newline."""
    with pytest.raises(config_only.NotConfigOnly):
        config_only.version("    rev: v1.37.0\n")


# ---------------------------------------------------------------------------
# 2. `detect`'s step `c`, run against a real merge commit
# ---------------------------------------------------------------------------


def _git_env():
    """The caller's `GIT_*` out; `git commit` exports `GIT_DIR` to its hooks."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_git_env(),
    ).stdout.strip()


def merge_ref(tmp_path, change, base_files=None, commits=1):
    """A repository checked out the way a pull request run is: base merged with head.

    `change(repo)` edits the work tree; it is committed `commits` times over
    (the fan-out pushes several commits). Returns `(repo, head_sha)`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    files = {".pre-commit-config.yaml": BASE_CONFIG, "src/lib.rs": "fn main() {}\n"}
    extra = dict(base_files or {})
    executable = extra.pop("executable", False)
    files.update(extra)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    if executable:
        (repo / ".pre-commit-config.yaml").chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "head")
    for n in range(commits):
        if n == commits - 1:
            change(repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", f"head {n}")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "unrelated.txt").write_text("main moved on\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moves")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", head)
    return repo, head


def detect_step():
    steps = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))["jobs"]["detect"]["steps"]
    matching = [s for s in steps if s.get("id") == "c"]
    assert len(matching) == 1, "`detect` no longer has exactly one step `c`"
    return matching[0]


def run_step(tmp_path, repo, head, event="pull_request", action="synchronize"):
    """Step `c`'s `run:` verbatim, as a consumer runs it, with the classifier from `.ci-versions`."""
    (repo / ".ci-versions" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "config_only.py", repo / ".ci-versions" / "scripts")
    script = tmp_path / "step_c.sh"
    script.write_text(detect_step()["run"], encoding="utf-8")
    out, summary, temp = tmp_path / "out", tmp_path / "summary", tmp_path / "rt"
    out.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    temp.mkdir()
    env = _git_env()
    env.update(
        UNDER_REVIEW="yadgarhq/store", EVENT=event, ACTION=action, HEAD_SHA=head,
        GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summary), RUNNER_TEMP=str(temp),
    )
    proc = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script)],
        cwd=repo, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (repo / ".ci-versions").exists(), "step `c` must delete `.ci-versions`"
    written = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return written, summary.read_text()


def bump(to="v1.37.0", old=ACTIONS_REV):
    def change(repo):
        path = repo / ".pre-commit-config.yaml"
        path.write_text(bumped(path.read_text(), to, old), encoding="utf-8")

    return change


def both(*changes):
    def change(repo):
        for c in changes:
            c(repo)

    return change


def write(name, text):
    def change(repo):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")

    return change


def test_detect_reports_a_hook_rev_bump(tmp_path):
    repo, head = merge_ref(tmp_path, bump(), commits=3)
    written, summary = run_step(tmp_path, repo, head)
    assert written == {
        "config_only": "true",
        "config_only_from": "v1.36.1",
        "config_only_to": "v1.37.0",
        # The base tip the merge was built on: `main` after it moved on.
        "config_only_base": git(repo, "rev-parse", "HEAD^1"),
    }
    assert "v1.36.1 -> v1.37.0" in summary


@pytest.mark.parametrize(
    ("name", "change", "base_files"),
    [
        ("config + one .rs file", both(bump(), write("src/lib.rs", "fn x() {}\n")), None),
        ("two files", both(bump(), write("README.md", "x\n")), None),
        ("rev + hook id", both(bump(), lambda r: (r / ".pre-commit-config.yaml").write_text(
            bumped().replace("id: prettier", "id: prettier-x"))), None),
        ("downgrade", bump(to="v1.36.0"), None),
        ("same version (no config change)", lambda r: None, None),
        ("another repo's block", bump(to="v5.1.0", old="    rev: v5.0.0"), None),
        ("rename of the config", lambda r: (
            (r / ".pre-commit-config.yaml").rename(r / "pre-commit.yaml")), None),
        ("rename of another file", both(bump(), lambda r: (r / "a.txt").rename(r / "b.txt")),
         {"a.txt": "same\n"}),
        ("mode change", both(bump(), lambda r: (r / ".pre-commit-config.yaml").chmod(0o755)), None),
        ("mode change back to 644", both(bump(), lambda r: (r / ".pre-commit-config.yaml").chmod(0o644)),
         {"executable": True}),
        ("the same bump in another file", lambda r: (r / "hooks.yaml").write_text(bumped()),
         {"hooks.yaml": BASE_CONFIG}),
        ("symlink", lambda r: ((r / ".pre-commit-config.yaml").unlink(),
                                (r / "real.yaml").write_text(bumped()),
                                (r / ".pre-commit-config.yaml").symlink_to("real.yaml")), None),
    ],
)
def test_detect_runs_everything_for_any_other_diff(tmp_path, name, change, base_files):
    repo, head = merge_ref(tmp_path, change, base_files=base_files)
    written, _ = run_step(tmp_path, repo, head)
    assert written == {"config_only": "false"}, name


def test_detect_runs_everything_when_head_is_not_the_merge(tmp_path):
    repo, head = merge_ref(tmp_path, bump())
    git(repo, "checkout", "-q", head)
    written, _ = run_step(tmp_path, repo, head)
    assert written == {"config_only": "false"}


def test_detect_runs_everything_when_the_merge_is_of_another_head(tmp_path):
    repo, _ = merge_ref(tmp_path, bump())
    written, _ = run_step(tmp_path, repo, "0" * 40)
    assert written == {"config_only": "false"}


@pytest.mark.parametrize(
    ("event", "action"),
    [("pull_request", "edited"), ("push", ""), ("workflow_dispatch", ""), ("", "")],
)
def test_detect_reports_only_on_a_pull_request_that_moved(tmp_path, event, action):
    repo, head = merge_ref(tmp_path, bump())
    written, _ = run_step(tmp_path, repo, head, event=event, action=action)
    assert written == {"config_only": "false"}


def test_detect_runs_everything_when_the_classifier_crashes(tmp_path):
    repo, head = merge_ref(tmp_path, bump())
    (repo / ".ci-versions" / "scripts").mkdir(parents=True)
    (repo / ".ci-versions" / "scripts" / "config_only.py").write_text(
        "print('config_only=true')\nraise SystemExit(3)\n", encoding="utf-8"
    )
    script = tmp_path / "s.sh"
    script.write_text(detect_step()["run"], encoding="utf-8")
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    (tmp_path / "rt").mkdir()
    env = _git_env()
    env.update(UNDER_REVIEW="yadgarhq/store", EVENT="pull_request", ACTION="opened",
               HEAD_SHA=head, GITHUB_OUTPUT=str(out),
               GITHUB_STEP_SUMMARY=str(tmp_path / "sum"), RUNNER_TEMP=str(tmp_path / "rt"))
    proc = subprocess.run(["bash", str(script)], cwd=repo, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.read_text() == "config_only=false\n"


def test_step_c_reads_the_event_rather_than_being_told():
    env = detect_step().get("env") or {}
    assert env == {
        "UNDER_REVIEW": "${{ github.repository }}",
        "EVENT": "${{ github.event_name }}",
        "ACTION": "${{ github.event.action }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }


def test_detect_checks_out_the_merge_with_its_parents_and_the_classifier():
    detect = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))["jobs"]["detect"]
    first = detect["steps"][0]
    assert first["with"]["fetch-depth"] == 2
    assert first["with"]["ref"] == "${{ inputs.ref }}"
    sparse = [s for s in detect["steps"] if (s.get("with") or {}).get("path") == ".ci-versions"]
    assert len(sparse) == 1
    assert "scripts/config_only.py" in sparse[0]["with"]["sparse-checkout"].split()
    ids = [s.get("id") for s in detect["steps"]]
    assert ids.index("versions") < ids.index("c")
    for key in ("config_only", "config_only_from", "config_only_to", "config_only_base"):
        assert detect["outputs"][key] == "${{ steps.c.outputs.%s }}" % key


# ---------------------------------------------------------------------------
# 3. The gate re-derives it
# ---------------------------------------------------------------------------

REPO = "yadgarhq/store"
HEAD = "35b0fcda0a23316f5abc79113bf217c1864efbe9"
MERGE_BASE = "5f854fbc12129a0a75fd4e01e260f9c148712446"
# The base branch's tip, ahead of the merge base, as `detect` saw it.
TIP = "c0ffee" + "1" * 34
OLD_BLOB = "73d0765" + "0" * 33
NEW_BLOB = "a915f0d" + "0" * 33
PATCH = (
    "@@ -7,7 +7,7 @@ repos:\n   # Pinned to a tag; the fan-out moves it.\n \n"
    "   - repo: https://github.com/yadgarhq/actions\n-    rev: v1.36.1\n+    rev: v1.37.0\n"
    "     hooks:\n       - id: gitleaks\n       - id: prettier"
)


# `classify` accepts both of these; `parsed_agrees` must not.
DUPLICATE_REPO_KEY = BASE_CONFIG.replace(
    "  - repo: https://github.com/yadgarhq/actions\n",
    "  - repo: https://github.com/yadgarhq/actions\n    repo: https://github.com/evil/x\n",
)
QUOTED_FAKE_REV = "    rev: v1.36.1\n  \""
QUOTED_FAKE = BASE_CONFIG.replace(
    "        entry: cargo fmt\n",
    "        entry: \"echo\n  - repo: https://github.com/yadgarhq/actions\n    rev: v1.36.1\n  \"\n",
)

QUOTED_FAKE_BUMPED = QUOTED_FAKE.replace(QUOTED_FAKE_REV, "    rev: v1.37.0\n  \"")


def api(old=BASE_CONFIG, new=None, files=None, mode_old="100644", mode_new="100644",
        truncated=False, type_new="blob", **file_override):
    """The five responses the gate reads, keyed by URL, shaped as store#40's were."""
    new = bumped() if new is None else new
    entry = {
        "filename": ".pre-commit-config.yaml", "status": "modified", "additions": 1,
        "deletions": 1, "changes": 2, "previous_filename": None, "patch": PATCH,
    }
    entry.update(file_override)
    blob = lambda text: {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}  # noqa: E731
    tree = lambda mode, sha, kind="blob": {"truncated": truncated, "tree": [  # noqa: E731
        {"path": "Cargo.toml", "mode": "100644", "type": "blob", "sha": "1" * 40},
        {"path": ".pre-commit-config.yaml", "mode": mode, "type": kind, "sha": sha}]}
    return {
        ci_verdict.branch_url(REPO, "main"): {"object": {"sha": TIP}},
        ci_verdict.compare_url(REPO, "main", HEAD): {
            "merge_base_commit": {"sha": MERGE_BASE},
            "files": [entry] if files is None else files,
        },
        ci_verdict.tree_url(REPO, MERGE_BASE): tree(mode_old, OLD_BLOB),
        ci_verdict.tree_url(REPO, HEAD): tree(mode_new, NEW_BLOB, type_new),
        ci_verdict.blob_url(REPO, OLD_BLOB): blob(old),
        ci_verdict.blob_url(REPO, NEW_BLOB): blob(new),
    }


def detect_says(value="true", frm="v1.36.1", to="v1.37.0", base=TIP):
    return {"detect": {"result": "success", "outputs": {
        "config_only": value, "config_only_from": frm, "config_only_to": to,
        "config_only_base": base}}}


def resolve(responses, needs=None, base_ref="main", sha=HEAD):
    urls = []

    def fetch(url):
        urls.append(url)
        if url not in responses:
            raise AssertionError(f"unexpected read {url}")
        return responses[url]

    r = ci_verdict.make_config_only_resolver(
        needs=detect_says() if needs is None else needs,
        repo=REPO, base_ref=base_ref, sha=sha, token="t", fetch=fetch,
    )
    return r("expr"), urls


def test_the_gate_earns_a_true_from_the_api():
    value, urls = resolve(api())
    assert value == "true"
    assert sorted(urls) == sorted(api())


@pytest.mark.parametrize("value", ["false", ""])
def test_nothing_is_read_unless_detect_says_true(value):
    out, urls = resolve({}, needs=detect_says(value))
    assert out == value and urls == []


REFUSALS = {
    "two files": dict(files=[{"filename": ".pre-commit-config.yaml"}, {"filename": "src/lib.rs"}]),
    "config + one .rs file": dict(files=[
        {"filename": ".pre-commit-config.yaml", "status": "modified"},
        {"filename": "src/main.rs", "status": "modified"}]),
    "no files": dict(files=[]),
    "config + .rs, the config entry complete": dict(files=[
        {"filename": ".pre-commit-config.yaml", "status": "modified", "additions": 1,
         "deletions": 1, "previous_filename": None, "patch": PATCH},
        {"filename": "src/main.rs", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}]),
    "status changed": dict(status="changed"),
    "status added": dict(status="added"),
    "not a blob": dict(type_new="tree"),
    "truncated: no patch": dict(patch=None),
    "truncated: empty patch": dict(patch=""),
    "renamed": dict(status="renamed", previous_filename="old.yaml"),
    "previous_filename on a modify": dict(previous_filename="old.yaml"),
    "another file": dict(filename="src/lib.rs"),
    "two additions": dict(additions=2),
    "rev + hook id": dict(new=bumped().replace("id: prettier", "id: prettier-x")),
    "extra line": dict(new=bumped() + "# trailing\n"),
    "downgrade": dict(new=bumped(to="v1.36.0"), patch=PATCH.replace("v1.37.0", "v1.36.0")),
    "another repo's block": dict(new=bumped(old="    rev: v5.0.0", to="v5.1.0")),
    "mode change": dict(mode_new="100755"),
    "symlink": dict(mode_old="120000"),
    "truncated tree": dict(truncated=True),
    "submodule": dict(mode_new="160000"),
    "patch disagrees with blobs": dict(patch=PATCH.replace("v1.37.0", "v1.38.0")),
    # F3: shapes the line classifier accepts and only a YAML parse refuses.
    "a duplicate repo key": dict(
        old=DUPLICATE_REPO_KEY, new=bumped(DUPLICATE_REPO_KEY)),
    "a fake actions block in a quoted entry": dict(
        old=QUOTED_FAKE, new=QUOTED_FAKE_BUMPED),
}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_the_gate_refuses_whatever_the_api_does_not_confirm(name):
    with pytest.raises(ci_verdict.Refused):
        resolve(api(**REFUSALS[name]))


def test_the_gate_refuses_a_rev_that_disagrees_with_detect():
    with pytest.raises(ci_verdict.Refused, match="detect"):
        resolve(api(), needs=detect_says(to="v1.38.0"))
    with pytest.raises(ci_verdict.Refused, match="detect"):
        resolve(api(), needs=detect_says(frm="v1.36.0"))


@pytest.mark.parametrize(("base_ref", "sha"), [("", HEAD), ("main", ""), ("main", "abc")])
def test_the_gate_refuses_when_the_change_is_not_named(base_ref, sha):
    with pytest.raises(ci_verdict.Refused, match="not named"):
        resolve(api(), base_ref=base_ref, sha=sha)


def test_an_api_error_reddens_the_gate(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(ci_verdict.urllib.request, "urlopen", boom)
    r = ci_verdict.make_config_only_resolver(detect_says(), REPO, "main", HEAD, "t")
    with pytest.raises(ci_verdict.Refused, match="contents: read"):
        r("expr")


def test_the_compare_is_three_dot_against_the_base_branch():
    assert ci_verdict.compare_url(REPO, "main", HEAD) == (
        f"https://api.github.com/repos/{REPO}/compare/main...{HEAD}"
    )
    assert "release%2F1" in ci_verdict.compare_url(REPO, "release/1", HEAD)


# ---------------------------------------------------------------------------
# The whole gate over the real workflow
# ---------------------------------------------------------------------------

SKIPPED_BY_CONFIG_ONLY = [
    "workflows", "vulnerabilities", "test", "proto",
    "portability", "service_immutable", "pre_pin_parent", "release_ready",
]


def conditions():
    return ci_verdict.load_conditions(CI_PR, "passed")


def test_exactly_the_eight_jobs_carry_the_conjunct():
    carries = [j for j, c in conditions() if c is not None and ci_verdict.CONFIG_ONLY_REF in str(c)]
    assert sorted(carries) == sorted(SKIPPED_BY_CONFIG_ONLY)
    # `precommit` IS the check a hook bump gets, and it is also what tells a
    # full sibling from a text-only one in `pending_siblings`.
    assert "precommit" not in carries


def test_config_only_is_not_a_plain_fact():
    assert ci_verdict.CONFIG_ONLY_REF not in ci_verdict._FACTS


def test_the_gate_step_is_given_the_base_branch():
    passed = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))["jobs"]["passed"]
    env = passed["steps"][-1]["env"]
    assert env["BASE_REF"] == "${{ github.base_ref }}"
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"


def bump_run(**results):
    """`toJSON(needs)` for a bot's hook bump: detect, precommit run; the rest skip."""
    ctx = {}
    for job, _ in conditions():
        default = "skipped" if job in SKIPPED_BY_CONFIG_ONLY + ["template"] else "success"
        ctx[job] = {"result": results.get(job, default), "outputs": {}}
    ctx["detect"]["outputs"] = {"rust": "true", "proto": "false", "text_edit": "false",
                                **detect_says()["detect"]["outputs"]}
    return ctx


def gate(ctx, responses, author="Bot"):
    def fetch(url):
        return responses[url]

    ci_verdict.register_deferred_reference(
        ci_verdict.TEXT_EDIT_REF,
        ci_verdict.make_text_edit_resolver(ctx, REPO, HEAD, "1", "ci / passed", "", fetch=fetch),
    )
    ci_verdict.register_deferred_reference(
        ci_verdict.CONFIG_ONLY_REF,
        ci_verdict.make_config_only_resolver(ctx, REPO, "main", HEAD, "t", fetch=fetch),
    )
    try:
        resolve = ci_verdict.make_resolver("pull_request", author, REPO, ctx)
        return ci_verdict.verdict(conditions(), ctx, resolve)
    finally:
        ci_verdict._DEFERRED.clear()


def test_a_store_40_shaped_bump_passes_on_detect_and_precommit_alone():
    ok, rows = gate(bump_run(), api())
    assert ok, [r for r in rows if not r[4]]
    ran = sorted(r[0] for r in rows if r[1])
    assert ran == ["detect", "precommit"], ran


def test_a_hook_bump_must_still_run_precommit():
    ok, _ = gate(bump_run(precommit="skipped"), api())
    assert not ok


def test_the_same_skips_without_the_fact_are_refused():
    ctx = bump_run()
    ctx["detect"]["outputs"]["config_only"] = "false"
    ok, _ = gate(ctx, {})
    assert not ok


def test_the_same_skips_on_a_wider_diff_are_refused():
    with pytest.raises(ci_verdict.Refused):
        gate(bump_run(), api(**REFUSALS["config + one .rs file"]))


@pytest.mark.parametrize("merge_base", [None, {}, {"sha": ""}, {"sha": "main"}])
def test_the_gate_refuses_a_compare_with_no_merge_base(merge_base):
    responses = api()
    compare = responses[ci_verdict.compare_url(REPO, "main", HEAD)]
    if merge_base is None:
        del compare["merge_base_commit"]
    else:
        compare["merge_base_commit"] = merge_base
    with pytest.raises(ci_verdict.Refused, match="merge base"):
        resolve(responses)


@pytest.mark.parametrize(
    "blob",
    [
        {"encoding": "utf-8", "content": "x"},
        {"encoding": "base64"},
        {"encoding": "base64", "content": base64.b64encode(b"\xff\xfe").decode()},
    ],
)
def test_the_gate_refuses_a_blob_it_cannot_decode(blob):
    responses = api()
    responses[ci_verdict.blob_url(REPO, NEW_BLOB)] = blob
    with pytest.raises(ci_verdict.Refused, match="blob"):
        resolve(responses)


def test_main_registers_the_resolver_and_reads_nothing_on_an_ordinary_run():
    """Through the subprocess, as CI runs it: no `config_only` output, no API read."""
    ctx = {job: {"result": "success", "outputs": {}} for job, _ in conditions()}
    for job in ("test", "proto", "template"):
        ctx[job]["result"] = "skipped"
    ctx["detect"]["outputs"] = {"rust": "false", "proto": "false", "text_edit": "false",
                                "config_only": "false"}
    env = {"PATH": "/usr/bin:/bin", "WORKFLOW": str(CI_PR), "NEEDS": json.dumps(ctx),
           "EVENT": "pull_request", "AUTHOR_TYPE": "Bot", "REPO": REPO,
           "PYTHONPATH": str(ROOT / "scripts")}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_verdict.py")],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    ctx["detect"]["outputs"]["config_only"] = "true"
    env["NEEDS"] = json.dumps(ctx)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_verdict.py")],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 1 and "not named" in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# F3: the text check passes and the parse refuses
# ---------------------------------------------------------------------------

PARSE_ONLY = {
    "a duplicate repo key": (DUPLICATE_REPO_KEY, bumped(DUPLICATE_REPO_KEY), "appears twice"),
    "a fake actions block in a quoted entry": (QUOTED_FAKE, QUOTED_FAKE_BUMPED, "parses differently"),
}


@pytest.mark.parametrize("name", sorted(PARSE_ONLY))
def test_the_line_classifier_accepts_what_only_the_parse_refuses(name):
    old, new, why = PARSE_ONLY[name]
    assert config_only.classify(old, new) == ("v1.36.1", "v1.37.0"), "not a parse-only case"
    with pytest.raises(ci_verdict.Refused, match=why):
        ci_verdict.parsed_agrees(old, new, "v1.36.1", "v1.37.0")


def test_the_parse_accepts_the_real_bump():
    ci_verdict.parsed_agrees(BASE_CONFIG, bumped(), "v1.36.1", "v1.37.0")


@pytest.mark.parametrize(
    ("old", "why"),
    [
        ("repos: {}\n", "no `repos` list"),
        ("- 1\n", "no `repos` list"),
        ("repos:\n  - repo: local\n", "0 yadgarhq/actions entries"),
        (BASE_CONFIG + "  - repo: https://github.com/yadgarhq/actions\n    rev: v1.36.1\n",
         "2 yadgarhq/actions entries"),
        ("repos: [ [\n", "not YAML"),
        ("repos:\n  - {? [a]: 1}\n", "unhashable"),
    ],
)
def test_the_parse_refuses_a_file_it_cannot_pin_to_one_entry(old, why):
    with pytest.raises(ci_verdict.Refused, match=why):
        ci_verdict.parsed_agrees(old, old, "v1.36.1", "v1.37.0")


def test_the_parse_requires_the_entry_rev_to_be_the_changed_line():
    with pytest.raises(ci_verdict.Refused, match="parsed rev"):
        ci_verdict.parsed_agrees(BASE_CONFIG, bumped(), "v1.30.0", "v1.37.0")


def test_the_parse_refuses_a_duplicate_key_on_the_head_side_too():
    with pytest.raises(ci_verdict.Refused, match="appears twice"):
        ci_verdict.parsed_agrees(BASE_CONFIG, bumped(DUPLICATE_REPO_KEY), "v1.36.1", "v1.37.0")


# ---------------------------------------------------------------------------
# F4: the base has not moved since `detect`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tip", [MERGE_BASE, "", None, "c0ffee"])
def test_the_gate_refuses_when_the_base_tip_is_not_detects(tip):
    responses = api()
    responses[ci_verdict.branch_url(REPO, "main")] = {"object": {"sha": tip}} if tip is not None else {}
    with pytest.raises(ci_verdict.Refused, match="same base"):
        resolve(responses)


@pytest.mark.parametrize("claimed", ["", None, "c0ffee"])
def test_the_gate_refuses_when_detect_names_no_base(claimed):
    needs = detect_says(base=claimed)
    with pytest.raises(ci_verdict.Refused, match="same base"):
        resolve(api(), needs=needs)


def test_the_gate_refuses_when_neither_side_names_a_base():
    responses = api()
    responses[ci_verdict.branch_url(REPO, "main")] = {}
    with pytest.raises(ci_verdict.Refused, match="same base"):
        resolve(responses, needs=detect_says(base=None))


def test_the_base_tip_is_read_by_branch_name():
    assert ci_verdict.branch_url(REPO, "main") == (
        f"https://api.github.com/repos/{REPO}/git/ref/heads/main"
    )
