"""What `chart_publicly_pullable.py` REFUSES, pinned so it cannot quietly stop refusing.

THE GATE EXISTS BECAUSE A CHART PUBLISH CANNOT FAIL TODAY. `ci-release.yaml`'s
`chart` job packages, pushes and reports success, and that success is not itself
proof the package is pullable — the job never sets visibility, and nothing else
asks. ADR-0705 makes a package no installation can pull fatal rather than
untidy: an installation references the published chart from its own GitOps
repository, and the three Argo `Application`s already consuming OCI charts in
`yadgarhq/deploy` carry no repository Secret at all. Every installation's pull
is anonymous by design.

LEDGER 992 — THIS DOCSTRING DOES NOT CLAIM A NEW GHCR PACKAGE IS PRIVATE BY
DEFAULT. ADR-0723 measured the opposite and is the record that holds:
`yadgarhq/config` tagged `v0.1.0` on 2026-09-19, and the `charts/config` package
that push created was PUBLIC from the moment it existed. `#83` corrected
`chart_publicly_pullable.py`'s own docstring and its refusal messages, and left
this file saying the refuted thing — so a reader of the tests believed a premise
the module under test had already dropped. The gate stays for what it VERIFIES,
not for a defect it no longer has evidence of: visibility is a GitHub-side
behaviour no API sets, so this asks the registry the question an installation
asks, every release.

MOST OF THIS FILE IS RED CASES, for the reason `test_helm_pin_agrees.py` gives: a
suite that only feeds a gate conforming input certifies the fixture rather than
the gate. Every refusal the script's docstring claims has a case here that
DEMANDS it.

AND EVERY GREEN CASE IS PAIRED WITH A RED ONE ON THE SAME FIXTURE, which is
`test_d80_portability.py`'s discipline and the half easier to forget. The passing
manifest is the one `ghcr.io/yadgarhq/charts/iam:0.8.19` actually returned to an
unauthenticated client on 2026-09-19 — transcribed rather than invented, because a
fixture somebody wrote to match the parser certifies the parser against itself —
and it is fed alongside the same manifest with `layers: []`, which must redden.

THE THREE TESTS TO READ FIRST, because they are the three ways this estate's
gates have failed before:

`test_no_credential_reaches_the_registry` is the whole point of the gate. An
AUTHENTICATED pull of a private package SUCCEEDS, so a check that carried the
job's `GITHUB_TOKEN` would report success on the exact artefact the defect is
about — vacuous, and indistinguishable from working. It asserts the headers of
both requests are byte-identical whether or not `GITHUB_TOKEN`, `GH_TOKEN`,
`HELM_REGISTRY_CONFIG`, `DOCKER_CONFIG` and `HOME` are set to poison values, and
that the only `Authorization` anywhere is the bearer the ANONYMOUS token
exchange handed back.

`test_a_glob_that_matched_nothing_is_not_a_pass` is the anti-vacuity arm. Four
gates in this estate have exited 0 having inspected nothing — `if ! cmd | jq -e`,
a shallow-clone guard, an actionlint hook written as `command -v actionlint &&
actionlint || echo`, and a `one_source_per_knob.py` that reported "0 knobs across
0 files" and passed. A release that packaged no chart must redden here, not
sail through on an empty set.

`test_an_oci_index_is_pullable` is the false-refusal arm, and it is measured
rather than imagined: `ghcr.io/yadgarhq/iam:latest` answered HTTP 404
`MANIFEST_UNKNOWN` — "OCI index found, but Accept header does not support OCI
indexes" — to a request offering only the OCI manifest type. A gate this file
runs in EVERY consumer's release job; one that refuses a shape it merely failed
to ask for blocks every release in the estate.

Run: python3 -m pytest scripts/tests/ -q
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "chart_publicly_pullable.py"
CI_RELEASE = ROOT / ".github" / "workflows" / "ci-release.yaml"

sys.path.insert(0, str(ROOT / "scripts"))

import chart_publicly_pullable as gate  # noqa: E402

REGISTRY = "ghcr.io"
OWNER = "yadgarhq"
VERSION = "0.9.0"
CHART = "iam"

# WHAT AN UNAUTHENTICATED CLIENT ACTUALLY GOT BACK, verbatim from
# `ghcr.io/yadgarhq/charts/iam:0.8.19` on 2026-09-19 — this estate's own published
# chart, in the exact `<owner>/charts/<name>:<version>` layout the gate builds,
# reached with no credential of any kind. Every digest, size and annotation is
# that response's own. See the module docstring for why it is transcribed rather
# than written.
PUBLIC_MANIFEST = {
    "schemaVersion": 2,
    "config": {
        "mediaType": "application/vnd.cncf.helm.config.v1+json",
        "digest": "sha256:fa58c85a4b38a04410efdd8051c55b49cec7d474e4985fa81a1f9e2084b7879b",
        "size": 176,
    },
    "layers": [
        {
            "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
            "digest": "sha256:f03501aef6e040b5ced531032243dffeb69a06f3ecaecc8bf93f1cd2f409257b",
            "size": 25760,
        }
    ],
    "annotations": {
        "org.opencontainers.image.created": "2026-09-06T13:12:32Z",
        "org.opencontainers.image.description": (
            "The identity module's logic service — business rules, no store."
        ),
        "org.opencontainers.image.title": "iam",
        "org.opencontainers.image.version": "0.8.19",
    },
}

# The OTHER shape the registry may answer with. `manifests` rather than `layers`.
OCI_INDEX = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.index.v1+json",
    "manifests": [
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "size": 567,
        }
    ],
}

ANONYMOUS_TOKEN = "an-anonymous-bearer-token"


def package(tmp_path, *names, version=VERSION):
    """A workspace holding the tarballs `helm package` leaves behind."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (tmp_path / f"{name}-{version}.tgz").write_bytes(b"not really a chart")
    return tmp_path


