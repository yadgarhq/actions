"""LEDGER 824 AND 838. Discovery is crate-wide; crediting is one hop and two conjuncts.

LEDGER 838 IS THE SECOND HALF AND IT IS A TIGHTENING. Ledger 824 made resolution
crate-wide, and crediting a callee across a crate LOOSENED the gate: measured on
`gateway` on 2026-09-10, six of eight HTTP units went green with their own
`Call::start` deleted, where the shipped file-local gate refuses. Nothing was
wrong on the real tree — all eight are green under both rules on `origin/main` —
so the weakening was LATENT and visible only under mutation, which is the class
ADR-0645 governs precisely because a green tree cannot detect a loosening.

A callee's `Call::start` now stands in for its caller's only when BOTH hold: the
call is the caller's whole RESULT (its tail expression or its sole `return`), and
the `Call::start` is the callee's FIRST STATEMENT. `gateway`'s `tools/call`
satisfies the first and not the second, and takes a written exemption rather than
a rule bent to fit it. The exemption walk is tightened to true adjacency in the
same change, because Rule B depends on that hatch.

Read `test_gateway_measured_arms_are_credited_and_tools_call_is_not` first: it is
the only assertion that catches a conjunct 2 written as "at bracket depth one"
instead of "the first statement". Both `gateway` shapes sit at depth one, so a
depth test credits `tools_call` and the whole design silently evaporates —
measured: the real tree returns EXIT=0 with 0 of 8 refused under that reading.

--- LEDGER 824, unchanged below ---

`hooks/observe_coverage.py` resolves across the modules of a crate.

THE DEFECT THESE TESTS PIN. The gate used to resolve everything FILE-LOCALLY, so
it collided with the `complexity` hook's 500-line file ceiling: a repository
forced to split a large file then failed observability, and no split satisfied
both gates. Measured twice on 2026-09-10, by two independent mechanisms.

  1. A DELEGATION HOP WAS INVISIBLE. `iam`'s `impl IamService for Iam` was 865
     lines and a Rust trait impl cannot span files, so the handler bodies moved
     into sibling modules and each handler became
     `self.login_inner(req, call).await`. The gRPC side was a substring scan of
     the wrapper's own text and followed nothing; the HTTP side's search excluded
     any identifier preceded by `.`, so a method call was invisible to it too.
  2. A ROUTE HANDLER RESOLVED FILE-LOCALLY. Splitting `gateway`'s 2553-line
     `src/http.rs` moved six handler bodies out while the `.route(...)`
     registrations stayed, and the gate refused all six as "not defined in this
     file".

EVERY GREEN CASE HERE IS PAIRED WITH A RED ONE ON THE SAME TREE (ledger 731's
standard): same modules, same shape, one property flipped. A green-only test
would prove the gate can be satisfied, not that it still refuses anything —
which is the failure mode this hook already had once, when `#[cfg(test)]` was
used to decide which files to skip and it silently skipped them all.

The non-weakening pairs are the ones to read first:
`test_gateway_measured_arms_are_credited_and_tools_call_is_not`,
`test_credit_is_one_hop_and_never_two`,
`test_the_callee_is_credited_only_from_the_unit_s_result_position`,
`test_a_second_way_out_of_the_unit_removes_credit`,
`test_a_marker_separated_by_a_blank_line_does_not_attach`,
`test_a_marker_does_not_inherit_the_next_item_once_its_own_is_deleted`,
`test_delegating_handler_with_no_call_anywhere_is_red`,
`test_ambiguous_callee_fails_closed`,
`test_a_sibling_test_module_cannot_certify_a_production_handler`,
`test_an_inline_cfg_test_helper_cannot_certify_a_production_handler`,
`test_a_comment_mentioning_the_call_does_not_instrument`,
`test_a_callee_that_records_only_on_a_branch_does_not_instrument_its_caller` and
`test_an_associated_function_of_a_foreign_type_is_not_followed`.

The gate is run as a SUBPROCESS from the crate root with relative paths, exactly
how pre-commit invokes it in a consumer repository -- not imported, so the
`if __name__ == "__main__":` argv path is what is actually exercised.

Run: python3 -m pytest scripts/tests/test_observe_coverage.py -q
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "hooks" / "observe_coverage.py"


def crate(tmp_path: Path, **files: str) -> Path:
    """A one-package crate whose files are given as `src_lib_rs="..."`.

    The key is a path with `/` written as `__`, so `src__http__dispatch_rs`
    means `src/http/dispatch.rs`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "fixture"\nversion = "0.1.0"\n')
    for key, body in files.items():
        rel = key.replace("__", "/").replace("_rs", ".rs")
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return tmp_path


def run(root: Path, *relative: str) -> subprocess.CompletedProcess:
    """The gate, from `root`, over `relative` — or over every `.rs` under `src`."""
    paths = list(relative) or sorted(
        str(p.relative_to(root)) for p in (root / "src").rglob("*.rs")
    )
    return subprocess.run(
        [sys.executable, str(GATE), *paths],
        capture_output=True,
        text=True,
        cwd=str(root),
    )


