#!/usr/bin/env python3
"""Fail only when OUR layers add a vulnerability the pinned base did not carry.

WHY THIS EXISTS RATHER THAN A PLAIN `exit-code: 1`. `containers/estate-runner`
builds on `ghcr.io/actions/actions-runner`, GitHub's own published runner. That
base carries 122 CRITICAL/HIGH fixed-available findings on its own — measured
2026-09-05 with trivy 0.72.0 under the same `--severity CRITICAL,HIGH
--ignore-unfixed` this repository gates with. NONE of them come from the Ubuntu
apt layer: they are bundled Go binaries (dockerd, containerd, containerd-shim,
ctr, docker, docker-proxy, runc, docker-buildx) and a bundled Node.js tree that
the runner needs to execute `node20` actions such as actions/checkout. We cannot
fix them without forking upstream, and the count is identical before and after
our layers — our build contributes ZERO.

So a plain gate would be red forever, which is the same as no gate. The two easy
escapes are both refused for reasons this repository has already written down:
suppressing the individual CVEs "accepts findings that were never about us, and
the list rots as the toolchain moves" (the .trivyignore header), and raising
`exit-code` or the severity floor is the threshold "quietly raised until nothing
reports" that the same header names.

WHAT THIS GATES INSTEAD, and it is a real property: the delta. If our
Containerfile installs a package with a new CRITICAL/HIGH, this fails and names
it. If upstream's own count moves, this stays green and says so — that is
upstream's to fix, and pretending otherwise would make the gate dishonest rather
than strict.

WHAT IT DELIBERATELY DOES NOT CLAIM. It does not say the image is free of known
vulnerabilities; it plainly is not. The containment argument is separate and is
enforced elsewhere: the runner sits behind a NetworkPolicy and `yadgarhq/estate`
pins that as contract C-18, "nothing inside the cluster is reachable from the
front door runner", which passes on every smoke run. If C-18 is ever removed,
this gate's reasoning has to be revisited with it.
"""

import json
import sys


def findings(path: str) -> dict[tuple[str, str, str], str]:
    """Map (target type, package, vulnerability id) -> a human label.

    KEYED ON THE TRIPLE, NOT THE ID ALONE. One CVE id can appear against several
    packages — in the pinned base, two of its ids do — so keying on the id alone
    lets our layers introduce that same id in a DIFFERENT package and have it
    read as pre-existing. Measured: appending `CVE-2026-39821` against
    `libssl-dev` to an image report went unnoticed while the base carried it only
    in a Go binary.
    """
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    found: dict[tuple[str, str, str], str] = {}
    for result in report.get("Results") or []:
        kind = result.get("Type", "?")
        for vuln in result.get("Vulnerabilities") or []:
            pkg = vuln.get("PkgName", "?")
            found[(kind, pkg, vuln["VulnerabilityID"])] = f"{vuln['VulnerabilityID']}  in  {pkg} ({kind})"
    return found


def artifact(path: str) -> str:
    """What the report says it scanned, so a scan aimed elsewhere is visible."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("ArtifactName", "(no ArtifactName)")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: scan_delta.py <base-report.json> <image-report.json>", file=sys.stderr)
        return 2

    base, image = findings(sys.argv[1]), findings(sys.argv[2])

    print(f"base   {artifact(sys.argv[1])}: {len(base)} finding(s)")
    print(f"image  {artifact(sys.argv[2])}: {len(image)} finding(s)")

    # THE NON-DEGENERACY FLOOR, AND IT IS THE WHOLE DIFFERENCE BETWEEN A GATE AND
    # A DECORATION. A comparison is vacuously satisfied by an empty right-hand
    # side: an image report with no findings makes `image - base` empty and the
    # gate green. But an image built FROM this base cannot carry fewer findings
    # than nothing — a drop to zero means the scan did not scan the image, not
    # that the image is clean. Measured before this check existed: `{"Results":
    # []}`, `{}` and `{"Results":[{"Vulnerabilities":null}]}` all reported "our
    # layers add none" against a 39-finding base.
    #
    # It is deliberately NOT `base <= image`. `apt-get install` can legitimately
    # upgrade a package the base shipped and retire one of the base's own
    # findings, so a subset assertion would go red on an improvement.
    if base and not image:
        print(
            f"\nTHE IMAGE REPORT IS EMPTY WHILE THE BASE CARRIES {len(base)}.\n"
            "An image built from that base cannot carry none, so the scan did not\n"
            "scan the image. Check the image-ref and that the scan step ran.",
            file=sys.stderr,
        )
        return 1

    added = sorted(image[k] for k in set(image) - set(base))

    if not added:
        print("Our layers add none. This is what the gate asserts.")
        return 0

    print(f"\nOUR LAYERS ADD {len(added)} FINDING(S) THE BASE DID NOT CARRY:\n", file=sys.stderr)
    for line in added:
        print(f"  {line}", file=sys.stderr)
    print(
        "\nEach came from an instruction in containers/estate-runner/Containerfile,"
        "\nnot from the pinned base. Fix or justify it there.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
