<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so the `rust_parity`
  job reaches every Rust consumer the moment it merges, and the sweeper's
  rust-bump lane judges every public repository on its next run. The `main`
  ruleset forbids a direct push, so a rollback is a pull request, and GitHub's
  generated revert body carries none of the five template sections and is
  refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  FIRST, CHECK WHETHER A REVERT IS NEEDED AT ALL. If only the propagation
  misbehaves, set `classes.json` `rust_bump.live` to false (or disable the
  `rust-bump` workflow) and close the open `rust-bump/` pull requests. That
  stops every write without touching the sweeper or ci-pr.

  SCOPE. This reverts all of yadgarhq/actions#PR: `.github/workflows/rust-bump.yaml`,
  `scripts/rust_propagate.py`, `scripts/rust_version.py`, the `Lane` in
  `scripts/repin_publish.py`, the sweeper's `rust` kind (`BOTS["rust"]`,
  `RUST_FILES`, `check_rust_lines`, `rust_pairs`, `org_rust`, `head_text`), the
  `rust` arm of the bot mints in `sweep.yaml`, the `rust_parity` job in
  `ci-pr.yaml`, `classes.json` `rust_bump`, and their tests.
-->

## What

Reverts yadgarhq/actions#PR. The sweeper again merges dependabot and repin pull
requests only, and no longer admits a `rust-bump/` pull request. The
`rust-bump` workflow and the warn-only `rust_parity` job in `ci-pr.yaml` are
deleted. The repin flow is unchanged: the `Lane` it gains in #PR has repin as
every default.

## Why

The rust-bump lane misbehaved.

Expected signature, one of:

- the sweeper merged a `rust-bump/` pull request that changed more than Rust
  version lines, or refused a correct one (quote the `evaluate` row);
- the `rust-bump` workflow opened, refreshed or closed pull requests it should
  not have (quote the run summary);
- `rust_parity` failed to run on a consumer, for example the sparse checkout
  of `scripts/rust_version.py`.

Observed instead: [FILL IN - paste the failing run URL and the error line].

`rust_parity` is warn-only, carries `continue-on-error: true` and is not in
`ci / passed`'s `needs`, so it cannot block a merge by itself. ADR-1120 stays
accepted: this revert removes the mechanism, not the decision.

## Changelog

- revert: drop the rust-bump propagation, the sweeper's rust-bump lane and the warn-only rust_parity job (ADR-1120)

## Verification

`ci / passed` on this revert runs the restored suite. After the merge, confirm
that the next `sweep` run shows no `rust-bump/` row as a merge, and close every
open `rust-bump/` pull request by hand.

## Risk

Low. The revert restores the sweeper and `ci-pr.yaml` as they were before
#PR. Rust version fields that merged rust-bump pull requests already moved stay
moved, and they hold the correct value. D80: this renders no resource and
names no cloud or ingress. The only trust-boundary change is that the sweeper
narrows back to two bot lanes.