# --------------------------------------------------------------------------
# Mechanism 1 — a handler that delegates to a callee in a sibling module.
# --------------------------------------------------------------------------

SERVICE_WRAPPER = """\
use super::*;

#[tonic::async_trait]
impl IamService for Iam {
    async fn login(&self, req: Request<LoginRequest>) -> Result<Response<Reply>, Status> {
        let rid = request_id_of(&req);
        self.login_inner(req, rid).await
    }
}
"""

INNER_INSTRUMENTED = """\
use super::*;

impl Iam {
    pub(super) async fn login_inner(
        &self,
        req: Request<LoginRequest>,
        rid: String,
    ) -> Result<Response<Reply>, Status> {
        let call = Call::start(SERVICE, "Login", Kind::Write, tel(rid, ""));
        let answer = self.store.login(req).await;
        call.ok();
        answer
    }
}
"""

INNER_BARE = INNER_INSTRUMENTED.replace(
    '        let call = Call::start(SERVICE, "Login", Kind::Write, tel(rid, ""));\n', ""
).replace("        call.ok();\n", "")


def test_delegating_grpc_handler_is_followed_to_its_inner(tmp_path):
    root = crate(
        tmp_path,
        src__service__rpc_rs=SERVICE_WRAPPER,
        src__service__login_rs=INNER_INSTRUMENTED,
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


def test_delegating_handler_with_no_call_anywhere_is_red(tmp_path):
    """The same tree with the `Call::start` gone from the callee as well."""
    root = crate(
        tmp_path,
        src__service__rpc_rs=SERVICE_WRAPPER,
        src__service__login_rs=INNER_BARE,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`login` opens no observe::Call" in result.stdout


def test_a_bare_call_into_a_sibling_module_is_followed(tmp_path):
    """Not every delegation goes through `self` — a free function is followed too."""
    root = crate(
        tmp_path,
        src__service__rpc_rs="""\
use super::*;

#[tonic::async_trait]
impl TaskService for Task {
    async fn create(&self, req: Request<Create>) -> Result<Response<Reply>, Status> {
        create_inner(req).await
    }
}
""",
        src__service__create_rs="""\
use super::*;

pub(super) async fn create_inner(req: Request<Create>) -> Result<Response<Reply>, Status> {
    let call = Call::start(SERVICE, "Create", Kind::Write, tel(rid()));
    call.ok();
    store(req).await
}
""",
    )
    assert run(root).returncode == 0


def test_a_module_qualified_call_is_followed(tmp_path):
    root = crate(
        tmp_path,
        src__service__rpc_rs="""\
use super::*;

#[tonic::async_trait]
impl TaskService for Task {
    async fn create(&self, req: Request<Create>) -> Result<Response<Reply>, Status> {
        create::inner(req).await
    }
}
""",
        src__service__create_rs="""\
use super::*;

pub(super) async fn inner(req: Request<Create>) -> Result<Response<Reply>, Status> {
    let call = Call::start(SERVICE, "Create", Kind::Write, tel(rid()));
    call.ok();
    store(req).await
}
""",
    )
    assert run(root).returncode == 0


def test_two_service_impls_in_one_file_are_both_checked(tmp_path):
    """A file holding two services is what a 500-line ceiling encourages."""
    root = crate(
        tmp_path,
        src__lib_rs="""\
#[tonic::async_trait]
impl FirstService for A {
    async fn one(&self, req: Request<R>) -> Result<Response<Reply>, Status> {
        let call = Call::start(SERVICE, "One", Kind::Read, tel(rid()));
        call.ok();
        answer(req)
    }
}

#[tonic::async_trait]
impl SecondService for B {
    async fn two(&self, req: Request<R>) -> Result<Response<Reply>, Status> {
        answer(req)
    }
}
""",
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`two` opens no observe::Call" in result.stdout
    assert "`one` opens no observe::Call" not in result.stdout


def test_a_grpc_handler_may_be_exempted_with_a_reason(tmp_path):
    body = """\
#[tonic::async_trait]
impl FirstService for A {
    // observe-coverage: exempt — %s
    async fn one(&self, req: Request<R>) -> Result<Response<Reply>, Status> {
        answer(req)
    }
}
"""
    root = crate(tmp_path, src__lib_rs=body % "a streaming probe records per item")
    assert run(root).returncode == 0
    root = crate(tmp_path, src__lib_rs=body % "")
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`one` is marked exempt with no reason" in result.stdout


# --------------------------------------------------------------------------
# Mechanism 2 — a route handler defined in a sibling module.
# --------------------------------------------------------------------------

HANDLE_INSTRUMENTED = """\
use super::*;

pub(super) async fn handle(State(state): State<Arc<AppState>>, body: Bytes) -> Response {
    let call = Call::start(SERVICE, "mcp", Kind::Read, tel(request_id()));
    call.ok();
    reply(&state, body)
}
"""

HANDLE_BARE = HANDLE_INSTRUMENTED.replace(
    '    let call = Call::start(SERVICE, "mcp", Kind::Read, tel(request_id()));\n', ""
).replace("    call.ok();\n", "")


def _router_in_root(registration: str) -> str:
    return (
        "mod dispatch;\n\n"
        "pub fn router(state: Arc<AppState>) -> Router {\n"
        "    Router::new()\n"
        f"        {registration}\n"
        "        .with_state(state)\n"
        "}\n"
    )


def test_route_handler_in_a_sibling_module_is_followed(tmp_path):
    """The exact shape `gateway`'s split produced: `.route` here, body there."""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(handle))'),
        src__http__dispatch_rs=HANDLE_INSTRUMENTED,
    )
    assert run(root).returncode == 0


def test_route_handler_in_a_sibling_module_with_no_call_is_red(tmp_path):
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(handle))'),
        src__http__dispatch_rs=HANDLE_BARE,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`handle` opens no observe::Call" in result.stdout
    # The path names the file the HANDLER lives in, not the one registering it:
    # that is where the missing `Call::start` has to be written.
    assert "src/http/dispatch.rs: `handle`" in result.stdout


