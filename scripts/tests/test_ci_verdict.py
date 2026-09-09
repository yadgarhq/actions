"""What `ci_verdict.py` REFUSES, pinned so it cannot quietly stop refusing.

This suite guards the only required check on `main` in every repository in the
estate, so the thing it has to pin is not that a green run stays green — it is
that a run which checked LESS cannot report the same verdict as one that checked
everything.

THE FULL RESULT MATRIX IS EXERCISED, because the defect this replaces was
invisible in three quarters of it. `success`, `failure` and `cancelled` behaved
identically under the old shell loop and under this gate; only `skipped` differed,
and only for some jobs, and only on some events. A suite that tested the first
three would have passed against the bypass.

AND EVERY GREEN CASE IS PAIRED WITH A RED ONE ON THE SAME TREE, which is
`test_d80_portability.py`'s discipline and the half easier to forget. "The real
workflow with every job successful passes" also passes when the gate is dead, so
each such case is fed alongside a mutation of the SAME matrix that must redden.

`test_the_documented_bypass` IS THE ONE TO READ FIRST. It transcribes the shell
loop this gate replaced and runs both predicates over one job matrix, asserting
that the old one reports success and the new one refuses it. It prints the input
rather than only the verdict, so the failure is legible rather than a bare
`assert False`.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import pathlib
import subprocess
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "ci_verdict.py"
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"

sys.path.insert(0, str(ROOT / "scripts"))

import ci_verdict  # noqa: E402

# The jobs `passed` gates today. Read from the workflow in
# `test_the_gate_covers_every_job_it_needs` rather than trusted here; this
# literal exists so the fixtures below are readable.
GATED = [
    "detect",
    "precommit",
    "workflows",
    "vulnerabilities",
    "template",
    "test",
    "proto",
    "portability",
    "service_immutable",
]


def needs(results, rust="false", proto="false"):
    """A `toJSON(needs)` payload: every gated job with a result, detect with outputs.

    THE DEFAULT IS A CONSISTENT RUN, so that every fixture below states only its
    own mutation. `test` and `proto` default to what `detect`'s outputs imply,
    which is the whole point of the pair — a fixture that had to remember to skip
    them by hand would be a fixture that could forget.
    """
    ctx = {}
    for job in GATED:
        default = "success"
        if job == "test" and rust != "true":
            default = "skipped"
        if job == "proto" and proto != "true":
            default = "skipped"
        ctx[job] = {"result": results.get(job, default), "outputs": {}}
    if ctx["detect"]["result"] == "success":
        ctx["detect"]["outputs"] = {"rust": rust, "proto": proto}
    return ctx


def run(ctx, event="pull_request", author="User", workflow=None, repo="yadgarhq/actions"):
    """The gate as CI runs it: a subprocess reading its input from the environment."""
    env = {
        "PATH": "/usr/bin:/bin",
        "WORKFLOW": str(workflow or CI_PR),
        "NEEDS": json.dumps(ctx),
        "EVENT": event,
        "AUTHOR_TYPE": author,
        "REPO": repo,
        "PYTHONPATH": str(ROOT / "scripts"),
    }
    return subprocess.run(
        [sys.executable, str(GATE)], env=env, capture_output=True, text=True
    )


def verdict(ctx, **kw):
    proc = run(ctx, **kw)
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode == 0, proc


# ---------------------------------------------------------------------------
# The bypass, constructed
# ---------------------------------------------------------------------------


def legacy_predicate(ctx, event):
    """The predicate this gate replaced, transcribed from `ci-pr.yaml` verbatim.

    Both of its steps, because the first one is the reason the second one could
    argue that `skipped` was safe:

        step 1  push -> detect must be `skipped`; otherwise `success`
        step 2  every other job: `case "$r" in success|skipped) ;; *) exit 1`
    """
    required = "skipped" if event == "push" else "success"
    if ctx["detect"]["result"] != required:
        return False
    for job in GATED:
        if job == "detect":
            continue
        if ctx[job]["result"] not in ("success", "skipped"):
            return False
    return True


def test_the_documented_bypass(capsys):
    """A run in which NOTHING was checked, which the old predicate called success.

    Every gated job but `detect` reports `skipped` on a `pull_request` event.
    `detect` succeeded, so the old gate's first step is satisfied and its second
    step sees seven `skipped` results and waves all seven through. No hooks ran,
    no scanner ran, no test ran, the body was never read — and `ci / passed` is
    green on a repository whose ruleset requires exactly that context with
    `bypass_actors: []`.
    """
    ctx = needs({job: "skipped" for job in GATED if job != "detect"})

    with capsys.disabled():
        print("\n--- the input, not just the verdict ---")
        print("event: pull_request   author type: User")
        for job in GATED:
            print(f"  {job:<16} {ctx[job]['result']}")
        print(f"  detect outputs   {ctx['detect']['outputs']}")

    assert legacy_predicate(ctx, "pull_request") is True, (
        "the transcription is wrong: the predicate this replaces accepted this matrix"
    )

    ok, proc = verdict(ctx)
    with capsys.disabled():
        print("--- old predicate: PASS      new predicate: "
              f"{'PASS' if ok else 'FAIL'} ---")
        print(proc.stdout.rstrip())
        print(proc.stderr.rstrip())

    assert not ok
    # Named individually rather than counted, so a gate that reddened for one
    # unrelated reason cannot pass this test.
    for job in ("precommit", "workflows", "vulnerabilities", "portability", "template"):
        assert f"`{job}` reported 'skipped'" in proc.stderr


def test_one_skipped_job_is_enough_to_refuse():
    """The bypass does not need all seven; it needs one, and `precommit` is the one.

    `precommit` is where every hook in the estate runs — the formatters, the
    linters, `gitleaks`, and since ledger 686 `pytest scripts/tests/` itself. It
    has no condition beyond the event, so on a pull request there is no legitimate
    reason for it to skip at all.
    """
    ok, proc = verdict(needs({"precommit": "skipped"}))
    assert not ok
    assert "`precommit` reported 'skipped'" in proc.stderr


# ---------------------------------------------------------------------------
# The full matrix, per job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", GATED)
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_no_job_may_fail_or_be_cancelled(job, result):
    """`cancelled` is not `failure` and must not be treated as one either.

    A cancelled job ran partway and concluded nothing. The old loop already got
    this right — it is pinned here because the usual shape of this defect,
    `needs.X.result != 'failure'`, gets it wrong, and a future edit to this gate
    must not arrive at that shape by another road.
    """
    ok, _ = verdict(needs({job: result}))
    assert not ok


@pytest.mark.parametrize(
    "job", ["detect", "precommit", "workflows", "vulnerabilities", "portability"]
)
def test_a_job_with_no_reason_to_skip_may_not_skip(job):
    """These five have no condition but the event, so on a pull request they run."""
    ok, proc = verdict(needs({job: "skipped"}))
    assert not ok
    assert "had work to do and did not do it" in proc.stderr


def test_the_ordinary_pull_request_passes():
    """The paired green: a repository with no Cargo.toml and no PROTO_VERSION.

    This is `yadgarhq/actions` itself. `test` and `proto` skip because their
    conditions are false, which is the distinction the whole file exists to draw,
    and everything else succeeds.
    """
    ok, proc = verdict(needs({"test": "skipped", "proto": "skipped"}))
    assert ok, proc.stdout + proc.stderr
    assert "its condition was false" in proc.stdout


# ---------------------------------------------------------------------------
# The legitimate skips, each with its opposite
# ---------------------------------------------------------------------------


def test_test_skips_only_where_there_is_no_rust():
    """`test` may skip when detect found no `Cargo.toml`, and only then."""
    ok, _ = verdict(needs({"test": "skipped"}, rust="false"))
    assert ok

    ok, proc = verdict(needs({"test": "skipped"}, rust="true"))
    assert not ok, "a Rust repository skipped its tests and the gate allowed it"
    assert "`test` reported 'skipped'" in proc.stderr


def test_a_rust_repository_must_actually_run_its_tests():
    ok, proc = verdict(needs({}, rust="true"))
    assert ok, proc.stdout + proc.stderr


def test_test_may_not_run_where_there_is_no_rust():
    """The half an allow-list cannot check: a job that ran when it should not have.

    Nothing anywhere reported this before. It is not hypothetical — it is what a
    `detect` output read by the wrong job, or a condition edited to reference the
    wrong key, looks like from the outside.
    """
    ok, proc = verdict(needs({"test": "success"}, rust="false"))
    assert not ok
    assert "ran when the workflow says it should not have" in proc.stderr


def test_proto_skips_only_where_there_is_no_vendored_contract():
    ok, _ = verdict(needs({"proto": "skipped"}, proto="false"))
    assert ok

    ok, proc = verdict(needs({"proto": "skipped"}, proto="true"))
    assert not ok, "the vendored-drift check was switched off and the gate allowed it"
    assert "`proto` reported 'skipped'" in proc.stderr


def test_template_skips_for_a_bot_author_and_for_nobody_else():
    """The one exemption written into a condition rather than derived from the tree.

    `template` reads the pull request body, and a bot's body is generated rather
    than authored. The exemption is GitHub's `user.type`, which asks what the
    account IS rather than pattern-matching a name.
    """
    ok, _ = verdict(needs({"template": "skipped"}), author="Bot")
    assert ok

    ok, proc = verdict(needs({"template": "skipped"}), author="User")
    assert not ok, "a human pull request skipped the body gate and the gate allowed it"
    assert "`template` reported 'skipped'" in proc.stderr


def test_a_bot_pull_request_may_not_run_the_template_job():
    ok, proc = verdict(needs({}), author="Bot")
    assert not ok
    assert "`template`" in proc.stderr


# ---------------------------------------------------------------------------
# The other event
# ---------------------------------------------------------------------------


def test_on_a_push_to_main_every_gated_job_is_meant_to_skip():
    """Callers trigger on `pull_request` and `push`, and on push this gate reports.

    Every condition under `passed` is false on a push — `detect` carries
    `github.event_name != 'push'` and the rest key off it or off the event
    directly — so the required check reports on merged code without re-running
    what the pull request just ran.
    """
    ctx = needs({job: "skipped" for job in GATED})
    ok, proc = verdict(ctx, event="push")
    assert ok, proc.stdout + proc.stderr


def test_a_job_that_runs_on_a_push_is_refused():
    """The pairing: on a push, running is the anomaly and skipping is correct.

    This is the case the old first step was written for, generalised. A `detect`
    that executes on a push means the condition every other job keys off has been
    edited, and the gate says so instead of reporting on a run it did not model.
    """
    ctx = needs({job: "skipped" for job in GATED})
    ctx["detect"] = {"result": "success", "outputs": {"rust": "false", "proto": "false"}}
    ok, proc = verdict(ctx, event="push")
    assert not ok
    assert "ran when the workflow says it should not have" in proc.stderr


def test_the_old_first_step_is_preserved_by_the_general_rule():
    """A failed `detect` on a pull request reddens, with every downstream skipped.

    This is the matrix the replaced job's first step was added for: `detect`
    fails, so `precommit`, `test` and `proto` are all `skipped` — the result
    GitHub gives a job whose dependency did not succeed — and the loop waved all
    three through. The general rule catches it at `detect` itself, so the special
    case that used to be a separate step is no longer written down anywhere.
    """
    ctx = needs({"detect": "failure", "precommit": "skipped", "test": "skipped",
                 "proto": "skipped"})
    ok, proc = verdict(ctx)
    assert not ok
    assert "`detect` reported 'failure'" in proc.stderr


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def synthetic(tmp_path, condition, name="probe"):
    """A one-job workflow plus the gate that needs it, for conditions not in `ci-pr.yaml`."""
    text = (
        "name: probe\non: [pull_request]\njobs:\n"
        f"  {name}:\n    if: {condition}\n    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'true'}]\n"
        f"  passed:\n    needs: [{name}]\n    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'true'}]\n"
    )
    path = tmp_path / "probe.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_unregistered_detect_output_is_refused(tmp_path):
    """THE INTERLOCK, on the next reference somebody adds rather than on the last.

    This test used to read `text_edit`, because `text_edit` was the reference
    nobody had registered — and registering it, together with the assertion that
    earns it, is what ledger 685 did. The property being pinned was never about
    that name: it is that a conjunct added to a job's `if:` cannot buy a skip
    just by existing. A NEW `detect` output — the shape the next affordability
    argument will take — still refuses, because `_FACTS` is a list of full
    reference names rather than a pattern over `needs.detect.outputs.*`.

    Written against a name that is deliberately plausible. `docs_only` is exactly
    the conjunct somebody reaches for next, and it must cost the same deliberate
    decision `text_edit` cost.
    """
    wf = synthetic(
        tmp_path,
        "github.event_name != 'push' && needs.detect.outputs.docs_only != 'true'",
    )
    proc = run({"probe": {"result": "skipped", "outputs": {}}}, workflow=wf)
    assert proc.returncode == 1
    assert "refused to report a verdict" in proc.stderr
    assert "docs_only" in proc.stderr
    assert "justified by nothing" in proc.stderr


def test_a_reference_may_be_registered_with_the_assertion_that_earns_it(tmp_path):
    """The paired green, in-process: registering the reference makes it evaluable.

    Deliberately not exercised through the subprocess, because there is no way to
    register a reference from the environment — and there must not be, or the
    refusal above would be a suggestion.
    """
    ci_verdict.register_deferred_reference(
        "needs.detect.outputs.text_edit", lambda expr: "true"
    )
    try:
        resolve = ci_verdict.make_resolver("pull_request", "User", "o/r", {})
        assert ci_verdict.evaluate(
            "needs.detect.outputs.text_edit != 'true'", resolve
        ) is False
    finally:
        ci_verdict._DEFERRED.clear()


def test_a_condition_reading_a_job_result_is_refused(tmp_path):
    """`ci-release.yaml`'s former defect, refused rather than reproduced.

    `chart` ran on `needs.image.result != 'failure'` until ledger 729, so a
    CANCELLED image job published a chart. If that shape ever appears under
    `ci / passed`, the gate refuses instead of resolving it — a result is a
    consequence, and a skip caused by an upstream failure would read as a job
    that had nothing to do.

    The synthetic workflow below is what is under test; the real
    `ci-release.yaml` is checked by the chart-gate matrix at the foot of this
    file, which is where a revert of that fix reddens.
    """
    wf = synthetic(tmp_path, "always() && needs.image.result != 'failure'")
    proc = run({"probe": {"result": "skipped", "outputs": {}}}, workflow=wf)
    assert proc.returncode == 1
    assert "a consequence, not a reason" in proc.stderr


def test_an_unknown_function_is_refused(tmp_path):
    """`success()` is a statement about upstream results wearing a different hat."""
    wf = synthetic(tmp_path, "success()")
    proc = run({"probe": {"result": "skipped", "outputs": {}}}, workflow=wf)
    assert proc.returncode == 1
    assert "does not know" in proc.stderr


def test_a_bare_reference_is_not_a_condition(tmp_path):
    wf = synthetic(tmp_path, "github.event_name")
    proc = run({"probe": {"result": "success", "outputs": {}}}, workflow=wf)
    assert proc.returncode == 1
    assert "compare it explicitly" in proc.stderr


def test_a_gate_that_needs_nothing_is_refused(tmp_path):
    """A required check depending on no job reports success unconditionally."""
    path = tmp_path / "empty.yaml"
    path.write_text(
        "name: p\non: [pull_request]\njobs:\n"
        "  passed:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n",
        encoding="utf-8",
    )
    proc = run({}, workflow=path)
    assert proc.returncode == 1
    assert "gates nothing" in proc.stderr


def test_a_missing_result_is_refused():
    """The `needs` context and the workflow must describe the same run.

    The loud direction: the workflow lists a job the caller's run did not
    report. Paired with the green below, so this is not passing because the
    gate is dead.
    """
    assert verdict(needs({}))[0] is True
    ctx = needs({})
    del ctx["portability"]
    proc = run(ctx)
    assert proc.returncode == 1
    assert "no result was reported for 'portability'" in proc.stderr


def test_a_reported_job_the_gate_does_not_read_is_refused(capsys):
    """The QUIET direction, and a bypass rather than an error.

    The gate iterates the conditions it read, so a job present in `needs` and
    absent from the workflow file is never looked at. Fed a matrix in which
    exactly that job reports `failure`, every row the gate DID read is `ok` —
    so without the set check it prints eight green rows and exits 0 on a run
    with a failed job in it.

    Reachable because the two sets are two reads of one file at two moments:
    the caller resolved `ci-pr.yaml` when the run started, the gate checks it
    out minutes later. A job renamed on `main` inside that window, or any
    consumer pinning a tag that lags `main`, is this input.
    """
    ctx = needs({})
    ctx["legacy_job"] = {"result": "failure", "outputs": {}}

    with capsys.disabled():
        print("\n--- the input, not just the verdict ---")
        print("event: pull_request   author type: User")
        for job, entry in ctx.items():
            tail = "  <- not in this file's `passed.needs:`" if job == "legacy_job" else ""
            print(f"  {job:<16} {entry['result']}{tail}")

    proc = run(ctx)
    with capsys.disabled():
        print(f"--- exit {proc.returncode} ---")
        print(proc.stderr.rstrip())

    assert proc.returncode == 1
    assert "'legacy_job'" in proc.stderr
    assert "would not have been checked at all" in proc.stderr
    # The eight jobs it CAN read are all consistent, which is what makes this
    # the quiet direction: nothing else in the matrix gives it a reason to fail.
    assert verdict(needs({}))[0] is True


def test_the_verdict_names_how_many_jobs_it_read():
    """A gate that inspected nothing must not be able to phrase it as success."""
    ok, proc = verdict(needs({}))
    assert ok
    assert f"All {len(GATED)} jobs under `passed`" in proc.stdout


def test_an_empty_event_is_refused():
    proc = run(needs({}), event="")
    assert proc.returncode == 1
    assert "EVENT is empty" in proc.stderr


def test_a_non_json_needs_context_is_refused():
    ok = subprocess.run(
        [sys.executable, str(GATE)],
        env={"PATH": "/usr/bin:/bin", "WORKFLOW": str(CI_PR), "NEEDS": "not json",
             "EVENT": "pull_request"},
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 1
    assert "not JSON" in ok.stderr


# ---------------------------------------------------------------------------
# Against the real workflow
# ---------------------------------------------------------------------------


def test_the_gate_covers_every_job_it_needs():
    """Adding a job to `passed`'s `needs:` puts it under the gate with no second edit.

    Read from the file rather than compared to a list, so this asserts the
    coupling instead of restating it. The literal `GATED` above only keeps the
    fixtures readable.
    """
    conditions = ci_verdict.load_conditions(CI_PR, "passed")
    assert [j for j, _ in conditions] == GATED or set(j for j, _ in conditions) == set(
        GATED
    ), "GATED is stale; the fixtures below no longer describe the workflow"


def test_every_condition_in_the_real_workflow_is_evaluable():
    """No job under `passed` may carry a condition this gate cannot read.

    This is the test that fails when somebody edits a job's `if:` and the gate has
    not been taught what they wrote — before the change reaches a consumer, rather
    than as a refused merge in seventeen repositories at once.
    """
    ctx = {
        "detect": {
            "result": "success",
            # `text_edit` false, so no check run is read: an ordinary pull
            # request evaluates every condition without touching the API.
            "outputs": {"rust": "true", "proto": "true", "text_edit": "false"},
        }
    }
    ci_verdict.register_deferred_reference(
        ci_verdict.TEXT_EDIT_REF,
        ci_verdict.make_text_edit_resolver(
            needs=ctx,
            repo="yadgarhq/actions",
            sha=SHA,
            run_id="1",
            check_name="ci / passed",
            token="",
            fetch=no_fetch,
        ),
    )
    try:
        resolve = ci_verdict.make_resolver(
            "pull_request", "User", "yadgarhq/actions", ctx
        )
        for job, condition in ci_verdict.load_conditions(CI_PR, "passed"):
            assert isinstance(ci_verdict.evaluate(condition, resolve), bool), job
    finally:
        ci_verdict._DEFERRED.clear()


# ---------------------------------------------------------------------------
# `ci-release.yaml`'s chart gate (ledger 729)
# ---------------------------------------------------------------------------
#
# THE CONDITION IS EVALUATED, NOT MATCHED AS A STRING, and that is the whole
# difference between this block and the single assertion it replaces. A string
# assertion reds on a literal revert and passes on every OTHER predicate that
# admits a cancelled image, which is the property that actually needs pinning.
# `ci_verdict.evaluate` already reads this dialect — `always()` is in
# `_FUNCTIONS` and the parser's own docstring says parentheses exist "because
# `ci-release.yaml` would need them the moment its `chart` condition is written
# correctly" — so the matrix below runs the real file's real condition.
#
# It sidesteps a second, duller failure too: `prettier` formats YAML in this
# repository's hook chain, so the exact bytes of a folded `if: >-` block are not
# something a test should be asserting on.
#
# `ci-release.yaml` IS NOT GATED BY `ci_verdict.py`. It is tag-triggered and
# publishes rather than merges, so nothing evaluates these conditions in
# anger until a real tag. That is the reason to evaluate them here.


def release_resolver(image_detected, chart_detected, image_result):
    """The three references `ci-release.yaml`'s `chart` condition may read.

    LOCAL, AND REFUSES EVERYTHING ELSE. `make_resolver` refuses
    `needs.<job>.result` outright, and rightly — but that refusal is about what
    may justify a job checking LESS under the required merge check, which this
    workflow is not under. Here the success arm has no fact to stand on: "the
    digest exists" is not a property of the tree. The refusal is kept for every
    reference NOT named below, so a condition that grows a fourth one reds this
    matrix instead of being quietly evaluated against a default.
    """
    values = {
        "needs.detect.outputs.image": image_detected,
        "needs.detect.outputs.chart": chart_detected,
        "needs.image.result": image_result,
    }

    def resolve(ref, expr):
        if ref not in values:
            raise ci_verdict.Refused(f"{ref} is not a reference this test supplies ({expr!r})")
        return values[ref]

    return resolve


def chart_condition():
    import yaml

    return yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]["chart"]["if"]


# (what `detect` said about the image, what the image job reported, may publish)
#
# The four values GitHub renders for a job result, against the two things
# `detect` can say. `success`/`skipped` are the only results reachable for their
# respective rows — the image job's own `if:` is `needs.detect.outputs.image ==
# 'true'` — and the unreachable combinations are covered by the chart=false
# sweep below rather than invented here.
CHART_MATRIX = [
    ("true", "success", True),
    ("true", "cancelled", False),
    ("true", "failure", False),
    ("false", "skipped", True),
]


@pytest.mark.parametrize("detected,result,may_publish", CHART_MATRIX)
def test_the_release_chart_gate_publishes_only_what_it_can_pin(
    detected, result, may_publish
):
    """A chart is published when it can be pinned, or when there is nothing to pin.

    The cancelled row is the defect. `helm push` would publish the chart built
    from git's `values.yaml`, which by D65 carries a moving tag, because the
    digest-pin step is separately gated on `success` and does not run. Nothing
    errors and nothing reports it.
    """
    got = ci_verdict.evaluate(
        chart_condition(), release_resolver(detected, "true", result)
    )
    assert got is may_publish


@pytest.mark.parametrize("detected,result,_may_publish", CHART_MATRIX)
def test_the_release_chart_gate_never_runs_without_a_chart(detected, result, _may_publish):
    """The paired red for every green above, on the same tree.

    Same matrix with `detect` reporting no `chart/`, where the answer is always
    no. Without this, a condition that had lost its `chart` conjunct entirely
    would still satisfy the test above on all four rows.
    """
    assert (
        ci_verdict.evaluate(
            chart_condition(), release_resolver(detected, "false", result)
        )
        is False
    )


def test_the_old_release_chart_gate_would_have_published_a_cancelled_image():
    """The bypass transcribed, so the fix is shown to change an outcome.

    `test_the_documented_bypass`'s discipline applied to `ci-release.yaml`: run
    the predicate that shipped and the one in the file over the same row, and
    assert they disagree. A fix nobody can demonstrate changing a verdict is a
    fix nobody can review.
    """
    row = release_resolver("true", "true", "cancelled")
    was = "always() && needs.detect.outputs.chart == 'true' && needs.image.result != 'failure'"
    assert ci_verdict.evaluate(was, row) is True
    assert ci_verdict.evaluate(chart_condition(), row) is False


def test_the_release_chart_gate_still_admits_a_chart_only_repository():
    """`always()` is a deliberate allowance and this is the case that needs it.

    `yadgarhq/config` has a `chart/` and no `Containerfile`, so its image job
    never runs, and it calls `ci-release.yaml@main`. Narrowing this gate to
    demand `success` unconditionally would stop releasing its chart. Stated as
    its own test because the cancelled fix and this allowance pull in opposite
    directions, and only one of them is written in the condition's shape.
    """
    assert (
        ci_verdict.evaluate(
            chart_condition(), release_resolver("false", "true", "skipped")
        )
        is True
    )


def test_the_release_chart_gate_falls_closed_on_an_unexpected_detect_output():
    """`== 'false'` rather than `!= 'true'`, which reads as equivalent.

    `detect` writes the literal `false`. Fed anything else — an empty output
    from a job that did not complete, a typo, a third value — the gate must
    still demand a successful image. Under `!= 'true'` every row here publishes
    an unpinned chart instead.
    """
    for odd in ("", "False", "no", "0"):
        assert (
            ci_verdict.evaluate(
                chart_condition(), release_resolver(odd, "true", "cancelled")
            )
            is False
        ), odd


def test_the_release_chart_gate_pins_the_digest_whenever_it_has_one():
    """The job condition and the step condition are one decision in two places.

    The job may run with no digest only when there is no image at all, so the
    step's `success` test is what turns that allowance into "publish unpinned".
    If the job gate ever widens again without this step widening too, the pair
    goes back to publishing a floating tag in silence.
    """
    import yaml

    chart = yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]["chart"]
    pin = [s for s in chart["steps"] if "pin the image by digest" in s.get("name", "")]
    assert len(pin) == 1, "the digest-pin step was renamed or removed"
    assert pin[0]["if"] == "needs.image.result == 'success'"


def test_the_release_deployment_gate_is_the_reference_shape():
    """Unchanged by ledger 729, and the shape `chart` was converged onto.

    `deployment` writes a digest into `yadgarhq/argocd`, so it has never had a
    legitimate no-image case: a repository with no `chart/` is not deployed by
    the ApplicationSet at all.
    """
    import yaml

    jobs = yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]
    assert "needs.image.result == 'success'" in jobs["deployment"]["if"]


# ---------------------------------------------------------------------------
# Ledger 685: what a text-only edit's skips have to be worth
# ---------------------------------------------------------------------------
#
# THE BYPASS THIS BLOCK EXISTS FOR, in one sentence: a job fails, the author
# edits the description, the head sha does not move, seven jobs skip, and the
# `ci / passed` those skips produce SUPERSEDES the red one — GitHub gates on the
# latest check run for a context, the ruleset on `main` requires exactly that
# context with `required_approving_review_count: 0` and `bypass_actors: []`, so
# nobody is required to look. `yadgarhq/actions` already carries five
# `ci / passed` check runs on the single commit
# `7c3afdeaa439e1f19e53027593f5e6af0e8f1e73`, so the supersede is measured
# rather than argued.
#
# EVERY GREEN CASE BELOW IS PAIRED WITH A RED ONE ON THE SAME MATRIX, which is
# this file's discipline: "a text-only edit passes" also passes when the
# assertion is dead, so each such case is fed alongside the mutation that must
# redden it.

SHA = "7c3afdeaa439e1f19e53027593f5e6af0e8f1e73"

# THIS RUN. Every fixture's own check run carries it, because the whole subtlety
# is that the run asking the question is itself an answer to it.
OWN_RUN = "34009000000"


def check_run(run_id, conclusion="success", status="completed", at="2026-09-06T02:46:12Z"):
    """One entry shaped like the live endpoint's, fields this gate reads included.

    `details_url` is transcribed from a real response rather than invented —
    `https://github.com/yadgarhq/actions/actions/runs/34007636330/job/101417713159`
    — because the self-exclusion matches on the `/runs/<id>/` segment inside it
    and a made-up shape would pin nothing.
    """
    return {
        "id": int(run_id) % 1000000 + (0 if status == "completed" else 1),
        "name": "ci / passed",
        "status": status,
        "conclusion": conclusion,
        "completed_at": at if status == "completed" else None,
        "details_url": (
            f"https://github.com/yadgarhq/actions/actions/runs/{run_id}"
            f"/job/10141{run_id[-4:]}"
        ),
    }


def payload(*runs, total=None):
    return {"total_count": len(runs) if total is None else total, "check_runs": list(runs)}


def no_fetch(url):  # pragma: no cover - the point is that it is never called
    raise AssertionError(f"the gate read {url} on a run that is not a text-only edit")


# The jobs that skip on a text-only edit. Asserted against the workflow in
# `test_every_short_circuited_job_carries_the_conjunct` rather than trusted here.
SHORT_CIRCUITED = [
    "precommit",
    "workflows",
    "vulnerabilities",
    "test",
    "proto",
    "portability",
    "service_immutable",
]


def edited_needs(text_edit="true", **override):
    """The `toJSON(needs)` a text-only edit produces: seven skips and two runs.

    `detect` and `template` execute — `template` is the job that re-reads the
    edited body, which is the entire point of the short-circuit — and everything
    else reports `skipped`.
    """
    ctx = {}
    for job in GATED:
        default = "skipped" if job in SHORT_CIRCUITED else "success"
        ctx[job] = {"result": override.get(job, default), "outputs": {}}
    ctx["detect"]["outputs"] = {
        "rust": "false",
        "proto": "false",
        "text_edit": text_edit,
    }
    return ctx


def gate(ctx, response, run_id=OWN_RUN, repo="yadgarhq/actions", sha=SHA, token="t"):
    """The real workflow's real conditions, with the check-runs endpoint stubbed.

    IN-PROCESS RATHER THAN THROUGH THE SUBPROCESS, and deliberately: there is no
    way to hand a stubbed API to the gate from the environment, and there must
    not be, or the assertion would be a suggestion. What the subprocess covers
    is that no reference resolves without registration; what this covers is what
    the registration then asserts.
    """
    urls = []

    def fetch(url):
        urls.append(url)
        return response

    ci_verdict.register_deferred_reference(
        ci_verdict.TEXT_EDIT_REF,
        ci_verdict.make_text_edit_resolver(
            needs=ctx,
            repo=repo,
            sha=sha,
            run_id=run_id,
            check_name="ci / passed",
            token=token,
            fetch=fetch,
        ),
    )
    try:
        resolve = ci_verdict.make_resolver("pull_request", "User", repo, ctx)
        conditions = ci_verdict.load_conditions(CI_PR, "passed")
        ok, rows = ci_verdict.verdict(conditions, ctx, resolve)
        return ok, rows, urls
    finally:
        ci_verdict._DEFERRED.clear()


def test_every_short_circuited_job_carries_the_conjunct():
    """`SHORT_CIRCUITED` is read off the workflow, not kept in step with it by hand.

    The fixtures above would otherwise describe a run the workflow no longer
    produces the day a job is added to or removed from the short-circuit — which
    is the failure mode `test_the_gate_covers_every_job_it_needs` exists for one
    level up.
    """
    conditions = dict(ci_verdict.load_conditions(CI_PR, "passed"))
    carries = [
        job
        for job, cond in conditions.items()
        if cond is not None and "text_edit" in str(cond)
    ]
    assert sorted(carries) == sorted(SHORT_CIRCUITED)
    # THE TWO THAT MUST NOT CARRY IT. `template` re-reads the edited body, which
    # is the only work a text-only edit has; `detect` computes the predicate.
    assert "template" not in carries
    assert "detect" not in carries


def test_a_text_only_edit_carries_a_standing_green_verdict_forward():
    """THE LEGITIMATE EDIT, which the assertion must not break.

    A typo fixed in a description on a commit whose checks passed still
    short-circuits and still reports success. If this reddened, the whole
    short-circuit would be worthless — every consumer would re-run its full
    matrix for every body edit, which is the cost ledger 685 exists to avoid.
    """
    ctx = edited_needs()
    ok, rows, urls = gate(ctx, payload(check_run("34007298356", "success")))
    assert ok, [r for r in rows if not r[4]]
    skipped = {r[0] for r in rows if not r[1]}
    assert skipped == set(SHORT_CIRCUITED)
    assert len(urls) == 1, "the standing verdict is read once, not once per job"


def test_a_text_only_edit_cannot_turn_a_red_run_green():
    """THE BYPASS, CONSTRUCTED AND THEN REFUSED — the pair to the test above.

    Identical job matrix, identical event, identical commit. The only difference
    is the verdict standing on that commit, and that is the whole security
    property: a description edit may carry a green verdict forward and may not
    manufacture one.
    """
    ctx = edited_needs()
    with pytest.raises(ci_verdict.Refused) as exc:
        gate(ctx, payload(check_run("34007298356", "failure")))
    assert "text-only edit" in str(exc.value)
    assert "'failure'" in str(exc.value)
    assert "editing the description cannot make a red run green" in str(exc.value)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required", "neutral", "stale", "skipped"])
def test_only_success_carries_forward(conclusion):
    """Every conclusion that is not `success` refuses, rather than only `failure`.

    A predicate of the shape `!= 'failure'` is the defect this estate has already
    paid for once — `ci-release.yaml`'s `chart` job published from a CANCELLED
    image until ledger 729. Pinned as a matrix so the narrower form cannot be
    written here without a test going red.
    """
    ctx = edited_needs()
    with pytest.raises(ci_verdict.Refused):
        gate(ctx, payload(check_run("34007298356", conclusion)))


def test_absence_is_not_success():
    """No prior completed verdict at all — an edit that raced the first run.

    Refused rather than allowed, and the message says why in the words the
    specification used. An author who hits this pushes the branch again; a gate
    that guessed here would be green on a commit nothing had ever checked.
    """
    ctx = edited_needs()
    with pytest.raises(ci_verdict.Refused) as exc:
        gate(ctx, payload())
    assert "Absence is not success" in str(exc.value)


def test_the_most_recent_verdict_decides_rather_than_the_best_one():
    """An older green does not rescue a commit whose latest full run went red.

    This is TIGHTER than "a prior completed run concluded success", and the
    difference is reachable: `vulnerabilities` re-runs the same tree against a
    feed that moves, so a commit that passed on Monday can fail on Tuesday with
    no code change. Under the looser rule a body edit would then supersede
    Tuesday's red with Monday's green.
    """
    ctx = edited_needs()
    with pytest.raises(ci_verdict.Refused) as exc:
        gate(
            ctx,
            payload(
                check_run("34007298356", "success", at="2026-09-06T02:46:12Z"),
                check_run("34007360583", "failure", at="2026-09-06T02:47:18Z"),
            ),
        )
    assert "'failure'" in str(exc.value)


# ---------------------------------------------------------------------------
# The self-exclusion, and what it is load-bearing against
# ---------------------------------------------------------------------------


def naive_latest_not_red(response):
    """The assertion MINUS the self-exclusion, and nothing else — run, not described.

    Deliberately the closest possible neighbour to the real one: same endpoint,
    same `filter=all`, same "read the most recent" rule. The endpoint returns
    newest first, which is what the live probe showed, so this takes entry zero
    and accepts anything that did not conclude `failure`.

    The one line it is missing is the exclusion.
    """
    latest = response["check_runs"][0]
    return latest.get("conclusion") != "failure"


def test_without_the_self_exclusion_the_assertion_reads_its_own_answer():
    """THE SELF-EXCLUSION, DEMONSTRATED RATHER THAN ASSERTED.

    One response, two predicates. It holds this run's own `ci / passed` — created
    when this job started, same name, same commit, newest of the three — and it
    holds the red verdict that actually stands.

    The naive predicate passes, and it passes BECAUSE of the entry belonging to
    the run whose legitimacy is the question. That entry is the newest
    `ci / passed` on the commit — this job created it when it started — and a
    check run that is still running has a NULL conclusion, so "the most recent
    one did not fail" is satisfied by the fact that this run has not finished
    rather than by anything anybody checked. The assertion reads its own answer,
    and the bypass survives the fix that was meant to close it.

    `standing_verdict` refuses the same response, because it drops that entry
    twice over — by state (`status != 'completed'`) and by identity
    (`/runs/<this run id>/` inside `details_url`) — and then reads the newest of
    what is left, which is the red one.
    """
    response = payload(
        check_run(OWN_RUN, None, status="in_progress"),
        check_run("34007298356", "failure", at="2026-09-06T02:46:12Z"),
    )

    assert naive_latest_not_red(response) is True, (
        "the naive predicate should pass here — that is the bypass surviving"
    )

    latest = ci_verdict.standing_verdict(response, OWN_RUN, "ci / passed")
    assert latest["conclusion"] == "failure"
    assert OWN_RUN not in latest["details_url"]


def test_this_runs_own_check_run_is_not_a_standing_verdict():
    """The response holds nothing BUT this run's own entry, which is the live shape.

    `filter=all` is what makes the prior entries visible at all; with the
    endpoint's DEFAULT — `filter=latest`, one check run per name — the response
    on a live run holds exactly this: the current run's own. Measured on
    `yadgarhq/actions`, where the default returned `total_count: 1` for a commit
    that carries five.

    So the gate must refuse here rather than resolve, and it does: nothing
    completed, therefore no standing verdict, therefore no skip is earned.
    """
    response = payload(check_run(OWN_RUN, None, status="in_progress"))
    with pytest.raises(ci_verdict.Refused) as exc:
        ci_verdict.standing_verdict(response, OWN_RUN, "ci / passed")
    assert "Absence is not success" in str(exc.value)


def test_a_completed_check_run_of_this_same_run_is_still_excluded():
    """The identity test, exercised where the state test cannot help.

    A re-run of THIS run reuses the run id and completes its earlier attempt, so
    an entry can be both `completed` and this run's own. Excluded by
    `details_url` rather than by status, which is why both tests are there.
    """
    response = payload(check_run(OWN_RUN, "success", status="completed"))
    with pytest.raises(ci_verdict.Refused):
        ci_verdict.standing_verdict(response, OWN_RUN, "ci / passed")


def test_filter_all_is_in_the_query():
    """`filter=all`, pinned. The endpoint's default returns the latest only."""
    url = ci_verdict.check_runs_url("yadgarhq/actions", SHA, "ci / passed")
    assert "filter=all" in url
    assert f"/commits/{SHA}/check-runs" in url
    assert "check_name=ci%20%2F%20passed" in url