def body(payload):
    return json.dumps(payload).encode()


def registry(token=None, manifests=None):
    """A stand-in for GHCR: `(fetch, seen)`.

    `token` is the `(status, payload)` the token endpoint answers with.
    `manifests` is a LIST of `(status, payload)`, consumed one per manifest
    request, so a retry case can answer differently the second time. The last
    entry repeats once the list runs out.
    """
    token = token if token is not None else (200, {"token": ANONYMOUS_TOKEN})
    manifests = list(manifests or [(200, PUBLIC_MANIFEST)])
    seen = []

    def fetch(request):
        seen.append(request)
        if "/token" in request.full_url:
            status, payload = token
        else:
            status, payload = manifests[0] if len(manifests) == 1 else manifests.pop(0)
        return status, body(payload) if not isinstance(payload, bytes) else payload

    return fetch, seen


def run(
    tmp_path,
    monkeypatch,
    fetch,
    registry_host=REGISTRY,
    owner=OWNER,
    version=VERSION,
    poison=False,
):
    """The gate in process, reading the environment the release step gives it."""
    monkeypatch.chdir(tmp_path)
    for name, value in (
        ("REGISTRY", registry_host),
        ("OWNER", owner),
        ("VERSION", version),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    if poison:
        # EVERY CREDENTIAL CHANNEL THE JOB ACTUALLY HAS, set to something a
        # leaking gate would send. `helm registry login` in the step above this
        # one writes the first; `docker/login-action` writes the second in the
        # `image` job; `urllib` reads `.netrc` only when asked, and this is what
        # proves nobody asked.
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_poison_do_not_send_this")
        monkeypatch.setenv("GH_TOKEN", "ghp_poison_do_not_send_this")
        monkeypatch.setenv("HELM_REGISTRY_CONFIG", str(tmp_path / "registry.json"))
        monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
        monkeypatch.setenv("NETRC", str(tmp_path / "netrc"))
        monkeypatch.setenv("HOME", str(tmp_path))
    slept = []
    rc = gate.main(fetch=fetch, sleep=slept.append)
    return rc, slept


# --------------------------------------------------------------------------
# The pristine input, and the same fixture mutated so it must redden.
# --------------------------------------------------------------------------


def test_a_public_chart_passes(tmp_path, monkeypatch, capsys):
    """THE COMPANION ASSERTION (ADR-0646): the real public manifest must pass.

    On its own this test also passes when the gate is dead, which is why every
    other case here is a refusal on a mutation of this same input.
    """
    fetch, seen = registry()
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"{REGISTRY}/{OWNER}/charts/{CHART}:{VERSION}" in out
    # THE COUNT IS PART OF THE VERDICT, not decoration: "it is pullable" is what
    # a gate that asked nothing also says.
    assert "1 chart" in out
    assert len(seen) == 2


def test_a_manifest_with_no_layers_is_refused(tmp_path, monkeypatch, capsys):
    """The paired red on the green fixture: HTTP 200 carrying nothing to pull.

    An empty `layers` is a 200 that proves nothing, and a gate that treats any
    200 as a pass is a gate that would accept it.
    """
    empty = dict(PUBLIC_MANIFEST, layers=[])
    fetch, _ = registry(manifests=[(200, empty)])
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    err = capsys.readouterr().err
    assert rc == 1
    assert "::error::" in err


# --------------------------------------------------------------------------
# The demanded finding: an unpullable chart must redden and name the package.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,manifests",
    [
        # WHAT WAS MEASURED for a package an anonymous client cannot see: the
        # TOKEN endpoint itself answers 403 with no token at all
        # (`ghcr.io/yadgarhq/charts/config:0.1.0`, 2026-09-19).
        ((403, {"errors": [{"code": "DENIED"}]}), None),
        ((401, {"errors": [{"code": "UNAUTHORIZED"}]}), None),
        # AND THE SHAPE THAT WAS NOT MEASURED — every chart package in the
        # organisation is already public — covered so the verdict does not depend
        # on which of the two GHCR chooses for a private-but-existing package: a
        # token is issued and the manifest is then refused.
        (None, [(401, {"errors": [{"code": "UNAUTHORIZED"}]})]),
        (None, [(403, {"errors": [{"code": "DENIED"}]})]),
    ],
)
def test_an_unpullable_chart_is_refused_and_names_the_package(
    tmp_path, monkeypatch, capsys, token, manifests
):
    """THE DEMANDED FINDING. Both refusal shapes redden, and the message is actionable.

    The fix is the operator's and cannot be automated from inside the run — there
    is no API for package visibility — so the refusal has to carry the package
    name and the page.
    """
    fetch, _ = registry(token=token, manifests=manifests)
    rc, slept = run(package(tmp_path, CHART), monkeypatch, fetch)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == 1
    assert "::error::" in captured.err
    # The package, by the name it has in the registry and on the settings page.
    assert f"{OWNER}/charts/{CHART}" in text
    assert f":{VERSION}" in text
    assert "visibility" in text.lower()
    assert f"github.com/orgs/{OWNER}/packages" in text
    # It did not give up on the first answer.
    assert slept, "a single refused answer ended the check with no retry"


