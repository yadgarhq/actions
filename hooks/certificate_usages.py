#!/usr/bin/env python3
"""Every cert-manager leaf names exactly one direction: `server auth` XOR `client auth`.

LEDGER 720. ADR-0590/0594 rank mTLS and NetworkPolicy as equal controls, neither
primary. The mTLS half means something only because a leaf proves what its
holder may DO — a serving leaf answers, a client leaf dials — and one authority
signs both directions in this deployment, so the extended key usage is the ONLY
thing keeping them apart. A leaf carrying both lets a module that should only
dial also serve.

THE DEFAULT IS THE UNSAFE VALUE, which is why this is a gate and not a review
note. webpki's `KeyUsage::client_auth()` and `KeyUsage::server_auth()` are
`required_if_present` rather than `required`, so a leaf carrying NO extended key
usage passes BOTH checks — and a Certificate with no `usages` block is exactly
what cert-manager emits. Omitting the block is therefore worse than widening it,
and reads in review as one less line rather than as a hole.

WHAT THIS REPLACES, and why publishing it is the change rather than a tidy-up.
`yadgarhq/deploy` carried this rule as `repo: local`, `scripts/check-certificate-usages.py`,
scoped `files: ^infra/.*\\.yaml$` and to `issuerRef.name == "yadgar-internal-ca"`.
Measured against `origin/main` on 2026-09-06, that gate has three holes, each
demonstrated by running the hook itself rather than by reading it:

  * A Certificate naming BOTH directions, under the internal CA, committed at
    the REPOSITORY ROOT — `pre-commit run certificate-usages --all-files`
    reports `Passed`. `files:` filters the all-files list too, so a leaf outside
    `infra/` is never inspected, in CI or on a laptop.
  * The same Certificate inside `infra/` with `issuerRef.name` changed to
    `yadgar-internal-ca-v2` — `Passed`. The issuer allowlist means renaming the
    authority silently exempts every leaf under it.
  * No floor and no count. A glob that matches nothing exits 0, and this estate
    has now measured four separate instances of that class in one week.

So the rule is restated here without the two scopings that made it fail open,
and the file that carries it is published rather than copied — the argument
ledger 648 records for the ADR-0569 gate, which was byte-identical in five
repositories and absent in seven.

THE ISSUER ALLOWLIST IS GONE, and the CA is exempted by what it IS instead.
`isCA: true` is a property of the document, not a name that can be changed in
another file, and the exemption is a POSITIVE requirement rather than a skip: an
`isCA` Certificate must name NEITHER direction. A bare skip would mean adding
`isCA: true` to a serving leaf escapes the gate entirely, which trades one
fail-open for another. Dropping the allowlist also widens the rule to the edge
leaf `gateway-tls` under `yadgar-dev-ca`, which already satisfies it — the wall
is a property of leaves, not of one authority's leaves.

`always_run` WITH `pass_filenames: false`, because the subject is every
Certificate in the tree rather than the file somebody touched. That is what
closes the first hole above, and it is the same reason `no-compiled-in-defaults`
gives for the same choice.

TWO FLOORS, NOT ONE, because there are two ways this can go blind and they are
independent. `MINIMUM_CERTIFICATES` catches the walk finding nothing at all —
the repository restructured, the glob stale. `MINIMUM_LEAVES` catches every
Certificate turning into something the wall does not judge, which is precisely
what the old issuer rename did. Both are floors rather than the current counts:
adding a module is a legitimate change and must not redden.

`language: script` AND STDLIB ONLY. `language: python` makes pre-commit
pip-install THIS repository, which is not a package, and the error surfaces in
the consuming repo as an install failure that nothing there explains — learned
three times over in v1.4.0, and the reason `complexity`, `observe-coverage` and
`run-block-size` are all scripts. Under `language: script` pre-commit runs this
file from its own clone while the working directory stays the CONSUMER's root,
which is what lets the walk below see the consumer's tree.

That forces a hand-written scan rather than `yaml.safe_load_all`, and the two
traps are real rather than hypothetical. In `deploy/infra/internal-tls/certificates.yaml`
the key `usages:` is on line 269 and its first item is on line 306 — thirty-seven
lines of comment in between, so a parser that expects the item to follow the key
reads zero usages and refuses a CORRECT certificate. And the prose in those
comments says `client auth` in so many words ("`server auth` AND NOT `client
auth`"), so a scan that reads comments refuses a correct certificate the other
way. Comments are dropped first and items are gathered across them; the
equivalence test in `scripts/tests/test_certificate_usages.py` asserts this scan
extracts the same tuples as PyYAML over the real corpus, which is the only
evidence worth having that a hand parser is safe.

IT FAILS CLOSED ON A SHAPE IT CANNOT READ. A `kind: Certificate` document whose
`spec` is missing, or whose `usages` is neither a block sequence nor a flow
sequence, is refused rather than skipped — an unreadable certificate is the one
case where "no problems found" is least trustworthy.

WHAT IT DOES NOT CHECK, said here rather than left to be found.

  * A TEMPLATED FILE IS NOT READ, AND THE COUNT SAYS SO. Any file carrying a
    `{{` is skipped, because no parser here can read `{{ .Values.x }}` as a
    value. That is WIDER than "Helm templates", and saying otherwise was this
    file's own first version of the defect it exists to close: `${{ ... }}` is
    GitHub Actions expression syntax, so every workflow was already being
    skipped while the note claimed charts were. Measured in `yadgarhq/deploy` on
    2026-09-06, the skip drops three files — `.github/workflows/ci.yaml`,
    `infra/prometheus.yaml` and `infra/network-policies/shared-infrastructure.yaml`
    — and none holds a Certificate.

    SO THE SKIP IS COUNTED AND PRINTED, and a skipped file that DOES hold a
    `kind: Certificate` is REFUSED rather than passed over. The floors cannot
    catch that one: dropping a single leaf from ten still clears both. This is
    the same fail-closed rule the paragraph above states for a shape the scan
    cannot parse, applied to a file it never opened — the refusal asks for the
    gate to be widened, and is not a rule about where a Certificate may live.
  * A LEAF ISSUED OUTSIDE GIT. cert-manager is not the only way a Secret can
    hold a certificate, and a leaf minted by hand is invisible to a scan of the
    tree. The live cluster is the check for that, not this.
  * WHETHER THE DIRECTION IS THE RIGHT ONE. This refuses a leaf that names both
    directions or neither. Whether a module that answers was given `server auth`
    rather than `client auth` is a judgement about what the module does, and a
    hook that pretended to know would be the "two definitions of clean" failure
    this estate keeps refusing.

Tests: `python3 -m pytest scripts/tests/test_certificate_usages.py -q`.
"""

