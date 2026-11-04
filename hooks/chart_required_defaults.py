#!/usr/bin/env python3
"""YAADGAAR 332 (ADR-0705, ADR-1143): a GATED chart key carries no default.

ADR-0705 says a consequential knob has no default, so an adopter's render fails
until they state it. Ruling 1 of yaadgaar 327 chose WHICH keys this gate sees: the
keys a chart already declares required, plus a prefix rule. So a key is GATED
when any of these holds (ADR-1143 decision 6):

  (a) a template passes it to `required` AND the REQUIRED list does not classify it
      DEFAULT-OK;
  (b) a `values.schema.json` block lists it in `required`;
  (c) it sits under a path segment named `tls`, at any depth (`tls.*`, `*.tls.*`).

A GATED KEY MUST BE ABSENT FROM `values.yaml`, OR NULL. A default defeats both
mechanisms: helm merges `values.yaml` before it validates the schema and before
`required` runs, so a defaulted key is required on paper and optional in effect.
`#564` counted 52 schema-required leaves in that state.

ARM (a) READS THE REQUIRED LIST (ADR-1143, yaadgaar 571). A template `required` on
a key the ADR-0705 list classifies DEFAULT-OK is the refuse-empty idiom: the default
serves most adopters, and `required` stops one from blanking the key. Arm (a)
allows that default. The list is `data/required-knobs.json`, keyed by the `name:`
in Chart.yaml, generated from the plan's per-chart tables by
`scripts/required_knobs.py` and held equal to a vendored copy of them by
`scripts/tests/test_required_knobs.py`. FAIL CLOSED (decision 7): only an EXACT
DEFAULT-OK row exempts a key. A chart the list does not name, a key it does not
classify (an ancestor, a descendant, a new knob, an `index` segment the list cannot
spell) and every key of a list that is missing or malformed count as REQUIRED, and
a missing or malformed list is REQUIRED_LIST_MALFORMED, red. Arms (b) and (c) do not
read the list: a DEFAULT-OK key the schema requires or that sits under `tls` stays
gated, and such rows stay in the baselines until their status is resolved.

WHAT "A DEFAULT" MEANS, measured on helm 4.3.0. `required` refuses nil AND "",
and passes 0 and false. So "" is no default for a key a template `required`s
(whether or not arm (a) gates it: that is helm, not the list), and is a default for a key only (b) or (c) gates: "" satisfies a schema
`required` and renders into the manifest. A key written with nothing after the
colon, `null` or `~` is unset. A mapping is a default when any leaf under it is
one. A sequence, a flow collection or a block scalar at a gated path is a default.

THE RATCHET. Today's violations are listed, one dotted key per line, in
`chart/ci/required-defaults-baseline.txt`:

    tls.caSecret  # ADR-1140 R-a: trust anchor; argocd sets it before the default goes

The `# ADR-1140 <row>: <reason>` part is required: every exception carries the
ratified row it maps to and why it is still here. The gate is RED when

  NEW_DEFAULT       a gated key has a default the baseline does not list;
  STALE_BASELINE    a baseline entry is no longer a violation (shrink-only);
  BASELINE_GREW     the baseline lists a key the base's baseline did not.

No baseline file is an empty baseline. A tls.* key that a chart defaults on
purpose (an ADR-0845 `tls.enabled` switch while consumers catch up) is an
exception only through the baseline, with its reason. There is no other allowlist.

WHERE THE BASE COMES FROM. A pre-commit hook does not see the pull request.
`--base REV` wins. In GitHub Actions the base is `pull_request.base.sha` from
`$GITHUB_EVENT_PATH` (the precommit job checks out with `fetch-depth: 0`), else
`HEAD^1`. A local run compares the working tree against `HEAD`. A base that
cannot be read is BASE_UNREADABLE, never a pass. Outside any git repository
there is no history to grow from, and the success line says the half was skipped.

ADOPTION IS PER CLASS, THROUGH FAN-OUT. A repository's hook list is rendered from
its class in `classes.json`; an id a repository adds to its own
`.pre-commit-config.yaml` is dropped as `extra:` on the next release, and an
override cannot add a hook its class lacks. So the order is: each repository's
baseline lands first, and NOTHING CHECKS IT until its class names this hook; then
one change adds the id to the class. Growth is judged only when the BASE's
`.pre-commit-config.yaml` already references `chart-required-defaults`, so the
change that adopts writes each baseline whole, and the success line names that case.

THE TEMPLATE PARSER IS A TOKENISER, NOT A GREP (ADR-1136). `actions()` and
`stages()`' quoting rules are ported from yadgarhq/platform's
`scripts/tests/test_raw_prints.py`: an action ends at the first `}}` outside a
string literal, and a comment is only an action that opens with `/*`. A `required`
counts only as the function a command calls, so `(list "off" "required")` and
`.Values.database.instance.tls.required` are not calls. Resolved operands:

    required "m" .Values.a.b            required "m" (index .Values "a" "b")
    required "m" $.Values.a.b           .Values.a.b | required "m"
    required "m" $v.b                   required "m" (get $v "b")

where `$v` is assigned exactly once in the file, to a plain values path. Any
other operand is UNRESOLVED_REQUIRED, naming the file, the line and the form: a
key the gate cannot name is a key it cannot judge, so it is red, not skipped. Also
UNRESOLVED_REQUIRED: a `tpl` whose template string, a literal or a values string,
is not plain (yaadgaar 575: the string is parsed as a template, and any
`required`, `include`, `template`, `block`, nested `tpl` or unparsable action in
it is refused; a values string the scan cannot read is refused too); a `tpl` of
anything else (`printf`, a computed pipeline such as `toYaml`, or a variable this
gate cannot bind to a values path; a variable bound once to a values path is
resolved and judged); and an `include`, `template` or `block` that hands a helper
reaching `required` or a `tpl` (directly, through another helper, or through any
helper named dynamically) a context other than its caller's, since the helper's
`.Values.x` then names a different key. When NO helper in the chart reaches
`required` or a `tpl`, a helper reached only through a dynamic name is still
refused under another context, as UNRESOLVED_INCLUDE: the gate cannot follow the
name (a subchart's helper, say), and says so rather than claim a `required`. The caller's context is `$`, or a
`.` that no `with`, `range`, `else with` or `else range` has rebound. At top level
that is the chart root. Inside a define it is the define's own argument, NEVER the
root: such a site is not refused itself, it makes the define reaching, and the
define's own callers are judged instead. Every
file under `templates/` is scanned whatever its suffix, as helm parses them all; a
file that is not UTF-8 text is UNSCANNED_TEMPLATE.

THE SCHEMA WALK follows `properties` from the root. A `required` under `if` is a
condition, and one under `items`, `prefixItems` or `contains` speaks of array
elements, which have no dotted path; neither gates a key. A `required` under any
other keyword (`allOf`, `anyOf`, `oneOf`, `then`, `else`, `not`,
`additionalProperties`, ...) is UNMODELLED_SCHEMA. So is every keyword the walk does
not model: `$ref`, `$dynamicRef`, `$recursiveRef`, `dependentRequired`, draft-07
`dependencies` in both forms, a `required` that is not a list, and any unknown word.
Only the annotation and single-value validation keywords in ANNOTATION_KEYWORDS pass.

`language: script` AND STANDARD LIBRARY ONLY, as every chart hook here. The
`values.yaml` scan is `chart_baseline.scan_values`, which the suite already holds
equal to PyYAML over the estate's files; it is imported, not copied (ADR-0679).

Run:   python3 hooks/chart_required_defaults.py [--root DIR] [--base REV] [--report]
Tests: python3 -m pytest scripts/tests/test_chart_required_defaults.py scripts/tests/test_required_knobs.py -q
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chart_baseline import NOT_MODELLED, Values, find_charts, scan_values  # noqa: E402

HOOK = "chart-required-defaults"
BASELINE = Path("ci") / "required-defaults-baseline.txt"
# The ADR-0705 REQUIRED list (ADR-1143 decision 7). Shipped beside the hook, never
# taken from the command line or the environment: a consumer cannot aim the gate at
# a more permissive list.
KNOBS = Path(__file__).resolve().parent / "data" / "required-knobs.json"
CHART_NAME = re.compile(r"""^name:\s*(?P<q>["']?)(?P<name>[^"'#\s]+)(?P=q)\s*(#.*)?$""", re.M)
NULL_WORDS = frozenset({"null", "Null", "NULL", "~"})
EMPTY_STRINGS = frozenset({'""', "''"})
ARRAY_KEYWORDS = frozenset({"items", "prefixItems", "contains", "additionalItems", "unevaluatedItems"})
SUBSCHEMA_KEYWORDS = frozenset(
    {"then", "else", "not", "additionalProperties", "unevaluatedProperties", "propertyNames"}
)
MAP_KEYWORDS = frozenset({"patternProperties", "dependentSchemas", "$defs", "definitions"})
LIST_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf"})
# Keywords that validate or annotate one value and can hold no subschema, so no
# `required` hides under them. Everything not here or above is refused.
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema", "$id", "$comment", "$anchor", "$vocabulary", "title", "description",
        "type", "enum", "const", "default", "examples", "deprecated", "readOnly", "writeOnly",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minLength", "maxLength", "pattern", "format", "contentMediaType", "contentEncoding",
        "minItems", "maxItems", "uniqueItems", "minContains", "maxContains",
        "minProperties", "maxProperties",
    }
)
BASELINE_LINE = re.compile(
    r"^(?P<key>\S+)\s+#\s*ADR-1140\s+(?P<row>[^:]+?)\s*:\s*(?P<reason>.*?)\s*$"
)
DOTTED_KEY = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")
SEGMENT = re.compile(r"^[A-Za-z0-9_\-]+$")
ADOPTED = re.compile(r"^\s*-\s*id:\s*[\"']?" + re.escape(HOOK) + r"[\"']?\s*(#.*)?$", re.M)
ASSIGNMENT = re.compile(r"^\$(?P<name>\w+)\s*:?=\s*(?P<rhs>.+)$", re.S)
BINDING = re.compile(r"^(?:range|with)\s+(?P<names>\$\w+(?:\s*,\s*\$\w+)?)\s*:?=", re.S)
CONTROL = re.compile(r"^(?:else\s+if|else\s+with|if|with|range|else|end|define|block|template|break|continue)\b")


