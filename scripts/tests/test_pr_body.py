"""What `pr_body.py` asserts, pinned so it cannot quietly stop asserting it.

THE TWO MODES ARE THE POINT OF THIS FILE. The same body is read twice in a
change's life: once as the pull request body, which is exactly what the author
typed, and once as the squash commit message, which GitHub HARD-WRAPS at about
72 columns on its way into history. A Changelog bullet that was one line in the
pull request arrives in `main` as a bullet line plus continuation lines that are
not bullets — so a parser that demands "every line is a bullet" is correct
before the merge and wrong after it.

That wrapping is measured rather than assumed: the fixture below is the real
`## Changelog` section of `yadgarhq/store` `2198a4f`, and the one-line form
beside it is that pull request's body as the API returns it.

Run: python3 -m pytest scripts/tests/ -q
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pr_body  # noqa: E402

GATE = Path(__file__).resolve().parents[1] / "pr_body.py"


def body(what="a thing", why="a reason", changelog="- fix: a thing",
         verification="ran it", risk="none"):
    """A body with all five sections, so a test can break exactly one of them."""
    return (
        f"## What\n\n{what}\n\n## Why\n\n{why}\n\n## Changelog\n\n{changelog}\n\n"
        f"## Verification\n\n{verification}\n\n## Risk\n\n{risk}\n"
    )


def run(text, wrapped="false", author=""):
    return subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        env={
            "BODY": text,
            "WRAPPED": wrapped,
            "SUBJECT": "the body",
            "AUTHOR": author,
            "PATH": "/usr/bin:/bin",
        },
    )


# ---------------------------------------------------------------- the contract


def test_a_conformant_body_has_no_problems():
    problems, bump, count = pr_body.review(body(), wrapped=False)
    assert problems == []
    assert (bump, count) == ("patch", 1)


def test_a_missing_section_is_named():
    text = body().replace("## Risk\n\nnone\n", "")
    problems, _, _ = pr_body.review(text, wrapped=False)
    assert any("Risk" in p for p in problems)


def test_a_section_holding_only_a_comment_is_empty():
    text = body(why="<!-- the reason goes here -->")
    problems, _, _ = pr_body.review(text, wrapped=False)
    assert any("Why" in p and "empty" in p for p in problems)


def test_prose_under_changelog_is_refused():
    problems, _, _ = pr_body.review(body(changelog="Made it better."), wrapped=False)
    assert any("Conventional Commits" in p for p in problems)


def test_a_bullet_with_no_type_is_refused():
    problems, _, _ = pr_body.review(body(changelog="- made it better"), wrapped=False)
    assert any("Conventional Commits" in p for p in problems)


def test_an_unknown_type_is_refused():
    problems, _, _ = pr_body.review(body(changelog="- improve: it"), wrapped=False)
    assert any("Conventional Commits" in p for p in problems)


# -------------------------------------------------------------------- the bump


def test_feat_implies_minor():
    _, bump, _ = pr_body.review(body(changelog="- feat: a thing"), wrapped=False)
    assert bump == "minor"


def test_a_bang_implies_major():
    _, bump, _ = pr_body.review(body(changelog="- fix!: a thing"), wrapped=False)
    assert bump == "major"


def test_the_highest_bullet_wins():
    text = body(changelog="- chore: a thing\n- feat: another\n- docs: a third")
    _, bump, count = pr_body.review(text, wrapped=False)
    assert (bump, count) == ("minor", 3)


def test_a_scope_does_not_change_the_type():
    _, bump, _ = pr_body.review(body(changelog="- feat(pool): a thing"), wrapped=False)
    assert bump == "minor"


# ------------------------------------------------------------------- wrapping

# `yadgarhq/store` 2198a4f, verbatim: what GitHub wrote into `main` from a pull
# request body whose four bullets were each ONE line.
WRAPPED_CHANGELOG = """\
- fix(pool)!: refuse `verify_ca` when a connection is built — it accepts
any publicly-trusted certificate for any name, with or without a CA
configured
- feat(pool)!: `PoolConfig::check_ssl_mode` refuses an ssl-mode that
cannot verify, beside `check_engine_headroom`, so a consumer can refuse
before reading a credential
- feat(pool)!: `connect_options` returns `Result<MySqlConnectOptions,
PoolError>` so the probe and the pool cannot route around the refusal
- docs(pool): correct the claim that pointing `verify_ca` at a CA
reached verification, on `ssl_ca`, in the `connect_options` block"""


def test_a_real_wrapped_commit_message_passes_in_wrapped_mode():
    problems, bump, count = pr_body.review(body(changelog=WRAPPED_CHANGELOG), wrapped=True)
    assert problems == []
    assert (bump, count) == ("major", 4)


def test_the_same_wrapped_message_is_refused_in_unwrapped_mode():
    """The two modes must actually differ, or one of them is decoration."""
    problems, _, _ = pr_body.review(body(changelog=WRAPPED_CHANGELOG), wrapped=False)
    assert any("Conventional Commits" in p for p in problems)


def test_wrapped_mode_still_refuses_an_unparseable_entry():
    text = body(changelog="- fix: a thing\nwrapped on\n- improve: it\nwrapped too")
    problems, _, _ = pr_body.review(text, wrapped=True)
    assert any("improve" in p for p in problems)


def test_wrapped_mode_refuses_prose_that_opens_a_paragraph():
    """A continuation continues something. Nothing precedes this one."""
    text = body(changelog="Made it better.\n- fix: a thing")
    problems, _, _ = pr_body.review(text, wrapped=True)
    assert any("Conventional Commits" in p for p in problems)


def test_wrapped_mode_refuses_prose_after_a_blank_line():
    text = body(changelog="- fix: a thing\n\nand some prose")
    problems, _, _ = pr_body.review(text, wrapped=True)
    assert any("Conventional Commits" in p for p in problems)


def test_a_continuation_beginning_with_a_dash_is_not_read_as_a_bullet():
    """`-D warnings` wrapped onto its own line must not be taken for an entry."""
    text = body(changelog="- chore: run clippy with\n-D warnings everywhere")
    problems, _, count = pr_body.review(text, wrapped=True)
    assert problems == []
    assert count == 1


# ------------------------------------------------------------- the bot escape


def test_a_bot_author_is_exempt_in_all_three_shapes():
    """Name, noreply address, and the `%an <%ae>` form the workflow passes."""
    assert pr_body.is_bot("dependabot[bot]")
    assert pr_body.is_bot("49699333+dependabot[bot]@users.noreply.github.com")
    assert pr_body.is_bot("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>")


def test_a_bot_name_alone_is_enough_when_the_address_is_not_a_noreply():
    """The exemption must not depend on the address happening to carry `[bot]`."""
    assert pr_body.is_bot("yadgarhq-bot[bot] <releases@example.invalid>")


def test_a_person_is_not_exempt():
    assert not pr_body.is_bot("Max Agahi")
    assert not pr_body.is_bot("64974579+m-agahi@users.noreply.github.com")


def test_a_name_merely_containing_bot_is_not_exempt():
    assert not pr_body.is_bot("Robotics Team")


def test_the_release_app_is_a_bot():
    """`ci-release.yaml` writes a one-line commit per release into `argocd`.

    Verified against that repository's history rather than assumed. Without the
    exemption every release in the estate would redden argocd's push to main.
    """
    author = "yadgarhq-bot[bot] <324366854+yadgarhq-bot[bot]@users.noreply.github.com>"
    assert pr_body.is_bot(author)


# ------------------------------------------------------------------- the gate


def test_the_gate_is_green_on_a_conformant_body():
    result = run(body())
    assert result.returncode == 0
    assert "1" in result.stdout


def test_the_gate_is_red_and_names_the_problem():
    result = run(body(changelog="Made it better."))
    assert result.returncode == 1
    assert "Conventional Commits" in result.stdout + result.stderr


def test_a_commit_message_with_no_sections_at_all_names_the_likely_cause():
    """The `yadgarhq/yadgar` shape: `squash_merge_commit_message` is not PR_BODY.

    Five `missing section` lines tell that repository's maintainer nothing about
    why. The body never became the commit message in the first place.
    """
    problems, _, _ = pr_body.review("ci: gateway 0.9.3", wrapped=True)
    assert "squash_merge_commit_message" in problems[0]


def test_the_hint_is_not_offered_when_some_sections_are_present():
    """A body missing one section is a body problem, not a settings problem."""
    text = body().replace("## Risk\n\nnone\n", "")
    problems, _, _ = pr_body.review(text, wrapped=True)
    assert not any("squash_merge_commit_message" in p for p in problems)


def test_an_empty_pull_request_body_is_not_blamed_on_the_squash_setting():
    """Unwrapped means a pull request body, where the setting is not the cause."""
    problems, _, _ = pr_body.review("", wrapped=False)
    assert problems
    assert not any("squash_merge_commit_message" in p for p in problems)


def test_the_gate_refuses_an_empty_body_rather_than_passing_it():
    """The degenerate input. An absent body proves nothing and must not pass."""
    result = run("")
    assert result.returncode == 1


def test_the_gate_exempts_a_bot_author_and_says_which():
    result = run("ci: gateway 0.9.3", author="yadgarhq-bot[bot] <x@y>")
    assert result.returncode == 0
    assert "yadgarhq-bot[bot]" in result.stdout


def test_the_gate_does_not_exempt_a_person_with_the_same_body():
    """The exemption must turn on the author, not on the body being short."""
    assert run("ci: gateway 0.9.3", author="Max Agahi <x@y>").returncode == 1


def test_the_gate_reads_the_wrapped_flag():
    text = body(changelog=WRAPPED_CHANGELOG)
    assert run(text, wrapped="false").returncode == 1
    assert run(text, wrapped="true").returncode == 0


# ------------------------------------------------ what the wrap can and cannot
#
# THE ARITHMETIC IS THE WHOLE OF THIS SECTION. GitHub wraps at 72 COLUMNS on
# word boundaries and starts continuations at column 0, so a continuation line
# can begin with a one-character word only when the line above it is already 71
# or 72 columns — there was no room for even a `-`. That makes "could this line
# be a wrap artifact" a question about the PREVIOUS line's length rather than a
# guess, and the fixtures below pin both answers.

# 72 columns, so the line after it beginning `- ` is explained by the wrap.
FULL = "- chore: run clippy over the whole workspace with the lint profile names"

# 20 columns, so nothing after it is explained by the wrap.
SHORT = "- fix: a short entry"


def test_the_fixtures_are_the_widths_this_section_claims():
    """The tests below are arithmetic, so the arithmetic is asserted first."""
    assert len(FULL) == 72
    assert len(SHORT) == 20
    assert pr_body.WRAP == 72


def test_a_dash_token_at_a_wrap_boundary_is_not_a_malformed_bullet():
    """Ledger 687(a). `- W for warnings` mid-sentence, landed at a boundary.

    Read strictly this is a bullet with no Conventional Commits type, and the
    gate reds the push to main for a body two humans reviewed and `ci /
    template` passed. The wrap is what put the dash there.
    """
    text = body(changelog=FULL + "\n- W for warnings among them, so it fails")
    problems, bump, count = pr_body.review(text, wrapped=True)
    assert problems == []
    assert (bump, count) == ("patch", 1)


def test_a_dash_line_the_wrap_cannot_explain_is_still_refused():
    """The absorption is not a blanket amnesty for unparseable bullets."""
    text = body(changelog=SHORT + "\n- W for warnings among them, so it fails")
    problems, _, _ = pr_body.review(text, wrapped=True)
    assert any("Conventional Commits" in p for p in problems)


def test_a_bullet_the_wrap_could_have_produced_is_ambiguous():
    """Ledger 687(c). The two readings derive different bumps, so neither wins.

    Read strictly these are two entries and the second one carries a `!`, so
    the tag cut from this message is a MAJOR. Read as the wrap explains it,
    the second line continues the first and the release is a patch. A parser
    cannot tell, and this one is the last thing between a reviewed patch and
    an immutable major nobody can withdraw.
    """
    text = body(changelog=FULL + "\n- feat!: and the bump the two readings imply differs")
    problems, bump, count = pr_body.review(text, wrapped=True)
    assert any("ambiguous" in p for p in problems)
    assert any("major" in p and "patch" in p for p in problems)
    assert (bump, count) == (None, 0)


def test_a_bullet_the_wrap_cannot_explain_is_read_as_an_entry():
    """The other side of it: a real second bullet is still a real second bullet."""
    text = body(changelog=SHORT + "\n- feat!: a genuine second entry")
    problems, bump, count = pr_body.review(text, wrapped=True)
    assert problems == []
    assert (bump, count) == ("major", 2)


def test_a_disagreement_that_does_not_change_the_bump_is_not_a_problem():
    """Measured 12 of 288 real wrapped bodies; 0 of them changed the bump.

    Refusing on a differing COUNT would red main on four percent of merges for
    a reading that implies the same release. The bump is what gets cut, so the
    bump is what has to agree.
    """
    text = body(changelog=FULL + "\n- docs: a line the readings disagree about")
    problems, bump, count = pr_body.review(text, wrapped=True)
    assert problems == []
    assert (bump, count) == ("patch", 2)


def test_the_first_entry_of_a_block_is_never_absorbed():
    """The invariant that keeps the two readings comparable.

    Nothing precedes the first line of a paragraph, so it cannot be anybody's
    continuation. Without it the lenient reading could empty a Changelog the
    strict one reads, and `bump_for([])` answers `patch` rather than refusing.
    """
    assert not pr_body.is_wrap_continuation(None, "- feat: a thing")
    assert not pr_body.is_wrap_continuation("", "- feat: a thing")


def test_a_wrap_continuation_is_decided_by_the_previous_line_length():
    assert pr_body.is_wrap_continuation("x" * 72, "- feat: a thing")
    assert pr_body.is_wrap_continuation("x" * 71, "- feat: a thing")
    assert not pr_body.is_wrap_continuation("x" * 70, "- feat: a thing")


def test_a_line_wider_than_the_wrap_was_not_produced_by_the_wrap():
    """The upper half of the bound, and the corpus is what found it.

    A repository whose squash message is not the pull request body reaches
    history unwrapped, so its bullets are single lines far wider than 72. With
    a one-sided bound every one of them absorbs the next, and the nine-entry
    Changelogs in `yadgarhq/iam-db` and `yadgarhq/task-db` read as one patch.
    """
    assert not pr_body.is_wrap_continuation("x" * 73, "- feat: a thing")
    assert not pr_body.is_wrap_continuation("x" * 200, "- feat: a thing")


def test_an_unwrapped_commit_message_keeps_every_entry():
    long_bullets = "\n".join(
        f"- {t}: " + "a description wide enough that no wrap could have made it " * 2
        for t in ("chore", "feat", "fix")
    )
    problems, bump, count = pr_body.review(body(changelog=long_bullets), wrapped=True)
    assert problems == []
    assert (bump, count) == ("minor", 3)


def test_the_real_wrapped_fixture_holds_no_ambiguity():
    """`yadgarhq/store` 2198a4f again: the change must be a no-op on it.

    Its three bullet boundaries follow lines of 10, 27 and 69 columns, none of
    which is full, so every one of its four entries survives both readings.
    """
    problems, bump, count = pr_body.review(body(changelog=WRAPPED_CHANGELOG), wrapped=True)
    assert problems == []
    assert (bump, count) == ("major", 4)


def test_an_unwrapped_body_is_read_exactly_as_before():
    """The absorption belongs to wrapped mode. A pull request body is verbatim."""
    text = body(changelog=FULL + "\n- feat!: and the bump the two readings imply differs")
    problems, bump, count = pr_body.review(text, wrapped=False)
    assert problems == []
    assert (bump, count) == ("major", 2)


def test_the_gate_is_red_on_an_ambiguous_message():
    text = body(changelog=FULL + "\n- feat!: and the bump the two readings imply differs")
    result = run(text, wrapped="true")
    assert result.returncode == 1
    assert "ambiguous" in result.stdout + result.stderr
