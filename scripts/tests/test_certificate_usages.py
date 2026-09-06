"""LEDGER 720. The EKU wall's gate, and in particular its hand-written YAML scan.

`hooks/certificate_usages.py` is `language: script` and therefore stdlib-only —
a `language: python` hook makes pre-commit pip-install this repository, which is
not a package, and the failure lands in the CONSUMING repo where nothing
explains it. So the scan cannot use PyYAML, and a hand parser standing between a
security rule and its subject is worth more evidence than "it looked right".

THE CENTRAL TEST HERE IS THE EQUIVALENCE ONE. It runs the scan and
`yaml.safe_load_all` over the same corpus and asserts the extracted
`(name, isCA, usages)` tuples are IDENTICAL. PyYAML is available to this suite
(the `pytest-scripts` hook installs it) even though it is not available to the
hook, which is exactly the asymmetry that makes the comparison possible.

THE CORPUS REPRODUCES THE TWO TRAPS THAT ARE REAL rather than inventing hard
cases. Both were measured in `yadgarhq/deploy` on 2026-09-06:

  * A THIRTY-SEVEN LINE COMMENT BETWEEN THE KEY AND ITS FIRST ITEM.
    `infra/internal-tls/certificates.yaml` has `usages:` on line 269 and
    `- server auth` on line 306. A parser expecting the item to follow the key
    reads zero usages and refuses a CORRECT certificate — the worst failure
    available here, because it reddens honest work and gets the gate removed.
  * PROSE THAT NAMES THE FORBIDDEN VALUE. The comment above `project-db-tls`
    reads "`server auth` AND NOT `client auth`". A scan that reads comments
    finds both directions and refuses a correct certificate the other way.

WHAT IS ASSERTED IS THE REFUSAL AS WELL AS THE PASS, for the reason
`test_no_compiled_in_defaults.py` records: a suite of conforming inputs proves
the gate returns 0, and so does `true`. Both floors are exercised, because a
floor that has never been seen to fire is the same unverified claim as the gate
it protects.

The gate is run as a SUBPROCESS against a temporary tree, because its subject is
the working directory it is invoked in — which is how pre-commit invokes it in a
consumer.
"""

import subprocess
import sys
from pathlib import Path

import yaml

GATE = Path(__file__).resolve().parents[2] / "hooks" / "certificate_usages.py"

# A serving leaf whose `usages` items sit far below the key, behind a comment
# block of the kind the real file carries. Shortened from thirty-seven lines to
# eight; the parser does not count them, it only has to survive them.
SERVING_WITH_COMMENT_GAP = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: iam-tls
  namespace: yadgar
spec:
  secretName: iam-tls
  commonName: iam
  dnsNames:
    - iam
    - iam.yadgar
  duration: 2160h # 90d
  renewBefore: 726h # 30d +6h; the ladder in this file's header
  usages:
    # `server auth` AND NOT `client auth`. This module answers and never calls,
    # so a `client auth` bit here would widen the leaf for a caller that does
    # not exist — and under the ONE authority this deployment runs, the absence
    # of that bit is what stops a stolen serving certificate being replayed as
    # a client credential.
    #
    # OMITTING THIS BLOCK WOULD BE THE TRAP RATHER THAN WIDENING IT:
    # cert-manager then issues a leaf with no extended key usage at all.
    - server auth
    - digital signature
  issuerRef:
    name: yadgar-internal-ca
    kind: Issuer
"""

# The other direction, written the ordinary way.
CLIENT = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: gateway-client-tls
spec:
  secretName: gateway-client-tls
  commonName: gateway-caller
  usages:
    - client auth
    - digital signature
  issuerRef:
    name: yadgar-internal-ca
"""

# The authority. Exempt from the wall, but by a POSITIVE requirement: it must
# name neither direction.
AUTHORITY = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: yadgar-internal-ca
spec:
  isCA: true
  commonName: yadgar internal CA
  secretName: yadgar-internal-ca
  usages:
    - cert sign
    - crl sign
  issuerRef:
    name: yadgar-internal-selfsign
"""

# The edge leaf, under a DIFFERENT authority. The gate this one replaces
# exempted it by an issuer allowlist; this one judges it, and it passes.
EDGE = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: gateway-tls
spec:
  secretName: gateway-tls
  usages:
    - server auth
  issuerRef:
    name: yadgar-dev-ca
"""