def test_a_module_qualified_route_registration_resolves(tmp_path):
    """`post(dispatch::handle)` used to match nothing at all, so the unit vanished."""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=HANDLE_BARE,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`handle` opens no observe::Call" in result.stdout


def test_a_route_handler_outside_the_crate_is_still_refused(tmp_path):
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/metrics", get(exporter))'),
        src__http__dispatch_rs=HANDLE_INSTRUMENTED,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "route handler `exporter` is not defined in this crate" in result.stdout


# --------------------------------------------------------------------------
# Mechanism 3 — a per-module `routes()` merged in a root `router()`.
# --------------------------------------------------------------------------

MERGED_ROOT = """\
mod admin;
mod dispatch;

pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .merge(dispatch::routes())
        .merge(admin::routes())
        .with_state(state)
}
"""


def _module_with_routes(handler: str, instrumented: bool) -> str:
    call = f'    let call = Call::start(SERVICE, "{handler}", Kind::Write, tel(request_id()));\n'
    return (
        "use super::*;\n\n"
        "pub(super) fn routes() -> Router<Arc<AppState>> {\n"
        "    Router::new()\n"
        f'        .route("/{handler}", post({handler}))\n'
        "}\n\n"
        f"pub(super) async fn {handler}(body: Bytes) -> Response {{\n"
        f"{call if instrumented else ''}"
        "    reply(body)\n"
        "}\n"
    )


def test_per_module_routes_merged_in_a_root_router_is_understood(tmp_path):
    root = crate(
        tmp_path,
        src__http_rs=MERGED_ROOT,
        src__http__dispatch_rs=_module_with_routes("handle", True),
        src__http__admin_rs=_module_with_routes("create", True),
    )
    assert run(root).returncode == 0