class Refusal(Exception):
    """A form this gate cannot resolve. Always red, never a pass."""


# ------------------------------------------------------------- template actions


def actions(source: str):
    """Yield (offset, body, is_comment) for every `{{ }}` action.

    Ported from yadgarhq/platform `scripts/tests/test_raw_prints.py` (ADR-1136):
    an action ends at the first `}}` outside a string literal, and a comment is
    only an action that OPENS with `/*`. An unclosed action raises Refusal.
    """
    index = 0
    while True:
        start = source.find("{{", index)
        if start < 0:
            return
        cursor = start + 2
        if source.startswith("-", cursor) and source[cursor + 1 : cursor + 2].isspace():
            cursor += 1
        head = cursor
        while head < len(source) and source[head] in " \t\n":
            head += 1
        if source.startswith("/*", head):
            close = source.find("*/", head + 2)
            end = source.find("}}", close + 2) if close >= 0 else -1
            if close < 0 or end < 0:
                raise Refusal(f"an unclosed comment at line {line_of(source, start)}")
            yield start, "", True
            index = end + 2
            continue
        quote, position = "", cursor
        while position < len(source):
            character = source[position]
            if quote:
                if character == "\\" and quote in "\"'":
                    position += 1
                elif character == quote:
                    quote = ""
            elif character in "\"`'":
                quote = character
            elif source.startswith("}}", position):
                break
            position += 1
        if position >= len(source):
            raise Refusal(f"an unclosed action at line {line_of(source, start)}")
        body = source[cursor:position]
        if body.rstrip().endswith(" -") or body.rstrip() == "-":
            body = body.rstrip()[:-1]
        yield start, body.strip(), False
        index = position + 2


