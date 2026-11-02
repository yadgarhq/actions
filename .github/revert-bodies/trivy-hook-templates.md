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

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the hook pass
  (`hook_pass`, `hook_manifests`, `_helm_template_args`) in `trivy_gate.py`,
  its README sentence, its tests and this file. Every refusal that existed
  before yadgarhq/actions#PR stays, on every values-file pass.
-->

## What

Reverts yadgarhq/actions#PR. `trivy_gate.py` no longer runs `helm template`.
Objects annotated `helm.sh/hook` are not scanned again, because trivy's helm
scanner never reports them. The values-file passes, their refusals and the
merge across passes do not change.

## Why

The `vulnerabilities` job went red in a consumer after the merge.

Expected signature, one of:

- a refusal that names `helm template` or `helm.sh/hook` and ends in `(pass: <values file>)`: the hook render or the hook scan failed. Run `helm template chart -f <values file>` with the `chart/ci/api-versions.txt` flags in the consumer before reverting.
- a new finding at a `chart/templates/` path that only a hook object renders. Fix the template before reverting; it is a real finding in the repository's own templates.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(ci): stop scanning helm.sh/hook objects in trivy_gate, reverting the helm template hook pass

## Verification

From the consumer, the `vulnerabilities` job that was red goes green on the
same head SHA. In `yadgarhq/actions`, `python3 -m pytest scripts/tests/ -q`
passes after this revert only with the test file reverted in the same commit;
the revert of yadgarhq/actions#PR does that.

## Risk

Brings back the hole yadgarhq/actions#PR closed: a chart's own hook templates
(`yadgarhq/platform` has seven) are never misconfiguration-scanned.
Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Correct, yes. Less secure, yes: the gate scans less. It
touches no cluster trust boundary.
