"""The operator API versions a chart declares its default render needs (ADR-0806).

WHY THIS EXISTS. The estate's charts refuse, at render time, a resource whose
kind the target cluster does not have: `require-api` asks
`.Capabilities.APIVersions.Has` and `fail`s with the name of the operator to
install. Against a real cluster helm fills `.Capabilities.APIVersions` from
discovery. OFFLINE it fills it with the built-in groups and nothing else, so a
chart whose DEFAULTS turn on a KEDA `ScaledObject` or a MariaDB `Database` does
not render in any shared gate unless the gate passes `--api-versions`. Measured
2026-09-27 on the parent chart with the whole-estate defaults of ADR-0803 step
B6: a bare render refuses at `task`'s KEDA check, and each of the four entries
below is individually required on helm v3.18.4.

ONE DECLARATION PER CHART REPOSITORY, READ BY EVERY SHARED GATE THAT RENDERS THE
CHART OFFLINE: the `helm-lint` hook, `d80_portability.py` and
`service_immutable.py`. One reader rather than a flag list per gate, so the
gates cannot drift apart on what the chart is rendered against.

THE FILE is `chart/ci/api-versions.txt` — `ci/` because helm's own convention
already uses that directory for "what this chart is exercised under", and inside
the chart so a chart archived at another revision (`service_immutable.py`'s base
side) carries the declaration it had then. One `group/version` per line, as
`--api-versions` takes it. `#` starts a comment, to the end of the line. Blank
lines are ignored. The file is UTF-8; a leading byte-order mark is ignored. For
example:

    # the operators the default render turns on
    cert-manager.io/v1
    gateway.envoyproxy.io/v1alpha1
    k8s.mariadb.com/v1alpha1
    keda.sh/v1alpha1

A CHART WITH NO FILE GETS NO FLAGS, and that is the whole backward-compatibility
argument: every gate appends these flags to the argv it already ran, so an
absent declaration leaves that argv byte-identical.

A LINE THAT IS NOT ONE API VERSION IS REFUSED BY FILE AND LINE rather than passed
through. helm accepts any string as an API version and would render happily
against a capability nobody meant to declare — two entries on one line, a
comma-joined list (helm itself would split that one), or a pasted flag.

`group/version/Kind` IS REFUSED TOO, although helm accepts it. The estate's
`require-api` asks `.Capabilities.APIVersions.Has "<group/version>"`, and a
Kind-form entry never satisfies that question (measured on helm v3.18.4 and
v4.3.0), so it would be a declaration that declares nothing the checks read.

THE D80 ALL-OFF RENDER NEVER RECEIVES THESE FLAGS. It is the bare-cluster proof;
see `d80_portability.py`.

STANDARD LIBRARY ONLY. The `helm-lint` hook imports this, and a
`language: script` hook cannot install anything.
"""

from __future__ import annotations

import re
from pathlib import Path

# Relative to the chart directory, never to the repository root: see above.
DECLARATION = Path("ci") / "api-versions.txt"

# `group/version`. The core group has no prefix, so `v1` alone is also an API
# version — but a core API is always present and declaring one changes nothing,
# so it is not accepted as an entry here.
ENTRY = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?/v[0-9]+[a-z0-9]*$")
KIND_FORM = re.compile(r"^([a-z0-9]([a-z0-9.-]*[a-z0-9])?/v[0-9]+[a-z0-9]*)/[A-Za-z][A-Za-z0-9]*$")


class DeclarationError(Exception):
    """A declaration exists and a line of it is not one API version."""


def declared_api_versions(chart_directory: Path) -> list[str]:
    """The API versions `chart_directory` declares, in file order; `[]` if none."""
    path = Path(chart_directory) / DECLARATION
    if not path.is_file():
        return []
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DeclarationError(
            f"{path}: is not UTF-8 (byte {error.start}). Write the declaration "
            f"as plain UTF-8 text, one `group/version` per line."
        ) from None
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        kind_form = KIND_FORM.match(entry)
        if kind_form:
            raise DeclarationError(
                f"{path}:{number}: `{entry}` names a Kind. Declare "
                f"`{kind_form.group(1)}` instead: the render checks ask "
                f"`.Capabilities.APIVersions.Has \"<group/version>\"`, and a "
                f"`group/version/Kind` entry never satisfies that."
            )
        if not ENTRY.match(entry):
            raise DeclarationError(
                f"{path}:{number}: `{entry}` is not one API version. Write one "
                f"`group/version` per line, as `helm template --api-versions` "
                f"takes it; `#` starts a comment."
            )
        found.append(entry)
    return found


def api_version_flags(chart_directory: Path) -> list[str]:
    """`--api-versions <v>` per declared entry, to append to a `helm template` argv."""
    flags: list[str] = []
    for version in declared_api_versions(chart_directory):
        flags += ["--api-versions", version]
    return flags
