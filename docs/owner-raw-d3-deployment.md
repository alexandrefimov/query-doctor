# Owner Raw D3 Deployment Contract

Last reviewed: 2026-11-03

This document defines the only supported shared/non-local deployment shape for
`source_visibility=owner_raw`. It is a deployment contract, not an invitation to
turn Query Doctor into a general multi-tenant service.

## Scope

`owner_raw` has two separate identities:

- C1 collection credential: the local process, Kerberos ticket, keytab, or
  service account used to collect bounded Recent/Running cases.
- C2 viewer identity: the authenticated human viewer allowed to open the
  isolated owner-only original source page for a selected case.

Raw source reveal is authorized only by C2 and `query.user`. It must never be
authorized by C1, by the keytab owner set, or by the account running the web
process.

D3 means Query Doctor is reachable over a non-local bind only behind a trusted
auth front door. Local-first mode may intentionally map collectable owners to
the local viewer. D3 must not do that.

Query Doctor supports one D3 application contract:

```text
trusted auth front door -> exactly one normalized viewer header -> Query Doctor owner check
```

The front door may use the site's approved authentication mechanism, such as
OIDC/SSO, SAML, SPNEGO/Kerberos, or an enterprise gateway. Query Doctor does
not perform native OIDC, SAML, SPNEGO, Kerberos, LDAP, password, MFA, session,
logout, token-refresh, or group/RBAC authentication for `owner_raw`. Those
responsibilities stay at the trusted front door. Query Doctor accepts only the
already authenticated, already normalized simple owner value in
`viewer_identity_header`.

## Readiness State

The application-side D3 contract can be checked before a real ingress or
reverse proxy exists. The implemented Query Doctor boundary now covers:

- normalized viewer-header parsing and duplicate-header fail-closed behavior;
- non-local `owner_raw` startup refusal without authenticated viewer identity
  configuration;
- isolated source-page authorization by C2 viewer identity and selected-case
  `query.user`, never by C1 collection credential or keytab owner set;
- the `owner_raw_source_enabled` kill switch;
- raw-free reason-coded audit lines and denied-page remediation;
- dev-only synthetic front-door smoke and policy simulation;
- focused validation in [test-matrix.md](test-matrix.md).

These checks do not prove a real D3 deployment. A deployment is not ready for
shared/non-local raw source access until the live front door proves TLS/auth,
network isolation, inbound header stripping, identity-to-owner mapping, and
audit handling in the target environment.

## Reference Shape

Use this order:

1. Client connects over TLS to a trusted ingress or reverse proxy.
2. The ingress authenticates the request with the site-approved identity
   provider.
3. The ingress strips any inbound copy of the configured viewer header.
4. The ingress sets exactly one viewer header containing the normalized simple
   owner user.
5. Query Doctor receives the request and derives C2 from that header.
6. Query Doctor compares C2 to the selected case `query.user` before showing
   the isolated raw source page.

In a Kubernetes-style deployment, the pod keytab or service account remains C1
only. It may allow collection across configured owner users, but it must not
grant raw reveal to viewers.

## Auth Front Door Recipes

These are deployment recipes for producing the same header contract, not
separate Query Doctor auth modes.

### OIDC/SSO At Ingress

Use this as the default D3 shape when a corporate identity provider, MFA,
device policy, logout, and session lifecycle already exist outside Query Doctor:

1. The ingress or auth proxy completes OIDC/SSO authentication and policy
   checks.
2. The ingress maps the authenticated subject to the query-owner namespace,
   preferably an Active Directory `sAMAccountName` or the same simple account
   name used by Impala `query.user`.
3. The ingress strips inbound copies of `viewer_identity_header`.
4. The ingress sets exactly one `viewer_identity_header` with that simple owner
   value.
5. Query Doctor treats the header as C2 only and still checks it against the
   selected case `query.user`.

Do not forward ID tokens, access tokens, refresh tokens, email addresses,
opaque OIDC subjects, groups, roles, or display names as the viewer header.

### SPNEGO/Kerberos At Ingress

Use this when the target environment is already Kerberos/AD-heavy:

1. The ingress or reverse proxy completes SPNEGO/Kerberos authentication.
2. The ingress derives the human account primary from the authenticated
   principal, rejecting service and host principals.
3. The ingress strips inbound copies of `viewer_identity_header`.
4. The ingress sets exactly one `viewer_identity_header` with the simple human
   owner value, for example `analyst_one`, not
   `analyst_one@EXAMPLE.COM` and not `impala/host@EXAMPLE.COM`.

