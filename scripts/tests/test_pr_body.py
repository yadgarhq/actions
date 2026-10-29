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

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pr_body  # noqa: E402

GATE = Path(__file__).resolve().parents[1] / "pr_body.py"
REPO = Path(__file__).resolve().parents[2]


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


def test_a_long_squash_subject_does_not_defeat_the_template_gates_wrap_reading():
    """LEDGER 941. The `version` job's WRAPPED=true call reads `git log -1
    --format=%B` -- subject included -- into `review`. Before `body_lines`,
    one over-long pull request title here disabled absorption for the whole
    message the identical way it did in `next_version.read`, and ledger 687's
    own rule is that two readers of the same message must not disagree.

    THE SAME CHANGELOG `test_an_ambiguous_wrap_reddens`-style fixture
    `next_version`'s own suite uses, because it is the one shape that makes
    the defect OBSERVABLE through `review`'s return value: a single-bullet
    fixture loses only its continuation TEXT when absorption is disabled,
    which `review` does not return at all. Here, losing absorption instead
    makes a WRAPPED `- feat!:` continuation read as a second entry -- turning
    a correctly-REFUSED ambiguity (short subject, below) into a silent
    `major` bump the gate reports no problem with at all.
    """
    short_subject = "fix: short (#1)"
    long_subject = (
        "fix: a title on its own long enough to blow the seventy two column "
        "wrap (#941)"
    )
    assert len(long_subject) > pr_body.WRAP
    assert len(long_subject.split()) > 1
    filler = "x" * 71
    changelog = f"- fix: a thing\n{filler}\n- feat!: absorbed\n"

    short = pr_body.review(short_subject + "\n\n" + body(changelog=changelog), wrapped=True)
    long_ = pr_body.review(long_subject + "\n\n" + body(changelog=changelog), wrapped=True)

    # THE CORRECT ANSWER, pinned first so the defect's own answer is visibly
    # wrong beside it: the wrap makes this Changelog ambiguous, so neither
    # subject length may derive a version from it.
    assert short[1:] == (None, 0)
    assert any("ambiguous" in p for p in short[0])
    assert long_ == short


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
#
# YAADGAAR 456. The name is a CLAIM and the commits API is the PROOF. A commit
# author is whatever `git commit --author=` was told, so `x[bot]` costs nothing
# to type; `author.id`, `committer.id` and `verification.verified` are set by
# GitHub. A commit is a bot's only when BOTH say so, and any failure to read the
# proof reads as a person.
#
# The fixtures are real responses, trimmed to the fields read:
# `GET /repos/yadgarhq/chart/commits/359fae8` (a `parent_bump.py` pin) and
# `GET /repos/yadgarhq/yadgar/commits/f1a149e` (a squash-merged dependabot PR),
# both read on 2026-10-28.

APP = "yadgarhq-bot[bot] <324366854+yadgarhq-bot[bot]@users.noreply.github.com>"
DEPENDABOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
APP_SHA = "359fae8b7de6dfbb9fca3c206786c2141d5a1898"
DEPENDABOT_SHA = "f1a149e5b0746b810558030d62c97862ec2f7315"


def api_commit(sha=APP_SHA, author_id=324366854, author_type="Bot",
               committer_id=19864447, verified=True):
    """One `commits/{sha}` response, real by default, so a test bends one field."""
    return {
        "sha": sha,
        "commit": {"verification": {"verified": verified, "reason": "valid"}},
        "author": {"login": "yadgarhq-bot[bot]", "id": author_id, "type": author_type},
        "committer": {"login": "web-flow", "id": committer_id, "type": "User"},
    }


def serve(monkeypatch, response):
    """Stand in for the commits API. A callable raises, as the network can."""
    seen = []

    def fetch(repo, sha, token):
        seen.append((repo, sha, token))
        if callable(response):
            return response()
        return response

    monkeypatch.setattr(pr_body, "fetch_commit", fetch)
    return seen


def test_the_name_claim_is_matched_in_all_three_shapes():
    """Name, noreply address, and the `%an <%ae>` form the workflow passes."""
    assert pr_body.claims_bot("dependabot[bot]")
    assert pr_body.claims_bot("49699333+dependabot[bot]@users.noreply.github.com")
    assert pr_body.claims_bot(DEPENDABOT)
    assert pr_body.claims_bot("yadgarhq-bot[bot] <releases@example.invalid>")


