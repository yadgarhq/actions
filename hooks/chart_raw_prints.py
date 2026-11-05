#!/usr/bin/env python3
"""YAADGAAR 557 (ADR-1136, ratifying ADR-1129): a chart prints no values-derived scalar raw.

THE CLASS. `{{ .Values.x }}` drops a value's characters straight into the
manifest. A newline ends the scalar and the next line becomes a key of its own;
a line starting `---` becomes a whole new document; `1e+06` changes type. The
platform#75 review measured an `isCA` Certificate injected this way at rc=0.

THE RULE. Every OUTPUT action whose operands reach a values-derived scalar ends
in an approved encoder, or is on the chart's allowlist with a reason. The gate
judges each output action ONCE, by its last pipeline stage, as platform's
`scripts/tests/test_raw_prints.py` does. A `.Values` nested in an argument is not
a print of its own, but the action holding it is still judged, so
`{{ printf "%s-x" .Values.name }}` is raw.

APPROVED ENCODERS (ENCODERS below). `quote`, `toJson`, `int64` (ADR-1136) and
`int` (ADR-1129's "checked int") as the last stage, in pipe or call form.
`toYaml` (ADR-1136) only when `nindent N` follows it, or `indent N` with the
action alone on its line: ADR-1129 refuses inline `toYaml`, because a block
scalar it emits for a multi-line string lands at the key's own indent and its
lines become keys. `squote` is raw (sprig does not escape `'` inside it), and so
is `b64enc`, which neither ADR lists. "An allowlist check for its kind" is a
render-time check the gate cannot see; it is an allowlist entry whose reason
names the check.

WHAT IS VALUES-DERIVED, decided fail-closed:

  * `.Values...` and `$.Values...`, anywhere;
  * a variable bound from a values-derived pipeline, by `:=`, `=`, or a
    `range`/`with` binding over one; a variable the file never binds; a
    variable bound by `range`/`with` (its taint is the ranged pipeline's);
  * `.` and `.field` inside a `with`/`range` over a values-derived pipeline;
  * `.` alone at the root or in a define (the root holds `.Values`), and any
    `.field` whose first segment is not a helm built-in object (`Release`,
    `Chart`, `Capabilities`, `Template`, `Files`);
  * any command fed a values-derived operand or input (`tpl`, `default`,
    `printf`, `required`, ...).

NOT VALUES-DERIVED: literals, the built-in objects, and the OUTPUT of an
`include` or `template` whose name is a string literal naming a define in this
chart's own templates. That define body is scanned where it is defined, with its
`.` treated as the root shape, so `{{ include "x" (dict "v" .Values.y) }}` is
judged at the helper's own prints, not at the call site. A helper the chart does
not define (a vendored subchart's or a library's, which the scan prunes) and a
name the gate cannot read (`include $name .`) are judged like any function: the
output carries its operands' taint.

NOT OUTPUT: comments, `if`/`else`/`with`/`range`/`define`/`block`/`end`/`break`/
`continue` actions, variable assignments and `fail`.

NOT MODELLED IS RED. An unclosed action, comment or string literal, an
unbalanced `(` or `)`, a stray `end` or a block left open at end of file, and a
file that is not UTF-8 are each NOT_MODELLED, naming file:line. They cannot be
allowlisted: a form the gate cannot read is rewritten, not excused.

WHICH FILES. Every file under each chart's `templates/`, whatever its suffix,
because helm renders them all, except `templates/NOTES.txt`, which helm prints
to the console and never applies. Vendored charts (`charts/`) are pruned, as
`chart_baseline.find_charts` prunes them.

THE ALLOWLIST. `<chart>/ci/raw-prints-allowlist.txt`, beside the
`ci/required-defaults-baseline.txt` of `chart-required-defaults`. One line per
excused print, keyed by the template path under `templates/` and the print's
whitespace-normalised expression:

    probe.yaml: $probe.probeName  # ADR-1136: the one-line values arm refuses a control character in it

`#` lines and blank lines are comments. The gate is RED on

  RAW_PRINT             a values-derived print with no final encoder, not allowlisted;
  NOT_MODELLED          an action or file the gate cannot read;
  ALLOWLIST_NO_REASON   an entry with no `# ADR-1136: <reason>`, or an empty reason;
  ALLOWLIST_MALFORMED   a line that is not `<path>: <expression>  # ADR-1136: <reason>`;
  STALE_ALLOWLIST       an entry that excuses no raw print any more;
  NO_CHART              a tree with no Chart.yaml: nothing judged is red, not green.

The tokeniser is `chart_required_defaults`' port of platform's, imported, not
copied (ADR-0679). `language: script`, standard library only.

Run:   python3 hooks/chart_raw_prints.py [--root DIR]
Tests: python3 -m pytest scripts/tests/test_chart_raw_prints.py -q
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chart_baseline import find_charts  # noqa: E402
from chart_required_defaults import (  # noqa: E402
    ASSIGNMENT,
    BINDING,
    Group,
    Refusal,
    Text,
    Word,
    actions,
    line_of,
    parse,
)

HOOK = "chart-raw-prints"
ALLOWLIST = Path("ci") / "raw-prints-allowlist.txt"
ENCODERS = frozenset({"quote", "toJson", "int64", "int"})
BLOCK_ENCODER = "toYaml"
BUILTINS = frozenset({"Release", "Chart", "Capabilities", "Template", "Files"})
OPAQUE_OUTPUT = frozenset({"include", "template"})
ALLOWLIST_LINE = re.compile(r"^(?P<path>[^\s:]+):\s+(?P<expression>.+?)\s+#\s*ADR-1136:(?P<reason>.*)$")
ALLOWLIST_SHAPE = re.compile(r"^(?P<path>[^\s:]+):\s+(?P<expression>\S.*?)\s*$")
OPENS = re.compile(r"^(if|with|range|define|block)\b")
ELSE = re.compile(r"^else(?:\s+(?P<kind>if|with|range)\b)?")
SILENT = re.compile(r"^(break|continue)\s*$")
FAIL = re.compile(r"^fail\b")
DEFINES = re.compile(r'^(?:define|block)\s+"([^"]+)"')

ROOT, TAINTED, CLEAN = "root", "tainted", "clean"


@dataclass
class Frame:
    kind: str
    dot: str
    outer_dot: str
    variables: dict


@dataclass
class Verdict:
    files: int = 0
    prints: int = 0
    derived: int = 0
    encoded: int = 0
    allowlisted: int = 0
    raw: list = field(default_factory=list)  # (relative path, line, expression)
    errors: list = field(default_factory=list)


def normalise(expression: str) -> str:
    return " ".join(expression.split())


def head(command: list) -> str:
    return command[0].text if command and isinstance(command[0], Word) else ""


def field_taint(path: str, dot: str) -> bool:
    """`path` is `.`, `.a.b`, or the part of `$.a.b` after `$`; `dot` is what `.` holds."""
    segments = [s for s in path.split(".") if s]
    if segments[:1] == ["Values"]:
        return True
    if dot == TAINTED:
        return True
    if dot == CLEAN:
        return False
    return not segments or segments[0] not in BUILTINS


def term_taint(term, dot: str, variables: dict, defines: frozenset) -> bool:
    if isinstance(term, Text):
        return False
    if isinstance(term, Group):
        return pipeline_taint(term.pipeline, dot, variables, defines)
    word = term.text
    if word.startswith("$"):
        match = re.match(r"^\$(\w*)(.*)$", word)
        name, rest = match.group(1), match.group(2)
        if not name:
            return field_taint(rest or ".", ROOT)
        return variables.get(name, True)
    if word.startswith("."):
        return field_taint(word, dot)
    return False


def is_opaque(command: list, defines: frozenset) -> bool:
    """An `include`/`template` of a define this chart holds: its body is judged where it is defined.

    A helper the chart does not define (a vendored subchart's or a library's, which
    the scan prunes) or a name the gate cannot read (`include $name .`) is judged
    like any other function: its output carries its operands' taint.
    """
    return (
        head(command) in OPAQUE_OUTPUT
        and len(command) > 1
        and isinstance(command[1], Text)
        and command[1].quote in "\"`"
        and command[1].text in defines
    )


def pipeline_taint(pipeline: list, dot: str, variables: dict, defines: frozenset) -> bool:
    carried = False
    for command in pipeline:
        if is_opaque(command, defines):
            carried = False
            continue
        carried = carried or any(term_taint(term, dot, variables, defines) for term in command)
    return carried


def is_encoded(pipeline: list, alone: bool) -> bool:
    last = head(pipeline[-1])
    if last == "nindent" or (last == "indent" and alone):
        return len(pipeline) > 1 and head(pipeline[-2]) == BLOCK_ENCODER
    return last in ENCODERS


def is_alone(source: str, offset: int) -> bool:
    start = source.rfind("\n", 0, offset) + 1
    end = source.find("\n", offset)
    line = source[start : len(source) if end < 0 else end]
    return not source[start:offset].strip() and line.rstrip().endswith("}}") and line.count("{{") == 1


def scan(source: str, where: str, verdict: Verdict, emit, defines: frozenset = frozenset()) -> None:
    """Judge every output action of one template; `emit(line, expression)` a raw one."""
    frames = [Frame("file", ROOT, ROOT, {})]

    def taint(expression: str) -> bool:
        return pipeline_taint(parse(expression), frames[-1].dot, frames[-1].variables, defines)

    def bind(names: list, tainted: bool, declare: bool) -> None:
        variables = frames[-1].variables
        for name in names:
            variables[name] = tainted if declare else (variables.get(name, True) or tainted)

    try:
        for offset, body, is_comment in actions(source):
            line = line_of(source, offset)
            if is_comment or not body:
                continue
            try:
                if body == "end" or body.startswith("end "):
                    if len(frames) == 1:
                        raise Refusal("an `end` with no open block")
                    merge(frames.pop().variables, frames[-1].variables)
                    continue
                if SILENT.match(body):
                    continue
                otherwise = ELSE.match(body)
                if otherwise:
                    if len(frames) == 1:
                        raise Refusal("an `else` with no open block")
                    frame = frames[-1]
                    merge(frame.variables, frames[-2].variables)
                    frame.variables = dict(frames[-2].variables)
                    frame.dot = frame.outer_dot
                    rest = body[otherwise.end() :].strip()
                    kind = otherwise.group("kind")
                    if kind in ("with", "range"):
                        names, rest = split_binding(f"{kind} {rest}")
                        tainted = pipeline_taint(parse(rest), frame.outer_dot, frame.variables, defines)
                        frame.dot = TAINTED if tainted else CLEAN
                        bind(names, tainted, True)
                    elif kind == "if":
                        parse(rest)
                    continue
                opener = OPENS.match(body)
                if opener:
                    kind = opener.group(1)
                    outer = frames[-1]
                    if kind in ("define", "block"):
                        parse(body[opener.end() :].strip())
                        frames.append(Frame(kind, ROOT, ROOT, {}))
                    elif kind == "if":
                        parse(body[opener.end() :].strip())
                        frames.append(Frame(kind, outer.dot, outer.dot, dict(outer.variables)))
                    else:
                        names, rest = split_binding(body)
                        tainted = pipeline_taint(parse(rest), outer.dot, outer.variables, defines)
                        frames.append(Frame(kind, TAINTED if tainted else CLEAN, outer.dot, dict(outer.variables)))
                        bind(names, tainted, True)
                    continue
                assignment = ASSIGNMENT.match(body)
                if assignment:
                    declare = ":=" in body[: assignment.start("rhs")]
                    bind([assignment.group("name")], taint(assignment.group("rhs")), declare)
                    continue
                pipeline = parse(body)
                if FAIL.match(body):
                    continue
                verdict.prints += 1
                if not pipeline_taint(pipeline, frames[-1].dot, frames[-1].variables, defines):
                    continue
                verdict.derived += 1
                if is_encoded(pipeline, is_alone(source, offset)):
                    verdict.encoded += 1
                    continue
                emit(line, normalise(body))
            except Refusal as refusal:
                verdict.errors.append(f"NOT_MODELLED {where}:{line}: {refusal} in `{{{{ {normalise(body)} }}}}`")
    except Refusal as refusal:
        verdict.errors.append(f"NOT_MODELLED {where}: {refusal}")
        return
    if len(frames) > 1:
        verdict.errors.append(f"NOT_MODELLED {where}: a `{frames[-1].kind}` block is never closed by `end`")


def merge(inner: dict, outer: dict) -> None:
    """An `=` inside a block reassigns the outer variable: taint only grows."""
    for name, tainted in inner.items():
        if name in outer:
            outer[name] = outer[name] or tainted


def split_binding(body: str):
    """`range $k, $v := P` -> (["k", "v"], "P"); `with P` -> ([], "P")."""
    binding = BINDING.match(body)
    if binding:
        return re.findall(r"\$(\w+)", binding.group("names")), body[binding.end() :].strip()
    return [], body.split(None, 1)[1] if len(body.split(None, 1)) > 1 else ""


def read_allowlist(chart: Path, root: Path):
    """{(path, expression): reason} and the refusals for the chart's allowlist file."""
    path = chart / ALLOWLIST
    where = path.relative_to(root).as_posix()
    entries, errors = {}, []
    if not path.is_file():
        return entries, errors
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return entries, [f"ALLOWLIST_MALFORMED {where}: not readable UTF-8 text"]
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ALLOWLIST_LINE.match(line)
        if match and match.group("reason").strip():
            entries[(match.group("path"), normalise(match.group("expression")))] = match.group("reason").strip()
        elif match or ALLOWLIST_SHAPE.match(line):
            errors.append(f"ALLOWLIST_NO_REASON {where}:{number}: `{line}` needs `  # ADR-1136: <why this print is safe>`")
        else:
            errors.append(f"ALLOWLIST_MALFORMED {where}:{number}: `{line}` is not `<template path>: <expression>  # ADR-1136: <reason>`")
    return entries, errors


