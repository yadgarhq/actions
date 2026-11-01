<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`,
  so this change reaches every consumer the moment it merges. The `main`
  ruleset forbids a direct push, so a rollback is a pull request, and GitHub's
  generated revert body ("Reverts yadgarhq/actions#N") carries none of the five
  template sections and is refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the job-level
  `timeout-minutes` and the bounded `install protoc` step in `ci-pr.yaml`; the
  `revision` and `version` labels and annotations and the split, retried
  `package` and `push` chart steps in `ci-release.yaml`; the `tag: null` in
  `scripts/d80_portability.py`; and the test changes.
-->

## What

Reverts yadgarhq/actions#PR. The `ci-pr.yaml` jobs carry no `timeout-minutes`
again and `install protoc` runs one unretried `apt-get` pair. The image build in
`ci-release.yaml` sets only the `source` label. The chart job runs one
`package and push` step with no retry. The D80 release render drops the image
tag with `pop` again.

## Why

A job failed or timed out in a consumer after the merge.

Expected signature, one of:

- A job cancelled with "The job running on runner ... exceeded the maximum execution time" at 30 or 45 minutes. A ceiling was set below a real run.
- `install protoc` red after 5 minutes on a slow mirror.
- A release red at the `package` or `push` chart step, or the build step refusing the `annotations:` input.
- The `portability` job red on a chart that prefers the tag over the digest.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(actions): drop the CI job timeouts, the protoc and chart publish retries, the image revision labels and the null image tag in the D80 render

## Verification

A consumer `ci / passed` run and a module release reach green. The
`python3 -m pytest scripts/tests/ -q` suite passes after this revert only if
`scripts/tests/test_ci_workflow_hardening.py` is deleted and the new case in
`scripts/tests/test_d80_portability.py` is removed in the same commit. Those
tests assert the wiring this revert removes. Say so here.

## Risk

Brings back the 360-minute default job ceiling, the unretried protoc install
(yaadgaar 320), the unretried chart publish that tagged v0.10.3 with no artefact
(yaadgaar 357), and images that cannot be mapped to a commit. Undo: re-apply
yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. The change adds ceilings, retries and
metadata. It adds no credential and widens no permission.