Do not move SPNEGO negotiation into Query Doctor. The web process should not
receive Kerberos tickets, keytab material, or raw proxy auth state as browser
visible or trusted-report data.

### AD/LDAP

Use AD/LDAP as an identity source behind the front door only. Do not configure
Query Doctor to bind to LDAP, collect user passwords, or manage login sessions.
If the site uses LDAP-backed authentication, the proxy or identity provider
must authenticate the request and emit the same normalized simple owner header.

## Public-Safe Front Door Snippets

These snippets are review patterns, not complete ingress or reverse-proxy
configuration. Keep real issuer URLs, client ids, client secrets, realm names,
hostnames, certificate paths, upstream addresses, and cluster selectors out of
committed docs. Adapt the pattern inside the site's approved front-door
technology.

The common invariant is:

```text
reject unauthenticated request
strip inbound X-Query-Doctor-Viewer
derive one simple owner value from verified front-door identity
set exactly one X-Query-Doctor-Viewer upstream header
deny direct client network access to Query Doctor
```

Direct access to the Query Doctor web process must be blocked by network
policy, firewall rules, sidecar policy, or an equivalent deployment control.
If a browser can reach Query Doctor without passing through the trusted front
door, the browser can spoof `viewer_identity_header` and the deployment is not
D3-safe.

### Common Upstream Header Pattern

Use one configured header name and one owner namespace:

```text
# Pseudocode at the trusted front door after authentication has succeeded.
strip_request_header("X-Query-Doctor-Viewer")
viewer = map_authenticated_identity_to_query_owner()
reject_unless_simple_owner(viewer)
set_upstream_header("X-Query-Doctor-Viewer", viewer)
drop_upstream_identity_tokens()
proxy_to_query_doctor()
```

`drop_upstream_identity_tokens()` means the front door must not forward raw
OIDC tokens, SAML assertions, Kerberos tickets, cookies used only for
front-door login, LDAP bind material, proxy auth blobs, groups, roles, or
display names as Query Doctor authorization inputs. Query Doctor needs only the
one normalized owner value.

### OIDC/SSO Claim Mapping Pattern

Prefer a claim that already matches the Impala `query.user` namespace, such as
an AD-backed `sAMAccountName` claim. If the identity provider does not emit
that namespace directly, do the mapping at the front door or identity provider,
not inside Query Doctor:

```text
# Pseudocode at the trusted front door.
claims = verified_oidc_or_sso_claims()
viewer = claims["sAMAccountName"]
reject_if_missing(viewer)
reject_unless_simple_owner(viewer)
strip_request_header("X-Query-Doctor-Viewer")
set_upstream_header("X-Query-Doctor-Viewer", viewer)
```

Do not use `sub`, email, UPN, display name, group, role, or tenant-scoped
opaque ids as the viewer header unless the front door first maps them to the
same simple owner namespace as `query.user`.

### SPNEGO/Kerberos Principal Mapping Pattern

SPNEGO negotiation belongs at the front door. Query Doctor should receive
neither tickets nor principals:

```text
# Pseudocode at the trusted front door.
principal = authenticated_kerberos_principal()
reject_if_service_or_host_principal(principal)
viewer = kerberos_human_primary(principal)
reject_if_contains_slash_realm_or_at_sign(viewer)
reject_unless_simple_owner(viewer)
strip_request_header("X-Query-Doctor-Viewer")
set_upstream_header("X-Query-Doctor-Viewer", viewer)
```

For example, the front door may map `analyst_one@EXAMPLE.REALM` to
`analyst_one`. It must reject service or host principals such as
`impala/host@EXAMPLE.REALM` or `HTTP/host@EXAMPLE.REALM`.

## Required Configuration

After the live review and source-enable gates pass, the enabled shared
`owner_raw` configuration includes the following fields. Keep
`owner_raw_source_enabled=false` during pre-proxy setup and rehearsal.

```json
{
  "host": "0.0.0.0",
  "source_visibility": "owner_raw",
  "viewer_identity_header": "X-Query-Doctor-Viewer",
  "owner_raw_source_enabled": true
}
```

Start the web process only with an explicit non-local bind review:

```bash
query-doctor-web --allow-nonlocal-web-bind
```

The header name may be provided in local config or with
`--viewer-identity-header`. Use `--disable-owner-raw-source`, or set
`owner_raw_source_enabled=false`, as the immediate kill switch for the isolated
original source page.

