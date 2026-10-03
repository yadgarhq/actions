#!/usr/bin/env python3
"""The `helm-lint` hook: `helm lint --strict chart`, then `helm template` it.

This was `bash -c 'helm lint --strict chart && helm template ci-render chart >
/dev/null'`. It became a script for one reason, ADR-0806: a chart that declares
the operator API versions its default render needs, in
`chart/ci/api-versions.txt`, gets each one on `helm template` as
`--api-versions`. `scripts/api_versions.py` reads the file and says why it
exists; this hook is one of the three shared gates that read it.

A CHART WITH NO DECLARATION SEES NO DIFFERENCE: the same two commands with the
same argv, `template` not run when `lint` fails, the rendered manifests
discarded, and helm's own exit status passed through. The suite pins each of
those against the old entry.

`helm lint` GETS NO API-VERSION FLAGS, and needs none. It has no
`--api-versions` on either helm major, and it grades a template's `fail` as
INFO rather than an error. Measured 2026-09-27 on helm v3.18.4 and v4.3.0 with
a chart that fails unless `keda.sh/v1alpha1` is declared: `helm lint --strict`
exits 0, a bare `helm template` exits 1, and `helm template --api-versions
keda.sh/v1alpha1` exits 0. So the render is the half that needs the
declaration.

`helm lint --strict` DOES VALIDATE EVERY SUBCHART'S `values.schema.json`
AGAINST THE COALESCED VALUES (C-A2), so a chart whose key is required with no
default refuses the bare lint exactly as it refuses the bare render.
`chart/ci/values.yaml` -- `chart_values_override.py`, the same shape as this
hook's own `api_versions.py` reader -- is passed to BOTH `helm lint --strict`
and `helm template` when it exists, for exactly that reason. A chart with no
such file sees no difference, the same backward-compatibility promise
`api-versions.txt` makes.
"""

import subprocess
import sys
from pathlib import Path

# The hook repository is checked out whole by pre-commit, so the shared reader
# is found beside this directory rather than copied into it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from api_versions import DeclarationError, api_version_flags  # noqa: E402
from chart_values_override import values_override_flags  # noqa: E402

CHART = "chart"


def helm(*args: str, discard_stdout: bool = False) -> int:
    try:
        return subprocess.run(
            ["helm", *args],
            stdout=subprocess.DEVNULL if discard_stdout else None,
            check=False,
        ).returncode
    except FileNotFoundError:
        # What `bash -c` printed and returned when helm was not installed.
        print("helm-lint: helm: command not found", file=sys.stderr)
        return 127


def main() -> int:
    try:
        flags = api_version_flags(Path(CHART))
    except DeclarationError as error:
        print(f"helm-lint: {error}", file=sys.stderr)
        return 1
    values_flags = values_override_flags(Path(CHART))
    status = helm("lint", "--strict", CHART, *values_flags)
    if status != 0:
        return status
    return helm(
        "template", "ci-render", CHART, *flags, *values_flags, discard_stdout=True
    )


if __name__ == "__main__":
    raise SystemExit(main())
