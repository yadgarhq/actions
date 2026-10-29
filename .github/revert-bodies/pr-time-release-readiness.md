<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so this change
  reaches every consumer the moment it merges, with no adoption step. The
  `main` ruleset forbids a direct push, so a rollback is a pull request, and
  GitHub's generated revert body ("Reverts yadgarhq/actions#N") carries none of
  the five template sections and is refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the `release_ready`
  job in `ci-pr.yaml` and its entry in `passed.needs`; `next_version.py`'s
  `--pr` mode (`ready`, `repair`, `pr_main`); the comment and docstring
  pointers to it; the `ci_verdict.py` prose corrections; and the test changes
  (`test_release_ready.py`, the job lists in `test_ci_verdict.py` and
  `test_config_only.py`, the site count in `test_own_sha_checkout.py`).
-->

## What

Reverts yadgarhq/actions#PR. Pull requests no longer run the `release_ready`
job, and `ci / passed` no longer needs it. A baseline the `version` job cannot
count from is again found only after a merge.

## Why

`release_ready` turned `ci / passed` red in a consumer where it should be green.

Expected signature, one of:

- `::error::` from `next_version.py --pr` in the step `the baseline this merge would release from`, naming a stray or non-semver tag, or tags none reachable from HEAD, in a repository whose baseline is healthy.
- `::error::the readiness check is missing at .ci-actions/scripts/next_version.py`.
- `::error::job.workflow_sha is not a 40-hex commit SHA` in `release_ready`'s step `the actions commit this job runs (ADR-0981)`.
- `ci / passed` names `release_ready` as reporting a result its condition did not ask for.

Observed instead: [FILL IN — paste the failing job URL and the error line].

A real non-semver or stray baseline is NOT a reason to revert: that red is the
check working. Repair the tag (the summary names which repair) instead.

## Changelog

- revert(ci-pr): drop the PR-time release_ready baseline check, reverting yaadgaar 324

## Verification

From the consumer that was red, `ci / passed` goes green on the same head SHA
with no `release_ready` row. In `yadgarhq/actions`,
`python3 -m pytest scripts/tests/ -q` passes after this revert only with the
test files reverted in the same commit; the revert of yadgarhq/actions#PR does
that.

## Risk

Brings back what yaadgaar 324 closed: a tagged repository whose baseline cannot
be counted from merges green and misses its release silently until the
post-merge `version` job reddens. Record what was observed before closing,
because ADR-1076 still stands and the check must come back.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. It touches no trust boundary and passes no
secret.