from __future__ import annotations

import pathlib
import re
import sys

SERVER = "server auth"
CLIENT = "client auth"

# Floors, not the current counts. `deploy` carries ten Certificates and nine
# leaves today; adding or retiring a module must not redden this. Dropping to
# one or zero means the file has stopped inspecting anything, which is the
# defect it exists to refuse.
MINIMUM_CERTIFICATES = 2
MINIMUM_LEAVES = 2

SUFFIXES = (".yaml", ".yml")

# Directories a tree walk must never descend. `.git` for the obvious reason;
# the rest are caches and vendored trees that can hold a copy of anything.
SKIP_DIRECTORIES = {".git", "node_modules", "target", ".venv", "__pycache__"}

# A template marker. A file carrying one is a Helm chart template or a GitHub
# Actions workflow (`${{ ... }}`), and neither is YAML this scan can read.
TEMPLATE = re.compile(r"\{\{")

# Enough to tell a templated file that holds a Certificate from one that does
# not. A grep rather than a parse, because a parse is exactly what is impossible
# here — and the only verdict it feeds is "this gate must be widened".
CERTIFICATE_KIND = re.compile(r"^\s*kind:\s*[\"']?Certificate[\"']?\s*$", re.MULTILINE)

DOCUMENT_BREAK = re.compile(r"^---\s*$")
TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z_][\w.-]*):\s*(?P<value>.*)$")
NESTED_KEY = re.compile(r"^(?P<indent> +)(?P<key>[A-Za-z_][\w.-]*):\s*(?P<value>.*)$")
SEQUENCE_ITEM = re.compile(r"^(?P<indent> +)-\s+(?P<value>.+)$")