def test_the_refusal_says_the_release_looks_successful(tmp_path, monkeypatch, capsys):
    """The message has to say what NOTHING ELSE would have said.

    Publishing succeeded, signing succeeded and every other step is green; the
    only broken thing is invisible until an installation syncs. A refusal that
    reads as a transient registry error teaches a re-run rather than a fix.
    """
    fetch, _ = registry(token=(403, {"errors": []}))
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    captured = capsys.readouterr()
    text = (captured.out + captured.err).lower()
    assert rc == 1
    assert "anonymous" in text
    assert "argo" in text or "installation" in text


# --------------------------------------------------------------------------
# It cannot report success having inspected nothing.
# --------------------------------------------------------------------------


def test_a_glob_that_matched_nothing_is_not_a_pass(tmp_path, monkeypatch, capsys):
    """No tarball on disk: refuse, and never ask the registry anything.

    This is the arm the four green-and-blind gates in this estate lacked. A
    release that packaged nothing has nothing for an adopter to pull, and
    `helm push ./*-$VERSION.tgz` would have been handed an unexpanded glob.
    """
    fetch, seen = registry()
    rc, _ = run(tmp_path, monkeypatch, fetch)
    captured = capsys.readouterr()
    assert rc == 1
    assert seen == [], "the gate asked the registry about a chart it never found"
    assert "::error::" in captured.err
    assert str(gate.MINIMUM_ARTIFACTS) in captured.err or "no chart" in (
        captured.out + captured.err
    ).lower()


def test_the_floor_is_a_constant_a_reader_can_find():
    """A floor of zero is not a floor. Pinned so nobody can lower it to one."""
    assert gate.MINIMUM_ARTIFACTS >= 1


def test_a_tarball_for_another_version_does_not_count(tmp_path, monkeypatch, capsys):
    """The set is the charts THIS release packaged, not whatever is lying about.

    A left-over tarball from another version is not what was just pushed, and
    counting it would let the gate report success about an artefact this run
    never published.
    """
    package(tmp_path, CHART, version="0.8.0")
    fetch, seen = registry()
    rc, _ = run(tmp_path, monkeypatch, fetch)
    assert rc == 1
    assert seen == []
    assert "::error::" in capsys.readouterr().err