def test_a_truncated_response_is_refused():
    """More check runs exist than were returned, so the newest may not be here."""
    with pytest.raises(ci_verdict.Refused) as exc:
        ci_verdict.standing_verdict(
            payload(check_run("34007298356", "success"), total=101),
            OWN_RUN,
            "ci / passed",
        )
    assert "may not be among them" in str(exc.value)


def test_an_ordinary_pull_request_reads_no_check_runs():
    """`text_edit` false: the API is not touched, so the permission is not exercised.

    An outage, a rate limit or a missing `checks: read` cannot redden a run that
    is checking everything anyway — only one that is checking almost nothing.
    """
    ctx = edited_needs(text_edit="false")
    for job in SHORT_CIRCUITED:
        ctx[job]["result"] = "success"
    ctx["test"]["result"] = "skipped"
    ctx["proto"]["result"] = "skipped"
    ok, rows, urls = gate(ctx, payload(), token="")
    assert ok, [r for r in rows if not r[4]]
    assert urls == []


def test_a_missing_permission_reddens_the_gate(monkeypatch):
    """HTTP 403 — a caller that never granted `checks: read` — refuses, loudly.

    THE FAILURE MOST LIKELY TO HAPPEN IN PRACTICE, because a called workflow's
    token can only be narrowed by its caller: declaring `checks: read` in
    `ci-pr.yaml` does nothing for a consumer whose `uses:` line grants only
    `contents: read`. That consumer must not merge on a run it could not verify.
    """

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(ci_verdict.urllib.request, "urlopen", boom)
    with pytest.raises(ci_verdict.Refused) as exc:
        ci_verdict.fetch_check_runs("https://api.github.com/x", "token")
    assert "HTTP 403" in str(exc.value)
    assert "checks: read" in str(exc.value)


def test_no_token_refuses_before_it_asks():
    with pytest.raises(ci_verdict.Refused) as exc:
        ci_verdict.fetch_check_runs("https://api.github.com/x", "")
    assert "Absence is not success" in str(exc.value)
