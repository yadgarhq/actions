"""LEDGER 1756 S2a (ADR-0981, ADR-0984). Every second checkout of
`yadgarhq/actions` inside a shared workflow checks out the commit of the
workflow file that is running, never `main`.

WHY. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`. GitHub
resolves `@main` once, when the run starts, and runs the workflow file at that
commit. A second checkout at `ref: main` resolves `main` again, later, so a push
to `main` during a run pairs the workflow with scripts it was never tested
against. ADR-0981 rules that the second checkout follows the running workflow's
own commit, records it in the run summary, and has no fallback to `main`.

HOW THE COMMIT IS READ (ADR-0984). `job.workflow_sha` holds the callee commit
(measured from a consumer in yadgarhq/docs runs 39580752734 and 39581019673).
actionlint 1.7.12 types the `job` context strictly and rejects
`job.workflow_sha`, so the workflows read it as
`fromJSON(toJSON(job)).workflow_sha`, through a step `env:`, and a check refuses
any value that is not 40 lowercase hex characters. The check cannot be a script
in this repository: it runs before this repository is on the runner. So this
suite takes the check's real `run:` block out of a workflow and runs that.

THE WIRING THIS SUITE HOLDS, per job that checks out `yadgarhq/actions`:

1. an earlier step `id: own_sha` whose keys are exactly `name`, `id`, `env`
   and `run` (no `if:`, no `continue-on-error:`), and whose `env` and `run`
   are exactly `OWN_SHA_ENV` and `OWN_SHA_RUN` below;
2. the checkout's `ref:` is exactly `${{ steps.own_sha.outputs.sha }}`, so the
   checkout can only consume a value the check accepted, and its `if:` names
   none of `always()`, `failure()`, `!cancelled()`, which would run it after
   the check refused, with an empty ref that `actions/checkout` reads as the
   default branch.

And per workflow that has such a checkout: exactly one step named
`RECORD_NAME`, which writes that commit to `$GITHUB_STEP_SUMMARY`.

EVERY CHECK IS PAIRED WITH A CONSTRUCTED RED CASE, for the reason
`test_no_test_skips.py` gives: a gate that only sees conforming input certifies
the fixture, not the gate.

Run: python3 -m pytest scripts/tests/ -q
"""

import os
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

ACTIONS_REPO = "yadgarhq/actions"
OWN_SHA_ID = "own_sha"
OWN_SHA_NAME = "the actions commit this job runs (ADR-0981)"
OWN_SHA_REF = "${{ steps.own_sha.outputs.sha }}"
OWN_SHA_ENV = {"OWN_SHA": "${{ fromJSON(toJSON(job)).workflow_sha }}"}
OWN_SHA_RUN = """\
set -euo pipefail
if [[ ! "$OWN_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::job.workflow_sha is not a 40-hex commit SHA: '${OWN_SHA}'. There is no fallback to main (ADR-0981)."
  exit 1
fi
echo "sha=${OWN_SHA}" >> "$GITHUB_OUTPUT"
"""
RECORD_NAME = "record the yadgarhq/actions commit this run executes"
# The check's only keys. Any other key can weaken it: `if:` skips it,
# `continue-on-error:` lets the job go on with an empty output.
OWN_SHA_KEYS = {"name", "id", "env", "run"}
# Status functions that let a step run after an earlier step failed.
AFTER_FAILURE = ("always()", "failure()", "!cancelled()")

# The workflows known to take a second checkout of this repository. The scan
# below finds every checkout on its own; this list exists so that a workflow
# whose checkouts silently vanish from the scan (a parse change, a rename)
# turns this suite red instead of passing over zero sites.
EXPECTED_SITES = {
    "ci-pr.yaml": 13,
    "ci-release.yaml": 4,
    "repin.yaml": 1,
    "seed-declaration.yaml": 1,
}


# ---------------------------------------------------------------------------
# A line-aware reading. `yaml.safe_load` drops line numbers, so the scan walks
# the composed node graph and reports each finding as `file:line`.
# ---------------------------------------------------------------------------