def test_a_person_makes_no_claim():
    assert not pr_body.claims_bot("Max Agahi")
    assert not pr_body.claims_bot("64974579+m-agahi@users.noreply.github.com")
    assert not pr_body.claims_bot("Robotics Team")


def test_a_verified_commit_by_the_release_app_is_a_bot(monkeypatch):
    seen = serve(monkeypatch, api_commit())
    assert pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")
    assert seen == [("yadgarhq/chart", APP_SHA, "t")]


def test_a_verified_commit_by_dependabot_is_a_bot(monkeypatch):
    """Dependabot is kept: its id is as unforgeable as the App's, and dropping it
    would redden `version` on every dependency merge and end the `fix(deps)`
    synthesis in `next_version.derive`."""
    serve(monkeypatch, api_commit(sha=DEPENDABOT_SHA, author_id=49699333))
    assert pr_body.is_bot(DEPENDABOT, DEPENDABOT_SHA, "yadgarhq/yadgar", "t")


def test_a_forged_bot_name_on_an_unverified_commit_is_a_person(monkeypatch):
    serve(monkeypatch, api_commit(verified=False))
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_a_forged_bot_name_resolving_to_another_user_is_a_person(monkeypatch):
    """`git commit --author='x[bot] <me@example.com>'`, signed and verified."""
    serve(monkeypatch, api_commit(author_id=64974579, author_type="User"))
    assert not pr_body.is_bot("x[bot] <me@example.com>", APP_SHA, "yadgarhq/chart", "t")


def test_a_forged_bot_name_resolving_to_no_user_is_a_person(monkeypatch):
    serve(monkeypatch, {**api_commit(), "author": None})
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_another_bot_is_a_person(monkeypatch):
    """Any GitHub App can be installed by somebody; only the two named are trusted."""
    serve(monkeypatch, api_commit(author_id=41898282))
    assert not pr_body.is_bot("github-actions[bot] <x@y>", APP_SHA, "yadgarhq/chart", "t")


def test_the_app_id_as_a_user_type_is_a_person(monkeypatch):
    serve(monkeypatch, api_commit(author_type="User"))
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_the_app_email_verified_by_a_person_committing_is_a_person(monkeypatch):
    """`verified` vouches for the COMMITTER's key, not the author. A person can
    set the App's noreply address as author, sign with their own key, and read
    back `author.id == 324366854` and `verified == true`. The committer gives it
    away: GitHub's merge commits carry `web-flow`."""
    serve(monkeypatch, api_commit(committer_id=64974579))
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_a_response_for_another_commit_is_a_person(monkeypatch):
    serve(monkeypatch, api_commit(sha="0" * 40))
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_verified_must_be_the_boolean_true(monkeypatch):
    serve(monkeypatch, api_commit(verified="true"))
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_an_id_must_be_an_integer(monkeypatch):
    """A string never matches the set; a float DOES (`324366854.0 in {324366854}`
    is True), and `json.loads` yields one for `324366854.0`. Booleans are ints to
    `in`, so `True` must not pass for an id of 1 either."""
    for author_id, committer_id in (("324366854", 19864447), (324366854.0, 19864447),
                                    (324366854, 19864447.0)):
        serve(monkeypatch, api_commit(author_id=author_id, committer_id=committer_id))
        assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t"), (author_id, committer_id)


def test_an_api_error_is_a_person(monkeypatch):
    """`fetch_commit` returns None for every failure; that is never a bot."""
    serve(monkeypatch, None)
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t")


def test_a_malformed_response_is_a_person(monkeypatch):
    for response in ({}, [], "x", {"sha": APP_SHA}, {**api_commit(), "commit": None}):
        serve(monkeypatch, response)
        assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "t"), response


def test_no_token_sha_or_repository_is_a_person_and_asks_nothing(monkeypatch):
    seen = serve(monkeypatch, api_commit())
    assert not pr_body.is_bot(APP, APP_SHA, "yadgarhq/chart", "")
    assert not pr_body.is_bot(APP, "", "yadgarhq/chart", "t")
    assert not pr_body.is_bot(APP, APP_SHA, "", "t")
    assert seen == []


