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

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the extra trivy pass
  per `chart/ci/*-values.yaml`, `extra_values_files` in
  `chart_values_override.py`, the merge of findings across passes, the pass label
  in refusals and log lines, and the tests for them. Every refusal that
  existed before yadgarhq/actions#PR stays, on the primary pass.
-->

## What

Reverts yadgarhq/actions#PR. `trivy_gate.py` runs one scan again, under
`chart/ci/values.yaml` only. A `chart/ci/*-values.yaml` file is no longer
scanned. Findings are no longer merged across passes. The `Skipping chart` WARN
refusal, the `--helm-values` zero-result check and the `chart/`-scoped
zero-result check stay, on the one pass.

## Why

The `vulnerabilities` job went red in a consumer after the merge.

Expected signature, one of:

- a refusal that ends in `(pass: chart/ci/<name>-values.yaml)`: the extra values file did not render (WARN, zero helm results, or zero helm results under `chart/`). Fix the values file before reverting; a values file that renders nothing is a real defect.
- a new finding in the repository's own templates, or a new upstream finding with no covering entry, from the extra pass. Fix the template or add a `.trivyignore.yaml` entry before reverting; it is a real finding.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(ci): scan only chart/ci/values.yaml in trivy_gate again, reverting the extra pass per chart/ci/*-values.yaml

## Verification

From the consumer, the `vulnerabilities` job that was red goes green on the
same head SHA. In `yadgarhq/actions`, `python3 -m pytest scripts/tests/ -q`
passes after this revert only with the test files reverted in the same commit;
the revert of yadgarhq/actions#PR does that.

## Risk

Brings back the hole yadgarhq/actions#PR closed: a chart whose own templates
render only under a second values set (`yadgarhq/platform`'s estate objects,
ADR-0787) is not misconfiguration-scanned in its own pull request.
Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Correct, yes. Less secure, yes: the gate scans less. It
touches no cluster trust boundary.
