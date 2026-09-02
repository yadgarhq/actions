#!/usr/bin/env python3
# The D80 portability gate, run by the `portability` job of
# `.github/workflows/ci-pr.yaml` against the repository under review.
#
# A CHECKED-IN FILE RATHER THAN A HEREDOC, and that is not tidiness. actionlint
# hands every `run:` block to shellcheck over a pipe, and above 8192 bytes the
# write deadlocks: actionlint waits forever and the job burns its whole timeout.
# This script was 20499 bytes inside a `run:` block, so no workflow in this
# repository could be linted at all while it lived there. It was also
# undiffable, untestable and invisible to every linter — the gate that enforces
# an invariant had no linting of any kind.
#
# It runs with the repository under review as the working directory, and reads
# `D80_REPO` from the environment. Nothing else is passed in.
import os, re, subprocess, sys, tempfile, pathlib
import yaml

# D80 is an INVARIANT: nothing shipped may depend on the environment it runs
# inside. This job checks the part of it a machine can check, and SAYS OUT LOUD
# which part it cannot. A gate that implies more coverage than it has is the
# failure mode this project has already found twice.

REPO = os.environ.get("D80_REPO", "")
# The reference deployment is EXEMPT BY NAME, and that is D80's own wording:
# "Reference deployments may require operators; a module's own chart may not."
REFERENCE_DEPLOYMENTS = {"yadgarhq/deploy"}

out, problems, notes = [], [], []


def w(line=""):
    out.append(line)


# ---------------------------------------------------------------- API groups
#
# AN ALLOWLIST OF BUILT-IN GROUPS, never a denylist of CRD groups. A denylist
# passes vacuously the moment somebody uses a group it was never told about,
# which is exactly how a gate ends up checking nothing. Here an unknown group is
# treated as CRD-bearing, so the unknown case fails safe.
BUILTIN_GROUPS = {
    "", "apps", "batch", "autoscaling", "policy",
    "networking.k8s.io", "rbac.authorization.k8s.io", "storage.k8s.io",
    "apiextensions.k8s.io", "admissionregistration.k8s.io",
    "apiregistration.k8s.io", "authentication.k8s.io", "authorization.k8s.io",
    "certificates.k8s.io", "coordination.k8s.io", "discovery.k8s.io",
    "events.k8s.io", "flowcontrol.apiserver.k8s.io", "node.k8s.io",
    "scheduling.k8s.io", "resource.k8s.io", "internal.apiserver.k8s.io",
}

# PRODUCT versus SPECIFICATION, because D80 draws that line and the first draft
# of this gate got it wrong. Gateway API is a specification with several
# conformant implementations, so depending on it is permitted; KEDA is one
# product an adopter has to install. BOTH still have to be switchable off — that
# is the property this job actually enforces. This table only explains a name in
# the output; it changes no verdict.
CRD_KINDS = {
    "gateway.networking.k8s.io": (
        "SPECIFICATION",
        "Gateway API: a SIG-Network specification with multiple conformant "
        "implementations (Envoy Gateway, Istio, NGINX Gateway Fabric, Traefik, "
        "Cilium, GKE, AWS). D71 chose it for that reason and D80 names it as "
        "permitted.",
    ),
    "keda.sh": ("PRODUCT", "KEDA: one autoscaler the adopter must install."),
    "cert-manager.io": ("PRODUCT", "cert-manager: one certificate controller."),
    "acme.cert-manager.io": ("PRODUCT", "cert-manager: one certificate controller."),
    "k8s.mariadb.com": ("PRODUCT", "mariadb-operator: one database operator."),
    "monitoring.coreos.com": ("PRODUCT", "Prometheus Operator: one metrics stack."),
    "argoproj.io": ("PRODUCT", "Argo: one delivery controller."),
    "external-secrets.io": ("PRODUCT", "External Secrets Operator."),
    "gateway.envoyproxy.io": (
        "PRODUCT",
        "Envoy Gateway's own extension API: an IMPLEMENTATION of Gateway API, "
        "not the specification. This is the exact shape D80 was written about.",
    ),
    "config.gateway.envoyproxy.io": ("PRODUCT", "Envoy Gateway's own extension API."),
}