def test_every_packaged_chart_is_checked(tmp_path, monkeypatch, capsys):
    """Two tarballs, two charts asked about — and one bad answer reddens the release.

    The push step pushes the whole glob, so a gate that checked only the first
    match would wave the rest through.
    """
    fetch, seen = registry(
        manifests=[(200, PUBLIC_MANIFEST), (401, {"errors": [{"code": "DENIED"}]})]
    )
    rc, _ = run(package(tmp_path, "iam", "task"), monkeypatch, fetch)
    captured = capsys.readouterr()
    assert rc == 1
    # BOTH charts were asked about by name — the second one only because the
    # first one's answer did not end the check.
    asked = {r.full_url.split("/manifests/")[0].split("/v2/")[-1] for r in seen if "/v2/" in r.full_url}
    assert asked == {f"{OWNER}/charts/iam", f"{OWNER}/charts/task"}
    assert "::error::" in captured.err
    assert f"{OWNER}/charts/task" in captured.out


# --------------------------------------------------------------------------
# Anonymous means anonymous.
# --------------------------------------------------------------------------


def test_no_credential_reaches_the_registry(tmp_path, monkeypatch, capsys):
    """THE TEST THE GATE EXISTS FOR. Poisoned credentials change nothing it sends.

    An authenticated pull of a PRIVATE package succeeds, so a check carrying the
    job's token would pass on the artefact the defect is about. The assertion is
    an EQUALITY rather than an absence: every header of both requests is
    byte-identical with the credentials set and with them unset.
    """
    clean_fetch, clean = registry()
    run(package(tmp_path / "a", CHART), monkeypatch, clean_fetch)
    capsys.readouterr()

    poisoned_fetch, poisoned = registry()
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    run(package(tmp_path / "b", CHART), monkeypatch, poisoned_fetch, poison=True)
    capsys.readouterr()

    assert [r.full_url for r in clean] == [r.full_url for r in poisoned]
    for one, other in zip(clean, poisoned):
        assert sorted(one.header_items()) == sorted(other.header_items())

    token_request, manifest_request = poisoned
    # Nothing is offered to the token endpoint. This is the request that decides
    # WHICH identity the registry answers as.
    assert token_request.get_header("Authorization") is None
    # ...and the only bearer that exists downstream is the one it handed back.
    assert manifest_request.get_header("Authorization") == f"Bearer {ANONYMOUS_TOKEN}"
    for request in poisoned:
        for header, value in request.header_items():
            assert "poison" not in str(value)
            assert header.lower() not in ("cookie", "proxy-authorization")


def test_the_gate_holds_no_credential_reading_code():
    """Read the source, not only its behaviour: no auth handler, no netrc, no config.

    The behavioural test above proves what this code sends. This one refuses the
    change that would make a future version send something else, and it is
    cheap: `urllib` acquires a credential only when a handler, a netrc or an
    opener is built for it.
    """
    source = GATE.read_text()
    for forbidden in (
        "netrc",
        "HTTPBasicAuthHandler",
        "HTTPPasswordMgr",
        "build_opener",
        "install_opener",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "docker/config.json",
        "registry_config",
    ):
        assert forbidden not in source, f"the gate mentions `{forbidden}`"