## Header Rules

The proxy or ingress must:

- authenticate every request before forwarding it;
- remove inbound copies of `viewer_identity_header`;
- set one trusted viewer header after authentication;
- set an already normalized simple owner user, such as an Active Directory
  `sAMAccountName` or Kerberos primary, not a UPN/email address, distinguished
  name, opaque subject, service principal, host principal, group name, role, or
  display name;
- do not forward raw identity-provider tokens or authorization material to
  Query Doctor; supply only the reviewed normalized viewer identity header.

Query Doctor treats missing, invalid, service-principal, and host-principal
viewer header values as unauthenticated. UPN/email-style values, distinguished
names, group/role-like values, opaque subjects, whitespace-separated display
names, and comma-separated subjects are also unauthenticated. If the HTTP
request exposes duplicate viewer header values, Query Doctor also treats the
viewer as unauthenticated. Raw source access then fails closed.

## Runtime Checks

Before enabling non-local `owner_raw`, verify:

- Direct browser access to the Query Doctor web process is blocked; only the
  trusted front door can reach the upstream web process.
- `query-doctor-web` refuses to start without authenticated viewer identity
  configuration.
- Requests without the viewer header receive no owner-raw source link and no
  source page.
- Requests with duplicate viewer header values receive no owner-raw source link
  and no source page, even if one value matches the query owner.
- Requests with a mismatched viewer header receive no owner-raw source link and
  no source page.
- Requests with a matching viewer header can open only the isolated source page
  for that viewer's own selected case.
- `owner_raw_source_enabled=false` hides the source link and blocks the source
  page without changing collection owner filters or optimizer policy.
- Owner-raw audit lines appear for source-page attempts and include only
  request id, route source, HTTP status, reason code, viewer mode/source, and
  switch state.
- Audit lines do not contain raw SQL, query ids, case ids, query users, local
  paths, header values, secrets, model names, runtime internals, or raw artifact
  filenames.
- Denied source pages show only reason-coded remediation text. They must not
  echo viewer values, query users, header values, query ids, local paths, SQL,
  secrets, or raw artifact filenames.

## Pre-Proxy Readiness Checklist

Complete this before a real auth proxy or ingress is available:

- Choose one external front-door mechanism: OIDC/SSO, SAML, SPNEGO/Kerberos, or
  an enterprise gateway. Do not add a second Query Doctor auth mode for the
  same deployment.
- Choose one owner namespace for the viewer header. Prefer the exact simple
  account namespace used by Impala `query.user`, such as an Active Directory
  `sAMAccountName` or Kerberos human primary.
- Choose one header name, for example `X-Query-Doctor-Viewer`, and configure
  Query Doctor with `viewer_identity_header`.
- Keep the upstream Query Doctor web process off direct client networks in the
  target design. The only intended browser path should be through the trusted
  front door.
- Keep `owner_raw_source_enabled=false` while the front door, identity mapping,
  and network isolation are still unproven.
- Prepare raw-free test personas: one matching owner viewer, one different
  human viewer, one unauthenticated request, and one spoofed-header request.
  Use placeholders in committed docs and keep real users, hosts, URLs, query
  ids, case ids, tokens, and SQL out of git.
- Run the local synthetic front-door smoke and policy simulator described
  below. These checks should be green before any live proxy work starts.
- Run the owner-raw D3 row in [test-matrix.md](test-matrix.md) after changing
  this contract, the policy helper, viewer identity parsing, web routes, or
  denied-page/audit wording.

At the end of pre-proxy readiness, the only remaining unknowns should be the
external controls: real TLS/auth, direct-network blocking, real identity claim
or principal mapping, real header stripping, and real audit transport.

## Staging Config Preflight

Before a real proxy or ingress is ready, audit the planned Query Doctor staging
config without retaining private deployment details:

```bash
python3 scripts/audit_owner_raw_staging_preflight.py \
  --config <ignored-local-web-config.json> \
  --allow-nonlocal-web-bind
```

If the startup command will use the CLI kill switch instead of config
`owner_raw_source_enabled=false`, include:

```bash
python3 scripts/audit_owner_raw_staging_preflight.py \
  --config <ignored-local-web-config.json> \
  --allow-nonlocal-web-bind \
  --disable-owner-raw-source
```

