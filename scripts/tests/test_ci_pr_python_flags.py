"""Yaadgaar 653, 655 and 656. Every Python call that `ci-pr.yaml`,
`ci-release.yaml`, `seed-declaration.yaml` and `sweep.yaml` run is spelled
`python3 -E -s` (a script) or `python3 -I -m` (a module). No `shell:` in them
names Python, and nothing in them sets a
`PYTHON*` variable. NOT_YET names the workflows not scanned yet.

The `ci-pr.yaml` jobs run a gate with the cwd inside the pull request's
checkout, and the `ci-release.yaml` jobs run one inside the consumer's tagged
checkout. `-E` makes the interpreter ignore every `PYTHON*` variable
(`PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME`, ...), and `-s` leaves the user
site-packages off `sys.path`. Neither the step env nor the runner's home can
then put code in front of the gate's own imports. `sweep.yaml` runs only this
repository's own checkout; it carries the flags too, so one rule holds for every
file this test reads (yaadgaar 655, fail-closed).

ONE SPELLING PER KIND OF RUN. The scan matches an interpreter by name, by path
or through a variable (see PYTHON below). It accepts `python3 -E -s <script>`,
and `python3 -I -m <module>` for a module run (ADR-1164): with `-m` the cwd is
first on `sys.path`, which `-E -s` leaves open and `-I` closes.
`python3 -I -c` is as safe as `-I -m`, so refusing it is a choice, not a safety
need: no scanned file has a `-c` site, and yaadgaar 663 decides between `jq`
for those sites and widening FLAGS. The scan reads one line at a time, so a
`python3` line continued by a trailing backslash with the flags on the next
line, and extra flags after `-E -s` (`-X`, `-u`), are refused too: that fails
closed. A call rewritten as `python` or
`python3.14` is refused even when it carries the flags: the bare name can be a
different interpreter, and a second spelling is a second place to forget them.

NO `PYTHON*` VARIABLE SET ANYWHERE: not in an `env:`, a `container:` or a
service, and not from a `run:` (`export`, an inline prefix, `$GITHUB_ENV`).
Under `-E` such a variable is dead config for the gate, and it still reaches the
children that run without `-E`: pip, and the pytest child of `pre_pin_parent`.

WHY NOT `-I`. On Python 3.11 and later `-I` also implies `-P`, and `-P` drops
the script's OWN directory from `sys.path`. Six gates import a sibling that
the job stages next to them (`next_version` -> `pr_body`, `d80_portability`,
`service_immutable` and `pre_pin_parent` -> `api_versions`, `example_pins` ->
`parent_pin`, `ci_verdict` -> `config_only`), and so do `parent_bump`,
`example_published` and `sweep`. Each one dies with `ModuleNotFoundError`
under `-I`. The script's own directory is where the script itself came from,
so `-P` closes no gap that `-E -s` leaves open.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# A Python interpreter. Yaadgaar 656 widened the scan past the bare command word,
# because a call spelled any other way slipped by it:
#   - by path: `/usr/bin/python3`, `.venv/bin/python`, `$HOME/.venv/bin/python3`.
#     The match starts at the first character of the path, so the line is
#     refused even when it carries `-E -s`: only `python3 -E -s` or `-I -m`;
#   - a free-threaded or alternative build: `python3.14t`, `pypy3`;
#   - through a variable: `$PY`, `"$PYTHON"`, `${PYTHON:-python3}`. A variable
#     whose name holds `python`, or is `py`, `py3` or `pypy*` in any case,
#     counts as an interpreter wherever it stands, fail-closed: the scan cannot
#     tell a command position from an argument;
# but not a longer name (`python3-yaml`, `setup-python`, `python_flags.py`), a
# directory (`/usr/lib/python3/`), or a variable such as `$PYYAML_VERSION`.
INTERPRETER = r"(?<![\w./-])(?:[\w.~${}-]*/)*(?:python|pypy)(?:\d+(?:\.\d+)*)?t?(?![\w./-])"
VARIABLE = r"(?i:\$\{?(?:\w*python\w*|py\d*t?|pypy\w*)(?!\w))"
PYTHON = re.compile(f"{INTERPRETER}|{VARIABLE}")
# Two spellings, one per kind of run (ADR-1164): a script is `python3 -E -s
# <path>`, a module is `python3 -I -m <module>`. `-E -s -m` is refused: with
# `-m` the cwd is first on `sys.path`, so a PR-added `pytest.py` is imported.
FLAGS = re.compile(r"python3 (?:-E -s (?!-)|-I -m )")
# A `shell:` that names Python runs the whole `run:` as a Python script, with no
# flags to check, at step, job `defaults.run` or workflow `defaults.run` level.
PYTHON_SHELL = re.compile(r"python|pypy", re.I)
# A `PYTHON*` variable set from inside a `run:`: an assignment, an inline
# `PYTHONPATH=x cmd` prefix, a `$GITHUB_ENV` line (`NAME=value` or the
# `NAME<<EOF` form), or an `export`/`declare` of the name. Not a longer name
# such as `PRE_PIN_PARENT_PYTHON`, and not a read of `$PYTHON...`.
PYTHON_NAME = r"(?<![\w$])PYTHON\w*"
SETS_PYTHON = re.compile(
    rf"{PYTHON_NAME}(?:\+?=|<<)|\b(?:export|declare|typeset|readonly)\b[^;&|]*?{PYTHON_NAME}"
)
# The sites measured on 2026-11-05, per file: a regex that matched nothing in
# one file must not hide behind the count of another. `seed-declaration.yaml`
# joined in yaadgaar 656: it is a `workflow_call`, so it runs in consumers.
MIN_SITES = {"ci-pr.yaml": 20, "ci-release.yaml": 4, "seed-declaration.yaml": 3, "sweep.yaml": 10}
# The workflows not scanned yet, each with the yaadgaar task that adds it to
# MIN_SITES. Move a file across one at a time; never add one here to make a
# red go away.
NOT_YET = {
    "repin.yaml": "yaadgaar 662, a workflow_call: six calls without -E -s",
    "base-images.yaml": "yaadgaar 663",
    "ci-self.yaml": "yaadgaar 663",
    "estate-runner-image.yaml": "yaadgaar 663",
    "fan-out.yaml": "yaadgaar 663",
    "rust-bump.yaml": "yaadgaar 663",
}


def code_lines(run):
    """The `run:` text without whole-line shell comments, which mention
    `python3` in prose."""
    return [line for line in run.splitlines() if not line.lstrip().startswith("#")]


def strings(value):
    """Every string inside a `with:` value, however deeply nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def step_texts(step):
    """The step's `run:`, and every string in the `with:` of a `uses:` step,
    which an action such as a script runner can execute."""
    yield step.get("run") or ""
    yield from strings(step.get("with") or {})


