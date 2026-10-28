"""Yaadgaar 464. `cosign sign` in `ci-release.yaml` talks to Rekor. On
2026-10-28 two of nine module releases failed with Rekor
`createLogEntryConflict` (HTTP 409): Rekor accepted the first POST and the
response was lost. Every `cosign sign` call makes a fresh ephemeral key and a
fresh Rekor entry, so a retry clears it. The `sign` step must retry, bounded
at 3 attempts, and must still fail the job when every attempt fails.

Each check is paired with a mutation: it must go red on an input that lacks
the property. Run: python3 -m pytest scripts/tests/ -q
"""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"


def sign_script(text=None):
    text = CI_RELEASE.read_text(encoding="utf-8") if text is None else text
    steps = yaml.safe_load(text)["jobs"]["image"]["steps"]
    signs = [s for s in steps if s.get("name") == "sign"]
    assert len(signs) == 1
    return signs[0]["run"]


def problems(script):
    found = []
    if "cosign sign --yes" not in script:
        found.append("no cosign sign call")
    if not re.search(r"for\s+\w+\s+in\s+1\s+2\s+3\s*;\s*do", script):
        found.append("no loop bounded at 3 attempts")
    if "::warning::" not in script:
        found.append("no ::warning:: on a failed attempt")
    if not re.search(r"^\s*exit\s+[1-9]", script, re.M):
        found.append("no non-zero exit after the final failure")
    if not re.search(r"\bsleep\b", script):
        found.append("no backoff")
    return found


def test_sign_step_retries_bounded_and_still_fails():
    assert problems(sign_script()) == []


def test_mutation_bare_call_is_red():
    assert problems('cosign sign --yes "$IMAGE"\n')


def test_mutation_unbounded_loop_is_red():
    script = (
        'while true; do\n  cosign sign --yes "$IMAGE" && break\n'
        '  echo "::warning::x"\n  sleep 10\ndone\nexit 1\n'
    )
    assert "no loop bounded at 3 attempts" in problems(script)


def test_mutation_swallowed_failure_is_red():
    script = (
        'for attempt in 1 2 3; do\n  cosign sign --yes "$IMAGE" && break\n'
        '  echo "::warning::x"\n  sleep 10\ndone\n'
    )
    assert "no non-zero exit after the final failure" in problems(script)
