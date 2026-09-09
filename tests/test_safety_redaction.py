from __future__ import annotations

from query_doctor.safety import redaction


def test_redact_host_identifiers_redacts_ipv6_without_touching_timestamps_or_embedded_text():
    text = (
        "RPC peer 2001:db8::2\n"
        "Start Time: 2026-04-30 11:21:41.656793000\n"
        "Trace token x2001:db8::3y\n"
    )

    redacted = redaction.redact_host_identifiers(text)

    assert "RPC peer host_01" in redacted
    assert "2001:db8::2" not in redacted
    assert "11:21:41.656793000" in redacted
    assert "x2001:db8::3y" in redacted


def test_redact_host_identifiers_hides_adversarial_host_corpus():
    bare_host = "prod-" + "worker-01"
    cm_host = ".".join(("cm-control", "prod", "example", "invalid"))
    bare_fqdn = ("acme" + "bank") + "." + "com"
    internal_fqdn = ".".join(("prod-cluster-42", "internal", "corp"))
    label_host = "edge-" + "daemon-02"
    url_host = ".".join(("cm-edge", "prod", "example", "invalid"))
    ipv6_host = "2001:db8::5"
    text = (
        f"failed on {bare_host}, {cm_host}, {bare_fqdn}, and {internal_fqdn} "
        f"with Host: {label_host}, backend=010.020.030.040, "
        f"url=https://{url_host}:7183/api, peer [{ipv6_host}]"
    )

    redacted = redaction.redact_host_identifiers(text)

    for fragment in (
        bare_host,
        cm_host,
        bare_fqdn,
        internal_fqdn,
        label_host,
        "010.020.030.040",
        url_host,
        ipv6_host,
    ):
        assert fragment not in redacted
    assert redacted.count("host_") >= 6


def test_redact_host_identifiers_preserves_safe_product_identifiers():
    text = (
        "SELECT * FROM db.table JOIN example_dim.users "
        "pool=root.analytics source_version=synthetic-trino-connector-metric-present-stats-v1"
    )

    redacted = redaction.redact_host_identifiers(text)

    assert "db.table" in redacted
    assert "example_dim.users" in redacted
    assert "root.analytics" in redacted
    assert "synthetic-trino-connector-metric-present-stats-v1" in redacted
    assert "host_" not in redacted


def test_redact_host_identifiers_preserves_safe_filenames():
    text = "ca-cert=/user/<user>/ssl/impala-ca.pem output=analysis_facts.md"

    redacted = redaction.redact_host_identifiers(text)

    assert "/user/<user>/ssl/impala-ca.pem" in redacted
    assert "analysis_facts.md" in redacted
    assert "host_" not in redacted


def test_redact_host_identifiers_redacts_bare_host_with_port():
    text = "coordinator: coordinator01.example.com:21000"

    redacted = redaction.redact_host_identifiers(text)

    assert "coordinator01.example.com" not in redacted
    assert "coordinator: host_01:21000" in redacted


def test_redact_host_identifiers_handles_codeql_adversarial_bare_tokens():
    hyphen_run = "-" * 6000
    short_role_host = "dn0" + "cm0" * 1000
    version_like_token = "query-doctor-0.5.0"
    text = f"{hyphen_run} {short_role_host} {version_like_token}"

    redacted = redaction.redact_host_identifiers(text)

    assert hyphen_run in redacted
    assert short_role_host not in redacted
    assert "host_01" in redacted
    assert version_like_token in redacted


def test_sanitize_text_for_log_redacts_adversarial_secret_assignment_names():
    auth_token = "abcdefgh" + "ijklmnop"
    text = (
        "credential=credential-value credentials='credentials-value' "
        "passphrase: passphrase-value private_key=private-key-value "
        "private key: private-key-text auth=auth-value "
        f"Authorization: Bearer {auth_token}"
    )

    redacted = redaction.sanitize_text_for_log(text)

    for fragment in (
        "credential-value",
        "credentials-value",
        "passphrase-value",
        "private-key-value",
        "private-key-text",
        "auth-value",
        auth_token,
    ):
        assert fragment not in redacted
    assert redacted.count("<redacted>") >= 7