class Unreadable(Exception):
    """A `kind: Certificate` document this scan cannot read. Always fatal."""


def strip_comment(line: str) -> str:
    """Drop a trailing `# ...`, leaving quoted text alone.

    `renewBefore: 744h # 30d +24h` is the shape this exists for. A `#` inside
    quotes is content, so the scan tracks them rather than splitting on the
    first hash.
    """
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line.rstrip()


def significant(text: str) -> list[str]:
    """The lines of a document with whole-line comments and blanks removed.

    Comment removal happens BEFORE anything is matched, which is what stops the
    prose in these files — it names `client auth` in several places — being read
    as a value.
    """
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = strip_comment(raw)
        if stripped.strip():
            lines.append(stripped)
    return lines


def scalar(value: str) -> str:
    """A YAML scalar as this scan needs it: unquoted, untrimmed of nothing else."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def flow_sequence(value: str) -> list[str]:
    """`[a, b]` as a list. Raises when the brackets do not close."""
    inner = value.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise Unreadable(f"`usages: {value}` is neither a block nor a flow sequence")
    inner = inner[1:-1].strip()
    if not inner:
        return []
    return [scalar(part) for part in inner.split(",")]


def documents(text: str) -> list[list[str]]:
    """A multi-document YAML file split into documents of significant lines."""
    chunks: list[list[str]] = [[]]
    for line in significant(text):
        if DOCUMENT_BREAK.match(line):
            chunks.append([])
        else:
            chunks[-1].append(line)
    return [chunk for chunk in chunks if chunk]


def read_usages(lines: list[str], start: int, value: str) -> list[str]:
    """The `usages` list, gathered across however many comment lines intervened.

    `start` indexes the `usages:` line itself. Items are the sequence entries
    that follow it, at any deeper indent, up to the next key at the `usages`
    indent or shallower. Comments are already gone, so a thirty-seven line gap
    between the key and its first item reads exactly like no gap at all.
    """
    if value.strip():
        return flow_sequence(value)

    key = NESTED_KEY.match(lines[start])
    assert key is not None  # the caller matched it
    depth = len(key.group("indent"))

    items = []
    for line in lines[start + 1 :]:
        item = SEQUENCE_ITEM.match(line)
        if item and len(item.group("indent")) > depth:
            items.append(scalar(item.group("value")))
            continue
        nested = NESTED_KEY.match(line)
        if nested and len(nested.group("indent")) <= depth:
            break
        if TOP_LEVEL_KEY.match(line):
            break
        if item:
            break
    return items


def certificates(path: pathlib.Path):
    """Every `kind: Certificate` in one file, as (name, is_ca, usages)."""
    for lines in documents(path.read_text(encoding="utf-8")):
        kind = None
        for line in lines:
            top = TOP_LEVEL_KEY.match(line)
            if top and top.group("key") == "kind":
                kind = scalar(top.group("value"))
                break
        if kind != "Certificate":
            continue

        name = None
        is_ca = False
        usages: list[str] | None = None
        section = None

        for index, line in enumerate(lines):
            top = TOP_LEVEL_KEY.match(line)
            if top:
                section = top.group("key")
                continue
            nested = NESTED_KEY.match(line)
            if not nested or len(nested.group("indent")) != 2:
                continue
            key, value = nested.group("key"), nested.group("value")
            if section == "metadata" and key == "name":
                name = scalar(value)
            elif section == "spec" and key == "isCA":
                is_ca = scalar(value).lower() in ("true", "yes", "on")
            elif section == "spec" and key == "usages":
                usages = read_usages(lines, index, value)

        where = name or "<unnamed>"
        if not any(TOP_LEVEL_KEY.match(line) and line.startswith("spec:") for line in lines):
            raise Unreadable(f"{path}: {where} has no `spec` this scan can read")
        yield where, is_ca, usages


def yaml_files(root: pathlib.Path):
    """Partition the YAML under `root` into what this scan reads and what it cannot.

    Returns `(readable, templated)`. A templated file carries a `{{` — a Helm
    value or a GitHub Actions expression alike — and no parser here can read it.
    The caller counts and reports the second list rather than dropping it
    silently, which is the whole point: an invisible skip in this gate is the
    defect the gate exists to refuse.
    """
    readable, templated = [], []
    for path in sorted(root.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if TEMPLATE.search(text):
            templated.append((path, CERTIFICATE_KIND.search(text) is not None))
        else:
            readable.append(path)
    return readable, templated


def judge(path, name, is_ca, usages):
    """The wall, as one certificate's verdict. Yields a message per problem."""
    named = set(usages or [])
    serves = SERVER in named
    dials = CLIENT in named

    if is_ca:
        if serves or dials:
            yield (
                f"{path}: `{name}` is `isCA: true` and names "
                f"{'`' + SERVER + '`' if serves else ''}"
                f"{' and ' if serves and dials else ''}"
                f"{'`' + CLIENT + '`' if dials else ''}. An authority signs; it "
                f"neither answers nor dials. Name `cert sign` and `crl sign` only."
            )
        return

    if serves and dials:
        yield (
            f"{path}: `{name}` names both `{SERVER}` and `{CLIENT}`. One authority "
            f"signs both directions here, so this list is the whole of what stops a "
            f"leaf that should only answer being replayed as a caller. Name one."
        )
    elif not serves and not dials:
        yield (
            f"{path}: `{name}` names neither `{SERVER}` nor `{CLIENT}` "
            f"(usages: {sorted(named) or 'absent'}). cert-manager then issues a leaf "
            f"with no extended key usage, which webpki accepts in BOTH directions — "
            f"so the omission is wider than naming both, not narrower. Name one."
        )