def test_one_merged_module_losing_its_call_is_red_and_the_other_is_not(tmp_path):
    root = crate(
        tmp_path,
        src__http_rs=MERGED_ROOT,
        src__http__dispatch_rs=_module_with_routes("handle", True),
        src__http__admin_rs=_module_with_routes("create", False),
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`create` opens no observe::Call" in result.stdout
    assert "`handle` opens no observe::Call" not in result.stdout


# --------------------------------------------------------------------------
# It must not weaken.
# --------------------------------------------------------------------------

AMBIGUOUS_TREE = {
    "src__service__rpc_rs": """\
use super::*;

#[tonic::async_trait]
impl TaskService for Task {
    async fn create(&self, req: Request<Create>) -> Result<Response<Reply>, Status> {
        %s
    }
}
""",
    "src__service__left_rs": """\
use super::*;

pub(super) async fn inner(req: Request<Create>) -> Result<Response<Reply>, Status> {
    let call = Call::start(SERVICE, "Create", Kind::Write, tel(rid()));
    call.ok();
    store(req).await
}
""",
    "src__service__right_rs": """\
use super::*;

pub(super) async fn inner(req: Request<Create>) -> Result<Response<Reply>, Status> {
    store(req).await
}
""",
}


def test_ambiguous_callee_fails_closed(tmp_path):
    """Two modules define `inner`; one is instrumented and one is not.

    Following both and passing on either would let the instrumented one certify a
    handler that reaches the other. There is no fact here that says which the
    compiler picks, so the gate refuses.
    """
    files = dict(AMBIGUOUS_TREE)
    files["src__service__rpc_rs"] = files["src__service__rpc_rs"] % "inner(req).await"
    result = run(crate(tmp_path, **files))
    assert result.returncode == 1, result.stdout
    assert "`create` opens no observe::Call" in result.stdout
    assert "defines in more than one module" in result.stdout


def test_the_same_ambiguity_resolves_once_the_call_names_its_module(tmp_path):
    files = dict(AMBIGUOUS_TREE)
    files["src__service__rpc_rs"] = files["src__service__rpc_rs"] % "left::inner(req).await"
    assert run(crate(tmp_path, **files)).returncode == 0

    files["src__service__rpc_rs"] = dict(AMBIGUOUS_TREE)["src__service__rpc_rs"] % (
        "right::inner(req).await"
    )
    result = run(crate(tmp_path, **files))
    assert result.returncode == 1, result.stdout
    assert "`create` opens no observe::Call" in result.stdout


def test_a_sibling_test_module_cannot_certify_a_production_handler(tmp_path):
    """`src/service/tests.rs` is out of the INDEX, not merely out of the scan."""
    root = crate(
        tmp_path,
        src__service__rpc_rs=SERVICE_WRAPPER,
        src__service__login_rs=INNER_BARE,
        src__service__tests_rs="""\
use super::*;

pub(super) async fn login_inner(req: Request<LoginRequest>, rid: String) -> Reply {
    let call = Call::start(SERVICE, "Login", Kind::Write, tel(rid, ""));
    call.ok();
    Reply::default()
}
""",
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`login` opens no observe::Call" in result.stdout


def test_an_inline_cfg_test_helper_cannot_certify_a_production_handler(tmp_path):
    root = crate(
        tmp_path,
        src__service__rpc_rs=SERVICE_WRAPPER,
        src__service__login_rs=INNER_BARE
        + """
#[cfg(test)]
mod tests {
    use super::*;

    fn login_inner(rid: String) -> Reply {
        let call = Call::start(SERVICE, "Login", Kind::Write, tel(rid, ""));
        call.ok();
        Reply::default()
    }
}
""",
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`login` opens no observe::Call" in result.stdout


def test_a_comment_mentioning_the_call_does_not_instrument(tmp_path):
    """`iam-db`'s `service.rs` and two `build.rs` files carry such a sentence."""
    root = crate(
        tmp_path,
        src__lib_rs="""\
#[tonic::async_trait]
impl FirstService for A {
    /// Renaming this would hide that it is the record `Call::start` emits.
    async fn one(&self, req: Request<R>) -> Result<Response<Reply>, Status> {
        answer(req)
    }
}
""",
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`one` opens no observe::Call" in result.stdout


def test_a_callee_that_records_only_on_a_branch_does_not_instrument_its_caller(tmp_path):
    """`gateway`'s `gate::guard` shape: a `Call` on the refusal path only.

    The paired green below is `gateway`'s `dispatch::measured` shape — a `Call`
    opened as the callee's first statement, which the caller cannot avoid.
    """
    branchy = """\
use super::*;

pub(super) fn guard(endpoint: &'static str, d: Decision) -> Option<Response> {
    match d {
        Decision::Allow => None,
        Decision::Refuse => {
            Call::start(SERVICE, endpoint, Kind::Write, tel(request_id())).fail("INTERNAL");
            Some(refusal())
        }
    }
}
"""
    handler = """\
use super::*;

pub(super) async fn handle(body: Bytes) -> Response {
    if let Some(refusal) = guard(ENDPOINT, decide(&body)) {
        return refusal;
    }
    reply(body)
}
"""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=handler,
        src__http__gate_rs=branchy,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`handle` opens no observe::Call" in result.stdout

    # MOVING THE CALLEE'S `Call` UP IS NOT ENOUGH ON ITS OWN, and that is ledger
    # 838's correction to this very test. `guard` is called in an `if let`
    # condition and `handle` carries on past it, so `guard`'s `Call` is not
    # `handle`'s however unconditional it is inside `guard`.
    unconditional = """\
use super::*;

pub(super) fn guard(endpoint: &'static str, d: Decision) -> Option<Response> {
    let call = Call::start(SERVICE, endpoint, Kind::Write, tel(request_id()));
    call.ok();
    match d {
        Decision::Allow => None,
        Decision::Refuse => Some(refusal()),
    }
}
"""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=handler,
        src__http__gate_rs=unconditional,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`handle` opens no observe::Call" in result.stdout

    # The paired GREEN is `gateway`'s `dispatch::measured` shape, and it needs BOTH
    # conjuncts: the callee's `Call` first, and the call as the handler's whole
    # result.
    measured = """\
use super::*;

pub(super) fn measured(endpoint: &'static str, build: impl FnOnce() -> Value) -> Response {
    let call = Call::start(SERVICE, endpoint, Kind::Write, tel(request_id()));
    let rendered = render(build());
    call.ok();
    reply(rendered)
}
"""
    delegating = """\
use super::*;

pub(super) async fn handle(body: Bytes) -> Response {
    measured(ENDPOINT, || answer(&body))
}
"""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=delegating,
        src__http__gate_rs=measured,
    )
    assert run(root).returncode == 0, run(root).stdout


def test_an_associated_function_of_a_foreign_type_is_not_followed(tmp_path):
    """`Response::new(...)` must not reach a crate `fn new` by its bare name."""
    root = crate(
        tmp_path,
        src__lib_rs="""\
mod build;

#[tonic::async_trait]
impl FirstService for A {
    async fn one(&self, req: Request<R>) -> Result<Response<Reply>, Status> {
        Ok(Response::new(Reply::default()))
    }
}
""",
        src__build_rs="""\
pub(super) fn new() -> Call {
    Call::start(SERVICE, "unrelated", Kind::Read, tel(request_id()))
}
""",
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`one` opens no observe::Call" in result.stdout


# --------------------------------------------------------------------------
# Labels — cosmetic, and never a reason to fail (ledger 827).
# --------------------------------------------------------------------------

DISPATCHING_HANDLER = """\
use super::*;

pub(super) async fn handle(body: Bytes) -> Response {
    let request = parse(body);
    match request.method.as_str() {
        DISCOVER => discover(),
        _ => unknown(),
    }
}

fn discover() -> Response {
    Response::new()
}
"""


def test_a_const_in_a_sibling_module_names_the_unit(tmp_path):
    """`gateway` reported `DISCOVER`; the const lives one module up."""
    root = crate(
        tmp_path,
        src__http_rs='const DISCOVER: &str = "server/discover";\n'
        + _router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=DISPATCHING_HANDLER,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`server/discover` opens no observe::Call" in result.stdout
    assert "`DISCOVER` opens no" not in result.stdout


def test_two_modules_disagreeing_about_a_const_keep_the_identifier(tmp_path):
    """A label a crate cannot agree on is reported as the identifier, not as red."""
    root = crate(
        tmp_path,
        src__http_rs='const DISCOVER: &str = "server/discover";\n'
        + _router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=DISPATCHING_HANDLER,
        src__http__admin_rs='use super::*;\nconst DISCOVER: &str = "admin/discover";\n',
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`DISCOVER` opens no observe::Call" in result.stdout
    # Exactly one unit failed. A naming disagreement must not mint a failure.
    assert result.stdout.count("opens no observe::Call") == 1


def test_a_catch_all_arm_is_still_not_a_handler(tmp_path):
    root = crate(
        tmp_path,
        src__http_rs='const DISCOVER: &str = "server/discover";\n'
        + _router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=DISPATCHING_HANDLER.replace(
            "        DISCOVER => discover(),\n",
            "        DISCOVER => measured(),\n",
        )
        + """
fn measured() -> Response {
    let call = Call::start(SERVICE, DISCOVER, Kind::Read, tel(request_id()));
    call.ok();
    discover()
}
""",
    )
    result = run(root)
    assert result.returncode == 0, result.stdout


# --------------------------------------------------------------------------
# Scope and arithmetic.
# --------------------------------------------------------------------------


def test_a_crate_with_no_router_reports_no_http_units(tmp_path):
    """Silence on a file that does not match the one dispatch shape, unchanged."""
    root = crate(
        tmp_path,
        src__lib_rs="""\
pub async fn handle(body: Bytes) -> Response {
    reply(body)
}
""",
    )
    assert run(root).returncode == 0


def test_a_failure_is_reported_once_however_many_paths_are_passed(tmp_path):
    """pre-commit hands over N changed files; the crate is indexed once."""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(handle))'),
        src__http__dispatch_rs=HANDLE_BARE,
        src__lib_rs="pub mod http;\n",
    )
    result = run(root, "src/lib.rs", "src/http.rs", "src/http/dispatch.rs")
    assert result.returncode == 1, result.stdout
    assert result.stdout.count("`handle` opens no observe::Call") == 1


def test_a_split_does_not_change_the_verdict(tmp_path):
    """One file, then the same code in three — the property ledger 824 is about."""
    one = crate(
        tmp_path / "one",
        src__http_rs=(
            'const DISCOVER: &str = "server/discover";\n'
            + _router_in_root('.route("/", post(handle))').replace("mod dispatch;\n", "")
            + DISPATCHING_HANDLER.replace("use super::*;\n", "")
            + """
fn measured() -> Response {
    let call = Call::start(SERVICE, DISCOVER, Kind::Read, tel(request_id()));
    call.ok();
    discover()
}
"""
        ).replace("        DISCOVER => discover(),\n", "        DISCOVER => measured(),\n"),
    )
    assert run(one).returncode == 0, run(one).stdout

    split = crate(
        tmp_path / "split",
        src__http_rs='const DISCOVER: &str = "server/discover";\n'
        + _router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=DISPATCHING_HANDLER.replace(
            "        DISCOVER => discover(),\n", "        DISCOVER => measured::wrap(),\n"
        ),
        src__http__measured_rs="""\
use super::*;

pub(super) fn wrap() -> Response {
    let call = Call::start(SERVICE, DISCOVER, Kind::Read, tel(request_id()));
    call.ok();
    reply()
}
""",
    )
    assert run(split).returncode == 0, run(split).stdout


# --------------------------------------------------------------------------
# LEDGER 838 — the two conjuncts credit travels on, and neither alone.
#
# THE DEFECT THESE PIN, measured on `gateway` on 2026-09-10. Crate-wide crediting
# followed a chain of calls, so six of `gateway`'s eight HTTP units went green
# with their OWN `Call::start` deleted, where the shipped file-local gate refuses.
# Nothing was wrong on the real tree — all eight are green under both rules on
# `origin/main` — which is exactly why it needed mutation to see: a loosening is
# LATENT, and a green tree cannot detect one. ADR-0645 forbids the direction
# whether or not it is disclosed.
#
# Every pair below is same-tree, one property flipped.
# --------------------------------------------------------------------------

# `gateway`'s two real shapes, side by side. `measured` opens its `Call` as its
# first statement; `tools_call` opens one four statements down, below an early
# `return` on the throttle path.
GATEWAY_DISPATCH = """\
use super::*;

pub(super) fn routes() -> Router<Arc<AppState>> {
    Router::new().route("/", post(handle))
}

pub(super) async fn handle(request: Request, state: Arc<AppState>) -> Response {
    match request.method.as_str() {
        DISCOVER => measured(DISCOVER, || discover(&id)),
        TOOLS_LIST => measured(TOOLS_LIST, || tools_list(&id)),
        "tools/call" => tools_call(state, &id, &request.params).await,
        other => reply(200, unknown(other)),
    }
}

pub(super) fn measured(tool: &'static str, build: impl FnOnce() -> Value) -> Response {
    let call = Call::start(SERVICE, tool, Kind::Read, tel(request_id()));
    let rendered = render(build());
    call.ok();
    reply(200, rendered)
}

pub(super) async fn tools_call(state: Arc<AppState>, id: &Value, params: &Value) -> Response {
    let name = params.get("name").and_then(Value::as_str).unwrap_or_default();
    let (label, module) = match resolved_tool(name, id) {
        Ok(pair) => pair,
        Err(refusal) => return *refusal,
    };
    if let Some(refusal) = throttled(&state, label, module).await {
        record_throttled(label, observed(request_id()));
        return refusal;
    }
    let call = Call::start(SERVICE, label, Kind::Write, tel(request_id()));
    let rendered = render(work(&state, params).await);
    call.ok();
    reply(200, rendered)
}
"""

GATEWAY_CONSTS = """\
mod dispatch;

pub const DISCOVER: &str = "server/discover";
pub const TOOLS_LIST: &str = "tools/list";

pub fn router(state: Arc<AppState>) -> Router {
    Router::new().merge(dispatch::routes()).with_state(state)
}
"""


def test_gateway_measured_arms_are_credited_and_tools_call_is_not(tmp_path):
    """THE SHAPE THAT DECIDES THE WHOLE DESIGN, and it is two shapes, not one.

    Both arms delegate, both delegate in result position, and only one may be
    credited:

      - `measured` opens its `Call` as the first statement of its body, so nothing
        the arm does can avoid it. A syntactic rule can credit this soundly.
      - `tools_call` opens its `Call` below an early `return` on the throttle path.
        It is CONDITIONAL, and no syntactic rule can credit it soundly. It happens
        to be correct — the early path is recorded by `record_throttled` — but
        that is a fact a person verified, not one this file can read.

    **AND `tools_call`'s `Call::start` SITS AT NET BRACKET DEPTH ONE, exactly like
    `measured`'s.** The `match` and the `if let` above it each open and close. So a
    conjunct-2 written as "at depth one" credits both and cannot tell them apart —
    which is why it is written as "the first statement" instead. This assertion is
    the only one in the suite that catches that substitution: under mutation both
    shapes are red either way, because a deleted `Call::start` fails any reading.
    """
    root = crate(tmp_path, src__http_rs=GATEWAY_CONSTS, src__http__dispatch_rs=GATEWAY_DISPATCH)
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`tools/call` opens no observe::Call" in result.stdout
    assert "is not its first statement" in result.stdout
    # The other two arms are green with NO exemption, which is what keeps this a
    # tightening rather than a rewrite: crate-wide discovery still finds them and
    # `measured` still instruments them.
    assert "`server/discover`" not in result.stdout
    assert "`tools/list`" not in result.stdout

    # Same tree, `tools_call`'s `Call` lifted above every early return.
    lifted = GATEWAY_DISPATCH.replace(
        "    let name = params.get(\"name\").and_then(Value::as_str).unwrap_or_default();\n",
        "    let call = Call::start(SERVICE, TOOLS_CALL, Kind::Write, tel(request_id()));\n"
        "    let name = params.get(\"name\").and_then(Value::as_str).unwrap_or_default();\n",
    ).replace("    let call = Call::start(SERVICE, label, Kind::Write, tel(request_id()));\n", "")
    root = crate(tmp_path, src__http_rs=GATEWAY_CONSTS, src__http__dispatch_rs=lifted)
    assert run(root).returncode == 0, run(root).stdout


def test_tools_call_takes_an_exemption_rather_than_a_rule_bent_to_fit_it(tmp_path):
    """The one hatch Rule B needs, on the arm and carrying a reason.

    A rule stretched until it accepted `tools_call`'s conditional `Call` would
    have accepted five wrong shapes with it — the five `guard` callers. So the
    judgement is written down as a judgement, where a reviewer reads it.
    """
    exempt = GATEWAY_DISPATCH.replace(
        '        "tools/call" => tools_call',
        "        // observe-coverage: exempt — both paths record: the throttle refusal\n"
        "        // through `record_throttled` and every other path through the `Call`\n"
        "        // `tools_call` opens once the label is bounded.\n"
        '        "tools/call" => tools_call',
    )
    root = crate(tmp_path, src__http_rs=GATEWAY_CONSTS, src__http__dispatch_rs=exempt)
    assert run(root).returncode == 0, run(root).stdout

    # A marker with no reason is not an exemption.
    bare = GATEWAY_DISPATCH.replace(
        '        "tools/call" => tools_call',
        "        // observe-coverage: exempt\n" '        "tools/call" => tools_call',
    )
    result = run(crate(tmp_path, src__http_rs=GATEWAY_CONSTS, src__http__dispatch_rs=bare))
    assert result.returncode == 1, result.stdout
    assert "marked exempt with no reason" in result.stdout


# --------------------------------------------------------------------------
# Conjunct 1 — result position.
# --------------------------------------------------------------------------

CREDITING_CALLEE = """\
use super::*;

pub(super) fn measured(endpoint: &'static str, build: impl FnOnce() -> Value) -> Response {
    let call = Call::start(SERVICE, endpoint, Kind::Write, tel(request_id()));
    let rendered = render(build());
    call.ok();
    reply(rendered)
}
"""


def _handler(body: str) -> str:
    return "use super::*;\n\npub(super) async fn handle(body: Bytes) -> Response {\n" + body + "}\n"


def _verdict(tmp_path, handler_body: str):
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=_handler(handler_body),
        src__http__gate_rs=CREDITING_CALLEE,
    )
    return run(root)


def test_the_callee_is_credited_only_from_the_unit_s_result_position(tmp_path):
    """One callee, one crate, five call sites. Only the result position credits."""
    # GREEN: the call is the whole tail expression.
    assert _verdict(tmp_path, "    measured(ENDPOINT, || answer(&body))\n").returncode == 0
    # GREEN: `.await` may follow it and it is still the result.
    assert _verdict(tmp_path, "    measured(ENDPOINT, || answer(&body)).await\n").returncode == 0
    # GREEN: a SOLE `return` is a result position too.
    assert (
        _verdict(tmp_path, "    return measured(ENDPOINT, || answer(&body));\n").returncode == 0
    )
    # RED: something else is the result — the call is an argument.
    assert _verdict(tmp_path, "    wrap(measured(ENDPOINT, || answer(&body)))\n").returncode == 1
    # RED: something follows the call, so the call is not the whole result.
    assert (
        _verdict(tmp_path, "    measured(ENDPOINT, || answer(&body)).into_response()\n").returncode
        == 1
    )
    # RED: the call is a statement and the result is somebody else.
    assert (
        _verdict(tmp_path, "    measured(ENDPOINT, || answer(&body));\n    reply(body)\n").returncode
        == 1
    )


def test_a_second_way_out_of_the_unit_removes_credit(tmp_path):
    """Multiple exits means no credit — a `Call` on one path is not one on the others."""
    # RED: a `?` is an exit, and an invisible one.
    assert (
        _verdict(tmp_path, "    let v = parse(&body)?;\n    measured(ENDPOINT, || v)\n").returncode
        == 1
    )
    # RED: an early `return` beside the tail expression is a second door.
    assert (
        _verdict(
            tmp_path,
            "    if body.is_empty() {\n        return reply(body);\n    }\n"
            "    measured(ENDPOINT, || answer(&body))\n",
        ).returncode
        == 1
    )
    # RED: a `match` result is several paths, not one.
    assert (
        _verdict(
            tmp_path,
            "    match decide(&body) {\n"
            "        Decision::Allow => measured(ENDPOINT, || answer(&body)),\n"
            "        Decision::Refuse => reply(body),\n"
            "    }\n",
        ).returncode
        == 1
    )
    # GREEN pair for all three: the same callee, the same crate, one way out.
    assert _verdict(tmp_path, "    measured(ENDPOINT, || answer(&body))\n").returncode == 0


def test_credit_is_one_hop_and_never_two(tmp_path):
    """LEDGER 838's measured loosening, in the shape it was measured in.

    `gateway`'s `guard` opens a `Call` only on its refusal arms, so it does not
    instrument its caller — the recursion this replaces then followed `guard`'s
    OWN callee `too_many`, whose `Call` IS its first statement, and accepted it.
    Two hops from a handler whose own `Call::start` had been deleted, and green.
    """
    chain = """\
use super::*;

pub(super) fn guard(endpoint: &'static str, d: Decision) -> Option<Response> {
    match d {
        Decision::Allow => None,
        Decision::Refuse => Some(too_many(endpoint)),
    }
}

pub(super) fn too_many(endpoint: &'static str) -> Response {
    Call::start(SERVICE, endpoint, Kind::Write, tel(request_id())).fail("RESOURCE_EXHAUSTED");
    text(StatusCode::TOO_MANY_REQUESTS, r#"{"error":"too many attempts"}"#)
}
"""
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=_handler("    guard(ENDPOINT, decide(&body)).unwrap_or(reply(body))\n"),
        src__http__gate_rs=chain,
    )
    result = run(root)
    assert result.returncode == 1, result.stdout
    assert "`handle` opens no observe::Call" in result.stdout

    # ONE hop to the same `Call` is credited: `too_many` opens it as its first
    # statement, and here it is the handler's whole result. The pair is what shows
    # the refusal above is about the HOP COUNT and not about `too_many`.
    root = crate(
        tmp_path,
        src__http_rs=_router_in_root('.route("/", post(dispatch::handle))'),
        src__http__dispatch_rs=_handler("    too_many(ENDPOINT)\n"),
        src__http__gate_rs=chain,
    )
    assert run(root).returncode == 0, run(root).stdout


# --------------------------------------------------------------------------
# LEDGER 838 — the exemption marker attaches only where it was written.
# --------------------------------------------------------------------------

def _grpc(marker: str, handler: str = "create") -> str:
    return (
        "use super::*;\n\n"
        "#[tonic::async_trait]\n"
        "impl TaskService for Task {\n"
        f"{marker}"
        f"    async fn {handler}(&self, req: Request<Create>) -> Result<Response<Reply>, Status> {{\n"
        "        store(req).await\n"
        "    }\n"
        "}\n"
    )


MARKER = "    // observe-coverage: exempt — the caller records this one\n"


def test_a_marker_directly_above_the_item_attaches(tmp_path):
    """The regression guard: tightening the walk must not stop it working."""
    assert run(crate(tmp_path, src__service__rpc_rs=_grpc(MARKER))).returncode == 0
    # A `///` doc run counts as a comment run: a doc block above an item is that
    # item's, and a marker written inside one belongs to it.
    doc = "    /// Answers from the cache.\n" + MARKER + "    /// See ADR-0645.\n"
    assert run(crate(tmp_path, src__service__rpc_rs=_grpc(doc))).returncode == 0


def test_a_marker_separated_by_a_blank_line_does_not_attach(tmp_path):
    """The walk used to SKIP blank lines, which is what let a marker drift."""
    result = run(crate(tmp_path, src__service__rpc_rs=_grpc(MARKER + "\n")))
    assert result.returncode == 1, result.stdout
    assert "`create` opens no observe::Call" in result.stdout
    # An attribute already stopped the walk, and still does.
    result = run(crate(tmp_path, src__service__rpc_rs=_grpc(MARKER + "    #[allow(unused)]\n")))
    assert result.returncode == 1, result.stdout


def test_a_marker_does_not_inherit_the_next_item_once_its_own_is_deleted(tmp_path):
    """THE DRIFT CASE, both directions, on one tree.

    A marker written for `create` must not become `update`'s the day `create` is
    edited away. Under the old walk it did, silently, and a stale exemption is a
    silent green on a D67 gate.

    **THE HOOK CANNOT CLOSE THIS COMPLETELY AND DOES NOT CLAIM TO.** A deletion
    that leaves the marker directly on top of `update`, with no blank line, is
    textually indistinguishable from a marker written for `update` — so it
    attaches, and it must, or nothing could be exempted at all. What adjacency
    buys is that the marker has to be MOVED, and moving it shows up in the diff.
    The reason itself is never judged by this file, only required to exist; it is
    a claim under human review like any other line.
    """
    two = (
        "use super::*;\n\n"
        "#[tonic::async_trait]\n"
        "impl TaskService for Task {\n"
        f"{MARKER}"
        "    async fn create(&self, req: Request<Create>) -> Result<Response<Reply>, Status> {\n"
        "        store(req).await\n"
        "    }\n"
        "\n"
        "    async fn update(&self, req: Request<Update>) -> Result<Response<Reply>, Status> {\n"
        "        let call = Call::start(SERVICE, \"Update\", Kind::Write, tel(rid()));\n"
        "        call.ok();\n"
        "        store(req).await\n"
        "    }\n"
        "}\n"
    )
    # BEFORE: `create` is exempt and `update` is instrumented, so the tree is green.
    assert run(crate(tmp_path, src__service__rpc_rs=two)).returncode == 0

    # AFTER: `create` is deleted, `update` loses its `Call`, and the orphaned
    # marker is left where it was — one blank line above `update`.
    drifted = (
        "use super::*;\n\n"
        "#[tonic::async_trait]\n"
        "impl TaskService for Task {\n"
        f"{MARKER}"
        "\n"
        "    async fn update(&self, req: Request<Update>) -> Result<Response<Reply>, Status> {\n"
        "        store(req).await\n"
        "    }\n"
        "}\n"
    )
    result = run(crate(tmp_path, src__service__rpc_rs=drifted))
    assert result.returncode == 1, result.stdout
    assert "`update` opens no observe::Call" in result.stdout
