# Kubernetes Auth Front Door

Last reviewed: 2026-11-03

This page defines the production-shaped Kubernetes auth-front-door contract for
configured Query Doctor web deployments.

Query Doctor does not implement native authentication in Kubernetes. OIDC, SAML,
SPNEGO/Kerberos, LDAP, passwords, MFA, sessions, token lifecycle, groups, roles,
RBAC, TLS termination, inbound auth-header stripping, and identity mapping stay
at a trusted ingress or auth proxy. Query Doctor remains the upstream diagnostic
application behind that front door.

This page is separate from the dev-only
[Dev Keycloak SSO Smoke](dev-sso-keycloak.md). The dev smoke is useful for
local wiring tests; this page is the public-safe operator contract for real
Kubernetes deployments.

## Supported Shape

For configured or shared Kubernetes access:

- The public Ingress routes to the trusted auth proxy Service, not directly to
  the Query Doctor web Service.
- The auth proxy routes upstream traffic to the configured Query Doctor Service
  inside the namespace.
- NetworkPolicy restricts auth-proxy ingress to the trusted ingress-controller
  pods and restricts Query Doctor web ingress to the auth-proxy pods.
- The OIDC client redirect URI is
  `https://<query-doctor-host>/oauth2/callback`.
- OAuth client secrets and cookie secrets are held in Kubernetes Secret-backed
  environment entries or an external secret integration, not inline container
  args or literal env values.
- The auth proxy must not forward access tokens, ID tokens, Basic auth, bearer
  tokens, client secrets, AD/LDAP credentials, or raw auth headers to Query
  Doctor.
- Query Doctor stays in configured private mode with SQL execution disabled and
  raw browser output disabled.
- Keep `source_visibility=safe` and `owner_raw_source_enabled=false` unless the
  separate [owner-raw D3 deployment contract](owner-raw-d3-deployment.md) has
  passed.

The upstream Helm chart intentionally leaves configured chart-owned ingress
disabled by default. Platform-owned SSO/auth front doors, such as
oauth2-proxy/Keycloak, should route to the chart Service.

## Post-Keycloak Acceptance

After the Keycloak realm/client is created or updated:

1. Register `https://<query-doctor-host>/oauth2/callback` as an allowed redirect
   URI for the Query Doctor auth-proxy client.
2. Store the client secret and cookie secret in a Kubernetes Secret or external
   secret source consumed by the auth proxy.
3. Configure the auth proxy with the issuer URL, client ID, redirect URL, PKCE
   code challenge method, secure cookies, token/basic-auth-forwarding disabled
   flags, and upstream Query Doctor Service.
4. Keep cookie-backed oauth2-proxy sessions compact: do not store OAuth tokens,
   do not load profile-URL claims, and point the groups claim at an intentionally
   absent claim unless Query Doctor explicitly needs group authorization.
5. Configure NetworkPolicy so only the trusted ingress controller can reach the
   auth proxy and only the auth proxy can reach the Query Doctor web pods.
6. Confirm an unauthenticated browser request to `https://<query-doctor-host>/`
   redirects to the identity provider.
7. Confirm a successful login returns to Query Doctor through
   `/oauth2/callback`.
8. Check Query Doctor readiness through the internal Service or a trusted
   operator path:

   ```bash
   curl -fsS http://127.0.0.1:<port>/deployment/readiness.json
   ```

9. Run a bounded Recent smoke from the UI only after the front door is accepted.
   Keep metadata collection, LLM reports, optimizer jobs, and owner-raw source
   reveal off unless those separate gates are intentionally in scope.

Do not commit retained resource dumps, readiness payloads, hostnames, client
IDs, Secret names, local paths, or smoke outputs.

## Live External Smoke

After the public Ingress and auth proxy are reachable, run the unauthenticated
front-door smoke:

```bash
python3 scripts/kubernetes_auth_front_door_smoke.py --compact \
  --base-url https://<query-doctor-host>/ \
  --expected-issuer-url https://<oidc-host>/realms/<realm> \
  --expected-client-id <oidc-client-id>
```

The smoke follows only the unauthenticated redirect chain. It fails if the
external URL returns Query Doctor directly, if the OIDC authorization redirect
is missing, if the authorization request points `redirect_uri` away from
`/oauth2/callback` on the external Query Doctor origin, if PKCE is not `S256`,
or if token-like auth material appears in response headers or cookie names. It
prints only raw-free booleans, status classes, redirect counts, cookie counts,
and issue codes.