The preflight is raw-free and path-free. It checks only config shape and safe
counts: at least one `owner_raw` source, one valid `viewer_identity_header`, the
owner-raw source page disabled by config or CLI kill switch, explicit review of
non-local bind startup, and no explicit disabling of privacy/redaction controls.
It prints only safe issue categories and does not print config paths, header
names, header values, users, URLs, local paths, credentials, query ids, or raw
source. A passing `owner_raw_staging_preflight_v1` summary does not replace the
live front-door review gate and must not be used to enable raw source reveal.

## D3 Readiness Gate

After the real front door has been reviewed and the raw-free live review summary
exists, combine the staging config and live review evidence before enabling raw
source reveal:

```bash
python3 scripts/audit_owner_raw_d3_readiness.py \
  --config <ignored-local-web-config.json> \
  --allow-nonlocal-web-bind \
  --front-door-review-json <raw-free-front-door-review.json>
```

If the staging startup uses the CLI kill switch instead of config
`owner_raw_source_enabled=false`, also pass `--disable-owner-raw-source`. The
readiness gate fails closed when the live front-door review summary is missing,
when the staging config preflight fails, or when the review summary is not the
owner-raw D3 profile. A passing `owner_raw_d3_readiness_v1` summary means the
operator has raw-free evidence that the config shape and front-door review are
ready for the next controlled source-enable step; it does not add native SSO,
does not perform authentication, and does not print config paths, review paths,
header names, header values, users, URLs, query ids, credentials, auth material,
or raw source.

## D3 Rehearsal Runner

When the dev SSO compose stack is running and a raw-free live front-door review
summary already exists, use the aggregate rehearsal runner to execute the
application-side sequence in one command:

```bash
python3 scripts/audit_owner_raw_d3_rehearsal.py \
  --config <ignored-local-web-config.json> \
  --allow-nonlocal-web-bind \
  --front-door-review-json <raw-free-front-door-review.json>
```

If the staging startup uses the CLI kill switch instead of config
`owner_raw_source_enabled=false`, also pass `--disable-owner-raw-source`. The
runner checks the dev Keycloak SSO smoke, the live front-door review summary,
the staging config preflight, and the aggregate readiness gate, then writes an
optional `owner_raw_d3_rehearsal_v1` summary with only safe gate status,
counts, and issue categories. It fails closed when any gate fails, including a
missing live review summary, an unreviewed front door, missing viewer identity
configuration, unproven inbound viewer-header stripping, or enabled raw source
without a rehearsal kill switch.

The rehearsal runner is an operator convenience for the D3 path; it is not a
new auth mode, not a substitute for the real live checks above, and not a raw
source enablement command. It does not print config paths, review paths, dev SSO
URLs, usernames, login secrets, header names or values, query ids, credentials,
auth material, or raw source.

## Source-Enable Canary Gate

After rehearsal passes, create a separate ignored local config for the planned
canary source-enable step. Keep the reviewed front door, viewer-header mapping,
owner-raw source set, non-local bind, and rollback plan unchanged from the
passing rehearsal. The canary config must explicitly set
`owner_raw_source_enabled=true`; do not rely on defaults for this step.

Before starting the canary web process, audit the planned config and the retained
raw-free rehearsal summary:

```bash
python3 scripts/audit_owner_raw_d3_source_enable.py \
  --config <ignored-local-source-enabled-config.json> \
  --rehearsal-summary-json <raw-free-d3-rehearsal-summary.json> \
  --allow-nonlocal-web-bind \
  --confirm-source-enable-canary \
  --confirm-no-disable-owner-raw-source \
  --confirm-no-front-door-or-header-change \
  --confirm-kill-switch-rollback-plan
```

The source-enable gate checks only safe statuses and counts. It requires a
passing `owner_raw_d3_rehearsal_v1` summary from a disabled-source rehearsal,
one matching non-local owner-raw source set, valid viewer identity config,
explicit `owner_raw_source_enabled=true`, unchanged front-door/header mapping by
operator confirmation, no planned `--disable-owner-raw-source` startup flag, and
a confirmed kill-switch rollback plan. It can write an
`owner_raw_d3_source_enable_canary_v1` summary with only safe gate status,
counts, failed-gate names, and issue categories.

This gate does not start Query Doctor, does not perform authentication, does not
enable raw source, and does not replace the runtime checks below. It must fail
closed if the rehearsal summary is missing or not green, if the planned config
changes the owner-raw source count, if the canary config omits explicit
`owner_raw_source_enabled=true`, if `viewer_identity_header` is missing or
invalid, if the bind is not D3/non-local, or if the operator confirmations are
missing. It does not print config paths, summary paths, URLs, users, header
names or values, query ids, credentials, auth material, or raw source.

