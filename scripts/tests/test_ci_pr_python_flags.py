"""Yaadgaar 653 and 655. Every Python call that `ci-pr.yaml`, `ci-release.yaml`
and `sweep.yaml` run is spelled `python3 -E -s`.

The `ci-pr.yaml` jobs run a gate with the cwd inside the pull request's
checkout, and the `ci-release.yaml` jobs run one inside the consumer's tagged
checkout. `-E` makes the interpreter ignore every `PYTHON*` variable
(`PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME`, ...), and `-s` leaves the user
site-packages off `sys.path`. Neither the step env nor the runner's home can
then put code in front of the gate's own imports. `sweep.yaml` runs only this
repository's own checkout; it carries the flags too, so one rule holds for every
file this test reads (yaadgaar 655, fail-closed).

ONE SPELLING. The scan matches `python`, `python3` and `python3.N` as command
words and accepts only `python3 -E -s`. A call rewritten as `python` or
`python3.14` is refused even when it carries the flags: the bare name can be a
different interpreter, and a second spelling is a second place to forget them.

NO `PYTHON*` IN ANY `env:`. Under `-E` such a variable is dead config, and it
reads as if the gate needed it.

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

# A Python interpreter as a command word: `python`, `python3` or `python3.N`,
# not part of a longer name (`python3-yaml`, `setup-python`,
# `PRE_PIN_PARENT_PYTHON`) or a path component other than the last.
PYTHON = re.compile(r"(?<![\w./-])python(?:3(?:\.\d+)?)?(?![\w.-])")
FLAGS = re.compile(r"python3 -E -s (?!-)")
# The sites measured on 2026-11-05, per file: a regex that matched nothing in
# one file must not hide behind the count of another.
MIN_SITES = {"ci-pr.yaml": 20, "ci-release.yaml": 4, "sweep.yaml": 10}


def code_lines(run):
    """The `run:` text without whole-line shell comments, which mention
    `python3` in prose."""
    return [line for line in run.splitlines() if not line.lstrip().startswith("#")]


def python_calls(workflow):
    calls = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for line in code_lines(step.get("run") or ""):
                for match in PYTHON.finditer(line):
                    calls.append((name, step.get("name", ""), line[match.start():].strip()))
    return calls


def unflagged(workflow):
    return [c for c in python_calls(workflow) if not FLAGS.match(c[2])]


def python_env(workflow):
    """Every `PYTHON*` key in the workflow's, a job's or a step's `env:`."""
    scopes = [("(workflow)", "", workflow.get("env") or {})]
    for name, job in workflow["jobs"].items():
        scopes.append((name, "", job.get("env") or {}))
        scopes += [(name, s.get("name", ""), s.get("env") or {}) for s in job.get("steps", [])]
    return [(job, step, key) for job, step, env in scopes for key in env if key.startswith("PYTHON")]


def load(name="ci-pr.yaml"):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_every_python_call_ignores_the_environment_and_the_user_site(name):
    offenders = unflagged(load(name))
    assert not offenders, f"a Python call not spelled `python3 -E -s` in {name}:\n" + "\n".join(
        f"  {job} / {step}: {line}" for job, step, line in offenders
    )


@pytest.mark.parametrize("name", sorted(MIN_SITES))
def test_the_scan_still_sees_every_site(name):
    """A regex that matched nothing would pass the test above on any workflow."""
    assert len(python_calls(load(name))) >= MIN_SITES[name]


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
    ("sweep.yaml", "wait", "python3 -E -s scripts/sweep.py wait"),
    ("sweep.yaml", "act", "python3 -E -s scripts/sweep.py act"),
])
def test_dropping_the_flags_in_another_workflow_is_caught(name, job, needle):
    w = mutate(load(name), job, needle, "python3 -E -s ", "python3 ")
    assert [c[0] for c in unflagged(w)] == [job]
