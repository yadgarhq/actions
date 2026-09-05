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


def ids(path: str) -> dict[str, str]:
    """Map vulnerability id -> the package it was found in."""
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    found: dict[str, str] = {}
    for result in report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            found[vuln["VulnerabilityID"]] = vuln.get("PkgName", "?")
    return found


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: scan_delta.py <base-report.json> <image-report.json>", file=sys.stderr)
        return 2

    base, image = ids(sys.argv[1]), ids(sys.argv[2])
    added = sorted(set(image) - set(base))

    print(f"base carries {len(base)} findings; the built image carries {len(image)}.")

    if not added:
        print("Our layers add none. This is what the gate asserts.")
        return 0

    print(f"\nOUR LAYERS ADD {len(added)} FINDING(S) THE BASE DID NOT CARRY:\n", file=sys.stderr)
    for vuln in added:
        print(f"  {vuln}  in  {image[vuln]}", file=sys.stderr)
    print(
        "\nEach came from an instruction in containers/estate-runner/Containerfile,"
        "\nnot from the pinned base. Fix or justify it there.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