def line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


@dataclass
class Word:
    text: str


@dataclass
class Text:
    text: str
    quote: str = '"'


@dataclass
class Group:
    pipeline: list


def tokens(expression: str) -> list:
    """Split an action body into string literals, parentheses, pipes and words."""
    found, index = [], 0
    while index < len(expression):
        character = expression[index]
        if character.isspace():
            index += 1
        elif character in "\"'`":
            end = index + 1
            while end < len(expression) and expression[end] != character:
                end += 2 if expression[end] == "\\" and character != "`" else 1
            if end >= len(expression):
                raise Refusal("an unclosed string literal")
            found.append(("text", expression[index + 1 : end], character))
            index = end + 1
        elif character in "()|":
            found.append((character, character))
            index += 1
        else:
            end = index
            while end < len(expression) and not expression[end].isspace() and expression[end] not in "()|\"'`":
                end += 1
            found.append(("word", expression[index:end]))
            index = end
    return found


def parse(expression: str) -> list:
    """A pipeline: a list of commands, each a list of Word, Text and Group terms."""
    stream = tokens(expression)
    pipeline, position = parse_pipeline(stream, 0)
    if position != len(stream):
        raise Refusal("an unbalanced `)`")
    return pipeline


def parse_pipeline(stream: list, position: int):
    pipeline, command = [], []
    while position < len(stream):
        kind, value = stream[position][:2]
        if kind == ")":
            break
        if kind == "|":
            pipeline.append(command)
            command = []
            position += 1
        elif kind == "(":
            inner, position = parse_pipeline(stream, position + 1)
            if position >= len(stream) or stream[position][0] != ")":
                raise Refusal("an unclosed `(`")
            command.append(Group(inner))
            position += 1
        elif kind == "text":
            command.append(Text(value, stream[position][2]))
            position += 1
        else:
            command.append(Word(value))
            position += 1
    pipeline.append(command)
    return pipeline, position


def strip_control(body: str) -> str:
    """The pipeline an action evaluates, with its control word or binding removed."""
    binding = BINDING.match(body)
    if binding:
        return body[binding.end() :].strip()
    match = CONTROL.match(body)
    if match:
        word = match.group(0)
        if word in ("end", "else", "break", "continue"):
            return ""
        return body[match.end() :].strip()
    assignment = ASSIGNMENT.match(body)
    if assignment:
        return assignment.group("rhs").strip()
    return body


def bindings(source: str) -> dict:
    """Every variable a file assigns, by name, with each right-hand side (None: bound by range/with)."""
    found: dict = {}
    for _, body, is_comment in actions(source):
        if is_comment:
            continue
        binding = BINDING.match(body)
        if binding:
            for name in re.findall(r"\$(\w+)", binding.group("names")):
                found.setdefault(name, []).append(None)
            continue
        assignment = ASSIGNMENT.match(body)
        if assignment:
            found.setdefault(assignment.group("name"), []).append(assignment.group("rhs").strip())
    return found


class Resolver:
    def __init__(self, variables: dict):
        self.variables = variables

    def variable(self, name: str, depth: int) -> list:
        assigned = self.variables.get(name, [])
        if len(assigned) == 0:
            raise Refusal(f"`${name}` is not bound in this file")
        if len(assigned) > 1:
            raise Refusal(f"`${name}` is assigned {len(assigned)} times in this file")
        rhs = assigned[0]
        if rhs is None or depth > 8:
            raise Refusal(f"`${name}` is not bound to a plain values path (bound by range or with)")
        try:
            pipeline = parse(rhs)
            if len(pipeline) != 1:
                raise Refusal("a pipeline")
            return self.command(pipeline[0], depth + 1)
        except Refusal:
            raise Refusal(f"`${name}` is not bound to a plain values path: `{rhs}`") from None

    def word(self, text: str, depth: int) -> list:
        for root in (".Values", "$.Values"):
            if text == root:
                return []
            if text.startswith(root + "."):
                return text[len(root) + 1 :].split(".")
        if text.startswith("$") and len(text) > 1:
            name, _, rest = text[1:].partition(".")
            base = self.variable(name, depth)
            return base + (rest.split(".") if rest else [])
        if text.startswith("."):
            raise Refusal(f"`{text}` is relative to `.`, which this gate does not track")
        raise Refusal(f"`{text}` is not a values path")

    def term(self, term, depth: int) -> list:
        if isinstance(term, Word):
            return self.word(term.text, depth)
        if isinstance(term, Group):
            if len(term.pipeline) != 1:
                raise Refusal("a parenthesised pipeline is not a values path")
            return self.command(term.pipeline[0], depth)
        raise Refusal(f'the string "{term.text}" is not a values path')

    def command(self, command: list, depth: int) -> list:
        if not command:
            raise Refusal("an empty command")
        if len(command) == 1:
            return self.term(command[0], depth)
        head = command[0]
        if isinstance(head, Word) and head.text in ("index", "get"):
            if head.text == "get" and len(command) != 3:
                raise Refusal("`get` takes a map and one key")
            base = self.term(command[1], depth)
            keys = []
            for argument in command[2:]:
                if not isinstance(argument, Text):
                    raise Refusal(f"an `{head.text}` key that is not a string literal")
                keys.append(argument.text)
            return base + keys
        name = head.text if isinstance(head, Word) else "a group"
        raise Refusal(f"`({name} ...)` is not a form this gate resolves")


