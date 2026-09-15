"""What the `version` job derives, and — as much — what it REFUSES loudly.

WHY THIS FILE EXISTS AT ALL. The derivation used to live in a `python3 - <<'PY'`
heredoc inside `ci-pr.yaml`, and `hooks/run_block_size.py` says what that costs:
"a heredoc is undiffable, untestable and invisible to every linter". Eight
branches of that heredoc ended in no tag and seven of the eight left the job
GREEN, which is this organisation's most-repeated defect class (ADR-0689,
ADR-0691, ledgers 701, 715, 720). A refusal nobody can test is a refusal nobody
can trust, so the branches moved into `scripts/next_version.py` and the verdict
of every one of them is pinned here.

THE THREE LAYERS, and each answers a question the one above it cannot.

  `derive` — the branch table, called directly. Fast, and it can construct
  shapes that do not exist in any repository yet.

  A REAL GIT REPOSITORY, built per test and run through the script as a
  subprocess. This is what proves the git plumbing agrees with the branch
  table — `derive` believing a range is empty is not evidence `git log` says so.

  THE WORKFLOW ITSELF, parsed. A tested script that the workflow does not call
  is a tested script nothing runs, and the `cred` step's refusal is SHELL rather
  than Python, so the shipped shell is extracted and executed here.

Run: python3 -m pytest scripts/tests/ -q
"""

import os
import pathlib
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import next_version  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "next_version.py"
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"

BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
HUMAN = "Max Agahi <64974579+m-agahi@users.noreply.github.com>"


def message(changelog="- fix: a thing that was wrong", what="a thing"):
    """A squash commit message of the shape this estate actually merges."""
    return (
        f"## What\n\n{what}\n\n## Why\n\na reason\n\n## Changelog\n\n{changelog}\n\n"
        "## Verification\n\nran it\n\n## Risk\n\nnone\n"
    )


def call(last="v0.2.17", tags=("v0.2.17",), messages=(), authors=None, files=()):
    """`derive` with the arguments a caller would have read out of git."""
    if authors is None:
        authors = [HUMAN] * len(messages)
    return next_version.derive(last, list(tags), list(messages), list(authors), list(files))


# ---------------------------------------------------------------------------
# The two branches that must stay GREEN, and why each one is not the others.
# ---------------------------------------------------------------------------


def test_no_baseline_at_all_stays_green():
    """`argocd`, `config`, `deploy`, `docs` and `estate` carry zero tags.

    All five are deliberate — two are GitOps manifest repositories Argo syncs
    from `main`, and a release model they never opted into must not redden every
    merge. This is the branch that keeps them quiet.
    """
    v = call(last="", tags=(), messages=[message()], authors=[HUMAN])
    assert v.rc == 0
    assert v.nxt == ""
    assert "no baseline" in "\n".join(v.lines).lower()


def test_head_already_released_stays_green():
    """An empty range is idempotent, and a re-run must not invent a refusal.

    THE ORDER OF THE CHECKS IS THE WHOLE POINT. `v` runs BEFORE the `is HEAD
    already released` step, so a refusal raised on an empty range pre-empts that
    step's green and reddens every re-run of a main-push run — and every LOST TAG
    RACE, which `ci-pr.yaml` documents as benign. The empty range is answered
    here, ahead of both the non-semver and the no-entries refusals.
    """
    v = call(messages=[], authors=[], files=[])
    assert v.rc == 0
    assert v.nxt == ""
    assert "already" in "\n".join(v.lines).lower()


def test_an_empty_range_is_not_a_bot_range():
    """`all(is_bot(a) for a in [])` is True, and that must not synthesise.

    A vacuous truth would let an empty range satisfy "every commit is
    bot-authored" and cut a second tag on a commit that already carries one.
    """
    v = call(messages=[], authors=[], files=["Cargo.lock"])
    assert v.nxt == ""
    assert v.rc == 0


# ---------------------------------------------------------------------------
# A non-semver baseline: RED, and the message decides which repair to attempt.
# ---------------------------------------------------------------------------


def test_non_semver_baseline_reddens():
    """`yadgarhq/yadgar` has cut ZERO automatic tags EVER, silently.

    `SEMVER` rejects its `v0.1.0a7` baseline on every merge, permanently, green:
    14 of its last 15 merges cut nothing, `src/` changes included. Going red
    forces the one-time human fix instead of hiding it forever.
    """
    v = call(last="v0.1.0a7", tags=("v0.1.0a7", "v0.1.0a6"), messages=[message()])
    assert v.rc == 1
    assert v.nxt == ""
    assert "v0.1.0a7" in "\n".join(v.lines)