# Flow style, which cert-manager accepts and nothing in the estate uses today.
FLOW = """\
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: flow-tls
spec:
  secretName: flow-tls
  usages: ["server auth", "digital signature"]
  issuerRef:
    name: yadgar-internal-ca
"""

# A document that is not a Certificate, in a file that also holds one. The
# scan must not judge it and must not count it.
NOT_A_CERTIFICATE = """\
apiVersion: v1
kind: Secret
metadata:
  name: unrelated
"""


def write(tree: Path, relative: str, *documents: str) -> Path:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n".join(documents))
    return path


def run(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE)], cwd=tree, capture_output=True, text=True
    )


def scanned(path: Path):
    """What the gate's own scan extracts, as a sorted list of tuples."""
    sys.path.insert(0, str(GATE.parent))
    try:
        import certificate_usages
    finally:
        sys.path.pop(0)
    return sorted(
        (name, is_ca, tuple(usages or ()))
        for name, is_ca, usages in certificate_usages.certificates(path)
    )


def loaded(path: Path):
    """What PyYAML extracts from the same file, in the same shape."""
    found = []
    for document in yaml.safe_load_all(path.read_text()):
        if not isinstance(document, dict) or document.get("kind") != "Certificate":
            continue
        spec = document.get("spec") or {}
        found.append(
            (
                (document.get("metadata") or {}).get("name"),
                bool(spec.get("isCA")),
                tuple(spec.get("usages") or ()),
            )
        )
    return sorted(found)


# --- the equivalence test, which is why this file exists --------------------


def test_scan_agrees_with_pyyaml_on_the_whole_corpus(tmp_path):
    """The hand parser and a real YAML parser must extract the same tuples.

    Every shape above in one file, including both traps. A disagreement here
    means the stdlib scan is not a safe stand-in for `yaml.safe_load_all` and
    the gate's design fails, whatever its verdicts happen to be.
    """
    path = write(
        tmp_path,
        "infra/all.yaml",
        SERVING_WITH_COMMENT_GAP,
        CLIENT,
        AUTHORITY,
        EDGE,
        FLOW,
        NOT_A_CERTIFICATE,
    )
    assert scanned(path) == loaded(path)


def test_the_comment_gap_does_not_swallow_the_usages(tmp_path):
    """Named separately, because reading zero usages here is a FALSE refusal."""
    path = write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP)
    assert scanned(path) == [("iam-tls", False, ("server auth", "digital signature"))]


def test_prose_naming_client_auth_is_not_read_as_a_value(tmp_path):
    """The comment says `client auth`; the certificate does not name it."""
    path = write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP)
    (name, _, usages), = scanned(path)
    assert "client auth" not in usages, name


# --- the verdicts -----------------------------------------------------------


def test_a_conforming_tree_passes_and_reports_its_count(tmp_path):
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(tmp_path, "infra/client.yaml", CLIENT, AUTHORITY)
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "inspected 4 Certificate(s), 3 of them leaves" in result.stdout


