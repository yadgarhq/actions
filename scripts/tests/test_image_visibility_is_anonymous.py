"""LEDGER 976. `ci-release.yaml`'s image-visibility guard, executed rather than read.

THE DEFECT, AND WHY NOTHING CAUGHT IT. The `image` job's third step is
`docker/login-action`, which writes a credential into the docker CLI's
configuration directory. The guard six steps later runs

    docker manifest inspect "$REF"

with that configuration still on disk, so docker answers it AS THE JOB — an
AUTHENTICATED pull. An authenticated pull of a private GHCR package SUCCEEDS.
The step therefore reports "Anonymous pull of <ref> works" about a request that
was not anonymous, and it cannot fail for the reason it exists: the fifth
check-that-cannot-fail this estate has found (ADR-0645).

The sibling gate in the same file already says so in prose. The `chart` job's
"an installation with no credential must be able to pull this chart" step is
headed SAME INTENT, DELIBERATELY NOT ITS MECHANISM, and names this step's
credentials-on-disk as the mistake it declines to copy. The prose was right and
the step was never fixed.

WHY THIS FILE EXECUTES THE STEP INSTEAD OF ASSERTING ON ITS TEXT. A structural
assertion — "the script mentions DOCKER_CONFIG" — is satisfied by a mention, and
this estate has shipped four gates that passed on a shape rather than on a fact.
So the step's own `run:` block is read out of `ci-release.yaml` and executed by
bash against a STUB `docker` that models the one asymmetry the defect turns on:

    a PUBLIC package answers anybody
    a PRIVATE package answers only a caller whose docker configuration holds a
      credential

Under that stub, the shipped step exits 0 on a private package and the fixed one
exits 1. Nothing else about docker is modelled, because nothing else is the bug.

THE COMPANION ASSERTION IS NOT OPTIONAL (ADR-0646). A step that refuses
everything would satisfy the red case above and break every release in the
estate, so the same stub is run against a PUBLIC package and the step must pass.
The two cases differ in one property of the registry and in nothing else.

Run: python3 -m pytest scripts/tests/test_image_visibility_is_anonymous.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"
STEP_NAME = "verify an adopter can actually pull this"

REF = "ghcr.io/yadgarhq/iam:1.2.3"

# GHCR, reduced to the one property the defect turns on. `PACKAGE_PUBLIC` is the
# registry's side of the question; the credential on disk is the caller's.
STUB_DOCKER = """\
#!/bin/sh
# Only `manifest inspect` is modelled; anything else is a test-fixture error.
if [ "$1" != "manifest" ] || [ "$2" != "inspect" ]; then
  echo "stub docker: unexpected argv: $*" >&2
  exit 127
fi
cfg="${DOCKER_CONFIG:-$HOME/.docker}"
if [ "$PACKAGE_PUBLIC" = "yes" ]; then
  echo '{"schemaVersion":2}'
  exit 0
fi
if grep -q '"auths"' "$cfg/config.json" 2>/dev/null; then
  echo '{"schemaVersion":2}'
  exit 0
fi
echo 'unauthorized' >&2
exit 1
"""

# What `docker/login-action` leaves behind, reduced to the key the stub reads.
# THE VALUE IS DELIBERATELY NOT CREDENTIAL-SHAPED. A realistic base64 blob here
# is a `generic-api-key` finding for the `gitleaks` hook — measured, not
# guessed — and an allowlist entry to carry a fixture would be the wrong half of
# that trade. The stub keys on the `"auths"` key, never on the value.
LOGIN_CONFIG = '{"auths":{"ghcr.io":{"auth":"placeholder-not-a-credential"}}}'


def guard_script() -> str:
    """The step's own `run:` block, read out of the workflow rather than copied."""
    doc = yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))
    steps = doc["jobs"]["image"]["steps"]
    matching = [s for s in steps if s.get("name") == STEP_NAME]
    assert len(matching) == 1, f"expected one step named {STEP_NAME!r}, found {len(matching)}"
    # The step must carry no `${{ }}` in its body, or bash is not what runs it.
    script = matching[0]["run"]
    assert "${{" not in script, "the script interpolates; this harness runs bash, not Actions"
    return script


def run_guard(tmp_path: Path, *, public: bool, logged_in: bool = True):
    home = tmp_path / "home"
    (home / ".docker").mkdir(parents=True)
    if logged_in:
        (home / ".docker" / "config.json").write_text(LOGIN_CONFIG)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB_DOCKER)
    stub.chmod(0o755)

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    summary = tmp_path / "summary.md"
    summary.touch()

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(home),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STEP_SUMMARY": str(summary),
        "PACKAGE_PUBLIC": "yes" if public else "no",
        "REF": REF,
        "OWNER": "yadgarhq",
        "REPO": "yadgarhq/iam",
    }
    proc = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", guard_script()],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    return proc, summary.read_text(encoding="utf-8")


def test_a_private_image_reddens_the_guard(tmp_path, capsys):
    """THE CONSTRUCTED RED CASE. The package is private and the job is logged in.

    This is the exact situation the step was written for, and the shipped step
    passes it. A check that cannot fail in the one state it exists to detect is
    not a weak check; it is an absent one wearing a name (ADR-0645).
    """
    proc, summary = run_guard(tmp_path, public=False, logged_in=True)
    with capsys.disabled():
        print("\n--- private package, job logged in ---")
        print(f"exit {proc.returncode}")
        print(proc.stdout.rstrip())
        print(proc.stderr.rstrip())
        print(f"--- step summary ---\n{summary.rstrip()}")
    assert proc.returncode == 1, "a private image must redden this step"
    assert "::error::Package is not publicly pullable" in proc.stdout
    assert "NOT publicly pullable" in summary


def test_a_public_image_still_passes(tmp_path):
    """THE COMPANION ASSERTION (ADR-0646). The fix must not refuse everything.

    `ci-release.yaml` runs in every releasing repository in this estate, so a
    guard that reddened a healthy release would be the worse defect of the two.
    """
    proc, summary = run_guard(tmp_path, public=True, logged_in=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Anonymous pull" in summary


def test_the_credential_on_disk_changes_nothing(tmp_path):
    """THE PROPERTY, rather than one instance of it: the verdict on a private
    package is the same logged in and logged out. That equality is what "this
    request is anonymous" means, and it is the assertion `#83` put on the chart
    gate. An absence is easy to write and easy to lose again."""
    logged_in, _ = run_guard(tmp_path / "in", public=False, logged_in=True)
    logged_out, _ = run_guard(tmp_path / "out", public=False, logged_in=False)
    assert logged_in.returncode == logged_out.returncode == 1


@pytest.mark.parametrize("public", [True, False])
def test_the_stub_is_not_a_constant(tmp_path, public):
    """The stub itself must distinguish the two registries, or every row above
    is measuring the same thing twice."""
    proc, _ = run_guard(tmp_path, public=public)
    assert proc.returncode == (0 if public else 1)
