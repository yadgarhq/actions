<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`
  (ADR-0981 keeps that), so this change reaches every consumer the moment it
  merges, with no adoption step. The `main` ruleset forbids a direct push, so a
  rollback is a pull request, and GitHub's generated revert body ("Reverts
  yadgarhq/actions#N") carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the versions read in
  `detect` (ci-pr, ci-release) and in ci-self, base-images,
  estate-runner-image, repin, seed-declaration and `passed`; every install
  step's read of that output; the sha256 checks; the trivy, rust and python
  pins; `scripts/tool_versions.py`; the `versions.yaml` changes; and the gate
  and test changes. Every literal comes back as it was, including
  `toolchain: stable`, `python-version: "3.x"` and the four trivy-action
  steps with no `version:`.
-->

## What

Reverts yadgarhq/actions#PR. The workflows name their tool versions inline
again instead of reading `versions.yaml`. The four downloaded tools (gitleaks,
hadolint, actionlint, cargo-deny) are piped from `curl` into `tar` again, with
no sha256 check. The rust toolchain goes back to `stable`, python to `3.x`, and
the four release and image trivy-action steps go back to the action default.

## Why

A job that installs a tool started to fail in a consumer after the merge.

Expected signature, one of:

- `::error::job.workflow_sha is not a 40-hex commit SHA` in `detect`'s step `the actions commit this job runs (ADR-0981)`. Every job that reads `needs.detect.outputs.*` then skips or fails.
- `::error::` from `tool_versions.py` in the step `the tool versions this run installs (versions.yaml)`: `versions.yaml has no tools: mapping`, `each tool must be a single-line flow mapping`, `is not a dotted number`, or `cannot read .ci-versions/versions.yaml`. The sparse checkout did not fetch the two files, or `versions.yaml` changed shape.
- `sha256sum: WARNING: 1 computed checksum did NOT match` or `<file>: FAILED` in `install gitleaks`, `install hadolint`, `install actionlint` or `install cargo-deny`. Upstream re-uploaded a release asset, or the sum in `versions.yaml` is wrong. A real mismatch is a supply-chain signal: check upstream BEFORE reverting.
- `::error::<tool> sha256 from versions.yaml is not 64 lowercase hex` in an install step. `detect` produced an empty output.
- `setup-python` with `Version 3.14 was not found`, or `dtolnay/rust-toolchain` failing to install `1.99.1`. The runner cannot fetch the pinned version.
- A new trivy finding in `ci-release`'s `scan the published image`, `base-images`' `scan`, or `estate-runner-image`'s scans. These moved from trivy 0.70.0 to 0.74.0, and a newer trivy can find more. The release scan runs after the push, so the image is already published.

Observed instead: [FILL IN — paste the failing job URL and the error line].

Every check fails closed by design. The failure blocks `ci / passed` in every
consumer at once. It does not let an unverified binary run.

## Changelog

- revert(ci): name tool versions inline in the shared workflows again and drop the sha256 checks, reverting the ledger 1756 S2b versions.yaml read

## Verification

From a consumer, the job that was red installs its tools with the inline
versions and goes green on the same head SHA. In `yadgarhq/actions`,
`python3 -m pytest scripts/tests/ -q` and `python3 scripts/versions_parity.py`
pass after this revert only with the test and gate files reverted in the same
commit; the revert of yadgarhq/actions#PR does that.

## Risk

Brings back what ledger 1756 S2b and ledger 1362 closed: a tool version set in
several places by hand, a release asset run as root with no digest check, a
floating rust toolchain and python, and release scans on a trivy other than
the pull request scan's. That is the known state before yadgarhq/actions#PR.
Record what was observed before closing, because ADR-0981 still stands and the
fix must come back.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Correct, yes. Less secure, yes: the revert removes the
digest check on four binaries that run as root on every pull request runner.
It touches no cluster trust boundary.