def defined_names(files: list) -> frozenset:
    """Every `define`/`block` name the chart's own templates declare."""
    names = set()
    for path in files:
        try:
            for _, body, is_comment in actions(path.read_text(encoding="utf-8")):
                match = DEFINES.match(body) if not is_comment else None
                if match:
                    names.add(match.group(1))
        except (OSError, UnicodeDecodeError, Refusal):
            continue  # scan() refuses the file itself
    return frozenset(names)


def judge(chart: Path, root: Path) -> Verdict:
    verdict = Verdict()
    templates = chart / "templates"
    allowlist, verdict.errors = read_allowlist(chart, root)
    used = set()
    files = sorted(p for p in templates.rglob("*") if p.is_file()) if templates.is_dir() else []
    defines = defined_names(files)
    for path in files:
        relative = path.relative_to(templates).as_posix()
        if relative == "NOTES.txt":
            continue
        verdict.files += 1
        where = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            verdict.errors.append(f"NOT_MODELLED {where}: not readable UTF-8 text")
            continue

        def emit(line, expression, relative=relative, where=where):
            key = (relative, expression)
            if key in allowlist:
                used.add(key)
                verdict.allowlisted += 1
            else:
                verdict.raw.append((where, line, expression))

        scan(source, where, verdict, emit, defines)
    for key in sorted(set(allowlist) - used):
        verdict.errors.append(
            f"STALE_ALLOWLIST {(chart / ALLOWLIST).relative_to(root).as_posix()}: `{key[0]}: {key[1]}` excuses no raw print any more"
        )
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=".", help="the repository to judge (default: .)")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root).resolve()
    if not root.is_dir():
        print(f"{HOOK}: --root {arguments.root} is not a directory")
        return 1
    charts = find_charts(root)
    if not charts:
        print(f"{HOOK}: NO_CHART: no Chart.yaml under {root}. A tree with nothing to judge is red, not green")
        return 1
    verdicts = [judge(chart, root) for chart in charts]
    red = False
    for verdict in verdicts:
        for where, line, expression in verdict.raw:
            print(f"{HOOK}: RAW_PRINT {where}:{line}: {{{{ {expression} }}}}")
        for error in verdict.errors:
            print(f"{HOOK}: {error}")
        red = red or bool(verdict.raw or verdict.errors)
    total = lambda name: sum(getattr(v, name) for v in verdicts)
    print(
        f"{HOOK}: {len(charts)} chart(s), {total('files')} template file(s), {total('prints')} output print(s), "
        f"{total('derived')} values-derived ({total('encoded')} encoded, {total('allowlisted')} allowlisted, "
        f"{sum(len(v.raw) for v in verdicts)} raw), {sum(len(v.errors) for v in verdicts)} other refusal(s). "
        f"{'RED' if red else 'green'}."
    )
    if red:
        print(
            f"{HOOK}: encode each raw print (`| quote`, `| toJson`, `| int64`, `| int`, or "
            f"`| toYaml | nindent N`), or list it in <chart>/{ALLOWLIST.as_posix()} with `# ADR-1136: <reason>`"
        )
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