def python_calls(workflow):
    calls = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for text in step_texts(step):
                for line in code_lines(text):
                    for match in PYTHON.finditer(line):
                        calls.append((name, step.get("name", ""), line[match.start():].strip()))
    return calls


def unflagged(workflow):
    return [c for c in python_calls(workflow) if not FLAGS.match(c[2])]


def python_shells(workflow):
    """Every `shell:` that names Python: a step's, or a `defaults.run.shell`
    of a job or of the workflow."""
    def default(holder):
        return (((holder.get("defaults") or {}).get("run")) or {}).get("shell") or ""

    shells = [("(workflow)", "defaults", default(workflow))]
    for name, job in workflow["jobs"].items():
        shells.append((name, "defaults", default(job)))
        shells += [(name, s.get("name", ""), s.get("shell") or "") for s in job.get("steps", [])]
    return [(job, step, shell) for job, step, shell in shells if PYTHON_SHELL.search(str(shell))]


def containers(name, job):
    """The job's `container:` and each of its `services:`, as (label, spec).
    A `container:` given as a bare image string has no env."""
    found = [("container", job.get("container"))]
    found += [(f"services.{svc}", spec) for svc, spec in (job.get("services") or {}).items()]
    return [(label, spec) for label, spec in found if isinstance(spec, dict)]