@dataclass
class Facts:
    """What one template file says about `required`, tagged with the enclosing `define`."""

    found: dict = field(default_factory=dict)  # dotted path -> {define name or None}
    refused: list = field(default_factory=list)  # (message, define name or None)
    # (at, helper name or None, context is the CALLER'S, context text, define). The
    # caller's context is the chart root at top level; inside a define it is that
    # define's own argument (`.` not rebound, or `$`), judged at the define's callers.
    sites: list = field(default_factory=list)
    tpl_paths: list = field(default_factory=list)  # (at, dotted values path a `tpl` renders)
    # Every define that holds a `tpl` site (yaadgaar 575): its `.Values.x` and the
    # `tpl` context are the define's argument, so the define reaches like `required`.
    tpl_defines: set = field(default_factory=set)
    # Dotted paths from an `index`/`get` literal that is not a plain name (`"a.b"`,
    # `"*"`): the joined path does not spell the key helm reads, so the REQUIRED
    # list cannot exempt it (ADR-1143 decision 7).
    unspelled: set = field(default_factory=set)


OPENER = re.compile(r"^(define|block|with|range|if|end|else|template)\b")
REBINDING_ELSE = re.compile(r"^else\s+(with|range)\b")


def template_facts(source: str, where: str) -> Facts:
    facts = Facts()
    try:
        variables = bindings(source)
        every = list(actions(source))
    except Refusal as error:
        facts.refused.append((f"{where}: {error}", None))
        return facts
    resolver = Resolver(variables)
    # Open blocks, innermost last: ("define", name) or ("scope", word). `.` is the
    # caller's context only when nothing rebound it since the enclosing define: no
    # `with`, `range`, `else with` or `else range`. Inside a define, `.` and `$` are
    # the define's argument, never the chart root.
    stack: list = []
    for offset, body, is_comment in every:
        if is_comment:
            continue
        at = f"{where}:{line_of(source, offset)}"
        define = next((name for kind, name in reversed(stack) if kind == "define"), None)
        dot_root = True
        for kind, _ in reversed(stack):
            if kind == "define":
                break
            if _ in ("with", "range"):
                dot_root = False
                break
        opener = OPENER.match(body)
        word = opener.group(1) if opener else ""
        if word == "end":
            if stack:
                stack.pop()
            continue
        try:
            if word == "template":
                pipeline = [[Word("template")] + parse(body[len("template"):])[0]]
            else:
                pipeline = parse(strip_control(body))
        except Refusal as error:
            facts.refused.append((f"{at}: {error} in `{body}`", define))
            continue
        for event in calls(pipeline, resolver):
            if event[0] == "path":
                facts.found.setdefault(event[1], set()).add(define)
                if not event[2]:
                    facts.unspelled.add(event[1])
            elif event[0] == "refused":
                facts.refused.append((f"{at}: {event[1]} in `{' '.join(body.split())}`", define))
            elif event[0] == "tpl":
                facts.tpl_paths.append((at, event[1]))
            if event[0] in ("tpl", "tpl-site"):
                facts.tpl_defines.add(define)
            elif event[0] == "site":
                _, name, context = event
                root = isinstance(context, Word) and (context.text == "$" or (context.text == "." and dot_root))
                facts.sites.append((at, name, root, describe_term(context), define))
        if word in ("define", "block"):
            head = pipeline[0][0] if pipeline and pipeline[0] else None
            if word == "block":
                # `block "n" ctx` defines `n` AND calls it with `ctx` at once.
                context = pipeline[0][1] if len(pipeline[0]) > 1 else None
                root = isinstance(context, Word) and (context.text == "$" or (context.text == "." and dot_root))
                facts.sites.append((at, head.text if isinstance(head, Text) else None, root, describe_term(context), define))
            stack.append(("define", head.text if isinstance(head, Text) else None))
        elif word in ("with", "range", "if"):
            stack.append(("scope", word))
        elif word == "else" and stack and REBINDING_ELSE.match(body):
            # `else with X` (and `else range X`) rebinds `.` for the rest of the block.
            stack[-1] = ("scope", "with")
    return facts


def describe_term(term) -> str:
    if term is None:
        return "no context"
    if isinstance(term, Word):
        return f"`{term.text}`"
    if isinstance(term, Text):
        return f'the string "{term.text}"'
    return "a parenthesised expression"


def template_required(source: str, where: str):
    """(paths a template `required`s, refusals naming file:line and form), for one file."""
    facts = template_facts(source, where)
    return set(facts.found), [message for message, _ in facts.refused]


def helper_refusals(every: list) -> list:
    """Refuse each `include`/`template` that hands a non-root context to a helper reaching `required`.

    A helper resolves `.Values.x` against the context it is given; under any context
    but the root, the key it names is not `x`. Reaching is transitive: a define that
    includes a reaching helper, or includes ANY helper by a dynamic name, reaches
    `required` too, and its own callers are judged. A site inside a define that
    hands on the define's own context (`.` or `$`) is therefore not refused itself:
    it rides on the caller's context.
    """
    seeds = {define for facts in every for defines in facts.found.values() for define in defines}
    seeds |= {define for facts in every for _, define in facts.refused}
    seeds |= {define for facts in every for define in facts.tpl_defines}
    seeds.discard(None)

    def closure(start: set, dynamic: bool) -> set:
        reached = set(start)
        while True:
            grown = {site[4] for facts in every for site in facts.sites if site[1] in reached or (dynamic and site[1] is None)}
            grown.discard(None)
            if grown <= reached:
                return reached
            reached |= grown

    # `named`: reaches `required` or a `tpl` through helpers named by a literal.
    # `reaching`: also through any helper named dynamically, which could be any.
    named, reaching = closure(seeds, False), closure(seeds, True)
    refused = []
    if not reaching:
        return refused
    for facts in every:
        for at, name, root, context, _ in facts.sites:
            if root or (name is not None and name not in reaching):
                continue
            if not seeds:
                # Nothing in this chart reaches `required` or a `tpl`; the gate still
                # cannot follow a dynamic name (a subchart's helper, say). Refused,
                # under its own name, without claiming a `required` (yaadgaar 575).
                what = "an `include` or `template` whose name is not a string literal" if name is None else f'`"{name}"`, which includes a helper by a name that is not a string literal,'
                refused.append(f"UNRESOLVED_INCLUDE: {at}: {what} is called with {context}, not the root context. No template this gate scanned calls `required` or `tpl` in a helper, but it cannot follow a dynamic name, so it cannot tell which key that helper reads")
            elif name is None:
                # Any helper is safe under the root context, so only a dynamic name
                # with another context is refused: it might be one that reaches `required`.
                refused.append(f"UNRESOLVED_REQUIRED: {at}: an `include` or `template` whose name is not a string literal is called with {context}, not the root context, in a chart where a helper reaches `required` or `tpl`")
            elif name in named:
                refused.append(f'UNRESOLVED_REQUIRED: {at}: `"{name}"` is called with {context}, not the root context, and it reaches `required` or `tpl`, so the key it names cannot be resolved')
            else:
                refused.append(f'UNRESOLVED_REQUIRED: {at}: `"{name}"` is called with {context}, not the root context, and it includes a helper by a name that is not a string literal, in a chart where a helper reaches `required` or `tpl`, so the key it names cannot be resolved')
    return refused


