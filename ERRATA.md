# Errata

Releases that broke an unchanged consumer without a `!` (breaking) Changelog
bullet, so they shipped as a minor or patch release. Tags are never rewritten.
A stricter gate (a new blocking job, or a hook that refuses more) is a minor
change by rule and is not listed here. Rule: ADR-1258.

## v1.7.0 (2026-09-03)

Should have been a major release. CI fails a repository that has a `proto/`
directory and no `PROTO_VERSION`.

## v1.3.0 (2026-08-30)

Should have been a major release. The aggregate check `ci-pr` was renamed to
`ci / passed`, so a ruleset that requires `ci-pr` waits forever.

## v1.2.0 (2026-08-30)

Should have been a major release. Image detection reads `Containerfile` only,
so a repository with a `Dockerfile` gets no image.
