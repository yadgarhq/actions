"""yaadgaar 319. A git `http.extraheader` never carries `Authorization: Bearer`.

WHY. GitHub's git smart-HTTP endpoint rejects the Bearer scheme with 401, and
git then fails with `could not read Username for 'https://github.com'`. It
accepts Basic of `x-access-token:<token>`. The first live repin run
(yadgarhq/task 40968378376) pushed with Bearer and failed that way. Bearer is
correct for the REST API (`gh api`, `curl` to api.github.com or ghcr.io), so
this suite looks only at values given to git as `http.extraheader`.

THE RULES, per workflow file:

1. no `http.extraheader` value (in `GIT_CONFIG_VALUE_n` at workflow, job or
   step `env:`, `git -c`, or `git config`, including URL-scoped keys such as
   `http.https://github.com/.extraheader`, any letter case) holds `Authorization: Bearer`;
2. a value that holds `Authorization: basic ${var}` for a computed `var` has an
   earlier `echo "::add-mask::${var}"` in the same `run:` block, as
   belt-and-braces: the runner already masks base64 encodings of registered
   secrets, and the explicit mask of the full header value costs nothing.

EVERY CHECK IS PAIRED WITH A CONSTRUCTED RED CASE (see `test_no_test_skips.py`).

Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

HEADER_CONTEXT = re.compile(r"http\.(?:\S*\.)?extraheader|GIT_CONFIG_VALUE_\d+", re.IGNORECASE)
BEARER = re.compile(r"Authorization:\s*Bearer", re.IGNORECASE)
BASIC_VAR = re.compile(r"Authorization:\s*basic\s+\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", re.IGNORECASE)


def _logical_lines(run):
    """The run block with backslash continuations joined."""
    return run.replace("\\\n", " ").splitlines()


def _env_violations(env, where):
    out = []
    for key, val in (env or {}).items():
        if re.fullmatch(r"GIT_CONFIG_VALUE_\d+", str(key)) and BEARER.search(str(val)):
            out.append(f"{where}: env `{key}` uses `Authorization: Bearer`; GitHub git rejects it (401)")
    return out


def scan(text, name):
    """Return violations as `name:job:step: message`."""
    violations = []
    data = yaml.safe_load(text) or {}
    violations.extend(_env_violations(data.get("env"), f"{name}:workflow"))
    for job_id, job in (data.get("jobs") or {}).items():
        violations.extend(_env_violations(job.get("env"), f"{name}:{job_id}"))
        for i, step in enumerate(job.get("steps") or []):
            violations.extend(_env_violations(step.get("env"), f"{name}:{job_id}:step{i}"))
            run = step.get("run")
            if not isinstance(run, str):
                continue
            where = f"{name}:{job_id}:step{i}"
            # A Bearer header inside an http.extraheader value. The header
            # context may sit on a neighbouring continuation line, so test the
            # joined statement, not the physical line.
            for line in _logical_lines(run):
                if HEADER_CONTEXT.search(line) and BEARER.search(line):
                    violations.append(
                        f"{where}: git http.extraheader uses `Authorization: Bearer`; "
                        "GitHub git rejects it (401). Use Basic of x-access-token:TOKEN"
                    )
            masked = set()
            for line in _logical_lines(run):
                m = re.search(r"::add-mask::\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", line)
                if m:
                    masked.add(m.group(1))
                b = BASIC_VAR.search(line)
                if b and HEADER_CONTEXT.search(line) and b.group(1) not in masked:
                    violations.append(
                        f"{where}: Basic header built from `{b.group(1)}` with no earlier "
                        f"`::add-mask::${{{b.group(1)}}}` in the same run block"
                    )
    return violations


def workflow_texts():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yaml"))}


def test_no_git_extraheader_uses_bearer_or_an_unmasked_basic():
    found = []
    for name, text in workflow_texts().items():
        found.extend(scan(text, name))
    assert not found, "\n".join(found)


def test_the_scan_still_sees_the_repin_push():
    text = workflow_texts()["repin.yaml"]
    assert "GIT_CONFIG_VALUE_0" in text
    assert BASIC_VAR.search(text), "repin.yaml no longer sends a Basic header"
    assert "::add-mask::${auth}" in text


# ---------------------------------------------------------------------------
# Constructed red cases
# ---------------------------------------------------------------------------

def _wf(run):
    body = "".join("          " + line + "\n" for line in run.splitlines())
    return f"on: workflow_call\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n{body}"


GOOD = """\
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
echo "::add-mask::${auth}"
GIT_CONFIG_COUNT=1 \\
GIT_CONFIG_KEY_0=http.extraheader \\
GIT_CONFIG_VALUE_0="Authorization: basic ${auth}" \\
  git push origin HEAD:refs/heads/x
"""


def test_conforming_push_is_clean():
    assert scan(_wf(GOOD), "good.yaml") == []


def test_bearer_in_env_form_is_refused():
    run = 'GIT_CONFIG_VALUE_0="Authorization: Bearer ${GH_TOKEN}" git push origin x\n'
    assert scan(_wf(run), "bad.yaml")


def test_bearer_in_git_dash_c_form_is_refused():
    run = 'git -c http.extraheader="Authorization: Bearer $T" push origin x\n'
    assert scan(_wf(run), "bad.yaml")


def test_bearer_in_git_config_form_is_refused():
    run = 'git config http.extraheader "Authorization: Bearer $T"\n'
    assert scan(_wf(run), "bad.yaml")


def test_basic_without_mask_is_refused():
    run = GOOD.replace('echo "::add-mask::${auth}"\n', "")
    assert scan(_wf(run), "bad.yaml")


def test_mask_after_use_is_refused():
    lines = GOOD.splitlines(keepends=True)
    run = lines[0] + "".join(lines[2:]) + lines[1]
    assert scan(_wf(run), "bad.yaml")


def test_mask_of_another_variable_is_refused():
    run = GOOD.replace("::add-mask::${auth}", "::add-mask::${other}")
    assert scan(_wf(run), "bad.yaml")


def test_bearer_to_the_rest_api_is_not_flagged():
    run = 'curl -H "Authorization: Bearer ${TOKEN}" https://api.github.com/x\n'
    assert scan(_wf(run), "ok.yaml") == []


def test_url_scoped_dash_c_key_is_refused():
    run = 'git -c http.https://github.com/.extraheader="Authorization: Bearer $T" push origin x\n'
    assert scan(_wf(run), "bad.yaml")


def test_url_scoped_git_config_key_is_refused():
    run = 'git config --local http.https://github.com/.extraheader "Authorization: Bearer $T"\n'
    assert scan(_wf(run), "bad.yaml")


def test_step_level_env_value_is_refused():
    text = (
        "on: workflow_call\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - env:\n          GIT_CONFIG_VALUE_0: 'Authorization: Bearer ${{ secrets.T }}'\n"
        "        run: git push origin x\n"
    )
    assert scan(text, "bad.yaml")


def test_job_level_env_value_is_refused():
    text = (
        "on: workflow_call\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
        "    env:\n      GIT_CONFIG_VALUE_0: 'Authorization: Bearer x'\n"
        "    steps:\n      - run: git push origin x\n"
    )
    assert scan(text, "bad.yaml")


def test_mixed_case_bearer_is_refused():
    run = 'git -c http.extraheader="AUTHORIZATION: bearer $T" push origin x\n'
    assert scan(_wf(run), "bad.yaml")