def unescape(literal: Text) -> str:
    """The string a Go template literal denotes. Raises Refusal on an escape it cannot read."""
    if literal.quote == "`" or "\\" not in literal.text:
        return literal.text
    try:
        return json.loads('"' + literal.text + '"')
    except ValueError:
        raise Refusal(f'the string "{literal.text}" carries an escape this gate cannot read') from None


def template_text_problem(text: str, where: str):
    """Why a template string a `tpl` renders cannot be accepted, or None (yaadgaar 575).

    The string is PARSED as a template, not grepped: any `required`, `include`,
    `template`, `block`, nested `tpl` or action the parser refuses is a problem. A
    plain `{{ .Values.x }}` is not. The substring `required` anywhere stays a problem.
    """
    if "required" in text:
        return "calls `required`"
    facts = template_facts(text, where)
    if facts.refused:
        return "holds an action this gate cannot parse"
    if facts.found or facts.sites or facts.tpl_defines:
        return "calls `include`, `template`, `block` or `tpl`"
    return None


def tpl_event(pipeline: list, index: int, arguments: list, resolver: Resolver):
    """A `tpl` renders a template string: read it, or refuse it. Never skipped.

    The string is the first argument, or the piped value (`"..." | tpl .`). A
    literal is parsed here as a template; a values path is judged against
    `values.yaml` by the caller; anything else (`printf`, a computed pipeline, a
    variable that is not bound once to a values path) cannot be read.
    """
    if len(arguments) == 2:
        template = arguments[0]
    elif len(arguments) == 1 and index > 0 and len(pipeline[index - 1]) == 1:
        template = pipeline[index - 1][0]
    else:
        return "refused", f"`tpl` with {len(arguments)} argument{'s' if len(arguments) != 1 else ''}, whose template this gate cannot read"
    if isinstance(template, Text):
        try:
            problem = template_text_problem(unescape(template), "a `tpl` literal")
        except Refusal as error:
            return "refused", f"`tpl` of a string literal this gate cannot read ({error})"
        if problem:
            return "refused", f"`tpl` of a string literal that {problem}, which this gate does not render"
        return "tpl-site", None
    if isinstance(template, Group) and not (
        len(template.pipeline) == 1
        and template.pipeline[0]
        and (len(template.pipeline[0]) == 1 or (isinstance(template.pipeline[0][0], Word) and template.pipeline[0][0].text in ("index", "get")))
    ):
        head = template.pipeline[0][0] if template.pipeline and template.pipeline[0] else None
        name = head.text if isinstance(head, Word) else "a group"
        return "refused", f"`tpl` of a computed template (`({name} ...)`), which this gate cannot read: it reads a string literal or a values path"
    try:
        path = resolver.term(template, 0)
    except Refusal as error:
        return "refused", f"`tpl` of a template this gate cannot read ({error})"
    if not path:
        return "refused", "`tpl` of the whole values tree"
    return "tpl", ".".join(path)


def calls(pipeline: list, resolver: Resolver):
    """Yield ("path", p, plain), ("refused", why) or ("site", helper, context) for a pipeline, nested ones included.

    `plain`: every segment of `p` is a plain name, so `p` spells the key helm reads.
    """
    for index, command in enumerate(pipeline):
        for position, term in enumerate(command):
            if isinstance(term, Group):
                yield from calls(term.pipeline, resolver)
            elif isinstance(term, Word) and term.text == "required" and position != 0:
                yield "refused", "`required` not at the head of a command"
        if not command or not isinstance(command[0], Word):
            continue
        head, arguments = command[0].text, command[1:]
        if head in ("include", "template"):
            name = arguments[0].text if arguments and isinstance(arguments[0], Text) else None
            if len(arguments) >= 2:
                context = arguments[1]
            elif index > 0 and len(pipeline[index - 1]) == 1:
                context = pipeline[index - 1][0]
            else:
                context = None
            yield "site", name, context
            continue
        if head == "tpl":
            yield tpl_event(pipeline, index, arguments, resolver)
            continue
        if head != "required":
            continue
        try:
            if len(arguments) == 2:
                path = resolver.term(arguments[1], 0)
            elif len(arguments) == 1 and index > 0:
                if index != 1:
                    raise Refusal("a piped operand longer than one command")
                path = resolver.command(pipeline[0], 0)
            else:
                raise Refusal(f"`required` with {len(arguments)} argument{'s' if len(arguments) != 1 else ''}")
            if not path:
                raise Refusal("an operand that is the whole values tree")
            yield "path", ".".join(path), all(SEGMENT.match(part) for part in path)
        except Refusal as error:
            yield "refused", str(error)