def python_env(workflow):
    """Every route that sets a `PYTHON*` variable: a key in the workflow's, a
    job's or a step's `env:`, in a job's `container:` or `services:` (`env:` or
    `-e`/`--env` in `options:`), and an assignment, `export` or `$GITHUB_ENV`
    line inside a `run:`."""
    scopes = [("(workflow)", "", workflow.get("env") or {})]
    found = []
    for name, job in workflow["jobs"].items():
        scopes.append((name, "", job.get("env") or {}))
        scopes += [(name, s.get("name", ""), s.get("env") or {}) for s in job.get("steps", [])]
        for label, spec in containers(name, job):
            scopes.append((name, label, spec.get("env") or {}))
            if re.search(PYTHON_NAME, str(spec.get("options") or "")):
                found.append((name, label, "options"))
        for step in job.get("steps", []):
            found += [(name, step.get("name", ""), line.strip())
                      for line in code_lines(step.get("run") or "") if SETS_PYTHON.search(line)]
    return [(job, step, key) for job, step, env in scopes for key in env if key.startswith("PYTHON")] + found


def load(name="ci-pr.yaml"):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_every_python_call_ignores_the_environment_and_the_user_site(name):
    offenders = unflagged(load(name))
    assert not offenders, f"a Python call not spelled `python3 -E -s` or `python3 -I -m` in {name}:\n" + "\n".join(
        f"  {job} / {step}: {line}" for job, step, line in offenders
    )


def test_every_workflow_is_scanned_or_named_as_not_yet():
    """A workflow left out of MIN_SITES escapes every check above, and no mutant
    of a file nobody scans can go red. So each file under `.github/workflows/`
    is scanned or is named in NOT_YET with the task that will scan it."""
    files = {p.name for p in WORKFLOWS.glob("*.y*ml")}
    assert not set(MIN_SITES) & set(NOT_YET)
    assert files == set(MIN_SITES) | set(NOT_YET), sorted(files ^ (set(MIN_SITES) | set(NOT_YET)))


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_the_scan_still_sees_every_site(name):
    """A regex that matched nothing would pass the test above on any workflow."""
    assert len(python_calls(load(name))) >= MIN_SITES[name]


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_no_shell_runs_python(name):
    offenders = python_shells(load(name))
    assert not offenders, f"a `shell:` that names Python in {name}:\n" + "\n".join(
        f"  {job} / {step}: {shell}" for job, step, shell in offenders
    )


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_no_env_sets_a_python_variable(name):
    offenders = python_env(load(name))
    assert not offenders, f"a `PYTHON*` variable in an `env:` of {name}, dead under -E:\n" + "\n".join(
        f"  {job} / {step}: {key}" for job, step, key in offenders
    )


def mutate(workflow, job, needle, old, new):
    for step in workflow["jobs"][job]["steps"]:
        if needle in (step.get("run") or ""):
            step["run"] = step["run"].replace(old, new, 1)
            return workflow
    raise AssertionError(f"no step in {job} runs {needle}")


def test_dropping_the_flags_at_one_site_is_caught():
    w = mutate(load(), "passed", "ci_verdict.py", "python3 -E -s ", "python3 ")
    assert [c[0] for c in unflagged(w)] == ["passed"]


def test_keeping_only_one_flag_is_caught():
    w = mutate(load(), "precommit", "secret_scan.py", "python3 -E -s ", "python3 -E ")
    assert [c[0] for c in unflagged(w)] == ["precommit"]


def test_a_python3_after_a_pipe_or_subshell_is_still_checked():
    w = mutate(load(), "sizes", "complexity.py", "python3 -E -s ", "python3 ")
    assert [c[0] for c in unflagged(w)] == ["sizes"]


@pytest.mark.parametrize("spelling", ["python ", "python3.14 ", "python3.14 -E -s ", "python -E -s "])
def test_another_interpreter_spelling_is_caught(spelling):
    w = mutate(load(), "passed", "ci_verdict.py", "python3 -E -s ", spelling)
    assert [c[0] for c in unflagged(w)] == ["passed"]


def test_a_pythonpath_back_on_the_pre_pin_parent_gate_is_caught():
    w = load()
    step = next(s for s in w["jobs"]["pre_pin_parent"]["steps"] if 'python3 -E -s "$RUNNER_TEMP/pre_pin_parent.py"' in (s.get("run") or ""))
    step.setdefault("env", {})["PYTHONPATH"] = "${{ runner.temp }}"
    assert [c[0] for c in python_env(w)] == ["pre_pin_parent"]


