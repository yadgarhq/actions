#!/usr/bin/env python3
"""The chart this release just pushed must be pullable BY A CLIENT WITH NO CREDENTIAL.

`ci-release.yaml`'s `chart` job packages the chart, pushes it to
`oci://ghcr.io/<owner>/charts` and reports success. That success is not itself
proof the package is pullable — this job never sets visibility, and this gate is
what turns "the push succeeded" into "an installation can actually pull it".

AN EARLIER VERSION OF THIS FILE ASSUMED A NEWLY CREATED GHCR PACKAGE IS PRIVATE
BY DEFAULT, EVEN FROM A PUBLIC REPOSITORY. ADR-0723 measured the opposite and is
the record that holds: `yadgarhq/config` tagged `v0.1.0` on 2026-09-19, and the
`charts/config` package this job created was PUBLIC from the moment it existed —
this gate ran seconds later, with no credential, and passed. There was no window
for anybody to visit a settings page by hand. The organisation's seven earlier
chart packages — `charts/iam`, `charts/iam-db`, `charts/task`, `charts/task-db`,
`charts/project`, `charts/project-db` and `charts/gateway` — DID each need a
manual flip when they were first created, which is why this file believed what
it did; that is not what a public repository's push does today.

THE GATE STAYS FOR WHAT IT ACTUALLY VERIFIES, not for a defect it no longer has
evidence of. Package visibility is a GitHub-side behaviour this file does not
control and cannot read except by asking the registry, and there is no API that
sets it either: a private package's only fix lives on the package's own settings
page. Trusting today's measurement to hold on every future release would make a
release green on an assumption; this gate asks the same question an installation
asks, every time, and names the one-time manual fix on the day the answer is no
rather than assuming it never will be.

ADR-0705 IS WHAT MAKES A PRIVATE PACKAGE FATAL RATHER THAN UNTIDY. An
installation clones nothing: it references this published chart from its own
GitOps repository and an upgrade is a version bump. The three Argo
`Application`s in `yadgarhq/deploy` that already consume OCI charts —
`infra/arc.yaml`, `infra/estate-front-runner.yaml`, `infra/envoy-gateway.yaml` —
carry NO repository Secret at all, so every installation's pull is anonymous by
design. A private package therefore fails every installation at sync, with the
release that produced it green.

SAME INTENT AS THE `image` JOB'S OWN GUARD, DELIBERATELY NOT ITS MECHANISM. That
step (`ci-release.yaml`, "verify an adopter can actually pull this") asks the same
question about the image, and the message below is shaped after its message on
purpose: name the package, name the one-time manual fix, and say that the release
looks successful without it. What is not reused is HOW it asks. It runs `docker
manifest inspect` in a job that logged in to the registry at its first step, so
docker answers it from the credentials on disk — an authenticated pull, which
succeeds against a private package. This gate asks the question the way an
installation does, or it is asking nothing.

ANONYMOUS MEANS ANONYMOUS, AND IT IS A PROPERTY OF THE REQUEST. The registry is
asked for a pull token with NOTHING offered — no header, no configuration file,
no environment — and the bearer it hands back is the only credential that exists
downstream. `scripts/tests/test_chart_publicly_pullable.py` asserts that as an
EQUALITY: every header of both requests is byte-identical whether or not the
job's tokens and the helm and docker configuration paths are set to poison
values. An absence is easy to write and easy to lose; an equality fails the day
somebody adds one.

WHY THE REGISTRY API AND NOT `helm show chart`. helm reads its own registry
configuration, which the push step one line earlier writes, so a helm-based check
would have to be talked out of a credential it goes looking for — the same shape
as the image guard's mistake. Two HTTP requests carry no credential unless the
code puts one in them, and that is a property a test can pin.

WHAT IT REFUSES, and every one of these has a case in the suite that demands it:

- an anonymous pull that is refused, at the token exchange OR at the manifest.
  Both shapes, because GHCR blurs "private" and "does not exist" deliberately and
  the one measured for an ABSENT package was 403 at the token endpoint. What a
  private-but-EXISTING chart package answers was not measured — every chart
  package in this organisation is already public — so the verdict must not depend
  on which of the two GHCR chooses.
- a 200 carrying nothing to pull: no layers and no child manifests.
- a body that is not JSON, a 429, a 5xx, or a registry that cannot be reached at
  all. Absence is not success; a gate that cannot get an answer has not got a good
  one. THOSE REFUSALS SAY UNKNOWN RATHER THAN PRIVATE, and they carry no settings
  link: only 401 and 403 are a visibility verdict, and telling an operator to make
  a package public over a rate limit — on a page that already reads Public — is
  how a gate becomes something people learn to ignore.
- NOTHING TO INSPECT. No `*-<version>.tgz` on disk means this release packaged no
  chart, and a glob that matched nothing is not a pass. Four gates in this estate
  have reported success having inspected nothing; `MINIMUM_ARTIFACTS` is the same
  floor `no_build_cache.py` keeps for the same reason.

WHAT IT MUST NOT REFUSE, which matters as much: this file runs in the release job
of every repository that ships a chart, so a gate that refuses wrongly blocks
every release in the estate.

- AN OCI INDEX. Measured 2026-09-19: `ghcr.io/yadgarhq/iam:latest` answered HTTP
  404 `MANIFEST_UNKNOWN` — "OCI index found, but Accept header does not support
  OCI indexes" — to a request offering only the OCI manifest type. A chart is a
  plain manifest today; a gate that asked for one shape only would refuse the day
  that changed, for a reason that has nothing to do with visibility. All four
  types are offered, and an index an anonymous client RECEIVED is a pass.
- A REGISTRY THAT HAS NOT CAUGHT UP. The push finished seconds ago, so every
  outcome is retried up to `ATTEMPTS` times with `DELAY_SECONDS` between them,
  refusals included. That costs a genuinely private package about ten seconds and
  buys a good release the margin it needs.

THE CHART NAME COMES OFF THE TARBALL ON DISK, not out of `chart/Chart.yaml`. Two
reasons, and the second is the load-bearing one. It is what was actually
packaged, so the gate asks about the artefact this run published rather than
about what the source tree says it meant to. And reading YAML would mean PyYAML
on a runner where the `chart` job installs helm and nothing else — an import that
would redden every release in the estate rather than a test here. This file
imports the standard library only, and the suite pins that.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The namespace `helm push ./*-$VERSION.tgz oci://$REGISTRY/$OWNER/charts`
# publishes into. The images sit at the top level and would collide with it (D61).
NAMESPACE = "charts"

# A floor rather than a count. Zero artefacts is a check that inspected nothing,
# which is the failure this estate has now measured four times over.
MINIMUM_ARTIFACTS = 1

# The push finished seconds ago. Everything is retried, refusals included — see
# the header for why a false refusal here is worse than a slow true one.
ATTEMPTS = 5
DELAY_SECONDS = 3
TIMEOUT_SECONDS = 30

# ALL FOUR SHAPES, and the header explains what was measured when only one was
# offered. An index is a legitimate answer to ask for, not a shape to refuse.
ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

USER_AGENT = "yadgarhq-actions/chart_publicly_pullable"


class Refused(Exception):
    """The gate has a verdict and it is no."""


class Unreachable(Exception):
    """The gate could not get an answer at all. Retried, then refused."""


def token_request(registry, repository):
    """The pull-token exchange, with NOTHING offered.

    This request is what decides which identity the registry answers as for the
    manifest request below, so it is the one that must stay bare. There is no
    `Authorization` header here and there must never be one.
    """
    # `safe=":/"`, so the scope reads `repository:<owner>/charts/<name>:pull` the
    # way the measurement that established this endpoint's behaviour sent it. A
    # percent-encoded scope is probably accepted too; probably is not a thing to
    # build a release gate on.
    query = urllib.parse.urlencode(
        {"service": registry, "scope": f"repository:{repository}:pull"}, safe=":/"
    )
    return urllib.request.Request(
        f"https://{registry}/token?{query}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )


def manifest_request(registry, repository, tag, token):
    """The manifest, carrying the anonymously obtained bearer and nothing else."""
    return urllib.request.Request(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={
            "Accept": ACCEPT,
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )


def fetch_url(request):
    """GET, as `(status, body)`. An HTTP error is an ANSWER here, not a crash.

    401 and 403 are the whole subject of this gate, so they must arrive as values
    to reason about rather than as an exception somebody wraps in a bare `except`.
    """
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b""
    except urllib.error.URLError as error:
        raise Unreachable(str(error.reason)) from error
    except OSError as error:  # a reset connection never becomes a URLError
        raise Unreachable(str(error)) from error


def parse(body, what):
    payload = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError(f"the {what} is not a JSON object")
    return payload


def anonymous_pull(registry, repository, tag, fetch):
    """`(ok, detail, visibility)` for one artefact, as an unauthenticated client sees it.

    `visibility` IS THE DIFFERENCE BETWEEN A VERDICT AND A SHRUG, and only 401 and
    403 earn it. Every other unhappy answer — 429, a 5xx, a body that is not JSON,
    a registry that cannot be reached — refuses too, because absence is not
    success, but it says UNKNOWN rather than PRIVATE. Sending an operator to a
    settings page that already reads Public, on the strength of a rate limit, is
    how a gate teaches people to ignore it.
    """
    try:
        status, body = fetch(token_request(registry, repository))
    except Unreachable as error:
        return False, f"the token endpoint could not be reached: {error}", False
    if status in (401, 403):
        return (
            False,
            (
                f"the registry refused an anonymous pull token (HTTP {status}). "
                "This is what a PRIVATE package looks like: GHCR answers the same "
                "way for a package that does not exist, deliberately."
            ),
            True,
        )
    if status != 200:
        return (
            False,
            (
                f"the token endpoint answered HTTP {status}, which is no answer. "
                "It says nothing either way about who can pull this package."
            ),
            False,
        )
    try:
        token = parse(body, "token response").get("token")
    except (ValueError, json.JSONDecodeError) as error:
        return False, f"the token response could not be read: {error}", False
    if not token:
        return False, "the registry issued no anonymous pull token at all", False

    try:
        status, body = fetch(manifest_request(registry, repository, tag, token))
    except Unreachable as error:
        return False, f"the manifest could not be reached: {error}", False
    if status in (401, 403):
        return (
            False,
            (
                f"an anonymous pull is refused (HTTP {status}). The package is "
                "PRIVATE."
            ),
            True,
        )
    if status == 404:
        return (
            False,
            (
                "an anonymous client is told there is no such manifest (HTTP "
                "404). For a tag pushed seconds ago this can be the registry "
                "catching up; it was retried."
            ),
            False,
        )
    if status != 200:
        return (
            False,
            f"the registry answered HTTP {status}, which is no answer",
            False,
        )
    try:
        manifest = parse(body, "manifest")
    except (ValueError, json.JSONDecodeError) as error:
        return False, f"the manifest could not be read: {error}", False

    layers = manifest.get("layers")
    children = manifest.get("manifests")
    if isinstance(layers, list) and layers:
        return True, f"{len(layers)} layer(s)", False
    if isinstance(children, list) and children:
        # An index an anonymous client RECEIVED is pullable: its children live
        # under the scope this same token was issued for.
        return True, f"an index of {len(children)} manifest(s)", False
    return (
        False,
        (
            "the registry answered 200 with a manifest carrying neither layers "
            "nor child manifests, so there is nothing for an installation to pull"
        ),
        False,
    )


def with_retries(registry, repository, tag, fetch, sleep):
    """`anonymous_pull` until it passes or the budget runs out."""
    detail, visibility = "the check never ran", False
    for attempt in range(ATTEMPTS):
        ok, detail, visibility = anonymous_pull(registry, repository, tag, fetch)
        if ok:
            return True, detail, visibility
        if attempt + 1 < ATTEMPTS:
            sleep(DELAY_SECONDS)
    return False, detail, visibility


def packaged_charts(version):
    """`(name, filename)` for every tarball `helm package` left in this workspace.

    THE SAME GLOB THE PUSH STEP USES, so the set asked about is the set
    published. Non-recursive on purpose: the second checkout this gate is staged
    from is a sibling directory, not a source of artefacts.
    """
    suffix = f"-{version}.tgz"
    found = []
    for filename in sorted(glob.glob(f"*{suffix}")):
        name = filename[: -len(suffix)]
        if name:
            found.append((name, filename))
    return found


def settings_url(owner, repository):
    """Where the one-time manual fix lives. There is no API for it."""
    package = urllib.parse.quote(repository.split("/", 1)[1], safe="")
    return f"https://github.com/orgs/{owner}/packages/container/{package}/settings"


def refuse(lines, private):
    """Print the operator's text and redden, claiming only what was established."""
    for line in lines:
        print(line)
    if private:
        message = (
            "::error::The published chart is not anonymously pullable. See the "
            "job summary."
        )
    else:
        message = (
            "::error::Whether the published chart is anonymously pullable could "
            "not be established. See the job summary."
        )
    print(message, file=sys.stderr)
    return 1


