<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers run `scripts/trivy_gate.py` through `ci-pr.yaml`'s
  `vulnerabilities` job at `@main`, so this change reaches every consumer the
  moment it merges. The `main` ruleset forbids a direct push, so a rollback is
  a pull request, and GitHub's generated revert body carries none of the five
  template sections and is refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the per-path staleness
  check, the `exp:` refusal in the legacy `.trivyignore` reader, the
  refusal of an existing `chart/Chart.yaml` with no helm result under `chart/`,
  and the tests for them. The `Skipping chart` text match was kept, so it is
  not part of this revert.
-->

## What

Reverts yadgarhq/actions#PR. `trivy_gate.py` judges an ignore entry stale per
entry again, not per path. The legacy `.trivyignore` reader takes an `ID exp:DATE`
line whole again. A chart with `chart/Chart.yaml` but no
`chart/ci/values.yaml` no longer refuses on zero helm results under `chart/`.
The `Skipping chart` WARN refusal and the `--helm-values` zero-result check
are untouched.

## Why

The `vulnerabilities` job went red in a consumer after the merge.

Expected signature, one of:

- `path ... matches no ... finding in this scan` in the stale-entry report: a multi-path entry in the consumer's `.trivyignore.yaml` has a dead path. Fix the entry before reverting, it is a real stale record.
- `carries an `exp:` expiry` from the legacy `.trivyignore` reader.
- `reported zero `helm`-typed results under `chart/`` in a repo whose chart has no `chart/ci/values.yaml`.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(ci): judge ignore-entry staleness per entry again and drop the `chart/` zero-helm-result and `exp:` refusals in trivy_gate, reverting the trivy_gate follow-ups

## Verification

From the consumer, the `vulnerabilities` job that was red goes green on the
same head SHA. In `yadgarhq/actions`, `python3 -m pytest scripts/tests/ -q`
passes after this revert only with the test file reverted in the same commit;
the revert of yadgarhq/actions#PR does that.

## Risk

Brings back the three holes yadgarhq/actions#PR closed: a dead path in a
multi-path ignore entry pre-approving a future finding, a silent `exp:` line in
the legacy file, and a chart with no `chart/ci` passing with no helm result.
Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Correct, yes. Less secure, yes: the gate is weaker. It
touches no cluster trust boundary.
