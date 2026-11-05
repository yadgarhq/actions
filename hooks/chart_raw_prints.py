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
  * a fresh map a `set`/`merge`/... wrote a values-derived operand into;
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
UTF-8 are each NOT_MODELLED, naming file:line.

A SET-FAMILY CALL IS ACCEPTED ON ONE TARGET ONLY (reviews of actions#202 and
#206). `set`, `unset`, `merge`, `mergeOverwrite`, `mustMerge`,
`mustMergeOverwrite`, `deepCopy` and `mustDeepCopy` write into a map in place,
and aimed at the root, a define's argument or a built-in object they make
`{{ .Release.Name }}` print a value (measured by the reviews on helm 4.3.0 and
3.18.4). Listing the operands that may BE such an object lost to `index`, `get`,
`first`, pipelines and `range` bindings in turn, so the rule is inverted.

  * FRESH: a plain variable `$x` that some binding in the same file gave a single
    `dict ...`/`list ...` call (or `set $y ...` and kin, which return a fresh
    `$y`), and that no binding anywhere in the file gave anything else, a
    `range`/`with` binding included. That is the map's identity: a new map.
  * VALUES-ONLY: a variable bound only to safe reads. A SAFE ARGUMENT is a
    literal, a bare PURE function (`list`), `.Values...` while `.` is the real
    root, `$.Values...` while `$` is, a values-only variable or a path into one,
    or a parenthesised PURE call on these; a values-only variable is bound only to
    such arguments through PURE or reading functions (`index`, `get`, `default`).
    A fresh `dict`/`list` on safe arguments is values-only.
  * VALUES MAPS ARE SHARED GO MAPS, AND A MERGE WRITES DEEP (actions#206): mergo
    walks into every nested map the target holds, `.Values` maps included, so a
    merge source holding `.Release` plants it there. So a `set` whose value is
    not a safe argument (or arrives through the pipe) PLANTS: its target stops
    being values-only, and so does every name a returning binding gave the same
    map (`$y := set $d ...`).

`set`/`unset` (shallow, measured by the #206 review) need a FRESH target. A merge,
`deepCopy` and a `tpl` context need a FRESH, VALUES-ONLY variable, and every
merge source must be a safe argument; a source through the pipe is unknown.
Every other target or source is NOT_MODELLED. Both properties are greatest fixed
points per file: the scan starts by assuming every variable has both and repeats
until nothing more is spoiled, and a refused binding spoils what it binds (and
reaches its alias), so the loop always ends.

None of these can be allowlisted: a form the gate cannot read is rewritten, not
excused.

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
# built-in print a value. So each is accepted ONLY on a FRESH variable (below), and
# every other target is NOT_MODELLED.
MUTATORS = frozenset(
    {"set", "unset", "merge", "mergeOverwrite", "mustMerge", "mustMergeOverwrite", "deepCopy", "mustDeepCopy"}
)
# The mutators whose result is their first operand: `$m = set $m ...` keeps `$m` fresh.
RETURNS_TARGET = frozenset({"set", "unset", "merge", "mergeOverwrite", "mustMerge", "mustMergeOverwrite"})
# SHALLOW writers touch only the target map's own keys (the actions#206 review
# measured `set`/`unset` shallow). MERGERS write DEEP: mergo walks into a nested
# map the target shares with `.Values` or with any map an argument held.
SHALLOW = frozenset({"set", "unset"})
MERGERS = frozenset({"merge", "mergeOverwrite", "mustMerge", "mustMergeOverwrite"})
# A safety net, not a bound the fixed point needs: each pass only shrinks the
# assumptions, so a chart converges in one pass per variable at most.
MAX_PASSES = 200
FRESH_CONSTRUCTORS = frozenset({"dict", "list"})
# Pure functions a fresh constructor's argument may call: none returns an operand map.
PURE = frozenset(
    {"dict", "list", "default", "quote", "toString", "int", "int64", "printf", "toYaml", "toJson", "lower", "upper", "trim"}
)
# Functions that read INTO their operand and return part of it, or a value: on a
# values-only operand their result is values-only.
READERS = frozenset({"index", "get", "first", "last", "pluck", "dig", "default", "required", "coalesce"})
VARIABLE_PATH = re.compile(r"^\$(\w+)(?:\.[\w.]*)?$")
LITERAL = re.compile(r"^(?:true|false|nil|-?[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][-+]?[0-9]+)?|0[xX][0-9a-fA-F_]+)$")
PLAIN_VARIABLE = re.compile(r"^\$(\w+)$")
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
    values: set  # variables bound only to values-only pipelines, never planted
    ident: set  # variables bound only to a fresh `dict`/`list` (FRESH), any arguments
    record: dict = None  # this pass's bookkeeping, for spoils a mutator causes
    piped: bool = False  # the command receives the previous stage as its last argument


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


def safe_argument(term, ctx: Context) -> bool:
    """A fresh constructor's argument that cannot be, or hold, the root or a built-in.

    A string or number literal, a PURE function called bare (`list`), `.Values...`
    while `.` is the real root, `$.Values...` while `$` is, a VALUES-ONLY variable or
    a path into one, or a parenthesised call of a PURE function on such arguments.
    A values map holds strings, numbers, lists and maps, never a Go object.
    """
    if isinstance(term, Text):
        return True
    if isinstance(term, Group):
        return values_only(term.pipeline, ctx, PURE)
    word = term.text
    if LITERAL.match(word) or word in PURE:
        return True
    variable = VARIABLE_PATH.match(word)
    if variable and variable.group(1) in ctx.values:
        return True
    if (word == ".Values" or word.startswith(".Values.")) and ctx.dot == ROOT:
        return True
    if (word == "$.Values" or word.startswith("$.Values.")) and ctx.dollar == ROOT:
        return True
    return False


def values_only(pipeline: list, ctx: Context, functions: frozenset) -> bool:
    """Every command calls one of `functions` (or none) on safe arguments only."""
    for command in pipeline:
        terms = command
        if command and isinstance(command[0], Word) and command[0].text[:1] not in ".$" and not LITERAL.match(command[0].text):
            if command[0].text not in functions:
                return False
            terms = command[1:]
        if not all(safe_argument(t, ctx) for t in terms):
            return False
    return True


def is_fresh_pipeline(pipeline: list, ctx: Context) -> bool:
    """`dict ...`/`list ...` (any arguments), or a mutator returning a fresh variable.

    Freshness is the map's IDENTITY only. Whether its contents are safe is the
    separate values-only question: a fresh map on safe arguments is values-only,
    because `dict`/`list` are PURE, until a `set` plants something else in it.
    """
    if len(pipeline) != 1 or not pipeline[0]:
        return False
    command = pipeline[0]
    name = head(command)
    if name in FRESH_CONSTRUCTORS:
        return True
    target = returned_target(command)
    return bool(target) and target in ctx.ident


def returned_target(command: list):
    """The variable a RETURNS_TARGET call hands back (`set $d ...` -> "d"), or None."""
    if head(command) in RETURNS_TARGET and len(command) > 1 and isinstance(command[1], Word):
        target = PLAIN_VARIABLE.match(command[1].text)
        return target.group(1) if target else None
    return None


def check_mutation(command: list, carried, ctx: Context) -> None:
    """Accept a set-family call or `tpl` ONLY on a fresh variable; everything else is NOT_MODELLED.

    The rule is inverted on purpose (review of actions#202): listing what may BE the
    root or a built-in lost to `index`, `get`, `first`, pipelines and `range`
    bindings in turn, so the gate names the one target it can prove is not.

    The actions#206 review then showed that a values map is a shared Go map and that
    a merge writes DEEP into it. So:

      * `set`/`unset` (shallow) need a target whose IDENTITY is fresh. A `set` whose
        value is not a safe argument PLANTS it: the target stops being values-only,
        and so does every name a returning binding gave it.
      * a merge needs a STRICT target (fresh on safe arguments, never planted) and
        every source a safe argument; a source arriving through the pipe is unknown.
      * `deepCopy` and a `tpl` context need the same fresh, unplanted variable.
    """
    name = head(command)
    target = command[1] if len(command) > 1 else None
    if name == "tpl":
        if len(command) < 3 or ctx.piped or not is_variable_in(command[2], ctx.ident, ctx.values):
            raise Refusal("a `tpl` whose context is not a variable bound only to a fresh, unplanted `dict`/`list`")
        return
    if name not in MUTATORS:
        return
    if name in SHALLOW:
        if name == "set":
            value = command[3:4] if len(command) > 3 else []
            if ctx.piped or not value or not safe_argument(value[0], ctx):
                planted = variable_name(target)
                if planted and ctx.record is not None:
                    ctx.record["values_spoiled"].add(planted)
        if target is None or not is_variable_in(target, ctx.ident):
            raise Refusal(f"a `{name}` whose first operand is not a variable bound only to a fresh `dict`/`list`")
    else:
        if target is None or not is_variable_in(target, ctx.ident, ctx.values):
            raise Refusal(f"a `{name}` whose first operand is not a variable bound only to a fresh, unplanted `dict`/`list`")
        if name in MERGERS and (ctx.piped or not all(safe_argument(term, ctx) for term in command[2:])):
            raise Refusal(f"a `{name}` with a source that is not a literal or values read: a merge writes deep into shared maps")
    tainted = carried[0] or carried[1] or any(any(term_eval(term, ctx)) for term in command[2:])
    if tainted:
        ctx.variables[variable_name(target)] = True


def variable_name(term):
    if not isinstance(term, Word):
        return None
    match = PLAIN_VARIABLE.match(term.text)
    return match.group(1) if match else None


def is_variable_in(term, *sets) -> bool:
    name = variable_name(term)
    return bool(name) and all(name in names for names in sets)


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
    for index, command in enumerate(pipeline):
        ctx.piped = index > 0
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


class _Everything:
    """The optimistic first assumption: every name. Each pass can only shrink it."""

    def __contains__(self, item) -> bool:
        return True


EVERYTHING = _Everything()


def scan(
    source: str, where: str, verdict: Verdict, emit, defines: frozenset = frozenset(), sensitive=None, fresh=None
) -> None:
    """Judge every output action of one template.

    `emit(line, expression)` reports a raw print and returns whether the allowlist
    excuses it. `sensitive` gains every define found to read its argument.

    `fresh` carries this file's ASSUMED fresh and values-only variables
    (`ident`, `values`; absent: every name) and records what this pass's bindings
    show (`ident_seen`, `ident_spoiled`, `values_seen`, `values_spoiled`, and the
    `aliases` a returning binding creates). A variable is FRESH
    when some binding gave it a fresh `dict`/`list` and NO binding anywhere in the
    file gave it anything else: the strictest state wins, so a reassignment later
    in a loop body still spoils an earlier `set`. The caller repeats the scan,
    feeding each pass's result back, until the assumptions stop shrinking.
    """
    sensitive = set() if sensitive is None else sensitive
    fresh = {} if fresh is None else fresh
    for key in ("values_seen", "values_spoiled", "ident_seen", "ident_spoiled", "aliases"):
        fresh[key] = set()
    frames = [Frame("file", ROOT, ROOT, ROOT, None, {})]
    clean_values = fresh.get("values", EVERYTHING)
    identity = fresh.get("ident", EVERYTHING)

    def context(dot=None) -> Context:
        frame = frames[-1]
        return Context(
            frame.dot if dot is None else dot, frame.dollar, frame.variables, defines, sensitive, clean_values,
            identity, fresh,
        )

    def evaluate(expression: str, dot=None):
        pipeline = parse(expression)
        ctx = context(dot)
        return pipeline, pipeline_eval(pipeline, ctx), ctx

    def bind(names: list, pipeline: list, value, ctx: Context, declare: bool, binding: bool = False) -> None:
        tainted = value[0] or value[1]
        is_ident = not binding and is_fresh_pipeline(pipeline, ctx)
        # `$y := set $d ...` names the SAME map: a plant into either reaches both.
        alias = returned_target(pipeline[0]) if not binding and len(pipeline) == 1 and pipeline[0] else None
        # A `range`/`with` binding over a values-only pipeline holds its elements.
        # A returning call names its target's map, so it shares the target's state.
        is_values = (alias in ctx.values) if alias else values_only(pipeline, ctx, PURE | READERS)
        for name in names:
            variables = frames[-1].variables
            variables[name] = tainted if declare else (variables.get(name, True) or tainted)
            (fresh["values_seen"] if is_values else fresh["values_spoiled"]).add(name)
            (fresh["ident_seen"] if is_ident else fresh["ident_spoiled"]).add(name)
            if alias:
                fresh["aliases"].add((name, alias))

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
                        bind(names, pipeline, value, ctx, True, binding=True)
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
                        bind(names, pipeline, value, ctx, True, binding=True)
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
                # A refused binding SPOILS what it binds. Skipping it instead would
                # make the fixed point flip: `$x = tpl "a" $x` is accepted only
                # while `$x` is fresh, and accepted it spoils `$x`.
                for name in bound_names(body):
                    fresh["values_spoiled"].add(name)
                    fresh["ident_spoiled"].add(name)
                    alias = refused_alias(body)
                    if alias:
                        fresh["aliases"].add((name, alias))
                verdict.errors.append(f"NOT_MODELLED {where}:{line}: {refusal} in `{{{{ {normalise(body)} }}}}`")
    except Refusal as refusal:
        verdict.errors.append(f"NOT_MODELLED {where}: {refusal}")
        return
    if len(frames) > 1:
        verdict.errors.append(f"NOT_MODELLED {where}: a `{frames[-1].kind}` block is never closed by `end`")


def refused_alias(body: str):
    """The target a refused `$y := set $d ...` would have named, so its spoil reaches `$d`."""
    assignment = ASSIGNMENT.match(body)
    if not assignment:
        return None
    try:
        pipeline = parse(assignment.group("rhs"))
    except Refusal:
        return None
    return returned_target(pipeline[0]) if len(pipeline) == 1 and pipeline[0] else None


def bound_names(body: str) -> list:
    """The variables an action binds: an assignment, or a `range`/`with` (or `else with`) binding."""
    assignment = ASSIGNMENT.match(body)
    if assignment:
        return [assignment.group("name")]
    binding = BINDING.match(re.sub(r"^else\s+", "", body))
    return re.findall(r"\$(\w+)", binding.group("names")) if binding else []


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


def spread_plants(state: dict) -> None:
    """A plant into one name of a map reaches every name a returning binding gave it."""
    changed = True
    while changed:
        changed = False
        for left, right in state["aliases"]:
            if (left in state["values_spoiled"]) != (right in state["values_spoiled"]):
                state["values_spoiled"].update((left, right))
                changed = True


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

    # SENSITIVITY AND FRESHNESS ARE FIXED POINTS: a define that passes its
    # argument to a sensitive define is sensitive, wherever the two live, and a
    # variable is fresh only if no binding in its file spoils it. Each pass can
    # only add names, so the loop ends.
    sensitive: set = set()
    fresh = {path: {} for path in sources}

    def snapshot():
        return set(sensitive), {p: (f.get("values"), f.get("ident")) for p, f in fresh.items()}

    passes = 0
    while True:
        passes += 1
        if passes > MAX_PASSES:
            return Verdict(errors=[f"NOT_MODELLED {chart.relative_to(root).as_posix() or '.'}: the fixed point did not converge in {MAX_PASSES} passes"])
        before = snapshot()
        for path, source in sources.items():
            if source is not None:
                state = fresh[path]
                scan(source, "", Verdict(), lambda line, expression: None, defines, sensitive, state)
                spread_plants(state)
                state["values"] = frozenset(state["values_seen"] - state["values_spoiled"])
                state["ident"] = frozenset(state["ident_seen"] - state["ident_spoiled"])
        if before == snapshot():
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

        scan(source, where, verdict, emit, defines, sensitive, fresh[path])
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