def test_both_directions_on_one_leaf_is_refused(tmp_path):
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(
        tmp_path,
        "infra/client.yaml",
        CLIENT.replace("    - client auth\n", "    - client auth\n    - server auth\n"),
        AUTHORITY,
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "gateway-client-tls" in result.stdout
    assert "names both" in result.stdout


def test_a_leaf_naming_neither_direction_is_refused(tmp_path):
    """The default cert-manager emits, and the wider of the two holes."""
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(
        tmp_path,
        "infra/client.yaml",
        CLIENT.replace("    - client auth\n", ""),
        AUTHORITY,
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "names neither" in result.stdout


def test_a_leaf_with_no_usages_block_at_all_is_refused(tmp_path):
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(
        tmp_path,
        "infra/client.yaml",
        CLIENT.replace("  usages:\n    - client auth\n    - digital signature\n", ""),
        AUTHORITY,
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "gateway-client-tls" in result.stdout


def test_the_edge_leaf_under_another_authority_is_judged(tmp_path):
    """The issuer allowlist this gate drops used to exempt it entirely."""
    write(
        tmp_path,
        "infra/certificates.yaml",
        SERVING_WITH_COMMENT_GAP,
        EDGE.replace("    - server auth\n", "    - server auth\n    - client auth\n"),
    )
    write(tmp_path, "infra/client.yaml", CLIENT, AUTHORITY)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "gateway-tls" in result.stdout


def test_an_authority_naming_a_direction_is_refused(tmp_path):
    """`isCA` is a positive requirement, not a skip. A skip would mean adding
    `isCA: true` to a serving leaf escapes the gate."""
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(
        tmp_path,
        "infra/client.yaml",
        CLIENT,
        AUTHORITY.replace("    - cert sign\n", "    - cert sign\n    - server auth\n"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "yadgar-internal-ca" in result.stdout
    assert "neither answers nor dials" in result.stdout


# --- the floors, which are the point of the whole file ----------------------


def test_a_tree_with_no_certificates_refuses(tmp_path):
    """A glob matching nothing exits 0. That is the class this gate is in."""
    write(tmp_path, "infra/unrelated.yaml", NOT_A_CERTIFICATE)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "found 0 `kind: Certificate` document(s)" in result.stdout


def test_a_tree_of_authorities_only_refuses(tmp_path):
    """Every certificate exempt as an authority means nothing was judged — the
    shape the replaced gate's issuer allowlist could reach by a rename."""
    write(
        tmp_path,
        "infra/issuer.yaml",
        AUTHORITY,
        AUTHORITY.replace("yadgar-internal-ca", "yadgar-second-ca"),
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "only 0 leaf/leaves" in result.stdout


def test_one_certificate_is_below_the_floor(tmp_path):
    """The floor is two, so a single conforming leaf still refuses."""
    write(tmp_path, "infra/certificates.yaml", EDGE)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "found 1 `kind: Certificate` document(s)" in result.stdout


# --- what it deliberately does not read -------------------------------------


def test_a_templated_file_is_skipped_and_counted(tmp_path):
    """The skip is wider than "Helm templates" and must say so out loud.

    `${{ ... }}` is GitHub Actions expression syntax, so a workflow is skipped
    by the same rule a chart template is. Neither holds a Certificate here, so
    the run passes — and reports what it did not read.
    """
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(tmp_path, "infra/client.yaml", CLIENT, AUTHORITY)
    write(
        tmp_path,
        ".github/workflows/ci.yaml",
        "on: push\njobs:\n  a:\n    steps:\n      - run: echo ${{ github.sha }}\n",
    )
    write(
        tmp_path,
        "chart/templates/service.yaml",
        "kind: Service\nmetadata:\n  name: {{ .Release.Name }}\n",
    )
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "inspected 4 Certificate(s)" in result.stdout
    assert "skipped 2 templated file(s)" in result.stdout


def test_a_templated_file_holding_a_certificate_is_refused(tmp_path):
    """The floors cannot catch this: dropping one leaf from ten still clears
    them. A certificate the gate never opened must not sit behind a pass."""
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(tmp_path, "infra/client.yaml", CLIENT, AUTHORITY)
    write(
        tmp_path,
        "chart/templates/cert.yaml",
        "kind: Certificate\nmetadata:\n  name: {{ .Release.Name }}\n"
        "spec:\n  usages:\n    - server auth\n    - client auth\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1
    assert "chart/templates/cert.yaml" in result.stdout
    assert "never read" in result.stdout


def test_a_trailing_comment_does_not_hide_a_templated_certificate(tmp_path):
    """`kind: Certificate  # serving` is legal YAML and must not be invisible.

    Anchoring the grep on `$` alone let a templated leaf carrying BOTH
    directions pass with exit 0 — this file's own defect one level down, for
    the third time. A comment after the value changes nothing about the
    document, so it must change nothing about the verdict.
    """
    write(tmp_path, "infra/certificates.yaml", SERVING_WITH_COMMENT_GAP, EDGE)
    write(tmp_path, "infra/client.yaml", CLIENT, AUTHORITY)
    write(
        tmp_path,
        "chart/templates/cert.yaml",
        "apiVersion: cert-manager.io/v1\n"
        "kind: Certificate  # serving leaf for {{ .Release.Name }}\n"
        "spec:\n  usages:\n    - server auth\n    - client auth\n",
    )
    result = run(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "chart/templates/cert.yaml" in result.stdout
    assert "never read" in result.stdout
