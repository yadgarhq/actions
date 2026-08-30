# actions — the only definition of CI

Three reusable workflows and a set of shared pre-commit hooks. Every repository
in the organisation carries a caller of about eight lines and nothing else.

Decisions: [`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D62 (this repo),
D59 (pre-commit everywhere), D61 (images and digests), D15 (additive-only).

## What runs when

| Event                | Runs                          |
| -------------------- | ----------------------------- |
| `pull_request`       | everything — this is the gate |
| `push` to `main`     | the version report **only**   |
| `push` of a `v*` tag | release                       |

**Validation on push to `main` was removed, not forgotten.** It re-ran exactly
what the pull request had just passed, on code already merged, with nobody
watching the result — and the ruleset makes direct pushes impossible, so there
was no drift for it to catch.

**Nothing replaces it, deliberately.** A repository tracking `@main` does not
revalidate when a shared workflow changes or a new advisory lands until its next
pull request. A schedule would cover that and was tried and removed: it is
machinery for a problem nobody has yet, on repositories that change often enough
that the next pull request is rarely far away.

**The version report is genuinely push-only.** It reads `main`'s history since
the last tag, which a pull request cannot see.

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

## Containerfile, not Dockerfile

This project builds with podman, whose native name that is, and every builder
worth using understands both. One convention rather than two — a repository
writing `Dockerfile` gets no image, and the detection step says so rather than
skipping quietly.

`buildkit` in CI still defaults to `Dockerfile`, so the release workflow names
the file explicitly.

## The pull request template is enforced

`pull_request_template.md` here is canonical; every repository carries a copy at
`.github/pull_request_template.md`, because GitHub only reads it from there.

**`ci-pr` fails when a section is missing or empty**, and HTML comments do not
count as an answer — an unfilled template is exactly a body consisting only of
its own guidance. A template nobody fills in is a template that does not exist,
and this is the only mechanism that would notice.

Sections: **What**, **Why**, **Verification**, **Risk**. `Why` asks for the
reason rather than the mechanism, and naming a decision or open item (`D42`,
`O19`) is what makes the record worth keeping. `Risk` accepts "None" — writing it
is the point, leaving it blank is not.

The body is read through an environment variable rather than interpolated into
the script. A pull request body is attacker-controlled text, and `${{ }}` inside
a `run:` block is a shell injection — the exact class `zizmor` audits for.

## Security scanning

| Where               | What                                                                                                                             |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| every PR            | `gitleaks` (full history), `zizmor` (workflow audit), `trivy fs` (deps, misconfig, secrets), `cargo-deny` (advisories, licences) |
| every release       | `trivy image` on the published digest, plus an SBOM                                                                              |
| base images, weekly | the same, scanned hardest — a CVE there is a CVE in sixty-one images                                                             |

**The image scan targets the digest that was just pushed**, not the source tree.
The filesystem scan on a PR catches dependency problems early; only the artifact
scan sees what the base image contributed, and the artifact is what an adopter
runs.

**`ignore-unfixed` is set deliberately.** An unfixable CVE in a base image is not
something a service build can act on, and a gate nobody can satisfy gets disabled
rather than fixed. The weekly base rebuild is what picks fixes up when they land.

**`zizmor` audits the workflows themselves**, because these run in every
repository with `packages: write` and an OIDC identity — the injection and
over-permission mistakes it finds are the ones that only surface when somebody
exploits them.

## One definition of "clean": the hooks

CI installs what the hooks need and runs **all of them**. There is no `SKIP`
list and no per-tool job duplicating a hook.

An earlier design had both — hooks locally, dedicated jobs in CI, and a `SKIP`
list holding them apart. That is two definitions of the same thing kept in
agreement by hand, and both failure modes arrived within an hour: removing a job
would silently drop coverage while CI stayed green, and adding a hook broke CI
with `Executable not found`, which says nothing about the code.

It also makes D55's argument true one level up. **What a developer runs is
exactly what CI runs**, rather than something that resembles it.

Two jobs are _not_ hook-covered, and that is the line — pre-commit owns what
pre-commit can define:

| Job                       | Why not a hook                                                         |
| ------------------------- | ---------------------------------------------------------------------- |
| `workflows` (zizmor)      | audits the workflows themselves; needs the repository, not a file list |
| `vulnerabilities` (trivy) | needs a vulnerability database and network                             |

**`fetch-depth: 0` is required, not incidental.** gitleaks scans history and
`buf breaking` compares against a tag. At the default depth there is neither, and
the failure looks like a leaked secret or a broken contract rather than a shallow
clone — which is exactly how it presented the first time.

## They detect what a repository is

Nothing is configured per repository. `Cargo.toml` means the cargo stages run,
`buf.yaml` means buf, `chart/` means a chart is published, `Containerfile` means an
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

## Base images

Two, published from `containers/` (D63):

| Image                         | Contains                                              | Ships?                                       |
| ----------------------------- | ----------------------------------------------------- | -------------------------------------------- |
| `ghcr.io/yadgarhq/rust-build` | pinned toolchain, musl target, `cargo-chef`, `protoc` | no — build stage only, ~1.9 GB and discarded |
| `ghcr.io/yadgarhq/runtime`    | distroless static, non-root, CA certs                 | yes — **3.2 MB**                             |

```containerfile
FROM ghcr.io/yadgarhq/rust-build:latest AS build
COPY . .
RUN cargo build --release --target x86_64-unknown-linux-musl --bin <service>

FROM ghcr.io/yadgarhq/runtime:latest
COPY --from=build /app/target/x86_64-unknown-linux-musl/release/<service> /<service>
ENTRYPOINT ["/<service>"]
```

**`:latest`, and that is deliberate.** A toolchain bump must not mean editing
sixty-odd `Containerfile`s — the same reasoning that has workflow callers track
`@main`. A dated `:YYYY.MM.DD` tag is published alongside for anyone who needs to
reproduce an old build.

What floats is the build _input_. The output stays pinned: D61 has the parent
chart reference each service image by **digest**, so a release names exactly what
runs regardless of which base produced it.

**`runtime` exists to mirror upstream, and that is its whole job.** D61 promises
adopters that a digest a release names is never deleted, and that promise cannot
be kept while a link in the chain belongs to somebody else — this project has
already been on the receiving end of exactly that. It also removes a second
registry from every adopter's build: `gcr.io` is read once, here, and nothing
downstream touches it.

Rebuilt weekly. An image published once and never rebuilt accumulates every CVE
fixed since.

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