def test_non_semver_baseline_with_no_plain_tag_asks_for_a_first_one():
    """`yadgar`'s repair is to MINT a plain tag: it has none to move to."""
    v = call(last="v0.1.0a7", tags=("v0.1.0a7", "v0.1.0a1"), messages=[message()])
    text = "\n".join(v.lines)
    assert "no plain" in text.lower()
    assert "by hand" in text.lower()


def test_non_semver_baseline_beside_plain_tags_names_the_stray():
    """`yadgarhq/actions`' shape, and a DIFFERENT repair from `yadgar`'s.

    `actions` carries 50 plain `vX.Y.Z` tags plus a bare `v1`, today 53 commits
    behind `main`, so `describe` returns `v1.22.0` and nothing reddens. Measured
    rather than assumed — and measured to be safe BY ACCIDENT. `git describe
    --tags --abbrev=0` breaks a distance tie in favour of the NEWER ANNOTATED
    tag: a fresh annotated `vANNOT` at `main` beat `v1.22.0`, while a lightweight
    `vLIGHT` at `main` lost to it. `v1` is an annotated tag object, so moving it
    to a release commit makes it win and reds the repository that publishes this
    workflow to all 19 consumers.

    The operator action there is to move or delete a stray, NOT to mint a first
    tag. A refusal that cannot tell the two apart sends them down the wrong path.
    """
    v = call(last="v1", tags=("v1", "v1.22.0", "v1.21.1"), messages=[message()])
    assert v.rc == 1
    text = "\n".join(v.lines)
    assert "v1.22.0" in text
    assert "stray" in text.lower()


# ---------------------------------------------------------------------------
# Change 1: a bot merge that would otherwise strand a dependency fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files",
    [
        ["Cargo.toml", "Cargo.lock"],
        # `gateway` `06a2e5e4` touched Cargo.lock ALONE, so the pair must not be
        # required — a grouped bump that moves no direct dependency edits only
        # the lockfile.
        ["Cargo.lock"],
        # `actions` `e473cb64` and `3e1a37d6` touched ONLY this, nested. Both
        # are in a tag-bearing repository and both cut nothing.
        ["containers/rust-build/Containerfile"],
    ],
)
def test_a_bot_only_dependency_merge_synthesises_a_patch(files):
    """The seven measured instances, and the two still stranded right now.

    `lifecycle` is one commit ahead of `v0.2.17` and `telemetry` one ahead of
    `v0.1.17`, both a dependabot Cargo bump, both untagged. The other five
    shipped only because a LATER human merge happened to cut a tag covering
    them — so whether a dependency fix ships depends on unrelated future
    activity, which is indistinguishable from working until nothing follows.
    """
    v = call(
        last="v0.2.17",
        tags=("v0.2.17",),
        messages=["build(deps): bump sha2 from 0.10.9 to 0.11.0 (#24)"],
        authors=[BOT],
        files=files,
    )
    assert v.rc == 0
    assert v.nxt == "0.2.18", "pre-1.0: a patch-classified entry bumps patch"
    assert any("fix(deps)" in e for e in v.entries)


def test_the_synthesised_entry_reaches_the_tag_message():
    """An annotated tag's message is the permanent record and cannot be moved.

    A synthesised entry that produced a version but no record of WHY would leave
    a tag nobody can account for.
    """
    v = call(
        messages=["build(deps): bump sha2 from 0.10.9 to 0.11.0 (#24)"],
        authors=[BOT],
        files=["Cargo.lock"],
    )
    assert len(v.entries) == 1
    assert v.entries[0].startswith("- fix(deps):")


def test_synthesis_survives_bump_for():
    """`matches` holds MATCH OBJECTS and `entries` holds strings.

    Appending the synthesised bullet as a string to `matches` would crash
    `bump_for` on `.group`. The version above is only derivable if both kinds
    went to the right list, so this asserts the classification directly.
    """
    v = call(
        last="v1.22.0",
        tags=("v1.22.0",),
        messages=["chore(deps): bump library/rust (#74)"],
        authors=[BOT],
        files=["containers/rust-build/Containerfile"],
    )
    assert v.nxt == "1.22.1", "at major >= 1 a patch entry bumps patch, unshifted"


def test_a_bot_merge_touching_no_artifact_manifest_stays_green():
    """The line is: does the bot's diff change WHAT SHIPS?

    Cargo.toml, Cargo.lock and a Containerfile do. A bumped action SHA under
    `.github/workflows/` does not — it changes how the repository is built, not
    the artifact — and synthesising `fix(deps):` for one would be a GUESS that a
    release is warranted, against this job's stated rule that writing no tag is
    always the cheaper mistake. The `github-actions` ecosystem is configured in
    every repository, so reddening this instead would be a weekly red with no
    repair available to anyone.
    """
    v = call(
        messages=["chore(deps): bump the actions group (#81)"],
        authors=[BOT],
        files=[".github/workflows/ci.yaml"],
    )
    assert v.rc == 0
    assert v.nxt == ""
    assert "ships" in "\n".join(v.lines).lower()


