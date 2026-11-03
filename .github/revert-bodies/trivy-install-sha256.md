<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`,
  so this change reaches every consumer the moment it merges. The `main`
  ruleset forbids a direct push, so a rollback is a pull request, and GitHub's
  generated revert body carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the `install trivy`
  step in `ci-pr.yaml`, `ci-release.yaml`, `base-images.yaml` and
  `estate-runner-image.yaml`, the `skip-setup-trivy: true` input on the five
  trivy-action steps, the `trivy_sha256` outputs, the `trivy` `sha256` in
  `versions.yaml` and `SHA256_REQUIRED`, and the tests for them.
-->

## What

Reverts yadgarhq/actions#PR. The five trivy-action steps run the action's own
`setup-trivy` again, which checks out trivy `main` and runs its `install.sh`.
The jobs no longer download the trivy release tarball and check its sha256.

## Why

A job that installs trivy started to fail after the merge.

Expected signature, one of:

- `sha256sum: WARNING: 1 computed checksum did NOT match` or `FAILED` in `install trivy`. Upstream re-uploaded `trivy_0.74.0_Linux-64bit.tar.gz`, or the sum in `versions.yaml` is wrong. A real mismatch is a supply-chain signal: check upstream BEFORE reverting.
- `::error::trivy sha256 from versions.yaml is not 64 lowercase hex` in `install trivy`. `detect` produced an empty `trivy_sha256`.
- `tar: trivy: Not found in archive`. The release layout changed.
- `trivy: command not found` in a scan step. The binary did not land on `PATH`.

Observed instead: [FILL IN — paste the failing job URL and the error line].

Every check fails closed by design. A failure here blocks `ci / vulnerabilities`
or `ci / passed`. It does not let an unverified binary run.

## Changelog

- revert(ci): run trivy-action's own setup-trivy again and drop the verified trivy install, reverting the yaadgaar 565 fix

## Verification

From a consumer, the job that was red runs `setup-trivy` again and goes green
on the same head SHA, if its cache holds a trivy binary. In
`yadgarhq/actions`, `python3 -m pytest scripts/tests/ -q` passes after this
revert only with the test files reverted in the same commit; the revert of
yadgarhq/actions#PR does that.

## Risk

Brings back the defect yaadgaar 565 closed: `setup-trivy` exits 6 on a cache
miss, so `ci / vulnerabilities` is red in every repository whose cache holds
no trivy binary. It also brings back an install that trusts trivy `main`
instead of a pinned sum. Record what was observed before closing.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Correct, yes. Less secure, yes: the revert removes the
digest check on a binary that runs on every pull request runner. It touches no
cluster trust boundary.