def test_the_gate_imports_only_the_standard_library():
    """It runs on a bare runner with no `pip install`, so an import must not need one.

    `ci-release.yaml`'s `chart` job installs helm and nothing else. A future
    `import yaml` here — the obvious way to read `Chart.yaml` — would redden
    every release in the estate rather than this suite, which is why the chart
    name is derived from the tarball on disk instead.
    """
    allowed = {
        "glob",
        "json",
        "os",
        "pathlib",
        "sys",
        "time",
        "urllib",
    }
    tree = ast.parse(GATE.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    assert found <= allowed, f"not on the bare-runner allowlist: {sorted(found - allowed)}"


# --------------------------------------------------------------------------
# What it asks for, and what it must not refuse for having failed to ask.
# --------------------------------------------------------------------------


def test_an_oci_index_is_pullable(tmp_path, monkeypatch, capsys):
    """MEASURED FALSE-REFUSAL ARM. An index an anonymous client received is a pass.

    `ghcr.io/yadgarhq/iam:latest` answered 404 `MANIFEST_UNKNOWN` — "OCI index
    found, but Accept header does not support OCI indexes" — to a request
    offering only the OCI manifest type. A chart is a plain manifest today; this
    arm exists so the day one is not, the gate does not refuse every release in
    the estate for a shape it declined to ask for.
    """
    fetch, _ = registry(manifests=[(200, OCI_INDEX)])
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    assert rc == 0, capsys.readouterr().out


def test_the_accept_header_offers_both_manifests_and_both_indexes(
    tmp_path, monkeypatch
):
    """The test that fails if anybody narrows `Accept` back to one media type."""
    fetch, seen = registry()
    run(package(tmp_path, CHART), monkeypatch, fetch)
    accept = seen[1].get_header("Accept") or ""
    for media_type in (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ):
        assert media_type in accept


def test_it_asks_the_registry_for_the_chart_the_push_created(tmp_path, monkeypatch):
    """The scope, the path and the tag are the ones `helm push` used.

    `helm push ./*-$VERSION.tgz oci://$REGISTRY/$OWNER/charts` publishes to
    `$OWNER/charts/<chart name>:<version>`, so anything else here would be a
    check on an artefact nobody published — green or red, it would mean nothing.
    """
    fetch, seen = registry()
    run(package(tmp_path, CHART), monkeypatch, fetch)
    token_request, manifest_request = seen
    assert f"repository:{OWNER}/charts/{CHART}:pull" in token_request.full_url
    assert token_request.full_url.startswith(f"https://{REGISTRY}/token?")
    assert (
        manifest_request.full_url
        == f"https://{REGISTRY}/v2/{OWNER}/charts/{CHART}/manifests/{VERSION}"
    )


def test_a_transient_answer_is_retried_rather_than_released(tmp_path, monkeypatch, capsys):
    """A registry that has not caught up yet must not fail a good release.

    GHCR answering 404 for a tag pushed seconds earlier is the likeliest false
    refusal in this whole design, and a false refusal here blocks every release
    in the estate.
    """
    fetch, _ = registry(
        manifests=[(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]}), (200, PUBLIC_MANIFEST)]
    )
    rc, slept = run(package(tmp_path, CHART), monkeypatch, fetch)
    assert rc == 0, capsys.readouterr().out
    assert len(slept) == 1


def test_the_retry_budget_is_finite_and_then_it_refuses(tmp_path, monkeypatch, capsys):
    """...but a registry that never answers is not a pass either. Absence is not success."""
    fetch, seen = registry(manifests=[(500, {"errors": []})])
    rc, slept = run(package(tmp_path, CHART), monkeypatch, fetch)
    assert rc == 1
    assert len(slept) == gate.ATTEMPTS - 1
    assert "::error::" in capsys.readouterr().err
    assert len(seen) == 2 * gate.ATTEMPTS


def test_an_unreachable_registry_refuses(tmp_path, monkeypatch, capsys):
    """A network error is a verdict the gate cannot make, so it refuses.

    `except` swallowing this into a pass is how a check becomes decoration.
    """

    def boom(request):
        raise gate.Unreachable("Name or service not known")

    rc, _ = run(package(tmp_path, CHART), monkeypatch, boom)
    captured = capsys.readouterr()
    assert rc == 1
    assert "::error::" in captured.err


def test_a_body_that_is_not_json_refuses(tmp_path, monkeypatch, capsys):
    fetch, _ = registry(manifests=[(200, b"<html>a proxy said something</html>")])
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    assert rc == 1
    assert "::error::" in capsys.readouterr().err


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_token_endpoint_that_cannot_answer_must_not_call_the_package_private(
    tmp_path, monkeypatch, capsys, status
):
    """A rate limit is not a visibility verdict, and must not be reported as one.

    THE ARM THAT WAS UNPINNED. Only 401 and 403 say an anonymous client is
    refused; the first version of this gate mapped EVERY non-200 from the token
    endpoint onto "this is what a PRIVATE package looks like", so a 429 produced a
    refusal asserting the package was private and sent the operator to a settings
    page that already reads Public. A gate that does that once is a gate people
    learn to click past — and the manifest arm was honest about the same status in
    the same file, so it was an inconsistency rather than a judgement.

    IT STILL REFUSES. Absence is not success; what changes is the claim, not the
    verdict.
    """
    fetch, seen = registry(token=(status, {"errors": []}))
    rc, slept = run(package(tmp_path, CHART), monkeypatch, fetch)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == 1
    assert "::error::" in captured.err
    assert f"HTTP {status}" in text
    # It says what it does not know, and claims nothing it did not measure.
    assert "private" not in text.lower()
    assert "settings" not in text.lower()
    assert f"github.com/orgs/{OWNER}/packages" not in text
    # ...and it did not stop at the first answer, nor go on to ask for a manifest
    # with a token it never got.
    assert len(slept) == gate.ATTEMPTS - 1
    assert all("/token" in request.full_url for request in seen)