def test_redact_ipv6_candidates_checks_long_colon_run_once(monkeypatch):
    adversarial_candidate = ":" + "0:" * 5000
    text = f"host header {adversarial_candidate} end"
    calls: list[str] = []

    def fake_ip_address(value: str) -> object:
        calls.append(value)
        raise ValueError

    monkeypatch.setattr(redaction.ipaddress, "ip_address", fake_ip_address)

    assert redaction.redact_ipv6_candidates(text, redaction.HostAliasRedactor()) == text
    assert calls == [adversarial_candidate]


def test_sanitize_text_for_log_handles_bounded_pathological_mixed_input():
    secret = "pathological-secret-value"
    malformed_ipv6 = ":" + "0:" * 4000
    host = "impalad-01.example.invalid"
    safe_table = "example_db.example_table"
    text = f"credential={secret} Host: {host} malformed_ipv6={malformed_ipv6} " + " ".join(
        f"FROM {safe_table}" for _ in range(200)
    )

    redacted = redaction.sanitize_text_for_log(text)

    assert secret not in redacted
    assert host not in redacted
    assert "credential=<redacted>" in redacted
    assert "Host: host_01" in redacted
    assert malformed_ipv6 in redacted
    assert safe_table in redacted


def test_sanitize_identifier_for_log_keeps_query_id_the_host_pass_reads_as_host_port():
    query_id = "db4d1e2f3a4b5c6d:7a8b9c0d00000000"

    assert redaction.sanitize_text_for_log(query_id) == "host_01:7a8b9c0d00000000"
    assert redaction.sanitize_identifier_for_log(query_id) == query_id


def test_sanitize_identifier_for_log_still_redacts_secrets_and_credentials():
    query_id = "db4d1e2f3a4b5c6d:7a8b9c0d00000000"
    text = f"query_id={query_id} password=" + "hunter2"

    safe = redaction.sanitize_identifier_for_log(text, secrets=("hunter2",))

    assert "hunter2" not in safe
    assert query_id in safe


def test_sanitize_text_for_log_cache_does_not_change_the_answer():
    text = "failed on prod-" + "worker-01 with Host: edge-" + "daemon-02"

    redaction.sanitized_text_for_log.cache_clear()
    cold = redaction.sanitize_text_for_log(text)
    warm = redaction.sanitize_text_for_log(text)
    uncached = redaction.redact_host_identifiers(redaction.sanitize_identifier_for_log(text))

    assert cold == warm == uncached
    assert redaction.sanitized_text_for_log.cache_info().hits >= 1


def test_sanitize_text_for_log_never_caches_a_call_carrying_secrets():
    secret = "hunter" + "2"
    text = f"password={secret} on prod-" + "worker-01"

    redaction.sanitized_text_for_log.cache_clear()
    safe = redaction.sanitize_text_for_log(text, secrets=(secret,))

    assert secret not in safe
    # The result depends on the secrets as well as the text, so it must not have
    # been served from or written to a cache keyed by text alone.
    assert redaction.sanitized_text_for_log.cache_info().misses == 0
    assert redaction.sanitized_text_for_log.cache_info().hits == 0


def test_browser_display_keeps_the_kerberos_primary_and_drops_the_realm():
    from query_doctor.safety.browser_display import (
        redact_infrastructure_identifiers_for_display,
    )

    # A Kerberos principal has the shape of an email address, so the email rule
    # used to turn every one of them into "<email>" and the User column stopped
    # answering who ran the query.
    assert redact_infrastructure_identifiers_for_display("analyst@EXAMPLE.REALM") == "analyst"
    assert (
        redact_infrastructure_identifiers_for_display("job-loader@EXAMPLE.REALM")
        == "job-loader"
    )
    # A service principal drops its instance host along with the realm.
    assert (
        redact_infrastructure_identifiers_for_display("impala/host1.example.internal@EXAMPLE.REALM")
        == "impala"
    )
    # A plain user label is unchanged, and a real email is still redacted.
    assert redact_infrastructure_identifiers_for_display("batch-loader") == "batch-loader"
    assert redact_infrastructure_identifiers_for_display("someone@example.com") == "<email>"
    assert redact_infrastructure_identifiers_for_display("First.Last@corp.example.org") == "<email>"
    # The explicit user field stays stricter than the bare label.
    assert (
        redact_infrastructure_identifiers_for_display("User: analyst@EXAMPLE.REALM")
        == "User: <user>"
    )