This smoke does not perform a real login and does not validate Keycloak policy,
AD/LDAP federation, group membership, MFA, session lifetime, certificate
operations, or owner-raw D3 launch readiness. Keep the successful-login browser
check and the static Kubernetes resource audit as separate gates.

For release handoff over an installed configured release, use the combined
wrapper after setting the same external auth values through environment
variables:

```bash
scripts/kubernetes-configured-release-gate.sh
```

It runs the configured metadata smoke, this external auth redirect smoke, and
the raw-free Kubernetes auth-front-door resource audit.

## Raw-Free Audit

Use `scripts/audit_kubernetes_auth_front_door.py` for a raw-free static check of
Ingress, Service, Deployment, and optional deployment-readiness wiring.

Collect only non-Secret resources:

```bash
kubectl get -n <namespace> ingress,deploy,svc,networkpolicy -o json \
  > <ignored-auth-front-door-resources.json>
```

Optionally collect raw-free Query Doctor readiness through a local
port-forward:

```bash
curl -fsS http://127.0.0.1:<port>/deployment/readiness.json \
  > <ignored-deployment-readiness.json>
```

Run the audit:

```bash
python3 scripts/audit_kubernetes_auth_front_door.py \
  --resources-json <ignored-auth-front-door-resources.json> \
  --namespace <namespace> \
  --query-doctor-service <query-doctor-service> \
  --auth-proxy-service <auth-proxy-service> \
  --expected-host <query-doctor-host> \
  --expected-issuer-url <oidc-issuer-url> \
  --expected-client-id <oidc-client-id> \
  --expected-code-challenge-method S256 \
  --require-compact-session-cookie \
  --expected-groups-claim <intentionally-absent-groups-claim> \
  --deployment-readiness-json <ignored-deployment-readiness.json>
```

For strict front-door isolation, include the expected ingress-controller
selector labels and require NetworkPolicy:

```bash
python3 scripts/audit_kubernetes_auth_front_door.py \
  --resources-json <ignored-auth-front-door-resources.json> \
  --namespace <namespace> \
  --query-doctor-service <query-doctor-service> \
  --auth-proxy-service <auth-proxy-service> \
  --expected-host <query-doctor-host> \
  --expected-issuer-url <oidc-issuer-url> \
  --expected-client-id <oidc-client-id> \
  --expected-code-challenge-method S256 \
  --require-compact-session-cookie \
  --expected-groups-claim <intentionally-absent-groups-claim> \
  --deployment-readiness-json <ignored-deployment-readiness.json> \
  --require-network-policy \
  --ingress-controller-namespace-label <namespace-label-key>=<namespace-label-value> \
  --ingress-controller-pod-label <pod-label-key>=<pod-label-value>
```

Pass `--ingress-controller-pod-label` multiple times when the ingress
controller selector needs more than one label.

The audit checks redirect, expected issuer, expected client ID, expected PKCE
code challenge method, compact cookie-session hardening, upstream, Secret
reference, token-forwarding, Basic auth forwarding, and NetworkPolicy
front-door isolation. It prints only raw-free status and issue codes. It
intentionally does not print hostnames, issuer URLs, redirect URLs, client IDs,
Secret names, Secret values, endpoint URLs, paths, query IDs, users, label
values, or case identifiers.

Expected output shapes:

```text
kubernetes auth front-door audit: ok
kubernetes auth front-door audit: warning warnings=<code>[,<code>]
kubernetes auth front-door audit: failed issues=<code>[,<code>]
```

`auth_proxy_trusted_proxy_ip_missing` is a warning because trusted-proxy IP
requirements depend on the ingress controller and network topology. Use
`--fail-on-warning` when the platform review requires a stricter fail-closed
gate.

`network_policy_missing` and the more specific NetworkPolicy issue codes become
blocking when `--require-network-policy` is passed. Without that flag they are
warnings, so early wiring checks can still distinguish front-door routing
mistakes from hardening work that is not complete yet.

`--require-compact-session-cookie` is intended for LDAP/AD-backed clients where
ID tokens or userinfo payloads can contain large group memberships. Query Doctor
does not currently use group authorization at the auth proxy, so the safer
default is to keep groups out of the oauth2-proxy cookie session instead of
increasing ingress header buffers.

## Boundary

This audit does not validate Keycloak policy, AD/LDAP integration, group
membership, MFA, certificate trust, ingress-controller security, runtime
NetworkPolicy enforcement, Secret rotation, or owner-raw D3 launch readiness.
It checks that Kubernetes resources match the Query Doctor front-door contract,
that NetworkPolicy selectors describe the two required hops, and that the Query
Doctor readiness payload stays in configured private, SQL-disabled,
raw-output-disabled mode.
