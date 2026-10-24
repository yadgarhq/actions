"""C7 of plans/auto-deploy-parent-chart.md (ADR-0946, ADR-0948 R3-a, ADR-1004).

When `yadgarhq/chart` publishes a parent chart version, `ci-release.yaml`'s
`argocd-pin-nudge` job sends `repository_dispatch` type `chart-published` to
`yadgarhq/argocd`, so `auto-pin.yaml` there starts in seconds rather than at
its next scheduled run (every 15 minutes). The payload is a nudge, never an input: argocd computes
the target itself (section 1, Trigger).

THE NUDGE MUST NEVER FAIL THE RELEASE. By the time it runs the parent chart is
published; a lost nudge costs at most one cron interval, because the C5
schedule poll is the backstop. So every failure here is a `::warning::` and
exit 0: no release App passed, a mint that fails, a dispatch that fails.

The shipped shell is EXECUTED, not matched as text, against a `gh` stub on
PATH — the same reason `test_parent_bump.py` executes the `cred` step.

Run: python3 -m pytest scripts/tests/ -q
"""

import json
import os
import pathlib
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"
NUDGE = "argocd-pin-nudge"


def job():
    return yaml.safe_load(CI_RELEASE.read_text(encoding="utf-8"))["jobs"][NUDGE]


def steps():
    return {s.get("id") or s.get("name"): s for s in job()["steps"]}


def run_step(step_id, env, tmp_path, gh_exit=0):
    """Execute a shipped `run:` block with `gh` stubbed. The stub records its
    argv and stdin, and exits `gh_exit`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    record = tmp_path / "gh.calls"
    stub = bindir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{record}"\n'
        f'cat >> "{record}"\n'
        f"exit {gh_exit}\n"
    )
    stub.chmod(0o755)
    summary = tmp_path / "summary"
    output = tmp_path / "output"
    proc = subprocess.run(
        ["bash", "-e", "-c", steps()[step_id]["run"]],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            **env,
        },
    )
    calls = record.read_text() if record.exists() else ""
    out = output.read_text() if output.exists() else ""
    return proc, calls, out


# ---------------------------------------------------------------------------
# Shape: scope, permissions, never-fail
# ---------------------------------------------------------------------------


def test_the_job_holds_no_github_token_permission():
    """No checkout and the App token does the work, so the job must not
    inherit the workflow's `packages: write` / `id-token: write`."""
    assert job()["permissions"] == {}


def test_the_job_cannot_fail_the_release():
    assert job()["continue-on-error"] is True
    assert job()["timeout-minutes"] <= 5


def test_the_mint_is_scoped_to_argocd_and_contents_write_only():
    mint = steps()["app"]
    w = mint["with"]
    assert w["repositories"] == "argocd"
    assert w["owner"] == "${{ github.repository_owner }}"
    perms = {k: v for k, v in w.items() if k.startswith("permission-")}
    assert perms == {"permission-contents": "write"}
    assert mint["continue-on-error"] is True
    assert mint["if"] == "steps.cred.outputs.configured == 'true'"


def test_the_dispatch_reads_the_version_through_env_not_interpolation():
    nudge = steps()["nudge"]
    assert "${{" not in nudge["run"]
    assert nudge["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert nudge["env"]["VERSION"] == "${{ needs.detect.outputs.version }}"
    assert nudge["if"] == "steps.app.outcome == 'success'"
    assert nudge["continue-on-error"] is True


def test_a_failed_mint_is_named_in_a_warning():
    warn = steps()["mint_failed"]
    assert warn["if"] == "steps.app.outcome == 'failure'"
    assert "::warning::" in warn["run"]


# ---------------------------------------------------------------------------
# Executed: the credential probe warns, it never reddens
# ---------------------------------------------------------------------------


def test_no_release_app_warns_and_stays_green(tmp_path):
    """Merge order is actions first, then chart: between the two merges a chart
    tag runs this job with two empty strings, and that release must stay green."""
    proc, _calls, out = run_step("cred", {"CLIENT_ID": "", "APP_KEY": ""}, tmp_path)
    assert proc.returncode == 0
    assert "::warning::" in proc.stdout
    assert "configured=false" in out


def test_half_a_credential_warns_and_stays_green(tmp_path):
    proc, _c, out = run_step("cred", {"CLIENT_ID": "Iv1.abc", "APP_KEY": ""}, tmp_path)
    assert proc.returncode == 0 and "configured=false" in out


def test_a_configured_release_app_is_reported(tmp_path):
    proc, _c, out = run_step(
        "cred", {"CLIENT_ID": "Iv1.abc", "APP_KEY": "-----BEGIN-----"}, tmp_path
    )
    assert proc.returncode == 0 and "configured=true" in out


# ---------------------------------------------------------------------------
# Executed: the dispatch
# ---------------------------------------------------------------------------

ENV = {"GH_TOKEN": "x", "OWNER": "yadgarhq", "VERSION": "0.34.12"}


def test_the_dispatch_sends_chart_published_with_the_version(tmp_path):
    proc, calls, _ = run_step("nudge", ENV, tmp_path)
    assert proc.returncode == 0, proc.stderr
    argv, body = calls.split("\n", 1)
    assert argv == "api -X POST repos/yadgarhq/argocd/dispatches --input -"
    assert json.loads(body) == {
        "event_type": "chart-published",
        "client_payload": {"version": "0.34.12"},
    }


def test_a_failed_dispatch_warns_and_stays_green(tmp_path):
    proc, calls, _ = run_step("nudge", ENV, tmp_path, gh_exit=1)
    assert calls, "the stub was never called"
    assert proc.returncode == 0
    assert "::warning::" in proc.stdout
    assert "schedule" in proc.stdout


def test_the_version_is_data_not_shell(tmp_path):
    """A hostile version string lands in the JSON body verbatim and runs nothing."""
    canary = tmp_path / "pwned"
    env = dict(ENV, VERSION=f'0.1.0"; touch {canary}; echo "$(touch {canary})')
    proc, calls, _ = run_step("nudge", env, tmp_path)
    assert proc.returncode == 0
    assert not canary.exists()
    body = json.loads(calls.split("\n", 1)[1])
    assert body["client_payload"]["version"] == env["VERSION"]
