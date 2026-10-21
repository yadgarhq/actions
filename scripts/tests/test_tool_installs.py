"""LEDGER 1756 S2b, ledger 1362. Every downloaded tool is checked against its
sha256 in `versions.yaml` before anything is installed, and a wrong sum fails
the step closed.

HOW. This suite takes the real `run:` block of every install step (a step whose
`env:` names one of the `*_VERSION` keys below) out of the workflows and runs
it under bash with two stubs first on `PATH`:

- `curl` writes a fixture artefact to the file named by `-o` and refuses any
  call without `-o`. A `curl | tar` pipe has no `-o`, so it fails here: the
  bytes must land in a file that can be checked before they are unpacked.
- `sudo` records its arguments and does nothing else. `sudo` is the install,
  so an empty record means nothing was installed.

Then each step must: install with the right sum; refuse a wrong sum, an empty
sum and a malformed sum, with nothing installed. And each step's `*_SHA256`
must come from the same versions read as its `*_VERSION`.

Run: python3 -m pytest scripts/tests/ -q
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

# env key of the version -> (tool in versions.yaml, env key of the sum)
INSTALLS = {
    "GITLEAKS_VERSION": ("gitleaks", "GITLEAKS_SHA256"),
    "HADOLINT_VERSION": ("hadolint", "HADOLINT_SHA256"),
    "ACTIONLINT_VERSION": ("actionlint", "ACTIONLINT_SHA256"),
    "CARGO_DENY_VERSION": ("cargo-deny", "CARGO_DENY_SHA256"),
}

# The workflows that download each tool today. A step that drops out of the
# scan (a rename, a parse change) turns this red instead of passing over zero.
EXPECTED = {
    ("ci-pr.yaml", "GITLEAKS_VERSION"),
    ("ci-self.yaml", "GITLEAKS_VERSION"),
    ("ci-pr.yaml", "HADOLINT_VERSION"),
    ("ci-pr.yaml", "ACTIONLINT_VERSION"),
    ("ci-pr.yaml", "CARGO_DENY_VERSION"),
}

READ = re.compile(r"^\$\{\{\s*(?:needs\.[\w-]+|steps\.[\w-]+)\.outputs\.([\w-]+)\s*\}\}$")


def install_steps():
    found = []
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        for job_id, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                env = step.get("env") or {}
                for key in INSTALLS:
                    if key in env:
                        found.append((path.name, job_id, key, step))
    return found


STEPS = install_steps()
IDS = [f"{name}:{job}:{key}" for name, job, key, _ in STEPS]

STUB_CURL = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
if [ -z "$out" ]; then
  echo "stub curl: no -o, so the download is not written to a file that can be checked" >&2
  exit 99
fi
cp "$FIXTURE" "$out"
"""

STUB_SUDO = """#!/usr/bin/env bash
echo "$*" >> "$SUDO_LOG"
"""


def run_step(tmp_path, step, key, sha):
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for name, body in (("curl", STUB_CURL), ("sudo", STUB_SUDO)):
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    fixture = tmp_path / "artefact"
    fixture.write_bytes(b"not a real artefact, only bytes with a known sum\n")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    log = tmp_path / "sudo.log"
    log.write_text("")
    _tool, sha_key = INSTALLS[key]
    env = {
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(runner_temp),
        "FIXTURE": str(fixture),
        "SUDO_LOG": str(log),
        key: "9.9.9",
        sha_key: sha(fixture),
    }
    proc = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc, log.read_text()


def right(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_known_download_is_scanned():
    assert {(name, key) for name, _job, key, _step in STEPS} == EXPECTED


@pytest.mark.parametrize("name,job,key,step", STEPS, ids=IDS)
def test_the_sum_and_the_version_come_from_one_versions_read(name, job, key, step):
    tool, sha_key = INSTALLS[key]
    env = step.get("env") or {}
    output = tool.replace("-", "_")
    version = READ.match(str(env.get(key, "")))
    assert version and version.group(1) == output, f"{name}:{job}: {key} = {env.get(key)!r}"
    assert sha_key in env, f"{name}:{job}: no {sha_key} in the step's env"
    digest = READ.match(str(env[sha_key]))
    assert digest and digest.group(1) == f"{output}_sha256", f"{name}:{job}: {sha_key} = {env[sha_key]!r}"
    assert env[key].split(".outputs.")[0] == env[sha_key].split(".outputs.")[0], (
        f"{name}:{job}: the version and the sum are read from different steps or jobs"
    )


@pytest.mark.parametrize("name,job,key,step", STEPS, ids=IDS)
def test_the_right_sum_installs(tmp_path, name, job, key, step):
    proc, installed = run_step(tmp_path, step, key, right)
    assert proc.returncode == 0, f"{name}:{job}: {proc.stdout}{proc.stderr}"
    assert installed.strip(), f"{name}:{job}: the step passed and installed nothing"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda p: "0" * 64, id="wrong"),
        pytest.param(lambda p: "", id="empty"),
        pytest.param(lambda p: right(p)[:63], id="short"),
        pytest.param(lambda p: right(p).upper(), id="uppercase"),
    ],
)
@pytest.mark.parametrize("name,job,key,step", STEPS, ids=IDS)
def test_a_bad_sum_fails_closed_and_installs_nothing(tmp_path, name, job, key, step, bad):
    proc, installed = run_step(tmp_path, step, key, bad)
    assert proc.returncode != 0, f"{name}:{job}: a bad sum passed: {proc.stdout}{proc.stderr}"
    assert installed == "", f"{name}:{job}: installed before the sum was checked: {installed}"