def test_a_variable_merely_named_after_python_is_not_caught():
    w = load()
    w["jobs"]["passed"].setdefault("env", {})["PRE_PIN_PARENT_PYTHON"] = "python3"
    assert python_env(w) == []


@pytest.mark.parametrize(("name", "job", "needle"), [
    ("ci-release.yaml", "detect", "/scripts/tool_versions.py\" \"$dir/versions.yaml\" >"),
    ("ci-release.yaml", "chart", 'python3 -E -s "$RUNNER_TEMP/chart_publicly_pullable.py"'),
    ("ci-release.yaml", "parent", "python3 -E -s scripts/parent_bump.py"),
    ("ci-release.yaml", "examples", 'python3 -E -s "$dir/example_published.py"'),
    ("seed-declaration.yaml", "declaration", '/scripts/tool_versions.py" "$dir/versions.yaml" >>'),
    ("seed-declaration.yaml", "declaration", "--part seed-no-ask-prompt"),
    ("seed-declaration.yaml", "declaration", "--part seed-front-matter"),
    ("sweep.yaml", "wait", "python3 -E -s scripts/sweep.py wait"),
    ("sweep.yaml", "act", "python3 -E -s scripts/sweep.py act"),
])
def test_dropping_the_flags_in_another_workflow_is_caught(name, job, needle):
    w = mutate(load(name), job, needle, "python3 -E -s ", "python3 ")
    assert [c[0] for c in unflagged(w)] == [job]


# Yaadgaar 656. The rows below run on a one-step workflow built here rather than
# on a file under `.github/workflows/`, so a revert of 653, 655 or this change
# leaves them standing: they test the scan, not one workflow.
def one_step(step, job=None, workflow=None):
    return {**(workflow or {}), "jobs": {"j": {**(job or {}), "steps": [step]}}}


@pytest.mark.parametrize("run", [
    "/usr/bin/python3 scripts/x.py",
    "/usr/bin/python3 -E -s scripts/x.py",
    ".venv/bin/python -E -s scripts/x.py",
    "$HOME/.venv/bin/python3 -E -s scripts/x.py",
    "$PY -E -s scripts/x.py",
    '"$PYTHON" -E -s scripts/x.py',
    '"${PYTHON:-python3}" -E -s scripts/x.py',
    "$python -E -s scripts/x.py",
    "python3.14t -E -s scripts/x.py",
    "python3t -E -s scripts/x.py",
    "pypy3 -E -s scripts/x.py",
    "pypy -E -s scripts/x.py",
    "cd sub && /usr/bin/python3 x.py",
    "out=$(.venv/bin/python3 x.py)",
])
def test_a_path_variable_or_variant_interpreter_is_caught(run):
    w = one_step({"run": run})
    assert [c[0] for c in unflagged(w)] == ["j"], run


@pytest.mark.parametrize("run", [
    "python3 -E -s scripts/x.py",
    'out=$(python3 -E -s "$RUNNER_TEMP/x.py" | tee -a "$GITHUB_STEP_SUMMARY")',
    "python3 -I -m pytest scripts/tests/ -q",
])
def test_the_one_spelling_is_still_accepted(run):
    w = one_step({"run": run})
    assert len(python_calls(w)) == 1 and unflagged(w) == []


@pytest.mark.parametrize("run", [
    'pip install --disable-pip-version-check "pyyaml==${PYYAML_VERSION}" "pytest==${PYTEST_VERSION}"',
    "sudo apt-get install -y python3-yaml",
    "ls /usr/lib/python3/dist-packages",
    "cat scripts/python_flags.py",
])
def test_a_word_that_only_contains_python_is_not_a_call(run):
    assert python_calls(one_step({"run": run})) == []


@pytest.mark.parametrize("make", [
    lambda shell: one_step({"run": "print(1)", "shell": shell}),
    lambda shell: one_step({"run": "print(1)"}, job={"defaults": {"run": {"shell": shell}}}),
    lambda shell: one_step({"run": "print(1)"}, workflow={"defaults": {"run": {"shell": shell}}}),
], ids=["step", "job-defaults", "workflow-defaults"])
@pytest.mark.parametrize("shell", ["python {0}", "python", "/usr/bin/python3 {0}", "pypy3 {0}", "Python {0}"])
def test_a_python_shell_is_caught(make, shell):
    assert len(python_shells(make(shell))) == 1


