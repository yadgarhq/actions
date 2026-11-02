<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so the blocking
  `rust_parity` job reaches every Rust consumer the moment it merges, and it
  sits in `ci / passed`'s `needs`: a Rust version field that drifts from
  `versions.yaml` reds every Rust pull request in the org. The `main` ruleset
  forbids a direct push, so a rollback is a pull request, and GitHub's
  generated revert body carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. A `git revert` of #166 NO LONGER APPLIES: `ci-pr.yaml` moved
  after it (the `--previous` and `--base` arguments, `fetch-depth: 2`, the
  `own_sha` step). Make the partial revert BY HAND in a pull request: edit the
  `rust_parity` job of `ci-pr.yaml` only, as the SCOPE below says, and keep
  every later line. Open the pull request, then paste this file as the body in
  place of the generated line. Replace every `#PR` with the number of YOUR
  pull request. Fill in the bracketed slot under `## Why` with the signature
  you actually saw. Delete this comment. Change nothing else.

  FIRST, CHECK WHETHER A REVERT IS NEEDED AT ALL. If the red is a real drift,
  merge the rust-bump pull request that fixes it (or run the `rust-bump`
  workflow) instead of reverting. A revert is for a check that is wrong, not
  for a repository that is behind.

  SCOPE. In `ci-pr.yaml` only, the `rust_parity` job returns to warn-only:
  `--mode fail` becomes `--mode warn`, the job gets `continue-on-error: true`,
  and `rust_parity` leaves `passed.needs`. KEEP the `--previous "$PREVIOUS"
  --base "$base"` arguments, the `fetch-depth: 2` checkout, the `own_sha` step
  and the grace-window comment: they belong to #167 and stay. Nothing else
  changes: the Rust version scripts, `classes.json` and the `rust-bump`
  workflow stay as they are. Edit the test that pins the job's mode
  (`scripts/tests/test_ci_pr_rust_parity_job.py`) to match.
-->

## What

Makes the `rust_parity` job in `ci-pr.yaml` warn-only again, by hand (a plain
`git revert` of #166 does not apply any more): `--mode warn`,
`continue-on-error: true`, and not in `ci / passed`'s `needs`. The grace-window
arguments of #167 stay. A drifted Rust version field prints a warning and no
longer reds a pull request.

## Why

The blocking `rust_parity` check misbehaved.

Expected signature, one of:

- it reddened a pull request whose Rust version fields are correct (quote the
  job log and the `rust_version.py` output);
- it failed to run on a consumer, for example the sparse checkout of
  `scripts/rust_version.py` (quote the error line);
- it reddened estate-wide because `versions.yaml` moved and the bump pull
  requests had not merged yet.

Observed instead: [FILL IN - paste the failing run URL and the error line].

ADR-1120 stays accepted: this revert removes the gate, not the decision. The
rust-bump bot still opens the pull requests that keep every repository current.

## Changelog

- revert: make the rust_parity check warn-only again and drop it from ci / passed (ADR-1120)

## Verification

`ci / passed` on this revert runs the restored suite. After the merge, confirm
that a Rust pull request with a drifted `rust-version` shows a `::warning::`
in the `rust_parity` job and a green `ci / passed`.

## Risk

Low. The edit is confined to the `rust_parity` job of `ci-pr.yaml` and the
test that pins its mode. No Rust version field changes, and the grace window
still works. D80: this renders no resource and names no cloud or ingress.
The only effect is that the org stops enforcing Rust version parity at PR
time, until the check is armed again.
