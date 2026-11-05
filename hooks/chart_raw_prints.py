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
  * `.` or `$` alone, at the root or in a define (the root holds `.Values`;
    `index $ "Values"` reads it), and any `.field` whose first segment is not a
    helm built-in object (`Release`, `Chart`, `Capabilities`, `Template`,
    `Files`). `Subcharts` is NOT a built-in here: it carries subcharts' values;
  * a map a `set`/`merge`/... wrote a values-derived operand into;
  * any command fed a values-derived operand or input (`tpl`, `default`,
    `printf`, `required`, ...).

NOT VALUES-DERIVED: literals, the built-in objects read off the real root, and
the OUTPUT of an `include` or `template` of an OPAQUE define. A define is one of
this chart's own, named by a string literal; inside it `.` and `$` are its
ARGUMENT, which only its callers know. A define is SENSITIVE when one of its
prints is raw, allowlisted, or prints a built-in read off its argument
(`{{ .Release.Name }}`) unencoded, or when it passes its argument to a
sensitive define. Sensitivity is a fixed point over the chart. A sensitive
define is judged AT EACH CALL SITE by what the caller passes: the real root
(`.` at file level, or `$` outside a define) is clean; a define's own argument
makes the caller sensitive in turn; anything else carries its taint, so
`include "x" (dict "Release" (dict "Name" .Values.evil))` is a raw print at the
call site, and an allowlisted helper body excuses no values caller. Every other
define is opaque: its body is judged where it is defined. A helper the chart
does not define (a vendored subchart's or a library's, which the scan prunes)
and a name the gate cannot read (`include $name .`) are judged like any
function: the output carries its operands' taint.

NOT OUTPUT: comments, `if`/`else`/`with`/`range`/`define`/`end`/`break`/
`continue` actions, variable assignments and `fail`.