def test_a_bash_shell_is_not_caught():
    w = one_step({"run": "true", "shell": "bash"}, job={"defaults": {"run": {"shell": "bash -eo pipefail {0}"}}})
    assert python_shells(w) == []


@pytest.mark.parametrize("value", ["python3 scripts/x.py", "/usr/bin/python3 -E -s scripts/x.py", "set -e\n$PY x.py"])
def test_python_in_the_with_of_an_action_is_caught(value):
    w = one_step({"uses": "some/run-action@0123456789abcdef0123456789abcdef01234567", "with": {"script": value}})
    assert [c[0] for c in unflagged(w)] == ["j"]


def test_a_python_version_input_is_not_a_call():
    w = one_step({"uses": "actions/setup-python@x", "with": {"python-version": "${{ steps.versions.outputs.python }}"}})
    assert python_calls(w) == []


@pytest.mark.parametrize("run", [
    'echo "PYTHONPATH=$RUNNER_TEMP" >> "$GITHUB_ENV"',
    "echo PYTHONSTARTUP=/tmp/s.py >> $GITHUB_ENV",
    'echo "PYTHONPATH<<EOF" >> "$GITHUB_ENV"',
    'PYTHONPATH="$RUNNER_TEMP" python3 -E -s x.py',
    "export PYTHONPATH=/tmp",
    "export PYTHONHOME",
    "PYTHONPATH+=:/tmp",
    "env PYTHONSAFEPATH= pip install x",
])
def test_a_python_variable_set_in_a_run_is_caught(run):
    assert [c[0] for c in python_env(one_step({"run": run}))] == ["j"], run


@pytest.mark.parametrize("job", [
    {"container": {"image": "python:3", "env": {"PYTHONPATH": "/tmp"}}},
    {"container": {"image": "python:3", "options": "--env PYTHONPATH=/tmp"}},
    {"services": {"db": {"image": "mariadb", "env": {"PYTHONSTARTUP": "/tmp/s.py"}}}},
    {"services": {"db": {"image": "mariadb", "options": "-e PYTHONSTARTUP=/tmp/s.py"}}},
])
def test_a_python_variable_in_a_container_or_service_is_caught(job):
    assert [c[0] for c in python_env(one_step({"run": "true"}, job=job))] == ["j"]


@pytest.mark.parametrize("run", [
    "python3 -E -s -m pytest scripts/tests/ -q",
    "python3 -m pytest scripts/tests/ -q",
    "python3 -I scripts/x.py",
    "python3 -I -c 'print(1)'",
    "python3 -I -E -s -m pytest",
])
def test_a_module_run_takes_i_and_a_script_takes_e_s(run):
    """ADR-1164 (argocd-verify#26): with `-m` the cwd is first on `sys.path`, so
    `python3 -E -s -m pytest` in a pull request's checkout imports a `pytest.py`
    the pull request adds. A module run must be `python3 -I -m`. A script run
    stays `python3 -E -s`: `-I` implies `-P` and drops the script's own
    directory, which breaks its sibling imports."""
    assert [c[0] for c in unflagged(one_step({"run": run}))] == ["j"], run


def test_a_variable_named_after_python_counts_wherever_it_stands():
    """Fail-closed: the scan cannot tell `$PRE_PIN_PARENT_PYTHON x.py` from
    `echo "$PRE_PIN_PARENT_PYTHON"`, so both count as a call."""
    assert [c[0] for c in unflagged(one_step({"run": 'echo "$PRE_PIN_PARENT_PYTHON"'}))] == ["j"]


@pytest.mark.parametrize(("run", "job"), [
    ('PRE_PIN_PARENT_PYTHON=python3 echo "$PRE_PIN_PARENT_PYTHON"', {}),
    ("true", {"container": "ubuntu:24.04"}),
    ("true", {"services": {"db": {"image": "mariadb", "env": {"MARIADB_ROOT_PASSWORD": "x"}}}}),
])
def test_a_route_that_sets_no_python_variable_is_not_caught(run, job):
    assert python_env(one_step({"run": run}, job=job)) == []
