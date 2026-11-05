"""Yaadgaar 653. Every `python3` that `ci-pr.yaml` runs carries `-E -s`.

The jobs run a gate with the cwd inside the pull request's checkout. `-E`
makes the interpreter ignore every `PYTHON*` variable (`PYTHONPATH`,
`PYTHONSTARTUP`, `PYTHONHOME`, ...), and `-s` leaves the user site-packages
off `sys.path`. Neither the step env nor the runner's home can then put code
in front of the gate's own imports.

WHY NOT `-I`. On Python 3.11 and later `-I` also implies `-P`, and `-P` drops
the script's OWN directory from `sys.path`. Six gates import a sibling that
the job stages next to them (`next_version` -> `pr_body`, `d80_portability`,
`service_immutable` and `pre_pin_parent` -> `api_versions`, `example_pins` ->
`parent_pin`, `ci_verdict` -> `config_only`), and each one dies with
`ModuleNotFoundError` under `-I`. The script's own directory is where the
script itself came from, so `-P` closes no gap that `-E -s` leaves open.

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_PR = ROOT / ".github" / "workflows" / "ci-pr.yaml"

# `python3` as a command word: not part of a longer name (`python3-yaml`,
# `PRE_PIN_PARENT_PYTHON`) or a path component other than the last.
PYTHON3 = re.compile(r"(?<![\w./-])python3(?![\w.-])")
FLAGS = re.compile(r"python3 -E -s (?!-)")
# The sites measured on 2026-11-05: 20 script-path calls.
MIN_SITES = 20


def code_lines(run):
    """The `run:` text without whole-line shell comments, which mention
    `python3` in prose."""
    return [line for line in run.splitlines() if not line.lstrip().startswith("#")]


def python_calls(workflow):
    calls = []
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for line in code_lines(step.get("run") or ""):
                for match in PYTHON3.finditer(line):
                    calls.append((name, step.get("name", ""), line[match.start():].strip()))
    return calls


def unflagged(workflow):
    return [c for c in python_calls(workflow) if not FLAGS.match(c[2])]


def load():
    return yaml.safe_load(CI_PR.read_text())


def test_every_python_call_ignores_the_environment_and_the_user_site():
    offenders = unflagged(load())
    assert not offenders, "python3 without `-E -s` in ci-pr.yaml:\n" + "\n".join(
        f"  {job} / {step}: {line}" for job, step, line in offenders
    )


def test_the_scan_still_sees_every_site():
    """A regex that matched nothing would pass the test above on any workflow."""
    assert len(python_calls(load())) >= MIN_SITES


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