## Post-Enable Canary Gate

After the short canary window, retain only a raw-free post-enable review
summary. Do not retain proxy logs, source screenshots, raw source pages, query
ids, case ids, user names, header values, URLs, paths, tokens, cookies, tickets,
or assertions. Validate the retained source-enable summary and post-enable
review with:

```bash
python3 scripts/audit_owner_raw_d3_post_enable.py \
  --source-enable-summary-json <raw-free-source-enable-summary.json> \
  --post-enable-review-json <raw-free-post-enable-review.json>
```

To create a fail-closed review template or inspect the supported field labels:

```bash
python3 scripts/audit_owner_raw_d3_post_enable.py --template-json <raw-free-post-enable-review.json>
python3 scripts/audit_owner_raw_d3_post_enable.py --list-required-fields
```

The post-enable gate requires a passing
`owner_raw_d3_source_enable_canary_v1` summary, confirms source was enabled by
the operator rather than by a Query Doctor script, and validates only boolean or
enum post-enable evidence: unchanged front door/header mapping, direct upstream
blocking, matching-viewer allow, different/missing/invalid/duplicate/spoofed
viewer denial, raw-free denied pages, raw-free audit lines, raw-free trusted
surfaces, rollback verification, monitoring, and a final source-state enum of
`leave_enabled` or `rollback_completed`.

This gate does not contact the proxy, does not open cases, does not read source
text, does not decide user access, and does not change the final source state.
It can write an `owner_raw_d3_post_enable_canary_v1` summary with only safe
gate status, counts, failed-gate names, issue categories, and the final
source-state enum. It must fail closed when the source-enable summary is missing
or not green, runtime allow/deny evidence is incomplete, rollback is unverified,
final source state is internally inconsistent, or retained evidence contains
raw fields.

## Launch Closure Gate

After the post-enable canary is reviewed, use the launch closure gate to bind
the retained raw-free D3 evidence into one final closure summary:

```bash
python3 scripts/audit_owner_raw_d3_launch_closure.py \
  --front-door-review-summary-json <raw-free-front-door-review-audit-summary.json> \
  --readiness-summary-json <raw-free-d3-readiness-summary.json> \
  --rehearsal-summary-json <raw-free-d3-rehearsal-summary.json> \
  --source-enable-summary-json <raw-free-source-enable-summary.json> \
  --post-enable-summary-json <raw-free-post-enable-summary.json>
```

To retain a single local manifest with safe relative references to those five
summaries, first build the manifest from a local evidence directory:

```bash
python3 scripts/build_owner_raw_d3_launch_closure_manifest.py \
  --redaction-reviewed \
  --front-door-review-summary-json <raw-free-front-door-review-audit-summary.json> \
  --readiness-summary-json <raw-free-d3-readiness-summary.json> \
  --rehearsal-summary-json <raw-free-d3-rehearsal-summary.json> \
  --source-enable-summary-json <raw-free-source-enable-summary.json> \
  --post-enable-summary-json <raw-free-post-enable-summary.json> \
  --out <raw-free-d3-launch-closure-manifest.json>

python3 scripts/audit_owner_raw_d3_launch_closure.py \
  --launch-closure-manifest <raw-free-d3-launch-closure-manifest.json>
```

The builder writes an `owner_raw_d3_launch_closure_manifest_v1` manifest with
one entry, safe relative `*.json` references, `redaction_reviewed=true`, and
only fixed limitation labels. It checks that referenced artifacts exist, that
the manifest output does not overwrite an input, and that references are
unique and safe to express relative to the manifest. It does not validate
summary contents; the launch closure gate remains the validator.

The launch closure gate consumes only already raw-free summaries. It verifies
the front-door review, disabled-source readiness, dev SSO rehearsal,
operator-planned source-enable canary, post-enable runtime allow/deny evidence,
rollback verification, and final source-state enum as one chain. It can write an
`owner_raw_d3_launch_closure_v1` summary with only safe gate status, counts,
failed-gate names, issue categories, a `closed` or `blocked` verdict, and a
final source-state enum of `leave_enabled` or `rollback_completed`.

This gate does not contact the proxy, perform authentication, open cases, read
source text, read config, enable or disable owner-raw source reveal, or decide
user access. It must fail closed when any retained summary is missing or not
green, when the chain does not preserve disabled rehearsal to operator-planned
enablement to reviewed post-enable closure, when Query Doctor script-driven
source enablement or native auth appears, when the final source state is
invalid, when the manifest is missing redaction review or safe relative
references, or when retained evidence contains raw fields.

