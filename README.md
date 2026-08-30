# actions — the only definition of CI

Three reusable workflows and a set of shared pre-commit hooks. Every repository
in the organisation carries a caller of about eight lines and nothing else.

Decisions: [`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D62 (this repo),
D59 (pre-commit everywhere), D61 (images and digests), D15 (additive-only).

## The workflows

| Workflow            | Runs on      | Does                                    |
| ------------------- | ------------ | --------------------------------------- |
| `ci-validation.yml` | called       | pre-commit, detected linters, licences  |
| `ci-pr.yml`         | pull request | calls `ci-validation`, then tests       |
| `ci-release.yml`    | tag          | image → digest → chart → sign → publish |

`ci-pr` **calls** `ci-validation` rather than repeating it. A pull request needs
both linting and tests, so two independent workflows would be two definitions of
"is this repository clean", and the one that drifts is whichever gets edited
second.

## They detect what a repository is

Nothing is configured per repository. `Cargo.toml` means the cargo stages run,
`buf.yaml` means buf, `chart/` means a chart is published, `Dockerfile` means an
image is.

The repositories are not uniform — most are Rust services, but `proto` has no
cargo at all, `docs` is prose, `deploy` and `argocd` are manifests. A flag per
repository kind would be a second configuration surface that drifts from the
first. **What detection buys is that adding the eleventh Rust module requires no
thought about CI at all.**

**Detection narrates.** Every run writes what it found and what it skipped to the
job summary. Silent magic is unmaintainable; a repository whose CI mysteriously
does nothing is the failure this guards against.

## Using it

```yaml
# .github/workflows/ci.yml in any repository
name: ci
on:
  pull_request:
  push:
    tags: ["v*"]

jobs:
  pr:
    if: github.event_name == 'pull_request'
    uses: yadgarhq/actions/.github/workflows/ci-pr.yml@main

  release:
    if: startsWith(github.ref, 'refs/tags/v')
    uses: yadgarhq/actions/.github/workflows/ci-release.yml@main
    with:
      version: ${{ github.ref_name }}
```

Require the **`ci-pr`** check in the repository's ruleset. It is a single name
that reports for every repository regardless of which stages actually ran —
without it, a repository with no `Cargo.toml` would have a required check that
never reports and pull requests that block forever.

## Third-party actions are pinned by SHA

Every `uses:` of an action outside this organisation names a **commit SHA**, with
the human-readable version in a trailing comment. A tag is a pointer the upstream
owner can move, so `@v4` is a promise about intent rather than about bytes — and
these workflows run in every repository in the organisation with `packages:
write` and an OIDC identity. That is the wrong place to accept a moving target.

The same reasoning D61 applies to container images, applied to CI: **the pin must
name what runs, not what it was called.**

One consequence worth knowing: `dtolnay/rust-toolchain` normally selects its
toolchain from the ref name (`@stable`). Pinned by SHA that information is gone,
so the toolchain is passed explicitly. Silently losing it would have meant CI
running whatever toolchain the action defaults to.

Upgrades are deliberate: resolve the new release to a SHA, update the pin and the
comment together.

## Callers track `@main`

A fix to a shared workflow reaches every repository the moment it merges, with
nothing to remember afterwards.

A moving version tag was tried first and dropped. If the discipline is "move
`v1` after every green merge" then the tag is ceremony rather than safety —
`@main` with extra steps — and it carries its own failure: fix a bug, forget to
move the tag, and the fix ships to nobody. The `@v1` form is worth its cost only
when consumers genuinely need to stay behind, which none here do.

**What makes this safe is not a tag.** It is that this repository tests its own
workflows and protects `main`, so a broken change cannot merge. That is the
handbrake; a version tag was only ever a proxy for it, and a worse one.

Third-party actions inside these workflows stay pinned by SHA. That is a
different trust decision: those tags belong to somebody else and can move without
warning, while `main` here is ours and gated.

**pre-commit is the exception, and it has to be.** `rev:` is cached at first
install and never re-resolved — _"mutable references are never updated after
first install and are not supported"_ — so a moving ref there silently freezes
each machine at whatever it meant the day it first ran, with two developers on
different hook versions while both files read the same. Hooks pin an immutable
tag and bump with `pre-commit autoupdate`.

## Shared hooks

`package.json` is not decoration. pre-commit's `node` language asserts a
`package.json` exists in the hooks repository and fails with a bare
`AssertionError` if it does not — no message, no mention of the file. Deleting it
breaks every repository's prettier hook with an error that names nothing.

`.pre-commit-hooks.yaml` publishes the hooks each repository was otherwise
copying:

```yaml
- repo: https://github.com/yadgarhq/actions
  rev: v1.0.0 # immutable — see "Callers track @main" on why hooks differ
  hooks:
    - id: cargo-fmt
    - id: cargo-clippy
    - id: cargo-deny
    - id: gitleaks
```

## Why org rulesets are not used

They require GitHub Team. Verified 2026-08-30: `orgs/yadgarhq/rulesets` returns
403, _"Upgrade to GitHub Team to enable this feature"_, while repository rulesets
work on public repositories. Reusable workflows are free and carry the larger
half; **requiring** the check is a per-repository ruleset, scripted from here
rather than applied by hand. D38 originally claimed both and has been corrected.

## Status

Early. The workflows are written but **not yet exercised end to end**, and D62
requires this repository to carry tests of its own — a bug here reaches every
repository, and nothing else in the design would catch it.
