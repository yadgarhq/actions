"""PR-time release readiness: `next_version.py --pr` and the `release_ready` job.

YAADGAAR 324, ADR-1076. The `version` job runs only after a merge, so every
refusal it raises is found when the release is already missed. This mode asks
the BASELINE question before the merge:

    the repository carries no `v*` tag            notice, exit 0
    it carries tags, none reachable from HEAD     red
    the nearest `v*` tag is not plain semver      red
    the baseline is plain semver                  green, and the summary
                                                  names the version this body
                                                  WOULD cut

THE VERSION IS A REPORT, NEVER A VERDICT. `ci / template` already reddens a
body that does not parse, and a second reader of the body that could redden a
pull request would be a second gate over the same text in all twenty
consumers. So no body, an empty body and a bot body all stay green here.

THE SECRETS-PRESENT HALF IS NOT HERE. ADR-1076 picks option C from #324: no
PR-time secrets check at all; an organisation audit lists the repositories
missing the release App secrets. Nothing in this file reads a secret.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import next_version  # noqa: E402
import pr_body  # noqa: E402
from test_next_version import _git_env, build, git, message, HUMAN  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "next_version.py"
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"
TEMPLATE = ROOT / ".github" / "pull_request_template.md"


def filled_template(changelog):
    """The real template, every comment left in, with each section answered.

    The template's own comments carry indented example bullets, one of them
    `- feat!: drop the by-name arm of GetWikiPage`. A reader that let the
    comments through would read every pull request as a breaking change.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    answers = {
        "## What": "## What\n\na thing",
        "## Why": "## Why\n\na reason",
        "## Changelog": "## Changelog\n\n" + changelog,
        "## Verification": "## Verification\n\nran it",
        "## Risk": "## Risk\n\nnone",
    }
    for heading, answer in answers.items():
        text = text.replace(heading + "\n", answer + "\n", 1)
    return text


# ---------------------------------------------------------------------------
# The branch table, called directly.
# ---------------------------------------------------------------------------


def test_zero_tags_is_a_notice_and_green():
    verdict, level = next_version.ready("", [], message())
    assert verdict.rc == 0
    assert level == "notice"
    assert verdict.nxt == ""
    assert "carries **no** `v*` tag" in "\n".join(verdict.lines)


def test_tags_with_none_reachable_reddens():
    """`derive` reads an empty baseline as green whatever `tags` holds, so this
    mode tells "no tag ever" from "tags exist, none reachable" itself."""
    verdict, level = next_version.ready("", ["v0.1.0"], message())
    assert verdict.rc == 1
    assert level == "error"
    assert "none of them is\nreachable from HEAD" in "\n".join(verdict.lines)


def test_a_non_semver_baseline_beside_plain_tags_names_the_stray():
    verdict, level = next_version.ready("v1", ["v1", "v1.22.0"], message())
    assert verdict.rc == 1
    assert level == "error"
    text = "\n".join(verdict.lines)
    assert "**stray**" in text
    assert "`v1.22.0`" in text
    assert "will not release" in text


def test_a_non_semver_baseline_with_no_plain_tag_asks_for_a_first_one():
    verdict, level = next_version.ready("v0.1.0a7", ["v0.1.0a7"], message())
    assert verdict.rc == 1
    assert level == "error"
    assert "cut the first plain one by hand" in "\n".join(verdict.lines)


def test_a_healthy_baseline_reports_the_version_it_would_cut():
    verdict, level = next_version.ready(
        "v0.2.17", ["v0.2.17"], message("- feat: a new thing"))
    assert verdict.rc == 0
    assert level == "ok"
    assert verdict.nxt == "0.2.18"
    assert "would release **`v0.2.18`**" in "\n".join(verdict.lines)


def test_a_post_1_0_baseline_uses_the_unshifted_ladder():
    verdict, _ = next_version.ready(
        "v1.26.1", ["v1.26.1"], message("- feat!: a breaking change"))
    assert verdict.rc == 0
    assert verdict.nxt == "2.0.0"