## Artifact Workspace Helper

Use the artifact workspace helper when an operator needs a repeatable local
directory containing the D3 config templates, fail-closed review templates, an
operator checklist, the latest deployment-bundle summary, and the final
support-readiness summary:

```bash
python3 scripts/prepare_owner_raw_d3_artifacts.py \
  --artifact-dir <ignored-local-d3-artifact-dir> \
  --confirm-local-ignored-artifact-dir \
  --allow-nonlocal-web-bind \
  --confirm-source-enable-canary \
  --confirm-no-disable-owner-raw-source \
  --confirm-no-front-door-or-header-change \
  --confirm-kill-switch-rollback-plan
```

The helper creates missing artifacts and preserves existing review evidence by
default. Pass `--replace-templates` only when intentionally resetting local
templates back to fail-closed defaults. Pass `--skip-bundle` to create or
preserve the scaffold without running the deployment bundle.

After the deployment bundle passes, the helper runs
`scripts/audit_owner_raw_sso_proxy_support_readiness.py` by default and writes a
local `support-readiness.summary.json`. A passing helper run therefore means
the retained local D3 evidence also satisfies the release-facing support claim
gate for deployment behind a trusted SSO/auth proxy via
`viewer_identity_header`. Pass `--skip-support-readiness` only when
intentionally stopping at scaffold or bundle preparation. Pass
`--require-source-left-enabled` when the release cutoff requires the canary to
finish with `final_source_state=leave_enabled`; without it, both
`leave_enabled` and `rollback_completed` are accepted as reviewed support-path
closure states.

The helper prints only raw-free status labels and issue categories. It never
prints the artifact directory, filenames, config paths, review paths, retained
manifest paths, dev SSO URLs, usernames, login secrets, header names or values,
query ids, credentials, auth material, or raw source. The workspace directory
must be local and ignored; do not commit the generated config templates, review
templates, summaries, checklists, or operator evidence.

## Deployment Bundle Gate

For an operator handoff that needs one raw-free deployment verdict, run the
deployment bundle gate with the ignored disabled-source config, the separate
ignored source-enabled canary config, the raw-free front-door review summary,
and the raw-free post-enable review summary:

```bash
python3 scripts/audit_owner_raw_d3_deployment_bundle.py \
  --config <ignored-local-web-config.json> \
  --source-enable-config <ignored-local-source-enabled-config.json> \
  --front-door-review-json <raw-free-front-door-review.json> \
  --post-enable-review-json <raw-free-post-enable-review.json> \
  --allow-nonlocal-web-bind \
  --confirm-source-enable-canary \
  --confirm-no-disable-owner-raw-source \
  --confirm-no-front-door-or-header-change \
  --confirm-kill-switch-rollback-plan
```

If a retained launch-closure manifest already exists, add
`--launch-closure-manifest <raw-free-d3-launch-closure-manifest.json>` to audit
that retained manifest in addition to the bundle-generated closure. If the
disabled-source rehearsal startup uses the CLI kill switch instead of config
`owner_raw_source_enabled=false`, also pass `--disable-owner-raw-source`.

The bundle runs the existing front-door review audit, readiness gate, rehearsal
runner, source-enable canary gate, post-enable gate, manifest builder, and
launch-closure gate in order. It suppresses child command output and writes an
optional `owner_raw_d3_deployment_bundle_v1` summary with only safe gate
status, counts, failed-gate names, issue categories, a `ready` or `blocked`
deployment verdict, and the final source-state enum. It is an orchestration
gate only: it does not add auth, start Query Doctor, contact a proxy outside
the existing dev SSO smoke, open cases, read source text, change source state,
or decide user access. It does not print config paths, review paths, retained
manifest paths, dev SSO URLs, usernames, login secrets, header names or values,
query ids, credentials, auth material, or raw source.

## SSO/Auth Proxy Support Readiness Gate

Use the support readiness gate before release notes, README wording, or an
operator handoff states that Query Doctor supports deployment behind a trusted
SSO/auth proxy via `viewer_identity_header`:

```bash
python3 scripts/audit_owner_raw_sso_proxy_support_readiness.py \
  --deployment-bundle-summary-json <raw-free-d3-deployment-bundle-summary.json>
```

