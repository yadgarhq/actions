#!/usr/bin/env python3
"""After a release publishes: every pin its example Applications carry is pullable.

The post-release half of ADR-0820. `example_pins.py` makes the commit tagged
`vN` pin `vN` in its examples, so no check on the push to `main` can pull that
pin: it is published only by the tag's own `chart` job. `ci-release.yaml`'s
`examples` job runs this once the pin CAN pass.

Usage: example_published.py, with REGISTRY and OWNER in the environment and the
tagged commit checked out. A repository without examples asks nothing.

Tests: `python3 -m pytest scripts/tests/ -q`.
"""

import os
import sys

from example_pins import EXAMPLES, Refusal, on_disk, pinned


def main(fetch=None, sleep=None):
    """After the release publishes: every pin the tagged examples carry is pullable.

    THE ORDERING THIS CLOSES. The commit tagged `vN` pins `vN`, which does not
    exist until the tag's own `chart` job pushes it, so a check at push time can
    only skip a pin newer than anything published. This is the check that runs
    once it CAN pass, asking the registry the way an adopter's Argo CD does —
    anonymously, through `chart_publicly_pullable`'s own request pair.
    """
    import chart_publicly_pullable as registry

    pins = pinned({path: on_disk(path) for path in EXAMPLES})
    if not pins:
        print("No example Applications pin a parent or platform chart here.")
        return 0
    host, owner = os.environ.get("REGISTRY", "").strip(), os.environ.get("OWNER", "").strip()
    if not host or not owner:
        print(f"::error::REGISTRY and OWNER are required; got {host!r} and {owner!r}.")
        return 1
    bad = []
    for path, name, version in pins:
        ok, detail, _ = registry.with_retries(
            host, f"{owner}/{registry.NAMESPACE}/{name}", version,
            fetch or registry.fetch_url, sleep or registry.time.sleep,
        )
        print(f"- `{path}` pins {name} {version}: {'pullable' if ok else detail}")
        if not ok:
            bad.append(f"{path} pins {name} {version} ({detail})")
    if bad:
        print(
            "::error::a released commit's examples pin a chart an adopter cannot "
            "pull: " + "; ".join(bad)
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Refusal as refusal:
        print(f"::error::the example pins could not be read: {refusal}")
        sys.exit(1)