def test_the_template_comments_do_not_move_the_version():
    """The real template, its `- feat!:` example still inside the comment."""
    body = filled_template("- fix: the real change")
    assert "- feat!: drop the by-name arm" in body
    verdict, _ = next_version.ready("v1.2.3", ["v1.2.3"], body)
    assert verdict.rc == 0
    assert verdict.nxt == "1.2.4"
    assert verdict.entries == ["- fix: the real change"]


def test_the_body_is_not_read_as_a_squash_message():
    """`body_lines` drops line one, the squash SUBJECT. A pull request body has
    no subject: dropping its first line drops an opening `<!--`, and a bullet
    inside that comment is then read as an entry."""
    body = "<!--\n- feat!: an example in a comment\n-->\n## Changelog\n\n- fix: the real change\n"
    as_squash, _, _ = pr_body.commit_entries(pr_body.body_lines(body))
    assert len(as_squash) == 2  # the trap is real
    verdict, _ = next_version.ready("v1.2.3", ["v1.2.3"], body)
    assert verdict.entries == ["- fix: the real change"]
    assert verdict.nxt == "1.2.4"


def test_a_body_with_no_changelog_stays_green():
    """A bot body, or one `ci / template` is already reddening. Not this job's
    verdict to give."""
    for body in ("", "Bumps serde from 1.0.1 to 1.0.2.", "## What\n\nprose only\n"):
        verdict, level = next_version.ready("v0.2.17", ["v0.2.17"], body)
        assert verdict.rc == 0, body
        assert level == "ok"
        assert verdict.nxt == ""
        assert "merge-time derivation decides" in "\n".join(verdict.lines)


def test_a_body_that_does_not_parse_stays_green():
    verdict, level = next_version.ready(
        "v0.2.17", ["v0.2.17"], "## Changelog\n\n- not a typed bullet\n")
    assert verdict.rc == 0
    assert level == "ok"


# ---------------------------------------------------------------------------
# A real git repository, run through the script as the workflow runs it.
# ---------------------------------------------------------------------------