def test_a_human_merge_with_no_changelog_still_refuses():
    """The refusal Change 1 must not weaken, paired with every synthesis case.

    Ahead of this, the `gate` step has already reddened a human-authored HEAD
    carrying no readable Changelog — it exits 1 and the job never reaches the
    derivation. This is the second line: were that gate ever exempted, widened,
    or outrun, the derivation refuses on its own rather than shipping nothing
    quietly.
    """
    v = call(
        messages=["a commit message with no Changelog at all"],
        authors=[HUMAN],
        files=["Cargo.toml", "Cargo.lock"],
    )
    assert v.rc == 1
    assert v.nxt == ""


def test_one_human_commit_in_a_bot_range_blocks_synthesis():
    """`every commit in the range` is the condition, not `HEAD`.

    A human commit with no parseable bullets sitting behind a bot commit is the
    shape the synthesis must not cover — it would put a `fix(deps):` claim on a
    change nobody classified.
    """
    v = call(
        messages=["build(deps): bump sha2 (#24)", "an unclassified human commit"],
        authors=[BOT, HUMAN],
        files=["Cargo.lock"],
    )
    assert v.rc == 1


def test_a_real_changelog_is_never_overwritten_by_synthesis():
    """Synthesis fires only where entries would OTHERWISE be empty."""
    v = call(
        messages=[message("- feat: a new thing")],
        authors=[BOT],
        files=["Cargo.toml"],
    )
    assert v.rc == 0
    assert v.nxt == "0.2.18", "pre-1.0: a minor-classified entry bumps patch"
    assert not any("fix(deps)" in e for e in v.entries)


# ---------------------------------------------------------------------------
# The remaining refusals, and the arithmetic that must keep working.
# ---------------------------------------------------------------------------


def test_an_ambiguous_wrap_reddens():
    """Refusing to guess was already right; staying green about it was not.

    The two readings of a wrapped message imply different releases here, and
    nobody can withdraw a tag. Across 288 real wrapped sections the readings
    disagreed about the bump zero times, so this reddens nothing that exists —
    it makes the refusal visible on the day one does.
    """
    long = "x" * 71
    v = call(messages=[f"## Changelog\n\n- fix: a thing\n{long}\n- feat!: absorbed\n"])
    assert v.rc == 1
    assert v.nxt == ""
    assert "ambiguous" in "\n".join(v.lines).lower()


@pytest.mark.parametrize(
    "last,changelog,expected",
    [
        ("v0.2.17", "- fix: a thing", "0.2.18"),
        ("v0.2.17", "- feat: a thing", "0.2.18"),
        ("v0.2.17", "- feat!: a thing", "0.3.0"),
        ("v1.22.0", "- fix: a thing", "1.22.1"),
        ("v1.22.0", "- feat: a thing", "1.23.0"),
        ("v1.22.0", "- feat!: a thing", "2.0.0"),
    ],
)
def test_the_ladder_is_unchanged(last, changelog, expected):
    """Pre-1.0 shifts the WHOLE ladder down a step, and this change must not move it."""
    v = call(last=last, tags=(last,), messages=[message(changelog)])
    assert v.rc == 0
    assert v.nxt == expected


# ---------------------------------------------------------------------------
# A REAL REPOSITORY. `derive` agreeing with itself is not evidence git agrees.
# ---------------------------------------------------------------------------


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def build(tmp_path, commits, tag=None):
    """A git repository whose history is `(message, author, file)` triples."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "root")
    if tag:
        git(tmp_path, "tag", "-a", tag, "-m", tag)
    for text, author, name in commits:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text}\n", encoding="utf-8")
        git(tmp_path, "add", "--", name)
        git(tmp_path, "commit", "-q", f"--author={author}", "-m", text)
    return tmp_path


def run(tmp_path):
    """The script as the workflow runs it: a subprocess, reading its exit code."""
    env = dict(os.environ)
    env.update(
        GITHUB_OUTPUT=str(tmp_path / "out.txt"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        RUNNER_TEMP=str(tmp_path),
        GATE_DIR=str(ROOT / "scripts"),
    )
    (tmp_path / "out.txt").touch()
    (tmp_path / "summary.md").touch()
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )
    return done, (tmp_path / "out.txt").read_text(encoding="utf-8")


def test_a_real_bot_only_cargo_merge_tags_end_to_end(tmp_path):
    """`lifecycle`'s stranded commit, replayed against real git."""
    build(tmp_path, [("build(deps): bump sha2 (#24)", BOT, "Cargo.lock")], tag="v0.2.17")
    done, out = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "next=0.2.18" in out

    # THE TAG MESSAGE IS THE ONE ARTIFACT THAT CANNOT BE CORRECTED LATER. The
    # `release-tags` ruleset blocks `update` and `deletion` on `refs/tags/v*`
    # with no bypass actors, so a message naming an empty baseline — which is
    # what reading git a second time after the range was consumed would
    # produce — is permanent. Asserting the file merely EXISTS would not catch
    # that, so its contents are read.
    written = (tmp_path / "tag_message.txt").read_text(encoding="utf-8")
    assert written.startswith("v0.2.18\n")
    assert "since v0.2.17." in written
    assert "fix(deps)" in written