def main() -> int:
    root = pathlib.Path(".")
    total = 0
    leaves = 0
    problems: list[str] = []

    readable, templated = yaml_files(root)

    for path in readable:
        try:
            found = list(certificates(path))
        except Unreadable as error:
            print(f"::error::{error}")
            return 1
        for name, is_ca, usages in found:
            total += 1
            if not is_ca:
                leaves += 1
            problems.extend(judge(path, name, is_ca, usages))

    print(
        f"certificate-usages: inspected {total} Certificate(s), "
        f"{leaves} of them leaves subject to the `{SERVER}` / `{CLIENT}` wall; "
        f"skipped {len(templated)} templated file(s) this scan cannot read."
    )

    unreadable = [path for path, holds_one in templated if holds_one]
    if unreadable:
        for path in unreadable:
            print(
                f"::error::{path} is templated — it carries `{{{{ ... }}}}` — and "
                "declares a `kind: Certificate` this gate therefore never read. A "
                "verdict over the certificates it COULD read would be a pass that "
                "inspected less than it reported, and the floors below cannot catch "
                "it: dropping one leaf from ten still clears them. Widen this gate "
                "to render the template, or declare the leaf where it can be read."
            )
        return 1

    if total < MINIMUM_CERTIFICATES:
        print(
            f"::error::found {total} `kind: Certificate` document(s), and "
            f"{MINIMUM_CERTIFICATES} is the fewest this can inspect. A gate with "
            "nothing to check reports success, which is the failure this file was "
            "written to stop. If this repository genuinely stopped declaring "
            "certificates, remove the hook id from `.pre-commit-config.yaml` rather "
            "than leaving it green and blind."
        )
        return 1

    if leaves < MINIMUM_LEAVES:
        print(
            f"::error::found {total} Certificate(s) but only {leaves} leaf/leaves the "
            f"wall judges, and {MINIMUM_LEAVES} is the fewest this can inspect. Every "
            "certificate here is exempt as an authority, so nothing was checked. This "
            "is the shape the issuer allowlist this gate replaced could reach by a "
            "rename; it must fail rather than pass."
        )
        return 1

    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