def run_pr(tmp_path, body=None):
    env = _git_env()
    env.update(GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))
    # NO token and NO repository: PR mode must not reach the commits API.
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_REPOSITORY", None)
    if body is not None:
        env["BODY"] = body
    (tmp_path / "summary.md").touch()
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--pr"], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )
    return done, (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_a_real_repository_with_no_tags_is_a_notice(tmp_path):
    build(tmp_path, [(message(), HUMAN, "README.md")])
    done, summary = run_pr(tmp_path, message())
    assert done.returncode == 0, done.stderr
    assert "::notice::" in done.stdout
    assert "::error::" not in done.stdout
    assert "## Release readiness" in summary


def test_a_real_healthy_baseline_is_green_and_names_the_version(tmp_path):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    done, summary = run_pr(tmp_path, message("- fix: a thing"))
    assert done.returncode == 0, done.stderr
    assert "::error::" not in done.stdout
    assert "`v0.2.18`" in summary


def test_a_real_tagged_head_is_still_green(tmp_path):
    """The PR's base is the release commit itself: the baseline is HEAD."""
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    git(tmp_path, "tag", "-a", "v0.2.18", "-m", "v0.2.18")
    done, summary = run_pr(tmp_path, message("- fix: a thing"))
    assert done.returncode == 0, done.stderr
    assert "`v0.2.19`" in summary


def test_a_real_non_semver_baseline_exits_nonzero(tmp_path):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.1.0a7")
    done, _ = run_pr(tmp_path, message())
    assert done.returncode == 1
    assert "::error::" in done.stdout


def test_a_real_stray_nearer_than_the_plain_tags_exits_nonzero(tmp_path):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    git(tmp_path, "tag", "-a", "v0-stray", "-m", "stray")
    done, summary = run_pr(tmp_path, message())
    assert done.returncode == 1
    assert "**stray**" in summary


def test_a_real_unreachable_tag_exits_nonzero(tmp_path):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")])
    git(tmp_path, "checkout", "-q", "--orphan", "side")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "side root")
    git(tmp_path, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    git(tmp_path, "checkout", "-q", "main")
    done, _ = run_pr(tmp_path, message())
    assert done.returncode == 1
    assert "::error::" in done.stdout


def test_a_real_run_with_no_body_is_green(tmp_path):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    done, _ = run_pr(tmp_path, None)
    assert done.returncode == 0, done.stderr


def test_pr_mode_never_asks_the_commits_api(tmp_path, monkeypatch):
    build(tmp_path, [(message(), HUMAN, "src/main.rs")], tag="v0.2.17")
    monkeypatch.chdir(tmp_path)
    for key in [k for k in list(__import__("os").environ) if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("BODY", message())

    def boom(*args):
        raise AssertionError("PR mode asked the commits API")

    monkeypatch.setattr(next_version, "is_bot", boom)
    assert next_version.main(["--pr"]) == 0


# ---------------------------------------------------------------------------
# THE WORKFLOW. A tested script the workflow does not call runs nowhere.
# ---------------------------------------------------------------------------


def jobs():
    return yaml.safe_load(CI_PR.read_text(encoding="utf-8"))["jobs"]


def test_the_job_exists_and_runs_before_the_merge():
    cond = " ".join(str(jobs()["release_ready"]["if"]).split())
    assert cond == (
        "github.event_name != 'push' && needs.detect.outputs.text_edit != 'true' "
        "&& needs.detect.outputs.config_only != 'true'"
    )
    assert jobs()["release_ready"]["needs"] == "detect"


def test_the_merge_gate_needs_it():
    assert "release_ready" in jobs()["passed"]["needs"]


def test_version_is_still_not_under_the_merge_gate():
    assert "version" not in jobs()["passed"]["needs"]


def readiness_step():
    steps = jobs()["release_ready"]["steps"]
    return next(s for s in steps if "next_version.py" in str(s.get("run", "")))


def test_the_step_runs_the_checked_in_script_in_pr_mode_under_errexit():
    run = readiness_step()["run"]
    assert "set -euo pipefail" in run
    assert 'next_version.py" --pr' in run
    assert "<<" not in run


def test_the_body_arrives_through_the_environment_only():
    step = readiness_step()
    assert step["env"]["BODY"] == "${{ github.event.pull_request.body }}"
    assert "${{" not in step["run"]


def test_no_secret_reaches_the_job():
    """ADR-1076, option C: the secrets half is an org audit, not a PR check."""
    assert "secrets." not in yaml.safe_dump(jobs()["release_ready"])


def test_the_checkout_carries_every_tag():
    """Without the tags `git describe` finds no baseline and a tagged
    repository reads as one with tags but none reachable."""
    first = jobs()["release_ready"]["steps"][0]
    assert first["uses"].startswith("actions/checkout@")
    assert first["with"]["fetch-depth"] == 0
    assert first["with"]["persist-credentials"] is False
    assert "if" not in first


def test_a_git_failure_is_red_not_a_zero_tag_notice(tmp_path):
    """`sh` drops git's exit code. Outside a repository `git tag --list` fails,
    and read through `sh` that is an empty list: zero tags, a green notice. The
    gate would fail open, so PR mode reads the tags with the exit code checked."""
    done, _ = run_pr(tmp_path, message())
    assert done.returncode == 1
    assert "::error::" in done.stdout
    assert "::notice::" not in done.stdout


def test_the_annotation_carries_no_markdown(tmp_path):
    build(tmp_path, [(message(), HUMAN, "README.md")])
    done, _ = run_pr(tmp_path, message())
    notice = next(line for line in done.stdout.splitlines() if line.startswith("::notice::"))
    assert "**" not in notice