NOT MODELLED IS RED. An unclosed action, comment or string literal, an
unbalanced `(` or `)`, a stray `end`/`else` or a block left open at end of file,
a `block` action, a `define` whose name is not a literal, and a file that is not
UTF-8 are each NOT_MODELLED, naming file:line. So is any `set`, `unset`,
`merge`, `mergeOverwrite`, `mustMerge`, `mustMergeOverwrite`, `deepCopy` or
`mustDeepCopy` whose first operand may be the root, a define's argument or a
built-in object (`.`, `$`, `.Release`, `$.Chart`, `(index $ "Release")`, a
variable bound to one, or a path into a map holding one): such a call can make
`{{ .Release.Name }}` print a value (measured by the actions#200 review on helm 4.3.0 and 3.18.4). None of
these can be allowlisted: a form the gate cannot read is rewritten, not excused.

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
# `Subcharts` is not here: it carries every subchart's `.Values`.
BUILTINS = frozenset({"Release", "Chart", "Capabilities", "Template", "Files"})
OPAQUE_OUTPUT = frozenset({"include", "template"})
# Functions that write into a map in place, plus the copies the review named. Aimed
# at the root, a define's argument or a built-in object, any of them can make a
# built-in print a value, so each such call is NOT_MODELLED.
MUTATORS = frozenset(
    {"set", "unset", "merge", "mergeOverwrite", "mustMerge", "mustMergeOverwrite", "deepCopy", "mustDeepCopy"}
)
# Functions whose result is a NEW value, never one of their operands. Any other
# function fed the root or a built-in may return it, so its result is an alias.
CONSTRUCTORS = frozenset(
    {"dict", "list", "tuple", "deepCopy", "mustDeepCopy", "fromYaml", "fromJson", "toYaml", "toJson",
     "printf", "print", "println", "quote", "squote", "toString", "len", "keys", "values", "include", "tpl"}
)
ALLOWLIST_LINE = re.compile(r"^(?P<path>[^\s:]+):\s+(?P<expression>.+?)\s+#\s*ADR-1136:(?P<reason>.*)$")
ALLOWLIST_SHAPE = re.compile(r"^(?P<path>[^\s:]+):\s+(?P<expression>\S.*?)\s*$")
OPENS = re.compile(r"^(if|with|range|define|block)\b")
ELSE = re.compile(r"^else(?:\s+(?P<kind>if|with|range)\b)?")
SILENT = re.compile(r"^(break|continue)\s*$")
FAIL = re.compile(r"^fail\b")
DEFINES = re.compile(r'^define\s+"([^"]+)"')

# What `.` (or `$`) holds. ROOT: the chart root at file level. ARG: a define's
# argument, which only its callers know. TAINTED / CLEAN: a `with`/`range` target.
ROOT, ARG, TAINTED, CLEAN = "root", "arg", "tainted", "clean"


@dataclass
class Frame:
    kind: str
    dot: str
    outer_dot: str
    dollar: str
    define: str | None
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


@dataclass
class Context:
    """What one expression is evaluated against."""

    dot: str
    dollar: str
    variables: dict
    defines: frozenset
    sensitive: set
    direct: set  # variables that ARE the root, an argument or a built-in object
    contains: set  # variables holding one somewhere inside


def normalise(expression: str) -> str:
    return " ".join(expression.split())


def head(command: list) -> str:
    return command[0].text if command and isinstance(command[0], Word) else ""


def path_eval(path: str, state: str):
    """(taint, argread) of `.`/`.a.b`, or of `$`'s path, where `.` holds `state`.

    `argread` marks a built-in read off a define's argument: harmless when the
    caller passes the real root, a value when the caller passes a dict.
    """
    segments = [s for s in path.split(".") if s]
    if segments[:1] == ["Values"] or state == TAINTED:
        return True, False
    if state == CLEAN:
        return False, False
    if not segments or segments[0] not in BUILTINS:
        return True, False
    return False, state == ARG


def term_eval(term, ctx: Context):
    if isinstance(term, Text):
        return False, False
    if isinstance(term, Group):
        return pipeline_eval(term.pipeline, ctx)
    word = term.text
    if word.startswith("$"):
        match = re.match(r"^\$(\w*)(.*)$", word)
        name, rest = match.group(1), match.group(2)
        if not name:
            return path_eval(rest or ".", ctx.dollar)
        return ctx.variables.get(name, True), False
    if word.startswith("."):
        return path_eval(word, ctx.dot)
    return False, False


def is_alias(term, ctx: Context) -> bool:
    """Whether `term` may BE the root, a define's argument or a built-in object."""
    if isinstance(term, Text):
        return False
    if isinstance(term, Group):
        return pipeline_alias(term.pipeline, ctx)
    word = term.text
    match = re.match(r"^\$(\w+)(.*)$", word)
    if match:
        name, rest = match.groups()
        return name in ctx.direct or (bool(rest) and name in ctx.contains)
    if word in (".", "$"):
        return True
    segments = [s for s in word.lstrip("$").split(".") if s]
    return bool(segments) and segments[0] in BUILTINS and word[0] in ".$"


def pipeline_alias(pipeline: list, ctx: Context) -> bool:
    carried = False
    for command in pipeline:
        if len(command) == 1 and not (isinstance(command[0], Word) and command[0].text[:1] not in ".$"):
            carried = carried or is_alias(command[0], ctx)
        elif head(command) in CONSTRUCTORS:
            carried = False
        else:
            carried = carried or any(is_alias(term, ctx) for term in command[1:])
    return carried


def holds_alias(pipeline: list, ctx: Context) -> bool:
    for command in pipeline:
        for term in command:
            if isinstance(term, Group) and holds_alias(term.pipeline, ctx):
                return True
            if is_alias(term, ctx):
                return True
    return False


def check_mutation(command: list, carried, ctx: Context) -> None:
    """Refuse a write into the root, an argument or a built-in; taint a fresh map written a value."""
    if head(command) not in MUTATORS:
        return
    operands = command[1:]
    if not operands or is_alias(operands[0], ctx):
        raise Refusal(f"`{head(command)}` on the root, a define's argument or a built-in object")
    tainted = carried[0] or carried[1] or any(any(term_eval(term, ctx)) for term in operands[1:])
    target = operands[0]
    if tainted and isinstance(target, Word) and target.text.startswith("$"):
        name = re.match(r"^\$(\w*)", target.text).group(1)
        if name:
            ctx.variables[name] = True


def call_eval(command: list, carried, ctx: Context):
    """An `include`/`template` of a define this chart holds.

    A define none of whose prints reads its argument raw is OPAQUE: its body is
    judged where it is defined. A SENSITIVE define (a raw, allowlisted or
    argument-reading print, here or in a define it calls with its argument) is
    judged at the call site by what the caller passes: the real root is clean,
    a define's own argument passes the question up to that define's callers, and
    anything else carries its taint.
    """
    if command[1].text not in ctx.sensitive:
        return False, False
    operands = command[2:]
    if not operands:
        return (carried[0] or carried[1]), False
    if len(operands) == 1 and isinstance(operands[0], Word) and operands[0].text in (".", "$"):
        state = ctx.dot if operands[0].text == "." else ctx.dollar
        if state == ROOT:
            return False, False
        if state == ARG:
            return False, True
    return any(any(term_eval(term, ctx)) for term in operands), False


def is_local_call(command: list, defines: frozenset) -> bool:
    return (
        head(command) in OPAQUE_OUTPUT
        and len(command) > 1
        and isinstance(command[1], Text)
        and command[1].quote in "\"`"
        and command[1].text in defines
    )


def pipeline_eval(pipeline: list, ctx: Context):
    carried = (False, False)
    for command in pipeline:
        check_mutation(command, carried, ctx)
        if is_local_call(command, ctx.defines):
            carried = call_eval(command, carried, ctx)
            continue
        taint, argread = carried
        for term in command:
            t, a = term_eval(term, ctx)
            taint, argread = taint or t, argread or a
        carried = (taint, argread)
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


def scan(source: str, where: str, verdict: Verdict, emit, defines: frozenset = frozenset(), sensitive=None) -> None:
    """Judge every output action of one template.

    `emit(line, expression)` reports a raw print and returns whether the allowlist
    excuses it. `sensitive` gains every define found to read its argument.
    """
    sensitive = set() if sensitive is None else sensitive
    frames = [Frame("file", ROOT, ROOT, ROOT, None, {})]
    direct, contains = set(), set()

    def context(dot=None) -> Context:
        frame = frames[-1]
        return Context(frame.dot if dot is None else dot, frame.dollar, frame.variables, defines, sensitive, direct, contains)

    def evaluate(expression: str, dot=None):
        pipeline = parse(expression)
        ctx = context(dot)
        return pipeline, pipeline_eval(pipeline, ctx), ctx

    def bind(names: list, pipeline: list, value, ctx: Context, declare: bool) -> None:
        tainted = value[0] or value[1]
        alias, held = pipeline_alias(pipeline, ctx), holds_alias(pipeline, ctx)
        for name in names:
            variables = frames[-1].variables
            variables[name] = tainted if declare else (variables.get(name, True) or tainted)
            if alias:
                direct.add(name)
            if held or alias:
                contains.add(name)

    def mark_sensitive() -> None:
        for frame in reversed(frames):
            if frame.define:
                sensitive.add(frame.define)
                return

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
                        pipeline, value, ctx = evaluate(rest, frame.outer_dot)
                        frame.dot = TAINTED if (value[0] or value[1]) else CLEAN
                        bind(names, pipeline, value, ctx, True)
                    elif kind == "if":
                        evaluate(rest)
                    continue
                opener = OPENS.match(body)
                if opener:
                    kind = opener.group(1)
                    outer = frames[-1]
                    if kind == "block":
                        raise Refusal("a `block` is both a define and a call, and the gate does not model it")
                    if kind == "define":
                        name = DEFINES.match(body)
                        if not name:
                            raise Refusal("a `define` whose name is not a string literal")
                        frames.append(Frame(kind, ARG, ARG, ARG, name.group(1), {}))
                    elif kind == "if":
                        evaluate(body[opener.end() :].strip())
                        frames.append(Frame(kind, outer.dot, outer.dot, outer.dollar, None, dict(outer.variables)))
                    else:
                        names, rest = split_binding(body)
                        pipeline, value, ctx = evaluate(rest)
                        dot = TAINTED if (value[0] or value[1]) else CLEAN
                        frames.append(Frame(kind, dot, outer.dot, outer.dollar, None, dict(outer.variables)))
                        bind(names, pipeline, value, ctx, True)
                    continue
                assignment = ASSIGNMENT.match(body)
                if assignment:
                    declare = ":=" in body[: assignment.start("rhs")]
                    pipeline, value, ctx = evaluate(assignment.group("rhs"))
                    bind([assignment.group("name")], pipeline, value, ctx, declare)
                    continue
                pipeline, (tainted, argread), _ = evaluate(body)
                if FAIL.match(body):
                    continue
                verdict.prints += 1
                if not tainted and not argread:
                    continue
                if is_encoded(pipeline, is_alone(source, offset)):
                    if tainted:
                        verdict.derived += 1
                        verdict.encoded += 1
                    continue
                if not tainted:
                    mark_sensitive()  # a built-in read off the argument: the callers decide
                    continue
                verdict.derived += 1
                mark_sensitive()
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
    """Every `define` name the chart's own templates declare."""
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
    templates = chart / "templates"
    allowlist, allowlist_errors = read_allowlist(chart, root)
    files = sorted(p for p in templates.rglob("*") if p.is_file()) if templates.is_dir() else []
    files = [p for p in files if p.relative_to(templates).as_posix() != "NOTES.txt"]
    defines = defined_names(files)
    sources = {}
    for path in files:
        try:
            sources[path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            sources[path] = None

    # SENSITIVITY IS A FIXED POINT: a define that passes its argument to a
    # sensitive define is sensitive, wherever the two live. Each pass can only
    # add names, so it ends within one pass per define.
    sensitive: set = set()
    while True:
        before = set(sensitive)
        for path, source in sources.items():
            if source is not None:
                scan(source, "", Verdict(), lambda line, expression: None, defines, sensitive)
        if sensitive == before:
            break

    verdict = Verdict(errors=list(allowlist_errors))
    used = set()
    for path, source in sources.items():
        verdict.files += 1
        where = path.relative_to(root).as_posix()
        if source is None:
            verdict.errors.append(f"NOT_MODELLED {where}: not readable UTF-8 text")
            continue
        relative = path.relative_to(templates).as_posix()

        def emit(line, expression, relative=relative, where=where):
            key = (relative, expression)
            if key in allowlist:
                used.add(key)
                verdict.allowlisted += 1
            else:
                verdict.raw.append((where, line, expression))

        scan(source, where, verdict, emit, defines, sensitive)
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
