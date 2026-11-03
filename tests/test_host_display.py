"""Host display opt-in must not alter identity or other redaction boundaries."""

import pytest
from pathlib import Path

from query_doctor.analyzer.backend_tail import parse_backend_host_facts
from query_doctor.analyzer.backend_tail_analysis import build_backend_tail_analysis, host_to_json
from query_doctor.analyzer.facts_renderer import render_backend_tail_evidence
from query_doctor.analyzer.models import BackendHostFact
from query_doctor.recent.history_store import safe_source_key
from query_doctor.recent.profile_budget import normalize_profile_lease_owner
from query_doctor.recent.profile_budget import safe_analysis_cache_payload
from query_doctor.safety.browser_display import (
    redact_browser_display_text,
    shared_browser_display_redactions,
)
from query_doctor.safety.host_display import HOST_DISPLAY_ENV, host_names_visible
from query_doctor.web.trusted_artifacts import sanitize_trusted_report_text_for_display


@pytest.mark.parametrize("setting", ["", "true", "NAMES", "aliases", "unknown"])
def test_host_display_requires_exact_opt_in(setting):
    assert not host_names_visible({HOST_DISPLAY_ENV: setting})
    assert host_names_visible({HOST_DISPLAY_ENV: "names"})


def test_names_survive_analysis_and_trusted_display_without_other_raw_content(monkeypatch):
    monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
    host = "impala-executor-17.example.invalid"
    fact = BackendHostFact(
        host=host,
        evidence_lines=[f"Host: {host}", "token=synthetic-secret", "User: private-user"],
    )
    payload = host_to_json(fact)
    assert payload["host"] == host
    assert host in payload["evidence_lines"][0]
    assert "synthetic-secret" not in str(payload)
    assert "private-user" not in str(payload)
    text = sanitize_trusted_report_text_for_display(
        f"Host: {host}\ntoken=synthetic-secret\nCookie: private-cookie\n"
        "SELECT secret_column FROM private_table\n/tmp/private-artifact\n"
        "User: private-user\nAuthorization: Bearer abcdefghijklmnop"
    )
    assert host in text
    for secret in (
        "synthetic-secret",
        "private-cookie",
        "secret_column",
        "private_table",
        "/tmp/private-artifact",
        "private-user",
        "abcdefghijklmnop",
    ):
        assert secret not in text


def test_default_display_masks_names_and_request_cache_respects_policy(monkeypatch):
    host = "impala-executor-17.example.invalid"
    monkeypatch.delenv(HOST_DISPLAY_ENV, raising=False)
    assert host_to_json(BackendHostFact(host=host))["host"] == "host_01"
    with shared_browser_display_redactions():
        assert host not in redact_browser_display_text(host, redact_infrastructure=True)
        monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
        assert host in redact_browser_display_text(host, redact_infrastructure=True)
        monkeypatch.delenv(HOST_DISPLAY_ENV)
        assert host not in redact_browser_display_text(host, redact_infrastructure=True)


def test_host_display_does_not_change_storage_or_lease_identity(monkeypatch):
    owner = "query-doctor-worker-123-aaaaa"
    source = "impala-executor-17.example.invalid"
    monkeypatch.delenv(HOST_DISPLAY_ENV, raising=False)
    identities = normalize_profile_lease_owner(owner), safe_source_key(source)
    monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
    assert identities == (normalize_profile_lease_owner(owner), safe_source_key(source))
    assert normalize_profile_lease_owner(identities[0]) == identities[0]


def test_explicit_display_environment_and_unsafe_host_value(monkeypatch):
    monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
    host = "impala-executor-17.example.invalid"
    assert host not in redact_browser_display_text(host, env={}, redact_infrastructure=True)
    assert "<script>" not in host_to_json(BackendHostFact(host="<script>"))["host"]


def test_profile_host_names_reach_rendered_evidence_without_changing_diagnosis(monkeypatch):
    profile = (Path(__file__).parent / "fixtures/backend_tail_case/profile_digest.md").read_text()
    facts = parse_backend_host_facts(profile)
    monkeypatch.delenv(HOST_DISPLAY_ENV, raising=False)
    aliases = build_backend_tail_analysis(facts)
    monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
    names = build_backend_tail_analysis(facts)
    markdown = "\n".join(render_backend_tail_evidence({"backend_tail": names}))
    assert "worker-" in sanitize_trusted_report_text_for_display(markdown)
    assert names["rows_parsed"] == aliases["rows_parsed"]
    for key in (
        "tail_candidate_count",
        "execution_tail_candidate_count",
        "data_skew",
        "execution_skew",
        "write_path_anomaly",
    ):
        assert names[key] == aliases[key]


def test_retained_diagnostic_values_keep_names_without_unmasking_keys_or_credentials(monkeypatch):
    host = "impala-executor-17.example.invalid"
    payload = {
        "failing_node": host,
        "error_message": f"RPC failed on {host}; token=private-token",
        "query": "SELECT hidden FROM hidden_table",
        "endpoint": "https://private.example.invalid",
    }
    monkeypatch.delenv(HOST_DISPLAY_ENV, raising=False)
    assert host not in str(safe_analysis_cache_payload(payload))
    monkeypatch.setenv(HOST_DISPLAY_ENV, "names")
    retained = safe_analysis_cache_payload(payload)
    assert retained["failing_node"] == host
    assert host in retained["error_message"]
    assert "private-token" not in str(retained)
    assert "query" not in retained and "endpoint" not in retained
    assert safe_analysis_cache_payload(retained) == retained
    monkeypatch.delenv(HOST_DISPLAY_ENV)
    assert host not in str(safe_analysis_cache_payload(retained))
