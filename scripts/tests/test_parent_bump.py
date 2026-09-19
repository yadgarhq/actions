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

Done = namedtuple("Done", "returncode stdout stderr")


def chart(pins=None):
    lines = [HEAD, "dependencies:\n"]
    for name, version in (pins or PINS).items():
        lines.append(f"  - name: {name}\n")
        lines.append(f"    version: {version}\n")
        lines.append("    repository: oci://ghcr.io/yadgarhq/charts\n")
    return "".join(lines)


class Api:
    """An argv-dispatching `gh` stub that models the blob sha and the tag refs."""

    def __init__(self, main=None, tags=None, published=None, interfere=None,
                 interfere_ref=None):
        self.text = {"main": main if main is not None else chart()}
        self.tags = list(tags if tags is not None else ["v0.1.0"])
        for tag in self.tags:
            self.text[tag] = published if published is not None else chart()
        self.blob = "blob-0"
        self.commits = 0
        self.objects = 0
        self.calls = []
        self.puts = []
        self.refs = []
        self.interfere = interfere
        self.interfere_ref = interfere_ref
        self.put_failures = 0
        self.ref_error = None

    # -- helpers ---------------------------------------------------------------

    def field(self, args, key):
        for index, arg in enumerate(args):
            if arg.startswith(f"{key}=") and args[index - 1] == "-f":
                return arg[len(key) + 1 :]
        return None

    def __call__(self, args):
        self.calls.append(list(args))
        path = next(a for a in args[2:] if not a.startswith("-"))

        if "-X" in args and "PUT" in args:
            return self.put(args)
        if "git/matching-refs/tags/v" in path:
            return Done(0, "".join(f"refs/tags/{t}\n" for t in self.tags), "")
        if path.endswith("git/tags"):
            self.objects += 1
            return Done(0, f"object-{self.objects}\n", "")
        if path.endswith("git/refs"):
            return self.ref(args)
        if "commits/main" in path:
            return Done(0, f"commit-{self.commits}\n", "")
        if "contents/chart/Chart.yaml?ref=" in path:
            ref = path.split("?ref=", 1)[1]
            if ref not in self.text:
                return Done(1, "", "gh: Not Found (HTTP 404)")
            return Done(
                0,
                json.dumps(
                    {
                        "sha": self.blob if ref == "main" else f"blob-{ref}",
                        "content": base64.b64encode(
                            self.text[ref].encode("utf-8")
                        ).decode("ascii"),
                    }
                ),
                "",
            )
        raise AssertionError(f"the stub was asked something it does not model: {args}")

    def put(self, args):
        if self.interfere is not None:
            self.interfere(self)
            self.interfere = None
        if self.put_failures:
            self.put_failures -= 1
            return Done(1, "", "gh: refused by the stub (HTTP 500)")
        if self.field(args, "sha") != self.blob:
            return Done(
                1,
                "",
                "gh: chart/Chart.yaml does not match "
                f"{self.field(args, 'sha')} (HTTP 409)",
            )
        text = base64.b64decode(self.field(args, "content")).decode("utf-8")
        self.text["main"] = text
        self.commits += 1
        self.blob = f"blob-{self.commits}"
        self.puts.append(
            {"text": text, "message": self.field(args, "message"),
             "branch": self.field(args, "branch")}
        )
        return Done(0, json.dumps({"commit": {"sha": f"commit-{self.commits}"}}), "")

    def ref(self, args):
        if self.interfere_ref is not None:
            self.interfere_ref(self)
            self.interfere_ref = None
        name = self.field(args, "ref")
        if self.ref_error is not None:
            return Done(1, "", self.ref_error)
        tag = name[len("refs/tags/") :]
        if tag in self.tags:
            return Done(1, "", "gh: Reference already exists (HTTP 422)")
        self.tags.append(tag)
        self.text[tag] = self.text["main"]
        self.refs.append({"ref": name, "sha": self.field(args, "sha")})
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
        api.text["main"] = chart({**PINS, "task": "0.5.29"})
        api.blob = "blob-winner"

    api = Api(interfere=winner)
    assert bump(api) == 0
    landed = pins_of(api.text["main"])
    assert landed["task"] == "0.5.29", "the winner's pin was overwritten"
    assert landed["gateway"] == "0.9.49", "this release's pin was not written"


def test_a_lost_content_race_is_refused_before_it_is_retried():
    """The first PUT must FAIL rather than land, or the race proves nothing."""

    def winner(api):
        api.blob = "blob-winner"

    api = Api(interfere=winner)
    assert bump(api) == 0
    assert len(api.puts) == 1
    assert sum(1 for c in api.calls if "-X" in c and "PUT" in c) == 2


def test_a_lost_tag_race_cuts_the_next_version_rather_than_exiting_zero():
    """The loser's pin must not end up under a tag that does not carry it.

    THE WINNER ARRIVES AFTER THE TAG LIST IS READ, which is the only window the
    re-derivation exists for. A winner who arrives earlier is handled by the
    listing itself and proves nothing about `Reference already exists`.
    """

    def winner(api):
        api.tags.append("v0.1.1")
        api.text["v0.1.1"] = chart({**PINS, "task": "0.5.29"})

    api = Api(interfere_ref=winner)
    assert bump(api) == 0
    # v0.1.1 was refused, the tags were re-listed, and the NEXT number was cut —
    # on a second tag object, which is what makes this the retry and not the
    # first pass.
    assert api.refs == [{"ref": "refs/tags/v0.1.2", "sha": "object-2"}]
    assert api.objects == 2


def test_two_module_releases_cut_two_parent_versions():
    """ADR-0722: a module release cuts a NEW parent version, so two means two."""
    api = Api()
    assert bump(api, module="gateway", version="0.9.49") == 0
    assert bump(api, module="task", version="0.5.29") == 0
    assert [r["ref"] for r in api.refs] == [
        "refs/tags/v0.1.1",
        "refs/tags/v0.1.2",
    ]
    landed = pins_of(api.text["main"])
    assert (landed["gateway"], landed["task"]) == ("0.9.49", "0.5.29")


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
    api.text.pop("main")
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
    assert pins_of(api.text["main"])["gateway"] == "0.9.49"


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


def test_the_chart_token_is_a_second_mint_and_not_a_wider_first_one():
    """The separation ruling: the argocd token must not be able to write the chart.

    Widening the `argocd` mint would hand the token that pins the cluster's
    images write access to the chart repository, and hand this write path the
    power to rewrite every `versions/*.yaml`.
    """
    mint = steps("parent")["app"]
    assert mint["with"]["repositories"] == "chart"
    assert mint["with"]["permission-contents"] == "write"
    assert steps("deployment")["app"]["with"]["repositories"] == "argocd"
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
