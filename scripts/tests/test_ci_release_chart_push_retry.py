"""YAADGAAR 243 (the yaadgaar 357 follow-up). The chart `push` step in
`ci-release.yaml` promises "each network hop is retried on its own", and
`helm registry login` is a network hop. A transient failure of the login must
be retried like `helm push`, and a login that never succeeds must still fail
the job.

The step's own shell runs against stub `helm` and `sleep` binaries: a script
that is never executed proves nothing, and this workflow's first real run is
post-merge in a consumer.

Run: python3 -m pytest scripts/tests/test_ci_release_chart_push_retry.py -q
"""

import os
import pathlib
import stat
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"


def push_script(text=None):
    text = CI_RELEASE.read_text(encoding="utf-8") if text is None else text
    steps = yaml.safe_load(text)["jobs"]["chart"]["steps"]
    pushes = [s for s in steps if s.get("name") == "push"]
    assert len(pushes) == 1
    return pushes[0]["run"]


def run(tmp_path, script, login_fails, push_fails=0):
    """Run `script` with stub helm. The first `login_fails` logins and the first
    `push_fails` pushes exit 1. Returns (exit status, the stub's call log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    helm = bin_dir / "helm"
    helm.write_text(
        "#!/usr/bin/env bash\n"
        'cat >/dev/null 2>&1 || true\n'
        f'echo "$1 $2" >> "{log}"\n'
        'verb="$1 $2"\n'
        f'n=$(grep -c "^$verb\\$" "{log}")\n'
        'case "$verb" in\n'
        f'  "registry login") [ "$n" -le {login_fails} ] && exit 1 ;;\n'
        f'  "push ./x-1.tgz") [ "$n" -le {push_fails} ] && exit 1 ;;\n'
        "esac\n"
        "exit 0\n"
    )
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    for f in (helm, sleep):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "x-1.tgz").write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VERSION": "1",
        "REGISTRY": "ghcr.io",
        "OWNER": "o",
        "ACTOR": "a",
        "TOKEN": "t",
    }
    done = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done.returncode, calls


def test_a_transient_login_failure_is_retried_and_the_push_happens(tmp_path):
    status, calls = run(tmp_path, push_script(), login_fails=2)
    assert status == 0
    assert calls.count("registry login") == 3
    assert calls.count("push ./x-1.tgz") == 1


def test_a_login_that_never_succeeds_fails_the_job_without_pushing(tmp_path):
    status, calls = run(tmp_path, push_script(), login_fails=99)
    assert status != 0
    assert calls.count("registry login") == 3
    assert "push ./x-1.tgz" not in calls


def test_a_transient_push_failure_is_still_retried(tmp_path):
    status, calls = run(tmp_path, push_script(), login_fails=0, push_fails=2)
    assert status == 0
    assert calls.count("push ./x-1.tgz") == 3


def test_mutation_a_single_login_outside_the_loop_is_red(tmp_path):
    """The shape before this change: it must fail the first test above."""
    once = (
        "set -euo pipefail\n"
        'echo "$TOKEN" | helm registry login "$REGISTRY" -u "$ACTOR" --password-stdin\n'
        "for i in 1 2 3; do\n"
        '  helm push ./*-"$VERSION".tgz "oci://${REGISTRY}/${OWNER}/charts" && break\n'
        '  [ "$i" = 3 ] && exit 1\n'
        "  sleep 1\n"
        "done\n"
    )
    status, _ = run(tmp_path, once, login_fails=2)
    assert status != 0