def _get(node, key):
    if not isinstance(node, yaml.MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.value == key:
            return v
    return None


def _scalar(node):
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _plain(node):
    """The node as plain Python, for whole-value comparisons."""
    return yaml.safe_load(yaml.serialize(node)) if node is not None else None


def _line(node):
    return node.start_mark.line + 1


def is_actions_checkout(step):
    uses = _scalar(_get(step, "uses")) or ""
    if not uses.startswith("actions/checkout@"):
        return False
    return _scalar(_get(_get(step, "with"), "repository")) == ACTIONS_REPO


def is_own_sha_step(step):
    return _scalar(_get(step, "id")) == OWN_SHA_ID


def scan(text, name):
    """Return (violations, sites, records) for one workflow's text.

    `violations` are `name:line: message` strings. `sites` counts the second
    checkouts of `yadgarhq/actions`. `records` counts summary steps named
    `RECORD_NAME`."""
    root = yaml.compose(text)
    violations = []
    sites = 0
    records = 0
    jobs = _get(root, "jobs")
    if jobs is None:
        return violations, sites, records
    for _job_key, job in jobs.value:
        steps = _get(job, "steps")
        if not isinstance(steps, yaml.SequenceNode):
            continue
        checked = None  # the line of a conforming own_sha step seen so far
        for step in steps.value:
            if _scalar(_get(step, "name")) == RECORD_NAME:
                records += 1
            if is_own_sha_step(step):
                problems = []
                keys = {k.value for k, _v in step.value}
                if keys != OWN_SHA_KEYS:
                    problems.append(
                        f"keys are not exactly {sorted(OWN_SHA_KEYS)} (got {sorted(keys)}); "
                        "an `if:` skips the check and `continue-on-error` fails it open"
                    )
                if _plain(_get(step, "env")) != OWN_SHA_ENV:
                    problems.append(f"env is not exactly {OWN_SHA_ENV}")
                if _scalar(_get(step, "run")) != OWN_SHA_RUN:
                    problems.append("run block differs from the canonical 40-hex check")
                for p in problems:
                    violations.append(f"{name}:{_line(step)}: the `own_sha` step {p}")
                if not problems:
                    checked = _line(step)
                continue
            if not is_actions_checkout(step):
                continue
            sites += 1
            cond = _scalar(_get(step, "if")) or ""
            for fn in AFTER_FAILURE:
                if fn in cond.replace(" ", ""):
                    violations.append(
                        f"{name}:{_line(step)}: a checkout of {ACTIONS_REPO} runs after a failed "
                        f"check (`{fn}` in its `if:`), where the own SHA is empty and checkout "
                        "falls back to the default branch"
                    )
            ref = _get(_get(step, "with"), "ref")
            if ref is None:
                violations.append(
                    f"{name}:{_line(step)}: a checkout of {ACTIONS_REPO} has no `ref:` "
                    f"(the default is the caller's ref); it must be `{OWN_SHA_REF}`"
                )
            elif _scalar(ref) != OWN_SHA_REF:
                violations.append(
                    f"{name}:{_line(ref)}: a checkout of {ACTIONS_REPO} uses "
                    f"`ref: {_scalar(ref)}`; it must be `{OWN_SHA_REF}` (ADR-0981)"
                )
            if checked is None:
                violations.append(
                    f"{name}:{_line(step)}: a checkout of {ACTIONS_REPO} has no conforming "
                    f"`id: {OWN_SHA_ID}` 40-hex check before it in the same job (ADR-0984)"
                )
    return violations, sites, records


def workflow_texts():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yaml"))}


# ---------------------------------------------------------------------------
# The real workflows
# ---------------------------------------------------------------------------


def test_every_second_checkout_follows_the_own_sha():
    found = []
    for name, text in workflow_texts().items():
        violations, _sites, _records = scan(text, name)
        found.extend(violations)
    assert not found, "\n".join(found)


def test_the_scan_still_sees_every_known_site():
    seen = {}
    for name, text in workflow_texts().items():
        _v, sites, _r = scan(text, name)
        if sites:
            seen[name] = sites
    assert seen == EXPECTED_SITES


def test_each_workflow_records_the_commit_once():
    wrong = {}
    for name, text in workflow_texts().items():
        _v, sites, records = scan(text, name)
        if sites and records != 1:
            wrong[name] = records
    assert not wrong, f"workflows without exactly one `{RECORD_NAME}` step: {wrong}"


def test_the_record_step_reads_the_checked_value():
    for name, text in workflow_texts().items():
        for job in (yaml.safe_load(text).get("jobs") or {}).values():
            for step in job.get("steps", []):
                if step.get("name") != RECORD_NAME:
                    continue
                assert "if" not in step, f"{name}: the record step must always run"
                assert step.get("env") == {"OWN_SHA": OWN_SHA_REF}, name
                assert "$GITHUB_STEP_SUMMARY" in step.get("run", ""), name


# ---------------------------------------------------------------------------
# Constructed red cases: the scan itself goes red on each defect
# ---------------------------------------------------------------------------

CONFORMING = f"""\
on: workflow_call
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - name: {OWN_SHA_NAME}
        id: own_sha
        env:
          OWN_SHA: ${{{{ fromJSON(toJSON(job)).workflow_sha }}}}
        run: |
{''.join('          ' + line + chr(10) for line in OWN_SHA_RUN.splitlines())}\
      - name: {RECORD_NAME}
        env:
          OWN_SHA: ${{{{ steps.own_sha.outputs.sha }}}}
        run: echo "$OWN_SHA" >> "$GITHUB_STEP_SUMMARY"
      - if: github.repository != 'yadgarhq/actions'
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          repository: yadgarhq/actions
          ref: ${{{{ steps.own_sha.outputs.sha }}}}
          path: .ci-actions
"""


def test_fixture_conforms():
    violations, sites, records = scan(CONFORMING, "fixture.yaml")
    assert violations == []
    assert (sites, records) == (1, 1)


def test_red_ref_main_names_file_and_line():
    text = CONFORMING.replace("ref: ${{ steps.own_sha.outputs.sha }}", "ref: main")
    violations, _s, _r = scan(text, "fixture.yaml")
    line = text.splitlines().index("          ref: main") + 1
    assert violations == [
        f"fixture.yaml:{line}: a checkout of yadgarhq/actions uses `ref: main`; "
        f"it must be `{OWN_SHA_REF}` (ADR-0981)"
    ]


def test_red_no_ref():
    text = CONFORMING.replace("          ref: ${{ steps.own_sha.outputs.sha }}\n", "")
    violations, _s, _r = scan(text, "fixture.yaml")
    assert len(violations) == 1 and "has no `ref:`" in violations[0]
    assert violations[0].startswith("fixture.yaml:")


def test_red_raw_expression_bypasses_the_check():
    text = CONFORMING.replace(
        "ref: ${{ steps.own_sha.outputs.sha }}\n          path",
        "ref: ${{ fromJSON(toJSON(job)).workflow_sha }}\n          path",
    )
    violations, _s, _r = scan(text, "fixture.yaml")
    assert len(violations) == 1 and "must be" in violations[0]


def test_red_missing_check():
    lines = CONFORMING.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if f"name: {OWN_SHA_NAME}" in l)
    end = next(i for i, l in enumerate(lines) if f"name: {RECORD_NAME}" in l)
    text = "".join(lines[:start] + lines[end:])
    violations, _s, _r = scan(text, "fixture.yaml")
    assert len(violations) == 1 and "no conforming `id: own_sha`" in violations[0]


