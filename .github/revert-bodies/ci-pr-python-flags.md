<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so this change reaches
  every consumer the moment it merges. The `passed` job runs `ci_verdict.py`
  with the new flags, so a flag that breaks a gate reds `ci / passed` on every
  pull request in the org. The `main` ruleset forbids a direct push, so a
  rollback is a pull request, and GitHub's generated revert body ("Reverts
  yadgarhq/actions#N") carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request (`git revert` of
  yadgarhq/actions#PR), then paste this file as the body in place of the
  generated line. Replace every `#PR` with the number of the pull request being
  reverted. Fill in the bracketed slot under `## Why` with the signature you
  actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: `-E -s` leaves the 20
  `python3 <script>` calls in `ci-pr.yaml`, and the tests go back with it.

  TESTS THE REVERT EDITS. A plain `git revert` already carries these. If
  `ci-pr.yaml` moved and you revert BY HAND, edit each one as well:
    - `scripts/tests/test_ci_pr_python_flags.py`, which asserts the flags on
      every call: since yaadgaar 655 and 656 it also reads `ci-release.yaml`,
      `seed-declaration.yaml` and `sweep.yaml`, so never delete it. Move
      `ci-pr.yaml` from `MIN_SITES` to `NOT_YET` (with this revert's number
      as the reason), and delete these six tests, which load `ci-pr.yaml`:
      `test_dropping_the_flags_at_one_site_is_caught`,
      `test_keeping_only_one_flag_is_caught`,
      `test_a_python3_after_a_pipe_or_subshell_is_still_checked`,
      `test_another_interpreter_spelling_is_caught`,
      `test_a_pythonpath_back_on_the_pre_pin_parent_gate_is_caught` and
      `test_a_variable_merely_named_after_python_is_not_caught`. The tests
      yaadgaar 656 added run on a one-step workflow built in the test, load
      no workflow file, and stay;
    - `scripts/tests/test_ci_pr_secret_scan_wiring.py`: `EXPECTED_RUN` and the
      substring in `test_the_precommit_job_runs_the_secret_scan` return to
      `python3 "$RUNNER_TEMP/secret_scan.py"`;
    - `scripts/tests/test_pr_body.py`: `test_both_readers_reach_the_gate_by_name`
      counts `python3 "$dir/pr_body.py"` again.
  Run `grep -rn -- '-E -s' scripts/tests` before you open the pull request,
  because a later test can pin the flags too.
-->

## What

Reverts yadgarhq/actions#PR. The 20 `python3 <script>` calls in `ci-pr.yaml`
run without `-E -s` again, so `PYTHON*` variables and the user site-packages
reach the gates' interpreter as they did before.

## Why

A gate failed in a consumer after the merge.

Expected signature, one of:

- `ModuleNotFoundError` or `ImportError` in a job that runs a `python3 -E -s`
  gate. A gate depended on `PYTHONPATH` or on a package in the user
  site-packages.
- A gate behaved differently because a `PYTHON*` variable it relied on (for
  example `PYTHONUTF8` or `PYTHONIOENCODING`) no longer applied.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(ci-pr): run the CI gates without `python3 -E -s` again

## Verification

`ci / passed` on this revert runs the restored suite. The
`python3 -m pytest scripts/tests/ -q` suite passes after a plain `git revert`.
After a hand revert it passes only if the three test edits in the comment above
are made in the same commit.

## Risk

Brings back the gap that yaadgaar 653 closed: a `PYTHONPATH`, `PYTHONSTARTUP` or
`PYTHONHOME` in the step env, or a module in the runner's user site-packages,
can run code inside a gate. Undo: re-apply yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. The change touches interpreter flags only. It
adds no credential and widens no permission.
