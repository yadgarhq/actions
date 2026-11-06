<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` and `ci-release.yaml` at `@main`,
  so this change reaches every consumer the moment it merges. `ci-release.yaml`
  is tag-triggered: the first run of its new flags is a consumer's next
  release, after the merge. The `main` ruleset forbids a direct push, so a
  rollback is a pull request, and GitHub's generated revert body ("Reverts
  yadgarhq/actions#N") carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request (`git revert` of
  yadgarhq/actions#PR), then paste this file as the body in place of the
  generated line. Replace every `#PR` with the number of the pull request being
  reverted. Fill in the bracketed slot under `## Why` with the signature you
  actually saw. Delete this comment. Change nothing else.

  SCOPE. This reverts the whole of yadgarhq/actions#PR: the 4 `python3` calls
  in `ci-release.yaml` and the 10 in `sweep.yaml` lose `-E -s`, the
  `pre_pin_parent` gate step in `ci-pr.yaml` sets `PYTHONPATH` again, and the
  tests go back with it.

  TESTS THE REVERT EDITS. A plain `git revert` already carries these. If the
  workflows moved and you revert BY HAND, edit each one as well:
    - `scripts/tests/test_ci_pr_python_flags.py`: since yaadgaar 656 it also
      reads `seed-declaration.yaml`, so never delete it and do not return it
      to its yaadgaar 653 form. Move `ci-release.yaml` and `sweep.yaml` from
      `MIN_SITES` to `NOT_YET` (with this revert's number as the reason), and
      delete their six rows in
      `test_dropping_the_flags_in_another_workflow_is_caught`; keep its three
      `seed-declaration.yaml` rows. This revert puts `PYTHONPATH` back on the
      `pre_pin_parent` step, which reds
      `test_no_env_sets_a_python_variable[ci-pr.yaml]`. If yaadgaar 653 is
      still applied (`ci-pr.yaml` is in `MIN_SITES`), make three edits:
      delete `test_a_pythonpath_back_on_the_pre_pin_parent_gate_is_caught`;
      in `test_a_variable_merely_named_after_python_is_not_caught`, change
      `assert python_env(w) == []` to `assert python_env(w) ==
      python_env(load())`, because that test reads the whole of `ci-pr.yaml`
      and the restored `PYTHONPATH` is now in it; and parametrize
      `test_no_env_sets_a_python_variable` over
      `sorted(set(MIN_SITES) - {"ci-pr.yaml"})` (an exclusion, never a skip:
      ADR-0650). If 653 was already reverted by hand, `ci-pr.yaml` is out of
      `MIN_SITES` and those two tests are gone, so this step does not apply;
    - `scripts/tests/test_sweep.py`: the two `run` pins in
      `test_the_workflow_takes_a_wait_input_and_waits_before_it_evaluates` and
      `test_the_follow_up_job_only_dispatches_this_workflow_live_after_act`
      return to `python3 scripts/sweep.py ...`.
  Run `grep -rn -- '-E -s' scripts/tests` before you open the pull request,
  because a later test can pin the flags too.
-->

## What

Reverts yadgarhq/actions#PR. The `python3` calls in `ci-release.yaml` and
`sweep.yaml` run without `-E -s` again, and the `pre_pin_parent` gate step in
`ci-pr.yaml` sets `PYTHONPATH: ${{ runner.temp }}` again.

## Why

A release job or a sweep job failed after the merge.

Expected signature, one of:

- `ModuleNotFoundError` or `ImportError` in the `detect`, `chart`, `parent` or
  `examples` job of `ci-release.yaml`, or in a `sweep.yaml` job. A script
  depended on `PYTHONPATH` or on a package in the user site-packages.
- A script behaved differently because a `PYTHON*` variable it relied on (for
  example `PYTHONUTF8` or `PYTHONIOENCODING`) no longer applied.
- `ModuleNotFoundError` in the `pre_pin_parent` job of `ci-pr.yaml`: the gate
  needed the `PYTHONPATH` this change deleted.

Observed instead: [FILL IN - paste the failing job URL and the error line].

## Changelog

- revert(ci): run the release and sweep scripts without `python3 -E -s` again

## Verification

`ci / passed` on this revert runs the restored suite. The
`python3 -m pytest scripts/tests/ -q` suite passes after a plain `git revert`.
After a hand revert it passes only if the test edits in the comment above are
made in the same commit.

## Risk

Brings back the gap that yaadgaar 655 closed: a `PYTHONPATH`, `PYTHONSTARTUP` or
`PYTHONHOME` in the step env, or a module in the runner's user site-packages,
can run code inside a release or sweep script. Undo: re-apply
yadgarhq/actions#PR.

**The D80 question.** If the operator ran a different ingress, a different
cloud, or a different set of operators, would this still be correct, and would
it still be secure? Yes to both. The change touches interpreter flags and one
`env:` entry only. It adds no credential and widens no permission.
