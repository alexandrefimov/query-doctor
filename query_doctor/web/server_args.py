"""Command-line argument parsing for the local web server."""

from __future__ import annotations

import argparse

from query_doctor.cli import collect_cm_profiles as cm_collector
from query_doctor.impala.hms_metadata import METADATA_SOURCES
from query_doctor.web.config import positive_int
from query_doctor.web.models import (
    DEFAULT_CORPUS_DIR,
    DEFAULT_HOST,
    DEFAULT_METADATA_TIMEOUT_SEC,
    DEFAULT_MODEL,
    DEFAULT_OPTIMIZER_MODEL,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT_SEC,
)
from query_doctor.report.llm_client import DEFAULT_LLM_PROVIDER, LLM_PROVIDER_CHOICES


def build_parser(
    *,
    description: str | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description
        or "Run the localhost-only Query Doctor web UI for recent scans, explicit CM query ids, and pasted SQL."
    )
    parser.add_argument(
        "--config",
        help=(
            "Local ignored Query Doctor JSON config. If omitted, "
            f"{cm_collector.DEFAULT_LOCAL_CONFIG_NAME} is loaded when present, falling back to "
            f"legacy {cm_collector.LEGACY_LOCAL_CONFIG_NAME}. Credentials still come from environment."
        ),
    )
    parser.add_argument("--host", help=f"Bind host. Default comes from config or {DEFAULT_HOST}.")
    parser.add_argument(
        "--port", type=positive_int, help=f"Bind port. Default comes from config or {DEFAULT_PORT}."
    )
    parser.add_argument(
        "--allow-nonlocal-web-bind",
        "--allow-nonlocal-demo-bind",
        dest="allow_nonlocal_web_bind",
        action="store_true",
        help=(
            "Allow binding outside localhost. Unsafe for this local web UI; prints a warning. "
            "--allow-nonlocal-demo-bind is accepted as a legacy alias."
        ),
    )
    parser.add_argument(
        "--viewer-identity-header",
        help=(
            "HTTP header name to trust as the authenticated viewer user. "
            "Use only behind an auth proxy or ingress that strips inbound copies of this header."
        ),
    )
    parser.add_argument(
        "--disable-owner-raw-source",
        action="store_true",
        help=(
            "Disable the isolated owner-only raw source page even when "
            "source_visibility=owner_raw is configured."
        ),
    )
    parser.add_argument(
        "--max-profile-bytes",
        type=positive_int,
        help=f"Override collector max profile bytes. Default comes from config or {cm_collector.DEFAULT_MAX_PROFILE_BYTES}.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Ollama model for reports. Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--report-llm-provider",
        choices=LLM_PROVIDER_CHOICES,
        help=f"LLM provider for reports. Default comes from config or {DEFAULT_LLM_PROVIDER}.",
    )
    parser.add_argument(
        "--report-llm-base-url",
        help="Base URL for the report LLM provider.",
    )
    parser.add_argument(
        "--report-llm-chat-path",
        help="OpenAI-compatible report chat path override.",
    )
    parser.add_argument(
        "--optimizer-model",
        help=(
            "Ollama model for Query LLM optimizer. "
            f"Default: {DEFAULT_OPTIMIZER_MODEL}. Override with config, QD_OPTIMIZER_MODEL, or this flag."
        ),
    )
    parser.add_argument(
        "--optimizer-llm-provider",
        choices=LLM_PROVIDER_CHOICES,
        help=(
            "LLM provider for Query LLM optimizer. "
            f"Default comes from config or {DEFAULT_LLM_PROVIDER}."
        ),
    )
    parser.add_argument(
        "--optimizer-llm-base-url",
        help="Base URL for the optimizer LLM provider.",
    )
    parser.add_argument(
        "--optimizer-llm-chat-path",
        help="OpenAI-compatible optimizer chat path override.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run report and optimizer actions in deterministic Python-only mode.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=positive_int,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"Per-step subprocess timeout. Default: {DEFAULT_TIMEOUT_SEC}.",
    )
    parser.add_argument(
        "--corpus-dir",
        help=(
            "Directory for web-collected or web-staged Query ID cases. "
            f"Default: {DEFAULT_CORPUS_DIR}."
        ),
    )
    parser.add_argument(
        "--recent-batch-root",
        help=(
            "Directory that will contain generated query-doctor-web-batch-* Recent scan outputs. "
            "Defaults to /tmp."
        ),
    )
    parser.add_argument(
        "--batch-summary",
        help=(
            "Optional local batch_summary.json to render read-only at / and /batch. "
            "The web UI never chooses this path from request parameters."
        ),
    )
    parser.add_argument(
        "--public-demo",
        action="store_true",
        help=(
            "Generate and run the read-only public synthetic demo. "
            "No config, credentials, --batch-summary, or --no-llm flag is required."
        ),
    )
    parser.add_argument(
        "--metadata-source",
        choices=METADATA_SOURCES,
        help=(
            "Where web metadata collection reads table metadata: impala or hms-postgres. "
            "Default comes from config or impala."
        ),
    )
    parser.add_argument(
        "--metadata-hms-postgres-dsn-env",
        help="Environment variable holding the metastore database DSN for hms-postgres.",
    )
    parser.add_argument(
        "--metadata-coordinator",
        help="Impala coordinator HOST:PORT for web metadata collection, on its HiveServer2 port.",
    )
    parser.add_argument(
        "--metadata-auth",
        help="Metadata auth mode. Default comes from config or kerberos.",
    )
    parser.add_argument(
        "--metadata-protocol",
        choices=("hs2", "hs2-http"),
        help="HiveServer2 transport for web metadata collection. Default comes from config or hs2.",
    )
    parser.add_argument(
        "--metadata-kerberos-service-name",
        help="Kerberos service principal short name for metadata collection, e.g. hive or impala.",
    )
    parser.add_argument(
        "--metadata-kerberos-host-fqdn",
        help="Expected Kerberos host FQDN for load-balanced metadata coordinators.",
    )
    parser.add_argument(
        "--metadata-ssl",
        action="store_true",
        help="Use TLS for the metadata coordinator connection.",
    )
    parser.add_argument(
        "--metadata-ca-cert", help="CA certificate path for --metadata-ssl metadata connections."
    )
    parser.add_argument(
        "--metadata-timeout-sec",
        type=positive_int,
        help=f"Timeout per metadata statement. Default comes from config or {DEFAULT_METADATA_TIMEOUT_SEC}.",
    )
    parser.add_argument(
        "--metadata-max-tables", type=positive_int, help="Maximum referenced tables to collect."
    )
    parser.add_argument(
        "--metadata-max-output-bytes", type=positive_int, help="Maximum metadata output bytes."
    )
    parser.add_argument(
        "--metadata-redact",
        action="store_true",
        help="Pass --metadata-redact to web metadata collection.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)