def test_a_person_is_never_looked_up(monkeypatch):
    """The name claim is a precondition, so the API can only narrow the old gate."""
    seen = serve(monkeypatch, api_commit())
    assert not pr_body.is_bot("Max Agahi <x@y>", APP_SHA, "yadgarhq/chart", "t")
    assert seen == []


def test_fetch_commit_fails_closed_on_every_error(monkeypatch):
    import urllib.error

    def boom(*_a, **_k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

    monkeypatch.setattr(pr_body.urllib.request, "urlopen", boom)
    assert pr_body.fetch_commit("yadgarhq/chart", APP_SHA, "t") is None

    def down(*_a, **_k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(pr_body.urllib.request, "urlopen", down)
    assert pr_body.fetch_commit("yadgarhq/chart", APP_SHA, "t") is None

    class NotJson:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return b"<html>"

    monkeypatch.setattr(pr_body.urllib.request, "urlopen", lambda *_a, **_k: NotJson())
    assert pr_body.fetch_commit("yadgarhq/chart", APP_SHA, "t") is None


def test_fetch_commit_reads_the_commit_with_the_token(monkeypatch):
    import json

    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return json.dumps(api_commit()).encode()

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization"), timeout))
        return Response()

    monkeypatch.setattr(pr_body.urllib.request, "urlopen", urlopen)
    assert pr_body.fetch_commit("yadgarhq/chart", APP_SHA, "t") == api_commit()
    assert seen == [(f"https://api.github.com/repos/yadgarhq/chart/commits/{APP_SHA}",
                     "Bearer t", 30)]


def test_fetch_commit_refuses_a_malformed_sha_or_repository(monkeypatch):
    """Both go into a URL that carries the token; neither may steer it."""
    # RECORDED rather than raised: `fetch_commit` swallows every exception by
    # design, so an assertion raised in here would read as a pass.
    reached = []
    monkeypatch.setattr(pr_body.urllib.request, "urlopen",
                        lambda req, **_k: reached.append(req.full_url))
    for repo, sha in (("yadgarhq/chart", "HEAD"),
                      ("yadgarhq/chart", APP_SHA.upper()),
                      ("yadgarhq/chart", APP_SHA + "/../../x"),
                      ("yadgarhq/chart/../../x", APP_SHA),
                      ("yadgarhq/..", APP_SHA),
                      ("../x", APP_SHA),
                      ("evil.example/x?", APP_SHA)):
        assert pr_body.fetch_commit(repo, sha, "t") is None, (repo, sha)
    assert reached == []


def test_the_gate_exempts_a_verified_bot_and_says_which(monkeypatch, capsys):
    serve(monkeypatch, api_commit())
    for name, value in {"BODY": "ci: gateway 0.9.3", "WRAPPED": "true",
                        "AUTHOR": APP, "SHA": APP_SHA,
                        "GITHUB_REPOSITORY": "yadgarhq/chart", "GH_TOKEN": "t"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert pr_body.main() == 0
    assert "yadgarhq-bot[bot]" in capsys.readouterr().out


def test_the_gate_refuses_a_forged_bot_with_the_same_body(monkeypatch):
    serve(monkeypatch, api_commit(verified=False))
    for name, value in {"BODY": "ci: gateway 0.9.3", "WRAPPED": "true",
                        "AUTHOR": APP, "SHA": APP_SHA,
                        "GITHUB_REPOSITORY": "yadgarhq/chart", "GH_TOKEN": "t"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert pr_body.main() == 1


def test_the_gate_refuses_a_bot_name_it_cannot_check():
    """No token, as a subprocess with nothing stubbed: the API is unreachable
    and the claim alone no longer exempts."""
    result = run("ci: gateway 0.9.3", author="yadgarhq-bot[bot] <x@y>")
    assert result.returncode == 1


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


def test_a_multi_word_line_wider_than_the_wrap_means_the_wrap_never_ran():
    """The corpus found this, not a test: 4,520 such lines in 605 messages.

    A repository whose squash message is not the pull request body reaches
    history unwrapped, so its bullets are single lines far wider than 72. Judge
    each line on its own and every one of them absorbs the next, and the
    nine-entry Changelogs in `yadgarhq/iam-db` and `yadgarhq/task-db` read as
    one patch.
    """
    assert not pr_body.looks_wrapped(["a word " * 20])
    assert pr_body.looks_wrapped(["x" * 72, "short", ""])


def test_one_over_long_token_does_not_make_a_message_unwrapped():
    """A greedy wrap emits an over-long line for a token it cannot break.

    A URL or a long path. 105 such lines exist in the same 605 messages, and
    calling them unwrapped would switch the absorption off for the whole
    message and let 687(a) back in behind a link.
    """
    url = "https://github.com/yadgarhq/actions/actions/runs/" + "3" * 40
    assert len(url) > pr_body.WRAP
    assert pr_body.looks_wrapped(["- fix: see", url, "and so on"])
    assert pr_body.is_wrap_continuation(url, "- W for warnings is not a bullet")


def test_a_bullet_after_a_long_url_is_still_a_continuation():
    """687(a) behind a link, which the two-sided bound used to let through."""
    url = "https://github.com/yadgarhq/actions/actions/runs/" + "3" * 40
    text = body(changelog=f"- chore: see the run at\n{url}\n- W for warnings, so it fails")
    problems, bump, count = pr_body.review(text, wrapped=True)
    assert problems == []
    assert (bump, count) == ("patch", 1)


def test_an_unwrapped_commit_message_keeps_every_entry():
    long_bullets = "\n".join(
        f"- {t}: " + "a description wide enough that no wrap could have made it " * 2
        for t in ("chore", "feat", "fix")
    )
    problems, bump, count = pr_body.review(body(changelog=long_bullets), wrapped=True)
    assert problems == []
    assert (bump, count) == ("minor", 3)


def test_an_unwrapped_message_does_not_absorb_a_bullet_under_a_full_line():
    """Six real messages have exactly this shape; none may lose an entry.

    `deploy`, `docs`, `iam`, `project-db` and `telemetry` each carry an
    unwrapped line of 71 or 72 columns immediately above a genuine bullet. Six
    of the seven near misses absorbed a `feat`, the class that decides a bump,
    so the zero divergence measured over the estate is a property of how many
    features each Changelog happened to hold rather than of the arithmetic.
    """
    changelog = (
        "- feat: generate the cache password and the broker account on sync\n"
        + FULL
        + "\n- feat: issue a per-service certificate from an internal CA, which "
        "is a line far wider than the wrap column and so proves the message "
        "was never wrapped at all"
    )
    problems, bump, count = pr_body.review(body(changelog=changelog), wrapped=True)
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


# ------------------------------------------------- the tag message, ledger 559

# The `## Changelog` of `yadgarhq/actions` v1.13.5 as the wrap wrote it into
# `main`, and beside it what the author typed. The tag cut from this message
# carries all five bullets cut off mid-sentence, permanently: the `v*` ruleset
# forbids moving or deleting a tag, so the record cannot be corrected.
TAG_FIXTURE = """\
- fix(pr_body): read a dash the wrap pushed to column 0 as a
continuation rather than a bullet with no type"""

TAG_FIXTURE_WHOLE = (
    "- fix(pr_body): read a dash the wrap pushed to column 0 as a "
    "continuation rather than a bullet with no type"
)


def reference(lines):
    """The derivation EXACTLY as it stood before ledger 559, kept as the oracle.

    Its counting is the behaviour that must not move, so the tests below compare
    against it rather than against numbers written down by hand. Its `entries`
    is the truncation itself.
    """
    absorb = pr_body.looks_wrapped(lines)
    entries, matches, lenient = [], [], []
    previous = None
    for line in lines:
        match = pr_body.BULLET.match(line)
        if not match:
            previous = line
            continue
        entries.append(line.strip())
        matches.append(match)
        if not (absorb and pr_body.is_wrap_continuation(previous, line)):
            lenient.append(match)
        previous = line
    return entries, matches, lenient


def test_the_old_derivation_really_did_truncate():
    """THE DEFECT, DEMANDED. If this stops failing, the fixture is not wrapped."""
    entries, _, _ = reference(TAG_FIXTURE.splitlines())
    assert entries == ["- fix(pr_body): read a dash the wrap pushed to column 0 as a"]
    assert entries[0] != TAG_FIXTURE_WHOLE


def test_a_wrapped_bullet_reaches_the_tag_whole():
    entries, _, _ = pr_body.commit_entries(TAG_FIXTURE.splitlines())
    assert entries == [TAG_FIXTURE_WHOLE]


def test_every_bullet_of_the_real_store_fixture_is_rejoined():
    entries, _, _ = pr_body.commit_entries(WRAPPED_CHANGELOG.splitlines())
    assert len(entries) == 4
    assert entries[0] == (
        "- fix(pool)!: refuse `verify_ca` when a connection is built — it accepts "
        "any publicly-trusted certificate for any name, with or without a CA "
        "configured"
    )
    assert all("\n" not in e for e in entries)


def test_joining_moves_no_count_on_the_real_fixtures():
    """THE INVARIANT THE CHANGE RESTS ON, asserted rather than asserted-in-prose.

    A continuation never matched `BULLET`, so it was never an entry. Only the
    TEXT of an entry may differ from the old derivation's; the three lists must
    be the same length, and `matches` and `lenient` identical.
    """
    for fixture in (TAG_FIXTURE, WRAPPED_CHANGELOG, FULL, SHORT):
        lines = fixture.splitlines()
        old_e, old_m, old_l = reference(lines)
        new_e, new_m, new_l = pr_body.commit_entries(lines)
        assert len(old_e) == len(new_e)
        assert [m.group(0) for m in old_m] == [m.group(0) for m in new_m]
        assert [m.group(0) for m in old_l] == [m.group(0) for m in new_l]


# Wider than the column and more than one word, so `looks_wrapped` refuses the
# whole message. `yadgarhq/iam-db` and `yadgarhq/task-db` both reach history in
# exactly this shape, because their squash message is not the pull request body.
UNWRAPPED = (
    "- feat: issue a per-service certificate from an internal CA, which is a "
    "line far wider than the wrap column and so proves the message was never "
    "wrapped at all"
)


def test_an_unwrapped_message_is_not_joined():
    """`looks_wrapped` gates the whole thing: a message the wrap never touched
    keeps every line the author put on a line of its own."""
    assert not pr_body.looks_wrapped([UNWRAPPED])
    lines = [UNWRAPPED, "and a line the author put on its own"]
    entries, _, _ = pr_body.commit_entries(lines)
    assert entries == [UNWRAPPED]


def test_joining_absorbs_as_freely_as_the_counting_already_did():
    """SAID HERE RATHER THAN DISCOVERED LATER, and it is not new behaviour.

    A 71-column bullet is indistinguishable from a line the wrap filled — that
    is the whole reason `is_wrap_continuation` exists and the reason the
    ambiguity refusal exists above it. So prose under a full-width bullet with
    no blank line between them joins the bullet. The counting already read this
    text the same way; only the tag message's text is affected, and the fix for
    an author who means two things is the blank line the template already has.
    """
    entries, matches, _ = pr_body.commit_entries([FULL, "and prose below it"])
    assert entries == [FULL + " and prose below it"]
    assert len(matches) == 1


def test_a_blank_line_closes_the_entry():
    lines = TAG_FIXTURE.splitlines() + ["", "and prose after a blank line"]
    entries, _, _ = pr_body.commit_entries(lines)
    assert entries == [TAG_FIXTURE_WHOLE]


def test_a_following_heading_is_not_absorbed_into_the_bullet():
    """The derivation reads WHOLE commit messages, with no notion of a section.

    A greedy wrap leaves the last line of a bullet short, so the arithmetic in
    `is_wrap_continuation` refuses the heading that follows it. Without that
    guard the tag message would swallow the rest of the commit.
    """
    lines = TAG_FIXTURE.splitlines() + ["## Verification", "ran the suite"]
    entries, _, _ = pr_body.commit_entries(lines)
    assert entries == [TAG_FIXTURE_WHOLE]


def test_prose_before_any_bullet_is_never_joined():
    """Nothing precedes the first entry, so nothing can be appended to it."""
    entries, _, _ = pr_body.commit_entries(["some prose first"] + TAG_FIXTURE.splitlines())
    assert entries == [TAG_FIXTURE_WHOLE]


def test_the_ambiguity_reading_is_unchanged_by_the_rejoining():
    """LEDGER 687(c) MUST STILL HOLD: a wrap-pushed `- feat!:` still diverges."""
    lines = (FULL + "\n- feat!: and the bump the two readings imply differs").splitlines()
    _, matches, lenient = pr_body.commit_entries(lines)
    assert pr_body.bump_for(matches) == "major"
    assert pr_body.bump_for(lenient) == "patch"


# ---------------------------------- what `sections()` does, now that two gates
#                                    read a body through it (ledger 730)
#
# Both behaviours below were previously described in prose — in `sections()`'s
# own docstring and in the pull request template's instructions — and pinned by
# nothing. Lifting the `template` job's reader onto this module makes them the
# shared contract for the pre-merge and post-merge readers at once, so they are
# now the kind of thing that has to break loudly rather than quietly.


def test_a_sub_heading_inside_a_required_section_makes_it_read_EMPTY():
    """THE TRAP THE TEMPLATE WARNS ABOUT, and it must keep behaving this way.

    ANY heading from `#` to `######` starts a new section, so a `###` inside
    `## What` ends `## What` and the prose beneath it belongs to the sub-heading.
    The section then collects nothing and is refused as empty, even though a
    reader can plainly see the answer. That is surprising, it is deliberate, and
    it is why the template says to use bold lead-ins for sub-structure.
    """
    text = body(what="### the detail\n\nthe prose that answers it")
    problems, _, _ = pr_body.review(text, wrapped=False)
    assert any("What" in p and "empty" in p for p in problems)


def test_a_bold_lead_in_is_the_way_to_write_the_same_thing():
    """THE PAIR. Without it the test above passes just as well against a gate
    that refuses every body, and the template's own advice goes unchecked."""
    text = body(what="**The detail.** The prose that answers it.")
    problems, _, _ = pr_body.review(text, wrapped=False)
    assert problems == []


def test_a_repeated_heading_is_last_wins_rather_than_merged():
    """LAST-WINS, asserted as a REFUSAL so it cannot pass vacuously.

    A second `## Risk` with nothing under it replaces the answered one, and the
    body is refused. A reader that merged the two, or kept the first, would call
    this body complete.
    """
    text = body(risk="the answer") + "\n## Risk\n"
    problems, _, _ = pr_body.review(text, wrapped=False)
    assert any("Risk" in p and "empty" in p for p in problems)


def test_the_later_heading_wins_in_the_other_direction_too():
    """THE PAIR, and it separates last-wins from "an empty repeat always fails".

    Empty first, answered second: last-wins accepts it. A reader that kept the
    FIRST occurrence would refuse.
    """
    sections = body().split("## Risk")[0]
    problems, _, _ = pr_body.review(sections + "## Risk\n\n## Risk\n\nthe answer\n",
                                    wrapped=False)
    assert problems == []


# ------------------------------------------- one reader, and no second copy of it


def test_no_workflow_carries_its_own_copy_of_the_contract():
    """LEDGER 730, AND THIS IS THE TEST THAT MAKES THE EXTRACTION STICK.

    The `template` job carried its own `REQUIRED`, `BULLET` and `TYPES` inside a
    heredoc, importing nothing — a third reader of the same artefact that
    nothing compared against this module. It agreed when it was lifted; the
    point of this test is that the next copy cannot be added quietly.

    STRUCTURAL RATHER THAN A LITERAL GREP: it matches the ASSIGNMENT of any of
    the three names, so re-inlining with a reordered alternation or a reworded
    heading list still trips it.
    """
    defines = re.compile(r"^\s*(REQUIRED|BULLET|TYPES)\s*=", re.M)
    offenders = []
    for workflow in sorted((REPO / ".github" / "workflows").glob("*.y*ml")):
        for match in defines.finditer(workflow.read_text()):
            offenders.append(f"{workflow.name}: {match.group(1)}")
    assert offenders == [], (
        "the template contract is defined in a workflow again; it belongs in "
        f"scripts/pr_body.py and nowhere else: {offenders}"
    )


def test_both_readers_reach_the_gate_by_name():
    """THE OTHER HALF: no inline copy AND the file is actually invoked.

    Deleting the step entirely would satisfy the test above. Two jobs run the
    script — `template` before the merge and `version` after it — and both reach
    it through the same staged path.
    """
    text = (REPO / ".github" / "workflows" / "ci-pr.yaml").read_text()
    assert text.count('python3 "$dir/pr_body.py"') == 2


# ---------------------------------------------------------------------------
# Ledger 1518: a bullet inside an HTML comment is the template's EXAMPLE, not an
# entry. `sections` already dropped comments for the pull-request gate; the
# derivation's reader, `commit_entries`, did not, so a commented `feat!:` example
# cut a spurious minor (chart v0.24.0).
# ---------------------------------------------------------------------------


def test_commit_entries_ignores_a_single_line_comment_bullet():
    lines = ["<!-- - feat!: an example -->", "- fix: the real one"]
    entries, matches, lenient = pr_body.commit_entries(lines)
    assert entries == ["- fix: the real one"]
    assert pr_body.bump_for(matches) == "patch"
    assert len(lenient) == 1


def test_commit_entries_ignores_a_multi_line_comment_bullet():
    lines = [
        "## Changelog",
        "",
        "<!--",
        "  One bullet per change, e.g.",
        "  - feat!: an example",
        "-->",
        "- fix: the real one",
    ]
    entries, matches, _ = pr_body.commit_entries(lines)
    assert entries == ["- fix: the real one"]
    assert pr_body.bump_for(matches) == "patch"


def test_commit_entries_still_counts_a_bullet_outside_a_comment():
    lines = ["<!-- - feat!: an example -->", "- feat: a real feature"]
    entries, matches, _ = pr_body.commit_entries(lines)
    assert entries == ["- feat: a real feature"]
    assert pr_body.bump_for(matches) == "minor"


def test_a_comment_opener_in_a_code_span_does_not_drop_a_later_feat_bang():
    """An unterminated opener must NOT swallow the rest: the bump would under-report."""
    lines = ["- fix: strip `<!--` openers", "- feat!: drop v1"]
    entries, matches, _ = pr_body.commit_entries(lines)
    assert entries == ["- fix: strip `<!--` openers", "- feat!: drop v1"]
    assert pr_body.bump_for(matches) == "major"
    problems, bump, count = pr_body.review(
        "## What\n\nx\n\n## Why\n\nx\n\n## Verification\n\nx\n\n"
        "## Risk\n\nx\n\n## Changelog\n\n"
        "- fix: strip `<!--` openers\n- feat!: drop v1\n",
        False,
    )
    assert (problems, bump, count) == ([], "major", 2)


def test_sections_keeps_later_headings_after_an_unterminated_opener():
    found = pr_body.sections("## A\n\n```\n<!--\n```\n\n## B\n\ntext\n")
    assert "B" in found


def test_strip_comments_leaves_an_unterminated_opener_alone():
    assert pr_body.strip_comments("a <!-- b") == "a <!-- b"


def test_review_judges_wrapping_over_the_comment_stripped_text():
    """Ledger 687: `review` and `commit_entries` must feed `looks_wrapped` alike."""
    long_comment = "<!-- " + "x" * 100 + " -->"
    body = (
        "subject\n" + long_comment + "\n## What\n\nx\n\n## Why\n\nx\n\n"
        "## Changelog\n\n- fix: a\n\n## Verification\n\nx\n\n## Risk\n\nx\n"
    )
    stripped = pr_body.strip_comments(body)
    assert pr_body.looks_wrapped(pr_body.body_lines(stripped)) != pr_body.looks_wrapped(
        pr_body.body_lines(body)
    )
    seen = []
    real = pr_body.looks_wrapped
    pr_body.looks_wrapped = lambda lines, *a, **k: seen.append(list(lines)) or real(lines, *a, **k)
    try:
        pr_body.review(body, True)
    finally:
        pr_body.looks_wrapped = real
    assert all(long_comment not in "\n".join(l) for l in seen)


def test_strip_comments_is_non_greedy():
    assert pr_body.strip_comments("a<!-- x -->b<!-- y -->c") == "abc"


def test_fetch_commit_token_is_not_forwarded_on_redirect(monkeypatch):
    """The bearer rides `unredirected_hdrs`, so urllib drops it on a redirect hop."""
    import urllib.request
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        raise OSError("stop")

    monkeypatch.setattr(pr_body.urllib.request, "urlopen", urlopen)
    pr_body.fetch_commit("yadgarhq/chart", APP_SHA, "secret-token")
    req = seen[0]
    assert req.get_header("Authorization") == "Bearer secret-token"
    assert "Authorization" not in req.headers
    hop = urllib.request.HTTPRedirectHandler().redirect_request(
        req, None, 302, "Found", {}, "https://elsewhere.example/x")
    assert hop is not None
    assert hop.get_header("Authorization") is None