def main(fetch=None, sleep=None):
    fetch = fetch or fetch_url
    sleep = sleep or time.sleep
    environment = os.environ

    registry = (environment.get("REGISTRY") or "").strip()
    owner = (environment.get("OWNER") or "").strip()
    version = (environment.get("VERSION") or "").strip()
    for name, value in (("REGISTRY", registry), ("OWNER", owner), ("VERSION", version)):
        if not value:
            print(
                f"::error::{name} is empty, so this gate would ask the registry "
                "about an artefact nobody published. Absence is not success.",
                file=sys.stderr,
            )
            return 1
    if "/" in registry or " " in registry:
        print(
            f"::error::REGISTRY is `{registry}`; this wants a bare host such as "
            "`ghcr.io`, because the scheme and the path are built here.",
            file=sys.stderr,
        )
        return 1

    charts = packaged_charts(version)
    if len(charts) < MINIMUM_ARTIFACTS:
        print(
            f"::error::found {len(charts)} packaged chart(s) matching "
            f"`*-{version}.tgz`, and {MINIMUM_ARTIFACTS} is the fewest this can "
            "inspect. `helm push` was handed the same glob, so either this "
            "release published no chart or it published something this gate "
            "cannot name — and a glob that matched nothing is not a pass. This "
            "gate exists to verify a published chart is anonymously pullable "
            "(ADR-0705); reporting success here having inspected nothing is the "
            "failure it was written to stop.",
            file=sys.stderr,
        )
        return 1

    bad = []
    for name, filename in charts:
        repository = f"{owner}/{NAMESPACE}/{name}"
        reference = f"{registry}/{repository}:{version}"
        ok, detail, visibility = with_retries(
            registry, repository, version, fetch, sleep
        )
        if ok:
            print(f"- `{reference}` — anonymous pull works ({detail}), from `{filename}`.")
        else:
            bad.append((reference, repository, detail, visibility))

    if not bad:
        print(
            f"\n{len(charts)} chart(s) published by this release, every one of "
            "them pullable by a client holding no credential at all."
        )
        return 0

    # TWO KINDS OF BAD ANSWER, RENDERED APART. The manual fix is only the right
    # instruction for the one the registry actually gave a verdict on.
    private = [row for row in bad if row[3]]
    undecided = [row for row in bad if not row[3]]

    lines = []
    if private:
        lines += ["### Published, but NOT anonymously pullable", ""]
        for reference, repository, detail, _ in private:
            lines += [
                f"`{reference}` was packaged and pushed, and {detail}",
                "",
                "This package is private, and no API can change that — it is a "
                "one-time manual step per package:",
                "",
                f"  {settings_url(owner, repository)}",
                "",
                "Danger Zone -> Change visibility -> Public.",
                "",
            ]
        lines += [
            "NOTHING ELSE WOULD HAVE SAID SO. The package was published and this "
            "release is otherwise green. ADR-0705 has an installation REFERENCE "
            "this chart from its own GitOps repository, and the Argo "
            "`Application`s that consume OCI charts in this estate carry no "
            "repository Secret — so every installation's pull is anonymous, and a "
            "private package fails each one at sync with nothing upstream "
            "reporting a problem.",
            "",
            "This check carried no credential, deliberately: an authenticated "
            "pull of a private package succeeds and would have reported success.",
            "",
        ]
    if undecided:
        lines += ["### Nobody could establish whether this chart is pullable", ""]
        for reference, _, detail, _ in undecided:
            lines += [f"`{reference}` was packaged and pushed, and {detail}", ""]
        lines += [
            f"THIS IS NOT A VISIBILITY VERDICT, after {ATTEMPTS} attempts. Only "
            "HTTP 401 and 403 say an anonymous client is refused, and nothing "
            "above says that happened here — so there is no package setting to "
            "change on the strength of it. Re-run the job. If the registry "
            "answers the same way again, the registry is the thing to look at. "
            "This still refuses, because a gate that cannot get an answer has not "
            "got a good one.",
        ]
    return refuse(lines, bool(private))


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Refused as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
