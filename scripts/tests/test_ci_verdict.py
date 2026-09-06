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


def test_ledger_685s_short_circuit_is_refused_until_it_is_registered(tmp_path):
    """THE INTERLOCK. Pull request 52's condition, evaluated by this gate.

    52 gives six jobs a `needs.detect.outputs.text_edit != 'true'` conjunct so a
    text-only pull request edit re-reads the body and nothing else. Against the
    predicate this replaced, the six resulting `skipped` results read as success
    and a red `ci / passed` is superseded by a green one on the same head sha.

    Against this gate the run REFUSES, because `text_edit` is not a registered
    reference. That is what makes 685 buildable rather than blocked: the work it
    has left is to register the reference together with the assertion that earns
    it — a prior completed `ci / passed` on this sha concluded success — rather
    than to discover afterwards that the skip proved nothing.
    """
    wf = synthetic(
        tmp_path,
        "github.event_name != 'push' && needs.detect.outputs.text_edit != 'true'",
    )
    proc = run({"probe": {"result": "skipped", "outputs": {}}}, workflow=wf)
    assert proc.returncode == 1
    assert "refused to report a verdict" in proc.stderr
    assert "text_edit" in proc.stderr
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
    """`ci-release.yaml`'s live defect, refused rather than reproduced.

    `chart` runs on `needs.image.result != 'failure'`, so a CANCELLED image job
    publishes a chart. If that shape ever appears under `ci / passed`, the gate
    refuses instead of resolving it — a result is a consequence, and a skip caused
    by an upstream failure would read as a job that had nothing to do.
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
    resolve = ci_verdict.make_resolver(
        "pull_request", "User", "yadgarhq/actions",
        {"detect": {"result": "success", "outputs": {"rust": "true", "proto": "true"}}},
    )
    for job, condition in ci_verdict.load_conditions(CI_PR, "passed"):
        assert isinstance(ci_verdict.evaluate(condition, resolve), bool), job


def test_the_release_workflows_chart_gate_is_the_unsafe_shape():
    """Pinned where it is, so the finding does not rot into a comment nobody reads.

    `ci-release.yaml` is not gated by this script — it is tag-triggered and
    publishes rather than merges. Its `chart` job carries the shape this whole
    file exists to refuse, and its `deployment` job carries the safe one. This
    test fails when either changes, which is when somebody should reread both.
    """
    import yaml

    jobs = yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"]
    assert "needs.image.result != 'failure'" in jobs["chart"]["if"]
    assert "needs.image.result == 'success'" in jobs["deployment"]["if"]