def test_red_check_after_the_checkout():
    doc = yaml.safe_load(CONFORMING)
    steps = doc["jobs"]["gate"]["steps"]
    doc["jobs"]["gate"]["steps"] = steps[1:] + steps[:1]
    violations, _s, _r = scan(yaml.safe_dump(doc, sort_keys=False), "fixture.yaml")
    assert any("no conforming `id: own_sha`" in v for v in violations)


def test_red_weakened_check():
    text = CONFORMING.replace("{40}$ ]]", "{7,40}$ ]]")
    violations, _s, _r = scan(text, "fixture.yaml")
    assert any("run block differs" in v for v in violations)
    assert any("no conforming" in v for v in violations)


def test_red_conditional_check():
    text = CONFORMING.replace(
        "        id: own_sha\n", "        id: own_sha\n        if: github.repository != 'yadgarhq/actions'\n"
    )
    violations, _s, _r = scan(text, "fixture.yaml")
    assert any("keys are not exactly" in v for v in violations)


def test_red_wrong_env_source():
    text = CONFORMING.replace(
        "OWN_SHA: ${{ fromJSON(toJSON(job)).workflow_sha }}", "OWN_SHA: ${{ github.sha }}"
    )
    violations, _s, _r = scan(text, "fixture.yaml")
    assert any("env is not exactly" in v for v in violations)