# ------------------------------------------------------------------ the schema


def schema_required(schema) -> tuple:
    """(dotted paths a schema `required`s unconditionally, refusals)."""
    found, refused = set(), []
    walk(schema, [], None, found, refused)
    return found, refused


def walk(node, path: list, context, found: set, refused: list) -> None:
    """Every keyword is either modelled here or refused: an unread keyword can carry a `required`."""
    if not isinstance(node, dict):
        return
    where = ".".join(path) or "the root"
    inherit = lambda word: context if context in ("array", "if") else word
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            for name, sub in value.items():
                walk(sub, path + [name], context, found, refused)
        elif key == "required" and isinstance(value, list):
            if context is None:
                found.update(".".join(path + [str(name)]) for name in value)
            elif context not in ("array", "if"):
                refused.append(f"`required` under `{context}` at {where}")
        elif key in ARRAY_KEYWORDS:
            for sub in value if isinstance(value, list) else [value]:
                walk(sub, path, "array", found, refused)
        elif key == "if":
            walk(value, path, inherit("if"), found, refused)
        elif key in SUBSCHEMA_KEYWORDS:
            walk(value, path, inherit(key), found, refused)
        elif key in LIST_KEYWORDS and isinstance(value, list):
            for sub in value:
                walk(sub, path, inherit(key), found, refused)
        elif key in MAP_KEYWORDS and isinstance(value, dict):
            for sub in value.values():
                walk(sub, path, inherit(key), found, refused)
        elif key in ANNOTATION_KEYWORDS:
            continue
        else:
            # `$ref`, `$dynamicRef`, `$recursiveRef`, `dependentRequired`, draft-07
            # `dependencies` (schema and array forms), a `required` that is not a
            # list, and every keyword this walk does not know.
            refused.append(f"`{key}` at {where}, a keyword this gate does not model")


# ------------------------------------------------------------------ the values


def read_values(text: str) -> Values:
    body = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if [line.strip() for line in body] in ([], ["{}"]):
        return Values({}, True)
    return scan_values(text)


def ancestors(path: str):
    parts = path.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def is_default(values: Values, path: str, template_required: bool) -> bool:
    """Whether `values.yaml` supplies a value at `path`. Raises Refusal when it cannot tell.

    `template_required`: a template passes `path` to `required`, which refuses "".
    """
    if path in values.scalars:
        raw = values.scalars[path]
        if raw is NOT_MODELLED:
            return True
        if raw in NULL_WORDS:
            return False
        if raw in EMPTY_STRINGS:
            return not template_required
        return True
    if path in values.mappings:
        prefix = path + "."
        leaves = [leaf for leaf in (*values.scalars, *values.nulls) if leaf.startswith(prefix)]
        return any(is_default(values, leaf, template_required) for leaf in leaves)
    if path in values.nulls:
        return False
    for ancestor in ancestors(path):
        if values.scalars.get(ancestor) is NOT_MODELLED:
            raise Refusal(f"`{path}` sits below `{ancestor}`, a value the values.yaml scan does not read")
        if ancestor in values.scalars:
            return False
    return False


def tpl_refusals(values: Values, tpl_paths: list) -> list:
    """A `tpl` of a values string is a template the chart ships: refuse it if it calls `required` or cannot be read."""
    refused = []
    for at, path in tpl_paths:
        raw = values.scalars.get(path)
        unread = raw is NOT_MODELLED or path in values.mappings or any(
            values.scalars.get(ancestor) is NOT_MODELLED for ancestor in ancestors(path)
        )
        if unread:
            refused.append(f"{at}: `tpl` renders `{path}`, which values.yaml gives as a value the scan does not read")
        elif isinstance(raw, str) and "required" in raw:
            refused.append(f"{at}: `tpl` renders `{path}`, whose values.yaml string calls `required`")
        elif isinstance(raw, str):
            try:
                problem = template_text_problem(yaml_string(raw), f"the values string `{path}`")
            except Refusal as error:
                problem = str(error)
            if problem:
                refused.append(f"{at}: `tpl` renders `{path}`, whose values.yaml string {problem}")
    return refused


def yaml_string(raw: str) -> str:
    """The string a scanned YAML scalar denotes. Raises Refusal on a double-quoted escape JSON does not share."""
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return json.loads(raw)
        except ValueError:
            raise Refusal("is a double-quoted string with an escape this gate cannot read") from None
    return raw


def tls_gated(values: Values) -> tuple:
    """(leaf paths under a `tls` segment, refusals for a `tls` block the scan cannot read)."""
    found, refused = set(), []
    for path in list(values.scalars) + list(values.nulls):
        parts = path.split(".")
        if "tls" in parts[:-1]:
            found.add(path)
        elif parts[-1] == "tls" and values.scalars.get(path) is NOT_MODELLED:
            refused.append(f"`{path}` is a value the values.yaml scan does not read, so the keys under it cannot be judged")
    return found, refused


# ------------------------------------------------------------------ the REQUIRED list