def test_a_real_non_semver_baseline_exits_nonzero(tmp_path):
    """`yadgar`'s shape, and the exit code is what the job conclusion reads."""
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.1.0a7")
    done, out = run(tmp_path)
    assert done.returncode == 1
    assert "next=\n" in out or out.strip().endswith("next=")
    assert "::error::" in done.stdout + done.stderr


def test_a_real_repository_with_no_tags_stays_green(tmp_path):
    """Nothing to count from, and five repositories depend on this staying quiet."""
    build(tmp_path, [(message(), HUMAN, "README.md")])
    done, out = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "next=" in out
    assert "next=0" not in out


def test_a_real_tagged_head_stays_green(tmp_path):
    """The idempotent re-run, proved against git's own empty range."""
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    git(tmp_path, "tag", "-a", "v0.2.18", "-m", "v0.2.18")
    done, out = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "next=" in out
    assert "next=0.2.19" not in out


# ---------------------------------------------------------------------------
# THE WORKFLOW. A tested script the workflow does not call runs nowhere.
# ---------------------------------------------------------------------------


def steps():
    loaded = yaml.safe_load(CI_PR.read_text(encoding="utf-8"))
    return {s.get("id") or s.get("name"): s for s in loaded["jobs"]["version"]["steps"]}


def test_the_derivation_is_the_checked_in_script():
    """No heredoc. `hooks/run_block_size.py` asks for exactly this, and a
    refusal that only exists in a heredoc is a refusal no test can reach."""
    run_block = steps()["v"]["run"]
    assert "next_version.py" in run_block
    assert "<<'PY'" not in run_block


def test_the_derivation_step_runs_under_errexit():
    """What carries the script's exit code to the job conclusion."""
    assert "set -euo pipefail" in steps()["v"]["run"]


def cred(client_id, app_key, nxt):
    """The `cred` step's SHIPPED shell, executed rather than read."""
    step = steps()["cred"]
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        capture_output=True, text=True,
        env={
            # The ambient PATH rather than a fixed one: `bash` is not under
            # /usr/bin on every machine a contributor runs this on, and the block
            # under test uses only shell builtins anyway.
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLIENT_ID": client_id, "APP_KEY": app_key,
            "NEXT": nxt, "GITHUB_OUTPUT": "/dev/null", "GITHUB_STEP_SUMMARY": "/dev/null",
        },
    )


def test_a_derived_version_with_no_release_app_reddens():
    """Ledger 580 flagged this as a silent success that is merely unreachable.

    Both organisation secrets are `visibility=all` today and all 19 callers pass
    them, so this cannot fire — which is the argument FOR reddening it rather
    than against. A version was derived and then dropped is not a success.
    """
    assert cred("", "", "0.2.18").returncode == 1


def test_an_id_without_a_key_reddens():
    """Both or neither. Half a credential fails inside the minting action later."""
    assert cred("Iv1.abc", "", "0.2.18").returncode == 1
    assert cred("", "-----BEGIN-----", "0.2.18").returncode == 1


def test_no_derived_version_keeps_the_release_app_check_quiet():
    """The documented off-switch, preserved rather than contradicted.

    `ci-pr.yaml` says this step is "how this ships into every repository before
    the App behind it exists". Reddening it unconditionally would also redden the
    five repositories that carry no tags and derive no version on every merge, so
    the refusal is gated on a version having been derived.
    """
    assert cred("", "", "").returncode == 0


def test_a_configured_release_app_passes():
    assert cred("Iv1.abc", "-----BEGIN-----", "0.2.18").returncode == 0


def test_the_release_app_check_reads_the_derived_version():
    """Through the environment, so the refusal is reachable by this test at all.

    A step-level `if:` would make the reddening unexercisable — the test could
    assert the condition's TEXT and never run the branch.
    """
    assert steps()["cred"]["env"]["NEXT"] == "${{ steps.v.outputs.next }}"


def test_head_already_released_stays_a_green_skip_in_the_workflow():
    """Legitimately idempotent, and the one no-tag branch left deliberately quiet."""
    block = steps()["g"]["run"]
    assert "exit 1" not in block
    assert "::error::" not in block