def classify(group):
    if group in CRD_KINDS:
        return CRD_KINDS[group]
    return (
        "UNCLASSIFIED",
        "not in this job's table. Treated as a product until D80 says "
        "otherwise; add it to CRD_KINDS with a reason.",
    )


# How the note below names a list, so "a unclassified" never gets printed.
LIST_PHRASE = {
    "PRODUCT": "a product the adopter must install",
    "UNCLASSIFIED": "a group this job has no entry for, so it is treated as a product",
}


def helm_render(values_file, label):
    """Render the chart. Returns (docs, error_text)."""
    cmd = ["helm", "template", "d80-render", "chart"]
    if values_file:
        cmd += ["-f", values_file]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip()
    docs = []
    for d in yaml.safe_load_all(r.stdout):
        if isinstance(d, dict) and d.get("apiVersion"):
            docs.append(d)
    return docs, None


def crd_docs(docs):
    found = []
    for d in docs:
        av = str(d.get("apiVersion", ""))
        group = av.split("/")[0] if "/" in av else ""
        if group in BUILTIN_GROUPS:
            continue
        name = (d.get("metadata") or {}).get("name", "<unnamed>")
        found.append((av, group, str(d.get("kind", "?")), str(name)))
    return found


def flip_enabled(node, path, flipped):
    """Set every key named `enabled`, at any depth, to false."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if k == "enabled" and isinstance(v, bool):
                node[k] = False
                flipped.append(p)
            else:
                flip_enabled(v, p, flipped)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            flip_enabled(v, f"{path}[{i}]", flipped)


# ------------------------------------------------------------- chart section
have_chart = pathlib.Path("chart").is_dir()

w("## D80 portability gate")
w()

if REPO in REFERENCE_DEPLOYMENTS:
    # EXEMPT BY NAME, and the whole job rather than the chart half. D80:
    # "Reference deployments may require operators; a module's own chart may
    # not", and "`deploy` staying opinionated is the point of a reference
    # deployment". Running Envoy Gateway, cert-manager, the MariaDB operator and
    # KEDA is what this repository IS. Every check below would be measuring it
    # against a rule D80 explicitly does not apply to it.
    w(f"`{REPO}` is a REFERENCE DEPLOYMENT and is EXEMPT BY NAME.")
    w()
    w(
        "D80: _\"Reference deployments may require operators; a module's own "
        "chart may not\"_, and _\"`deploy` staying opinionated is the point of a "
        "reference deployment\"_. An adopter who wants this exact stack gets it; "
        "an adopter with their own gets each module's values file."
    )
    w()
    print(summary_text := "\n".join(out))
    sp = os.environ.get("GITHUB_STEP_SUMMARY")
    if sp:
        with open(sp, "a") as fh:
            fh.write(summary_text + "\n")
    sys.exit(0)

if not have_chart:
    w("No `chart/` in this repository, so the chart checks do not apply.")
    w()

if have_chart:
    # BOTH RENDERS FIRST, then the report. The note about a product defaulting
    # on is only true of a resource that a value can in fact turn off, so it
    # cannot be written before the second render has been read.
    default_docs, err_default = helm_render(None, "defaults")
    if err_default:
        problems.append(
            f"`helm template chart` failed on the default values: {err_default}"
        )
        default_docs = []
    default_crds = crd_docs(default_docs)

    # ---- THE PROPERTY, proved by rendering twice -------------------------
    #
    # NOT "the defaults emit no CRDs". That formulation contradicts D80: it
    # would fail an HTTPRoute, which D80 explicitly permits, and force the
    # reference deployment's route off to satisfy a rule nobody wrote.
    #
    # The property is: EVERY CRD-BEARING RESOURCE CAN BE TURNED OFF BY A VALUE.
    # That is what makes a chart installable on a bare cluster, which is what an
    # adopter actually needs.
    off = yaml.safe_load(pathlib.Path("chart/values.yaml").read_text()) or {}
    flipped = []
    flip_enabled(off, "", flipped)

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(off, fh)
        off_path = fh.name

    off_docs, err = helm_render(off_path, "all-off")
    survivors = set() if err else {c[0] + "|" + c[3] for c in crd_docs(off_docs)}

    # ---- 1. what the DEFAULTS ask an adopter for -------------------------
    w("### What the default values ask an adopter to have installed")
    w()
    if not default_crds:
        w("Nothing. Every rendered resource is a built-in Kubernetes API.")
    else:
        w("| apiVersion | kind | name | list | why |")
        w("| --- | --- | --- | --- | --- |")
        for av, group, kind, name in default_crds:
            kind_of, why = classify(group)
            w(f"| `{av}` | {kind} | `{name}` | **{kind_of}** | {why} |")
        w()
        for av, group, kind, name in default_crds:
            kind_of, _ = classify(group)
            # A SPECIFICATION on by default is exactly what D80 permits, so it
            # gets no note. A resource a value cannot switch off is an error
            # below, not a note — saying "a value turns it off" about one that
            # does not would be the gate contradicting itself.
            if kind_of == "SPECIFICATION" or (av + "|" + name) in survivors:
                continue
            notes.append(
                f"`{kind}/{name}` needs `{group}`, {LIST_PHRASE[kind_of]}, and it "
                f"is ON by default. D80 permits that, because a value switches it "
                f"off. Consider whether it should default off anyway, the way "
                f"`keda.sh/ScaledObject` does in every chart here."
            )
    w()

    w("### Can an adopter with a bare cluster turn all of it off?")
    w()
    w(
        "The gate renders the chart a second time with **every values key named "
        "`enabled` set to false**, and requires that render to contain no "
        "resource outside the built-in Kubernetes API groups."
    )
    w()
    if flipped:
        w("Keys this render set to `false`: " + ", ".join(f"`{k}`" for k in flipped) + ".")
    else:
        w("This chart declares no `enabled` key, so the second render equals the first.")
    w()

    if err:
        problems.append(
            "the chart does not render with every `enabled` key set to false, so "
            f"an adopter cannot switch its optional parts off: {err}"
        )
    else:
        left = crd_docs(off_docs)
        if left:
            for av, group, kind, name in left:
                problems.append(
                    f"`{kind}/{name}` (`{av}`) still renders with every `enabled` "
                    f"key false. It needs a CRD from `{group}`, so this chart "
                    f"cannot install on a cluster that does not have it. Guard it "
                    f"behind a values key named `enabled`."
                )
            w("**No.** " + str(len(left)) + " resource(s) survived — listed as errors below.")
        else:
            w("**Yes.** The all-off render contains only built-in Kubernetes APIs.")
    w()

    # ---- 3. the RELEASE-SHAPED render ------------------------------------
    #
    # `ci-release` rewrites `.image.digest` and deletes `.image.tag` at package
    # time (D65), so a `latest` sitting in git is NOT a finding — checking the
    # in-git values would report a false positive on every repository here. The
    # shape that ships is the one worth checking, so the gate reproduces the
    # rewrite and inspects THAT.
    rel = yaml.safe_load(pathlib.Path("chart/values.yaml").read_text()) or {}
    if isinstance(rel.get("image"), dict):
        rel["image"]["digest"] = "sha256:" + "0" * 64
        rel["image"].pop("tag", None)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(rel, fh)
        rel_path = fh.name

    rel_docs, err = helm_render(rel_path, "release-shaped")
    w("### The release-shaped render")
    w()
    w(
        "Rendered as `ci-release` packages it — `.image.digest` set, "
        "`.image.tag` deleted (D65) — because that is the artifact an adopter "
        "installs. A floating `latest` in git is therefore not a finding here."
    )
    w()
    if err:
        problems.append(f"the chart does not render in its published shape: {err}")
    else:
        bad = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    p = f"{path}.{k}" if path else k
                    if k == "imagePullPolicy" and v == "Never":
                        bad.append(
                            f"`{p}: Never` — an image that must already be on the "
                            f"node. That is a property of one cluster, not of the chart."
                        )
                    if k == "hostPort":
                        bad.append(
                            f"`{p}: {v}` — a host port binds the chart to a node's "
                            f"network, which an adopter cannot re-map."
                        )
                    if k == "image" and isinstance(v, str) and v.endswith(":latest"):
                        bad.append(
                            f"`{p}: {v}` — a moving tag in the PUBLISHED shape. "
                            f"D61 requires a release to name exactly what runs."
                        )
                    walk(v, p)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

        for d in rel_docs:
            walk(d, f"{d.get('kind','?')}/{(d.get('metadata') or {}).get('name','?')}")
        if bad:
            problems.extend(bad)
            w("Findings are listed as errors below.")
        else:
            w(
                "No `imagePullPolicy: Never`, no `hostPort`, no `:latest` image "
                "reference."
            )
    w()

# ------------------------------------------------------ source-tree tripwires
#
# THESE ARE DENYLISTS AND THE OUTPUT SAYS SO. A denylist finds a name it was
# told about and nothing else, so it can only catch a KNOWN way of depending on
# the environment. It is a tripwire on the first introduction, never a proof of
# portability. The chart checks above are the ones that assert a property.
CLOUD = [
    (r"169\.254\.169\.254", "the cloud instance metadata endpoint"),
    (r"metadata\.google\.internal", "the GCP metadata endpoint"),
    (r"eks\.amazonaws\.com/role-arn", "an IRSA service-account annotation"),
    (r"iam\.gke\.io/gcp-service-account", "a GKE Workload Identity annotation"),
    (r"azure\.workload\.identity/", "an Azure Workload Identity annotation"),
]
# EXACT crate names. A `^aws-` prefix would match `aws-lc-rs` and `aws-lc-sys`,
# which are ring's crypto backend and have nothing to do with AWS the platform —
# `yadgar-client/Cargo.lock` carries both today, so that prefix would have made
# this check red on a repository doing nothing wrong.
CLOUD_CRATES = re.compile(
    r'^\s*(?:name\s*=\s*"|)(aws-config|aws-types|aws-sdk-[a-z0-9-]+|rusoto[_a-z0-9]*'
    r"|azure_core|azure_identity|azure_storage|google-cloud-[a-z0-9-]+|gcp_auth"
    r'|gcloud-sdk)("|\s*=|\s*$)'
)

# X-Forwarded-For is DELIBERATELY ABSENT: D80 names it as the behaviour every
# ingress shares, so depending on it is the portable form rather than a
# violation. What is listed is what belongs to ONE implementation.
INGRESS = [
    (r"x-envoy-[a-z0-9-]+", "an Envoy-specific header"),
    (r"x-forwarded-client-cert", "an Envoy/Istio-specific header"),
    (r"nginx\.ingress\.kubernetes\.io/", "an ingress-nginx annotation"),
    (r"nginx\.org/", "an NGINX Inc. ingress annotation"),
    (r"traefik\.(ingress\.kubernetes\.io|containo\.us)/", "a Traefik annotation"),
    (r"haproxy(-ingress\.github\.io|\.org)/", "an HAProxy annotation"),
    (r"alb\.ingress\.kubernetes\.io/", "an AWS ALB annotation"),
    (r"appgw\.ingress\.kubernetes\.io/", "an Azure Application Gateway annotation"),
    (r"\bx-real-ip\b", "an NGINX-specific header"),
    (r"\bcf-connecting-ip\b", "a Cloudflare-specific header"),
    (r"\bx-amzn-trace-id\b", "an AWS-specific header"),
    (r"\bx-azure-(clientip|socketip)\b", "an Azure-specific header"),
]

SKIP_DIRS = {".git", "target", "node_modules", "proto", ".github"}


def scan_files():
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f)
            if f in ("Cargo.toml", "Cargo.lock") or f.endswith((".rs", ".yaml", ".yml")):
                yield p


def exempt(lines, i):
    """`d80: exempt — why` on the line or the one above it, per the repo's
    existing observe-coverage convention. In the diff, where a reviewer sees it."""
    for j in (i, i - 1):
        if 0 <= j < len(lines) and re.search(r"d80:\s*exempt", lines[j], re.I):
            return True
    return False


cloud_hits, ingress_hits, dns_hits, lock_notes = [], [], [], []

for path in scan_files():
    try:
        lines = pathlib.Path(path).read_text(errors="replace").splitlines()
    except OSError:
        continue
    is_lock = path.endswith("Cargo.lock")
    for i, line in enumerate(lines):
        low = line.lower()
        if exempt(lines, i):
            continue
        for pat, what in CLOUD:
            if re.search(pat, low):
                cloud_hits.append((path, i + 1, what, line.strip()[:90]))
        if CLOUD_CRATES.search(line):
            hit = (path, i + 1, "a cloud-provider SDK crate", line.strip()[:90])
            # A TRANSITIVE pull is a NOTE, a declared dependency is an ERROR.
            # Failing on the lock would make one upstream's choice turn every
            # repository here red, which is not a gate anybody keeps.
            (lock_notes if is_lock else cloud_hits).append(hit)
        for pat, what in INGRESS:
            if re.search(pat, low):
                ingress_hits.append((path, i + 1, what, line.strip()[:90]))
        if path.endswith(".rs") and "svc.cluster.local" in low:
            dns_hits.append(
                (path, i + 1, "cluster-internal DNS compiled in", line.strip()[:90])
            )

w("### Environment dependencies in the source tree")
w()
w(
    "These are **denylists**. They find a name they were told about and nothing "
    "else, so they catch a KNOWN way of depending on the environment and cannot "
    "prove there is no other. The chart checks above assert a property; these do not."
)
w()
w("Scanned: `*.rs`, `*.yaml`, `*.yml`, `Cargo.toml`, `Cargo.lock`.")
w("Not scanned: `proto/` (a vendored contract), `.github/`, `target/`, and prose —")
w("a document explaining D80 would otherwise fail the gate for quoting it.")
w()
w("Suppress a deliberate one with `d80: exempt — why` on the line or the line above.")
w()

for label, hits in (
    ("cloud provider", cloud_hits),
    ("ingress implementation", ingress_hits),
    ("cluster DNS", dns_hits),
):
    if hits:
        for path, ln, what, txt in hits:
            problems.append(f"`{path}:{ln}` names {what} — `{txt}`")
    else:
        w(f"- No {label} name found.")

if lock_notes:
    w()
    for path, ln, what, txt in lock_notes:
        notes.append(
            f"`{path}:{ln}` names {what} transitively — `{txt}`. Reported, not "
            f"failed: a lockfile entry is somebody else's choice."
        )
w()

# ------------------------------------------- what this job does NOT check
w("### What this job does NOT check")
w()
w(
    "**Whether a security control rests on an undeclared environment default.** "
    "That is D80's highest-severity class — a component that keeps working and "
    "stops protecting — and no grep finds it. The audit source address is the "
    "worked example: making it trustworthy with an Envoy `ClientTrafficPolicy` "
    "would have compiled, rendered and passed every check here."
)
w()
w("It is a question for a human, and it is in the `## Risk` section of the pull request template:")
w()
w(
    "> If the operator ran a different ingress, a different cloud, or a different "
    "set of operators, would this still be correct — and would it still be SECURE?"
)
w()
w(
    "Also unchecked: whether an `enabled` key an adopter flips leaves the chart "
    "_useful_, and any dependency on the environment expressed in a way no "
    "pattern above names."
)
w()

if notes:
    w("### Notes")
    w()
    for n in notes:
        w(f"- {n}")
    w()

summary = "\n".join(out)
sp = os.environ.get("GITHUB_STEP_SUMMARY")
if sp:
    with open(sp, "a") as fh:
        fh.write(summary + "\n")
        if problems:
            fh.write("\n### D80 violations\n\n")
            for p in problems:
                fh.write(f"- {p}\n")
print(summary)

if problems:
    print("\n=== D80 violations ===")
    for p in problems:
        print(f"- {p}")
        print("::error::D80: " + re.sub(r"`", "", p))
    sys.exit(1)
print("\nD80 gate passed.")