def load_knobs(path: Path) -> tuple:
    """({chart name: {"required": keys, "default_ok": keys}}, problems). Any problem empties the map: fail closed."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return {}, [f"REQUIRED_LIST_MALFORMED: {path} cannot be read ({error.__class__.__name__})"]
    except ValueError as error:
        return {}, [f"REQUIRED_LIST_MALFORMED: {path} is not JSON: {error}"]
    problems, charts = [], {}
    if not isinstance(document, dict) or not isinstance(document.get("charts"), dict):
        problems.append("the top level is not an object with a `charts` object")
    elif set(document) - {"source", "charts"}:
        problems.append(f"unknown top-level key(s) {sorted(set(document) - {'source', 'charts'})}")
    else:
        for name, entry in document["charts"].items():
            if not isinstance(entry, dict) or set(entry) != {"required", "default_ok"}:
                problems.append(f"`{name}` is not an object of exactly `required` and `default_ok`")
                continue
            lists = {}
            for kind in ("required", "default_ok"):
                listed = entry[kind]
                if not isinstance(listed, list) or not all(isinstance(key, str) for key in listed):
                    problems.append(f"`{name}.{kind}` is not a list of strings")
                    continue
                bad = [key for key in listed if not DOTTED_KEY.match(key)]
                problems += [f"`{name}.{kind}` lists `{key}`, which is not a dotted key" for key in bad]
                twice = sorted({key for key in listed if listed.count(key) > 1})
                problems += [f"`{name}.{kind}` lists `{key}` twice" for key in twice]
                lists[kind] = set(listed)
            if len(lists) == 2:
                problems += [f"`{name}` lists `{key}` as both required and default_ok" for key in sorted(lists["required"] & lists["default_ok"])]
                charts[name] = lists
    if problems:
        return {}, [f"REQUIRED_LIST_MALFORMED: {path}: {problem}" for problem in problems]
    return charts, []


def chart_name(chart: Path):
    """The Chart.yaml `name:`, or None (an unknown chart: fail closed) unless exactly one top-level line names it."""
    try:
        found = CHART_NAME.findall((chart / "Chart.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return found[0][1] if len(found) == 1 else None


# ------------------------------------------------------------------ the baseline


def parse_baseline(text: str, where: str) -> tuple:
    """({key: row}, problems)."""
    entries, problems = {}, []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = BASELINE_LINE.match(stripped)
        if match is None:
            problems.append(f"{where}:{number}: `{stripped}` is not `<dotted key>  # ADR-1140 <row>: <reason>`")
            continue
        key = match.group("key")
        if not DOTTED_KEY.match(key):
            problems.append(f"{where}:{number}: `{key}` is not a dotted key")
        elif not match.group("reason"):
            problems.append(f"{where}:{number}: `{key}` gives no reason after its ADR-1140 row")
        elif key in entries:
            problems.append(f"{where}:{number}: `{key}` is listed twice")
        else:
            entries[key] = match.group("row")
    return entries, problems


def baseline_keys(text: str) -> set:
    keys = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped.split()[0])
    return keys


# ------------------------------------------------------------------ git and the base


def git(root: Path, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True)
    return result.returncode, result.stdout


def in_repository(root: Path) -> bool:
    status, out = git(root, "rev-parse", "--is-inside-work-tree")
    return status == 0 and out.strip() == "true"


def readable(root: Path, rev: str) -> bool:
    return git(root, "cat-file", "-e", f"{rev}^{{commit}}")[0] == 0


@dataclass
class Base:
    rev: str | None
    why: str
    error: str = ""


def resolve_base(root: Path, explicit: str | None) -> Base:
    if explicit:
        if not readable(root, explicit):
            return Base(None, "", f"BASE_UNREADABLE: `--base {explicit}` is not a commit in this repository")
        return Base(explicit, f"--base {explicit}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sha = ""
        event = os.environ.get("GITHUB_EVENT_PATH", "")
        try:
            sha = json.loads(Path(event).read_text())["pull_request"]["base"]["sha"]
        except (OSError, ValueError, KeyError, TypeError):
            sha = ""
        if sha:
            if not readable(root, sha):
                return Base(None, "", f"BASE_UNREADABLE: the pull request's base {sha} is not in this checkout (a shallow clone?)")
            return Base(sha, f"the pull request's base {sha[:12]}")
        if readable(root, "HEAD^1"):
            return Base("HEAD^1", "HEAD^1 (no pull request in the event)")
        return Base(None, "", "BASE_UNREADABLE: no pull request base in $GITHUB_EVENT_PATH and HEAD has no parent")
    if not in_repository(root):
        return Base(None, "skipped: no git history, so no base to grow from")
    if not readable(root, "HEAD"):
        return Base(None, "skipped: no commit yet, so no base to grow from")
    return Base("HEAD", "HEAD (a local run judges the working tree)")


def show(root: Path, rev: str, path: Path):
    status, out = git(root, "show", f"{rev}:{path.as_posix()}")
    return out if status == 0 else None


# ------------------------------------------------------------------ one chart


@dataclass
class Verdict:
    chart: str
    gated: dict = field(default_factory=dict)
    violations: dict = field(default_factory=dict)
    baselined: int = 0
    errors: list = field(default_factory=list)
    exempt: set = field(default_factory=set)  # template-required keys the list classifies DEFAULT-OK


def judge(chart: Path, root: Path, knobs: dict) -> Verdict:
    where = chart.relative_to(root).as_posix() or "."
    verdict = Verdict(where)
    gated: dict = {}
    templated: set = set()
    # ADR-1143: only an exact DEFAULT-OK row exempts a key from arm (a). No entry
    # for this chart (or no name) is an empty set: every key counts as REQUIRED.
    default_ok = knobs.get(chart_name(chart), {}).get("default_ok", set())

    # EVERY FILE under templates/, whatever its suffix: helm parses them all, so a
    # `required` in a `.json` or a suffix-less file is as real as one in `.yaml`. A
    # file that is not UTF-8 text cannot be scanned, and is refused, never skipped.
    templates = chart / "templates"
    every = []
    for path in sorted(templates.rglob("*")) if templates.is_dir() else []:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            verdict.errors.append(f"UNSCANNED_TEMPLATE: {relative} is not UTF-8 text the parser can read ({error.__class__.__name__})")
            continue
        facts = template_facts(source, relative)
        every.append(facts)
        for key in facts.found:
            templated.add(key)
            if key in default_ok and key not in facts.unspelled:
                verdict.exempt.add(key)
            else:
                gated.setdefault(key, set()).add("a")
        verdict.errors += [f"UNRESOLVED_REQUIRED: {message}" for message, _ in facts.refused]
    verdict.errors += helper_refusals(every)
    tpl_paths = [entry for facts in every for entry in facts.tpl_paths]

    schema_file = chart / "values.schema.json"
    if schema_file.is_file():
        try:
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
        except ValueError as error:
            verdict.errors.append(f"UNMODELLED_SCHEMA: {where}/values.schema.json is not JSON: {error}")
            schema = {}
        found, refused = schema_required(schema)
        for key in found:
            gated.setdefault(key, set()).add("b")
        verdict.errors += [f"UNMODELLED_SCHEMA: {where}/values.schema.json: {problem}" for problem in refused]

    values_file = chart / "values.yaml"
    values = read_values(values_file.read_text(encoding="utf-8") if values_file.is_file() else "")
    if not values.modelled:
        verdict.errors.append(f"UNMODELLED_VALUES: {where}/values.yaml: {values.reason}")
        return verdict
    verdict.errors += [f"UNRESOLVED_REQUIRED: {problem}" for problem in tpl_refusals(values, tpl_paths)]
    found, refused = tls_gated(values)
    for key in found:
        gated.setdefault(key, set()).add("c")
    verdict.errors += [f"UNMODELLED_VALUES: {where}/values.yaml: {problem}" for problem in refused]

    verdict.gated = gated
    for key in sorted(gated):
        try:
            if is_default(values, key, key in templated):
                verdict.violations[key] = describe(values, key)
        except Refusal as error:
            verdict.errors.append(f"UNMODELLED_VALUES: {where}/values.yaml: {error}")
    return verdict


def describe(values: Values, key: str) -> str:
    raw = values.scalars.get(key)
    if raw is NOT_MODELLED:
        return "a collection"
    if raw is None:
        return "a mapping with a default under it"
    return raw


def arms(letters) -> str:
    return "(" + ",".join(sorted(letters)) + ")"


def ratchet(verdict: Verdict, chart: Path, root: Path, base: Base) -> list:
    errors = []
    relative = (chart / BASELINE).relative_to(root)
    path = root / relative
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    entries, problems = parse_baseline(text, relative.as_posix())
    errors += [f"BASELINE_MALFORMED: {problem}" for problem in problems]
    for key in sorted(verdict.violations):
        if key in entries:
            verdict.baselined += 1
        else:
            errors.append(
                f"NEW_DEFAULT: `{key}` {arms(verdict.gated[key])} is gated and {verdict.chart}/values.yaml "
                f"gives it `{verdict.violations[key]}`. Remove the default (or write it null), or list it in "
                f"{relative.as_posix()} with its ADR-1140 row and reason"
            )
    for key in sorted(set(entries) - set(verdict.violations)):
        errors.append(f"STALE_BASELINE: {relative.as_posix()} lists `{key}`, which is no longer a gated key with a default. Delete the line")
    if base.rev is not None and adopted_at(root, base.rev):
        before = show(root, base.rev, relative)
        grown = sorted(set(entries) - baseline_keys(before or ""))
        errors += [f"BASELINE_GREW: {relative.as_posix()} adds `{key}`, which {base.why} does not list. The baseline only shrinks" for key in grown]
    return errors


def adopted_at(root: Path, rev: str) -> bool:
    config = show(root, rev, Path(".pre-commit-config.yaml"))
    return bool(config and ADOPTED.search(config))


# ------------------------------------------------------------------ main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=".", help="the repository to judge (default: .)")
    parser.add_argument("--base", default=None, help="the revision the baseline may not grow against")
    parser.add_argument("--report", action="store_true", help="print every violation as `key<TAB>(arms)<TAB>value` and exit 0")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root).resolve()
    if not root.is_dir():
        print(f"{HOOK}: --root {arguments.root} is not a directory")
        return 1
    charts = find_charts(root)
    if not charts:
        print(f"{HOOK}: NO_CHART: no Chart.yaml under {root}. A tree with nothing to judge is red, not green")
        return 1
    knobs, list_errors = load_knobs(KNOBS)
    verdicts = [judge(chart, root, knobs) for chart in charts]

    if arguments.report:
        for error in list_errors:
            print(f"! {error}")
        for verdict in verdicts:
            print(f"# {verdict.chart}: {len(verdict.gated)} gated, {len(verdict.violations)} with a default, {len(verdict.errors)} refused, {len(verdict.exempt)} template-required exempt as DEFAULT-OK")
            for key in sorted(verdict.violations):
                print(f"{key}\t{arms(verdict.gated[key])}\t{verdict.violations[key]}")
            for error in verdict.errors:
                print(f"! {error}")
        return 0

    base = resolve_base(root, arguments.base)
    errors = list_errors + ([base.error] if base.error else [])
    adopting = base.rev is not None and not adopted_at(root, base.rev)
    for chart, verdict in zip(charts, verdicts):
        errors += verdict.errors
        errors += ratchet(verdict, chart, root, base)
    for error in errors:
        print(f"{HOOK}: {error}")
    gated = sum(len(v.gated) for v in verdicts)
    by_arm = {arm: sum(1 for v in verdicts for a in v.gated.values() if arm in a) for arm in "abc"}
    exempt = sum(len(v.exempt) for v in verdicts)
    growth = (
        f"this change adopts the gate (the base's .pre-commit-config.yaml does not reference {HOOK}), so the baseline may be written whole"
        if adopting
        else (f"growth judged against {base.why}" if base.rev else f"growth {base.why}")
    )
    print(
        f"{HOOK}: {len(charts)} chart(s), {gated} gated keys "
        f"({by_arm['a']} template-required, {by_arm['b']} schema-required, {by_arm['c']} under tls; "
        f"{exempt} template-required key{'s' if exempt != 1 else ''} exempt as DEFAULT-OK), "
        f"{sum(len(v.violations) for v in verdicts)} with a default, "
        f"{sum(v.baselined for v in verdicts)} baselined; {growth}. "
        f"{'RED' if errors else 'green'}."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