def test_a_refused_token_is_still_reported_as_the_visibility_verdict(
    tmp_path, monkeypatch, capsys
):
    """The paired opposite of the case above, on the one status that IS a verdict.

    Splitting the wording is only correct if 403 keeps naming the package and the
    page. A change that made every refusal cautious would be the same defect
    pointing the other way.
    """
    fetch, _ = registry(token=(403, {"errors": [{"code": "DENIED"}]}))
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == 1
    assert "PRIVATE" in text
    assert f"github.com/orgs/{OWNER}/packages" in text


def test_a_token_endpoint_that_returns_no_token_refuses(tmp_path, monkeypatch, capsys):
    fetch, seen = registry(token=(200, {"expires_in": 300}))
    rc, _ = run(package(tmp_path, CHART), monkeypatch, fetch)
    assert rc == 1
    assert "::error::" in capsys.readouterr().err
    assert all("/token" in r.full_url for r in seen), (
        "the gate went on to ask for a manifest with no token at all"
    )


# --------------------------------------------------------------------------
# Its inputs.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["REGISTRY", "OWNER", "VERSION"])
def test_an_absent_input_refuses(tmp_path, monkeypatch, capsys, missing):
    """An empty input builds a URL that means something else. It must not be asked."""
    fetch, seen = registry()
    rc, _ = run(
        package(tmp_path, CHART),
        monkeypatch,
        fetch,
        **{
            {"REGISTRY": "registry_host", "OWNER": "owner", "VERSION": "version"}[
                missing
            ]: ""
        },
    )
    assert rc == 1
    assert seen == []
    assert "::error::" in capsys.readouterr().err


def test_a_registry_carrying_a_scheme_refuses(tmp_path, monkeypatch, capsys):
    """`https://ghcr.io` would build `https://https://ghcr.io/v2/...`.

    The workflow passes a bare host and always has; this refuses the edit that
    makes every release in the estate ask an address that does not exist.
    """
    fetch, seen = registry()
    rc, _ = run(
        package(tmp_path, CHART), monkeypatch, fetch, registry_host="https://ghcr.io"
    )
    assert rc == 1
    assert seen == []
    assert "::error::" in capsys.readouterr().err


def test_it_runs_as_a_script_and_refuses_with_no_input():
    """The file is executable by `python3 <file>` and its refusal path is real.

    Every other case here is in process, which is the only way to hand the gate a
    stubbed registry; this one is the gate as CI actually invokes it.
    """
    result = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "::error::" in result.stderr


# --------------------------------------------------------------------------
# The wiring. A gate nothing calls is a gate.
# --------------------------------------------------------------------------


def chart_job():
    return yaml.safe_load(CI_RELEASE.read_text())["jobs"]["chart"]["steps"]


def runs_the_gate(step):
    run = str(step.get("run", ""))
    return "python3" in run and GATE.name in run


def stages_the_gate(step):
    run = str(step.get("run", ""))
    return GATE.name in run and "python3" not in run


def only(steps, predicate, what):
    found = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(found) == 1, f"{len(found)} steps {what}"
    return found[0]


def test_the_release_workflow_runs_this_gate_after_the_push():
    """`ci-release.yaml`'s `chart` job must invoke it, and on the right side of the push.

    THREE POSITIONS, AND EACH ONE IS AN ARGUMENT. Before the push there is nothing
    to ask about — a gate wired in ahead of it would report on the PREVIOUS
    release for every release after the first. And the STAGING has to come before
    the push, so that a checkout flake or a missing file fails with nothing
    published rather than after.
    """
    steps = chart_job()
    pushed = only(steps, lambda s: "helm push" in str(s.get("run", "")), "push a chart")
    staged = only(steps, stages_the_gate, "stage the gate")
    verified = only(steps, runs_the_gate, "run the gate")
    assert staged < pushed < verified


def test_the_workflow_hands_the_gate_no_credential():
    """The step's own environment carries no token, which is the defect restated.

    `secrets.GITHUB_TOKEN` one step earlier is what makes `helm push` work. Given
    to this step it would make the check pass on a private package, which is
    exactly the green nobody can pull.
    """
    steps = chart_job()
    step = steps[only(steps, runs_the_gate, "run the gate")]
    rendered = str(step.get("env", {}))
    assert "secrets." not in rendered, rendered
    assert "token" not in rendered.lower(), rendered
