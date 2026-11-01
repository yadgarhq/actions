<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so the `sizes` job
  reaches every Rust consumer the moment it merges. The `main` ruleset forbids a
  direct push, so a rollback is a pull request, and GitHub's generated revert
  body carries none of the five template sections and is refused by
  `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the `sizes` job in `ci-pr.yaml`
  (`scripts/tests/test_ci_pr_sizes_job.py` goes with it). It does NOT revert the
  `--report` flag in `hooks/complexity.py`, which is inert unless passed and is
  covered by `scripts/tests/test_complexity.py`; the other parts of
  yadgarhq/actions#PR (the hook and gate widenings) are separate reverts if
  they are the cause.
-->

## What

Reverts the `sizes` job of yadgarhq/actions#PR. Pull requests no longer print
the sizes the `complexity` gate measured into the job summary. The gate itself,
in `precommit`, is unchanged.

## Why

The `sizes` job disturbed a pull request run.

Expected signature, one of:

- a run that is red or stuck on the `sizes` job in a Rust consumer;
- `::error::job.workflow_sha is not a 40-hex commit SHA` from the `sizes` job;
- a runner minute cost on large pull requests that the operator rejects.

Observed instead: [FILL IN - paste the failing job URL and the error line].

`sizes` is not in `ci / passed`'s `needs` and carries `continue-on-error: true`,
so it cannot turn `ci / passed` or the workflow run red. A revert for a red
`sizes` is for the noise, not for a blocked merge.

## Changelog

- revert(ci-pr): drop the sizes job that printed the complexity gate's measurements, reverting yaadgaar 260

## Verification

`ci / passed` goes green on the same head SHA with no `sizes` row.
`python3 -m pytest scripts/tests/ -q` passes with `test_ci_pr_sizes_job.py`
removed in the same commit.

## Risk

Brings back what yaadgaar 260 closed: a refactor pull request has no printed
size to quote and falls back to a hand count by another rule (task#51,
project#5, task-db#59). No consumer behaviour changes otherwise.

Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. It touches no trust boundary and passes no
secret.