The input must be a passing `owner_raw_d3_deployment_bundle_v1` summary from
the full live-front-door, disabled-source rehearsal, source-enable canary,
post-enable, and launch-closure chain. Dev Keycloak smoke output alone must not
be used as support evidence. Add `--require-source-left-enabled` only when the
operator handoff specifically requires the canary to finish with
`final_source_state=leave_enabled`; otherwise both `leave_enabled` and
`rollback_completed` are accepted because both prove the reviewed support path
and the kill-switch rollback path.

The gate writes an optional `owner_raw_sso_proxy_support_readiness_v1` summary
with only safe support status, issue categories, final source-state enum, and
the fixed support claim
`deployment_behind_trusted_sso_auth_proxy_via_viewer_identity_header`. It
does not add native OIDC, SAML, SPNEGO/Kerberos, LDAP, password, MFA, session,
group, RBAC, logout, or token handling inside Query Doctor. It does not contact
a proxy, perform authentication, start Query Doctor, open cases, read source
text, print artifact paths, URLs, users, header names or values, query ids,
credentials, auth material, or raw source.

## Policy Simulator

Use the dev-only `scripts/owner_raw_policy_simulator.py` helper to audit the
owner-raw source allow/deny matrix without opening cases or reading SQL:

```bash
python3 scripts/owner_raw_policy_simulator.py \
  --source-visibility owner_raw \
  --host 0.0.0.0 \
  --allow-nonlocal-web-bind \
  --viewer-identity-header-configured \
  --viewer-header-value sample_owner \
  --query-user sample_owner
```

The output is raw-free JSON: it includes the allow/deny result, reason code,
route class, source visibility, source switch, viewer mode, bind scope, and
input shape booleans/counts. It does not echo viewer values, query users, header
values, SQL, case ids, paths, or secrets. The simulator is an audit aid for the
policy matrix; it is not an auth proxy, deployment check, or substitute for the
runtime checks above.

## Front Door Smoke Harness

Use the dev-only `scripts/owner_raw_front_door_smoke.py` helper to exercise the
D3 front-door contract with synthetic inputs:

```bash
python3 scripts/owner_raw_front_door_smoke.py
```

The smoke checks these raw-free scenarios:

- a matching front-door identity strips spoofed inbound viewer headers and
  forwards exactly one normalized upstream viewer header;
- a Kerberos human principal maps to the same simple owner namespace;
- missing, mismatched, service-principal, and duplicate-upstream-header cases
  fail closed before raw source can be authorized.

The output is raw-free JSON and does not echo synthetic viewer values, query
users, header values, principals, SQL, case ids, paths, or secrets. The helper
does not contact an IdP, proxy, Kerberos service, LDAP server, or Query Doctor
web server. It is a local regression smoke for the application contract, not a
replacement for testing the real front-door deployment and network isolation.

## Dev Keycloak SSO Smoke

When a real Keycloak or ingress administrator is not available yet, the
developer-only compose harness in `dev/sso/compose.yaml` can exercise an actual
OIDC login flow through Keycloak and oauth2-proxy while preserving the same
Query Doctor contract:

```bash
docker compose -f dev/sso/compose.yaml up --pull missing
```

The harness is documented in [dev-sso-keycloak.md](dev-sso-keycloak.md). It
starts Query Doctor only inside the compose network, exposes Keycloak and
oauth2-proxy on localhost, configures Query Doctor to trust
`X-Forwarded-Preferred-Username`, and keeps
`owner_raw_source_enabled=false` by default. oauth2-proxy strips inbound
`X-Forwarded-*` auth headers, injects the authenticated preferred username, and
does not forward access tokens, ID tokens, Basic auth, or Authorization bearer
headers upstream.

With the compose stack running, use the dev-only live smoke to verify the local
OIDC path without printing cookies, tokens, code/state values, usernames, login
secrets, or URLs:

```bash
python3 scripts/dev_sso_keycloak_smoke.py --compact
```

This is a local SSO smoke, not live D3 validation. It does not prove corporate
TLS, MFA, device posture, identity lifecycle, production Keycloak operations,
target-environment network isolation, or audit transport. After a real proxy or
ingress exists, use the live review summary gate below and keep deployment
commands, logs, screenshots, URLs, users, realms, and secrets out of committed
docs.

## Live Review Summary Gate

After a real proxy or ingress exists, keep the deployment-specific commands and
logs in ignored local notes and retain only a raw-free review summary. Validate
that summary with:

