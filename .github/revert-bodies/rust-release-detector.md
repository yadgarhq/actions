<!--
  A PRE-WRITTEN REVERT BODY, required by ADR-0583.

  WHY IT EXISTS. Consumers call `ci-pr.yaml` at `@main`, so the grace window
  in the `rust_parity` job reaches every Rust consumer the moment it merges,
  and the sweeper's `rust-release` lane judges actions pull requests on its
  next run. The `main` ruleset forbids a direct push, so a rollback is a pull
  request, and GitHub's generated revert body carries none of the five
  template sections and is refused by `ci / template`.

  HOW TO USE IT. Open the revert pull request, then paste this file as the body
  in place of the generated line. Replace every `#PR` with the number of the
  pull request being reverted. Fill in the bracketed slot under `## Why` with
  the signature you actually saw. Delete this comment. Change nothing else.

  FIRST, CHECK WHETHER A REVERT IS NEEDED AT ALL. If only the detector
  misbehaves, set `classes.json` `rust_bump.release_live` to false and close
  the open `rust-release/` pull request. That stops every detector write
  without touching ci-pr or the sweeper.

  DO NOT REVERT WHILE `versions.yaml` CARRIES `previous` unless you also
  delete that key in the same pull request: the restored `tool_versions.py`
  ignores it, but nothing would then remove it, and the restored
  `rust_parity` refuses the old version at once, so every consumer whose
  rust-bump is still open reds `ci / passed`.

  SCOPE. This reverts all of yadgarhq/actions#PR: `scripts/rust_release.py`
  and the `upstream` job in `rust-bump.yaml`; `all_current` and the workspace
  filter in `scripts/rust_propagate.py`; the workspace filter and the grace
  window in `scripts/rust_version.py`; `previous` in `scripts/tool_versions.py`;
  in `ci-pr.yaml`, the `rust_toolchain_previous` output of `detect` and the
  `--previous`/`--base` arguments and `fetch-depth: 2` of `rust_parity`; the
  sweeper's `rust-release` kind (`BOTS["rust-release"]`, `RELEASE_FILES`,
  `check_release`, `main_text`, `release_index_ok`) and its arm of the bot
  mints in `sweep.yaml`; `classes.json` `rust_bump.release_live`; the comment
  rewordings in `versions.yaml` and the estate runner Containerfile; and their
  tests.
-->

## What

Reverts yadgarhq/actions#PR. The Rust release detector is deleted, the sweeper
no longer admits a `rust-release/` pull request, and `rust_parity` again
refuses any Rust version field that is not exactly `versions.yaml`
rust-toolchain. `rust_version.py` again reads every git-listed `Cargo.toml`.

## Why

The detector, its sweeper lane or the grace window misbehaved.

Expected signature, one of:

- the sweeper merged a `rust-release/` pull request that changed more than the
  three version lines, or refused a correct one (quote the `evaluate` row);
- the `upstream` job of `rust-bump` opened, refreshed or closed pull requests
  it should not have (quote the run summary);
- `rust_parity` passed a field at the old version that the pull request itself
  moved, or failed to resolve the merge base.

Observed instead: [FILL IN - paste the failing run URL and the error line].

ADR-1120 stays accepted: this revert removes the mechanism, not the decision.
A person moves `versions.yaml` and the images by hand again.

## Changelog

- revert: drop the Rust release detector, its sweeper lane and the rust_parity grace window (ADR-1120)

## Verification

`ci / passed` on this revert runs the restored suite. After the merge, confirm
that `versions.yaml` carries no `previous`, that the next `sweep` run shows no
`rust-release/` row as a merge, and close every open `rust-release/` pull
request by hand.

## Risk

Low once `previous` is gone. The revert restores `ci-pr.yaml` and the sweeper
as they were before #PR. A fixture or vendored `Cargo.toml` with its own
`rust-version` reds `rust_parity` again, as it did before. D80: this renders
no resource and names no cloud or ingress. The only trust-boundary change is
that the sweeper narrows back to three bot lanes.