def test_red_second_job_without_its_own_check():
    second = CONFORMING.split("    steps:\n", 1)[1]
    checkout = second[second.index("      - if: github.repository"):]
    text = CONFORMING + "  other:\n    runs-on: ubuntu-latest\n    steps:\n" + checkout
    violations, sites, _r = scan(text, "fixture.yaml")
    assert sites == 2
    assert len(violations) == 1 and "no conforming `id: own_sha`" in violations[0]


def test_the_own_repo_checkout_is_not_a_second_checkout():
    text = CONFORMING + (
        "  self:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - if: github.repository == 'yadgarhq/actions'\n"
        "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
        "        with:\n          persist-credentials: false\n"
        "          ref: ${{ inputs.ref }}\n"
    )
    violations, sites, _r = scan(text, "fixture.yaml")
    assert violations == [] and sites == 1


# ---------------------------------------------------------------------------
# The check's real run block refuses every value that is not a commit SHA
# ---------------------------------------------------------------------------


def shipped_run_block():
    """The `own_sha` run block exactly as `ci-pr.yaml` ships it."""
    jobs = yaml.safe_load((WORKFLOWS / "ci-pr.yaml").read_text(encoding="utf-8"))["jobs"]
    for job in jobs.values():
        for step in job.get("steps", []):
            if step.get("id") == OWN_SHA_ID:
                return step["run"]
    raise AssertionError("ci-pr.yaml carries no `own_sha` step")


def run_check(value, tmp_path):
    out = tmp_path / "github_output"
    out.write_text("")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OWN_SHA": value, "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run(
        ["bash", "-e", "-c", shipped_run_block()], env=env, capture_output=True, text=True, check=False
    )
    return proc, out.read_text()


VALID = "9b70f732606fe5fb23a7c493c5097dbf01bf920a"


def test_check_accepts_a_commit_sha(tmp_path):
    proc, output = run_check(VALID, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert output == f"sha={VALID}\n"


def test_check_refuses_bad_values(tmp_path):
    bad = {
        "empty": "",
        "short": VALID[:39],
        "abbreviated": VALID[:7],
        "long": VALID + "0",
        "uppercase": VALID.upper(),
        "non-hex": "g" + VALID[1:],
        "a branch name": "main",
        "trailing newline and payload": VALID + "\nsha=deadbeef",
        "leading space": " " + VALID,
    }
    accepted = {}
    for label, value in bad.items():
        proc, output = run_check(value, tmp_path)
        if proc.returncode == 0 or output:
            accepted[label] = (proc.returncode, output)
        else:
            assert "There is no fallback to main" in proc.stdout, label
    assert not accepted, f"the check accepted: {accepted}"


# ---------------------------------------------------------------------------
# Fail-open shapes (review of actions#121). A `continue-on-error: true` on the
# check leaves `steps.own_sha.outputs.sha` empty, and `actions/checkout` with
# `ref: ''` fetches the default branch: `main`, silently. A checkout whose
# `if:` runs after a failure (`always()`, `failure()`, `!cancelled()`) reaches
# the same empty ref when the check refused.
# ---------------------------------------------------------------------------


def test_red_check_continues_on_error():
    text = CONFORMING.replace(
        "        id: own_sha\n", "        id: own_sha\n        continue-on-error: true\n"
    )
    violations, _s, _r = scan(text, "fixture.yaml")
    assert any("keys are not exactly" in v for v in violations), violations
    assert any("no conforming" in v for v in violations), violations


def test_red_check_with_an_extra_key():
    text = CONFORMING.replace(
        "        id: own_sha\n", "        id: own_sha\n        shell: bash\n"
    )
    violations, _s, _r = scan(text, "fixture.yaml")
    assert any("keys are not exactly" in v for v in violations), violations


def test_red_checkout_runs_after_a_failure():
    for cond in ("always()", "failure()", "!cancelled()"):
        text = CONFORMING.replace(
            "      - if: github.repository != 'yadgarhq/actions'\n",
            # Inside `${{ }}`: a bare leading `!` is a YAML tag.
            f"      - if: ${{{{ {cond} && github.repository != 'yadgarhq/actions' }}}}\n",
        )
        violations, _s, _r = scan(text, "fixture.yaml")
        assert len(violations) == 1 and cond in violations[0], (cond, violations)
        assert "runs after a failed check" in violations[0], violations