```bash
python3 scripts/audit_owner_raw_live_front_door_review.py --template-json <raw-free-front-door-review.json>
python3 scripts/audit_owner_raw_live_front_door_review.py --review-json <raw-free-front-door-review.json>
```

For the stricter shared Trino hardening path, require the Trino profile:

```bash
python3 scripts/audit_owner_raw_live_front_door_review.py --template-json <raw-free-front-door-review.json> --require-trino-shared-hardening
python3 scripts/audit_owner_raw_live_front_door_review.py --review-json <raw-free-front-door-review.json> --require-trino-shared-hardening
```

The template is raw-free and fail-closed: it uses `review_status=unreviewed`
and false proof fields, so it must not pass until the operator edits only the
supported boolean and enum review fields after the live proxy checks complete.
The review summary must not contain real URLs, paths, users, header names or
values, query ids, case ids, auth subjects, tokens, cookies, tickets,
assertions, screenshots, SQL, proxy logs, or source text. Use this helper to
write or audit retained evidence, not to perform authentication or make network
requests. To print the supported field labels without reading the summary, run:

```bash
python3 scripts/audit_owner_raw_live_front_door_review.py --review-json <raw-free-front-door-review.json> --list-required-fields
```

## Live Front Door Validation Gate

Run this gate only after the target proxy or ingress exists. Passing the local
synthetic smoke is a prerequisite, not a substitute.

Required live checks:

- Direct client access to the upstream Query Doctor web process is blocked.
- Unauthenticated requests are denied by the front door before they reach Query
  Doctor.
- A request with a client-supplied viewer header is stripped or replaced by the
  front door after authentication.
- The front door forwards exactly one normalized simple owner value, not an
  email, UPN, distinguished name, opaque subject, group, role, display name,
  service principal, host principal, token, cookie, ticket, or assertion.
- A matching authenticated viewer can open only the isolated source page for
  that viewer's own selected case.
- A different authenticated human viewer is denied the same selected case.
- Missing, invalid, and duplicate upstream viewer header cases fail closed. If
  the deployed proxy cannot create a duplicate upstream header in normal
  operation, verify that it cannot forward duplicates and keep Query Doctor's
  duplicate-header deny test in the application suite.
- `owner_raw_source_enabled=false` hides the source link and blocks the source
  page without changing collection owner filters.
- Owner-raw source access audit lines contain only safe reason-coded fields and
  do not contain SQL, query ids, case ids, users, paths, header values, secrets,
  tokens, cookies, tickets, assertions, model names, runtime internals, or raw
  artifact filenames.

Retain only raw-free validation evidence: pass/fail checklist rows, command
names, HTTP status classes, reason codes, and boolean/count summaries. Do not
commit real hostnames, identity-provider URLs, realm names, usernames, query
ids, case ids, local paths, screenshots with source SQL, proxy logs containing
headers, or auth material. When retaining machine-readable evidence, use the
live review summary gate above and keep private command lines and raw proxy
outputs out of the retained JSON.

## Operator Response

If the auth proxy, identity mapping, audit pipeline, or ownership mapping is in
doubt:

1. Disable the original source page with `--disable-owner-raw-source` or
   `owner_raw_source_enabled=false`.
2. Keep `source_visibility=owner_raw` only if the collection owner filter is
   still needed and safe for the local process.
3. Switch to `source_visibility=safe` if owner-scoped collection is also in
   doubt.
4. Review reason-coded audit lines without using them as raw evidence.

Do not use a silent downgrade for a broken D3 auth front door. A missing or
invalid viewer identity must fail closed.

## Non-Goals

- No admin role bypasses owner raw access.
- No group, role, or delegated-subject expansion is implied by this contract.
- No native OIDC, SAML, SPNEGO, Kerberos, LDAP, password, MFA, session, logout,
  or token lifecycle inside Query Doctor for owner-raw access.
- No raw source appears in Details, Recent tables, trusted reports, report
  downloads, optimizer prompts, handoff exports, or audit logs.
- No public demo should run with owner-raw collection or raw source reveal.
- No deployment-specific hostnames, cluster ids, keytab paths, or identity
  provider secrets belong in committed docs.

## Related Contracts

- [safety-contract.md](safety-contract.md): canonical trust and redaction
  contract.
- [configuration.md](configuration.md): `source_visibility`,
  `viewer_identity_header`, and `owner_raw_source_enabled` reference.
- [../README.md](../README.md): public safety summary and support boundary.
