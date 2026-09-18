"""Subprocess helpers for the local web UI."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from query_doctor.config.contract import merge_kerberos_cache_env
from query_doctor.impala import hms_metadata, hs2_runner
from query_doctor.impala.hms_metadata import METADATA_SOURCE_HMS_POSTGRES

from query_doctor.web.config import metadata_configured
from query_doctor.web.models import WebError, WebSettings


Runner = Callable[..., subprocess.CompletedProcess[str]]
CancelCheck = Callable[[], bool]
WEB_CANCELLED_RETURN_CODE = -15
WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES = 1_048_576
WEB_SUBPROCESS_READ_CHUNK_BYTES = 8192
WEB_SUBPROCESS_STOP_GRACE_SEC = 2.0
SUBPROCESS_OUTPUT_HIDDEN_MESSAGE = (
    "Captured subprocess output is not shown because it may contain raw "
    "profile text, SQL, JSON, or credentials."
)


@dataclass(frozen=True)
class SafeSubprocessFailureHint:
    pattern: str
    message: str
    reason_code: str
    next_step: str
    title: str = "Impala workflow failed"


SAFE_SUBPROCESS_FAILURE_HINTS: tuple[SafeSubprocessFailureHint, ...] = (
    SafeSubprocessFailureHint(
        "krb5ccname is required before metadata collection can use kerberos",
        "Recognized safe failure reason: metadata Kerberos cache is not configured. "
        "Configure a valid Kerberos ticket cache or disable metadata collection.",
        "impala.metadata_kerberos_cache_missing",
        "Configure a valid Kerberos ticket cache, renew credentials, or disable metadata collection.",
        "Metadata Kerberos cache is not configured",
    ),
    SafeSubprocessFailureHint(
        "kerberos ticket cache is missing or expired",
        "Recognized safe failure reason: metadata Kerberos ticket is missing or expired. "
        "Renew the Kerberos ticket or disable metadata collection.",
        "impala.metadata_kerberos_ticket_unavailable",
        "Renew the Kerberos ticket for the web server environment or disable metadata collection.",
        "Metadata Kerberos ticket is unavailable",
    ),
    SafeSubprocessFailureHint(
        "kerberos ticket preflight could not run because klist is not available",
        "Recognized safe failure reason: Kerberos klist is not available. "
        "Install Kerberos client tools or disable metadata collection.",
        "impala.metadata_klist_unavailable",
        "Install Kerberos client tools for the web server environment or disable metadata collection.",
        "Metadata Kerberos preflight is unavailable",
    ),
    SafeSubprocessFailureHint(
        "kerberos ticket preflight timed out before metadata collection started",
        "Recognized safe failure reason: Kerberos ticket preflight timed out. "
        "Check local Kerberos responsiveness or disable metadata collection.",
        "impala.metadata_klist_timeout",
        "Check local Kerberos responsiveness, then retry or disable metadata collection.",
        "Metadata Kerberos preflight timed out",
    ),
    SafeSubprocessFailureHint(
        "kerberos ticket preflight could not inspect the ticket cache",
        "Recognized safe failure reason: Kerberos ticket cache could not be inspected. "
        "Check local Kerberos setup or disable metadata collection.",
        "impala.metadata_kerberos_cache_unreadable",
        "Check the Kerberos ticket cache visible to the web server or disable metadata collection.",
        "Metadata Kerberos cache could not be inspected",
    ),
    SafeSubprocessFailureHint(
        "impala metadata driver is not available",
        "Recognized safe failure reason: the impyla metadata driver is not installed. "
        "Install query-doctor[impala] or disable metadata collection.",
        "impala.metadata_driver_unavailable",
        "Install query-doctor[impala] in the web server environment or disable metadata collection.",
        "Metadata driver is unavailable",
    ),
    SafeSubprocessFailureHint(
        "metadata collection is not configured",
        "Recognized safe failure reason: metadata collection is not configured. "
        "Add metadata coordinator settings or disable metadata collection.",
        "impala.metadata_not_configured",
        "Add metadata coordinator settings or disable metadata collection.",
        "Metadata collection is not configured",
    ),
    SafeSubprocessFailureHint(
        "impala query discovery requires --impala-profile-host or local config impala_profile_hosts",
        "Recognized safe failure reason: direct Impala discovery has no configured impalad host. "
        "Select or fix a direct-Impala cluster with impala_profile_hosts.",
        "impala.direct_profile_host_missing",
        "Select or fix a direct-Impala source with impala_profile_hosts.",
        "Direct Impala profile host is not configured",
    ),
    SafeSubprocessFailureHint(
        "impala profile was not found on the configured impalad endpoints",
        "Recognized safe failure reason: direct Impala profile was not found on the configured "
        "impalad endpoints. The query may be outside daemon profile retention or the selected "
        "cluster may not include the coordinator that served it.",
        "impala.direct_profile_not_found",
        "Check daemon profile retention and whether the selected source includes the coordinator.",
        "Direct Impala profile was not found",
    ),
    SafeSubprocessFailureHint(
        "profile endpoint unavailable",
        "Recognized safe failure reason: direct Impala profile endpoint is unavailable. "
        "Check the configured direct-Impala endpoint or retry when the daemon profile page is available.",
        "impala.direct_profile_endpoint_unavailable",
        "Check the selected direct-Impala endpoint or retry when the daemon profile page is available.",
        "Direct Impala profile endpoint is unavailable",
    ),
    SafeSubprocessFailureHint(
        "impala profile endpoint request failed safely",
        "Recognized safe failure reason: direct Impala profile endpoint request failed. "
        "Check the configured direct-Impala endpoint or retry after the endpoint is reachable.",
        "impala.direct_profile_request_failed",
        "Check the selected direct-Impala endpoint and retry after it is reachable.",
        "Direct Impala profile request failed",
    ),
    SafeSubprocessFailureHint(
        "impala profile endpoint response exceeded the configured byte limit",
        "Recognized safe failure reason: direct Impala profile response exceeded the configured byte limit. "
        "Raise max_profile_bytes for this local session or inspect a smaller retained profile.",
        "impala.direct_profile_too_large",
        "Raise max_profile_bytes for this local session or inspect a smaller retained profile.",
        "Direct Impala profile exceeded the configured limit",
    ),
    SafeSubprocessFailureHint(
        "single-query collection failed: http 404 from cm",
        "Recognized safe failure reason: Cloudera Manager did not find a profile for the selected "
        "Query ID. The query may be outside CM retention, or the selected CM cluster/service may not "
        "match where it ran.",
        "impala.cm_profile_not_found",
        "Check CM retention and that the selected CM cluster/service matches where the query ran.",
        "Cloudera Manager profile was not found",
    ),
    SafeSubprocessFailureHint(
        "single-query collection failed: cm request failed",
        "Recognized safe failure reason: Cloudera Manager profile lookup failed before Query Doctor "
        "could collect a profile. Check the configured CM endpoint and selected cluster/service.",
        "impala.cm_profile_lookup_failed",
        "Check the configured CM endpoint, credentials, and selected cluster/service.",
        "Cloudera Manager profile lookup failed",
    ),
    SafeSubprocessFailureHint(
        "single-query collection failed: cm returned invalid json",
        "Recognized safe failure reason: Cloudera Manager query details returned an unexpected response. "
        "Check that the selected CM endpoint supports Impala query details for this service.",
        "impala.cm_response_rejected",
        "Check that the selected CM endpoint supports Impala query details for this service.",
        "Cloudera Manager response was rejected",
    ),
    SafeSubprocessFailureHint(
        "cm auth env is not set in this execution environment",
        "Recognized safe failure reason: Cloudera Manager credentials are not available. "
        "Start the web server with CM credentials, or select a direct-Impala cluster.",
        "impala.cm_credentials_missing",
        "Start the web server with CM credentials, or select a direct-Impala source.",
        "Cloudera Manager credentials are unavailable",
    ),
    SafeSubprocessFailureHint(
        "missing --cm-url",
        "Recognized safe failure reason: Cloudera Manager URL is not configured. "
        "Select or fix a configured CM cluster.",
        "impala.cm_url_missing",
        "Select or fix a configured CM source.",
        "Cloudera Manager URL is not configured",
    ),
    SafeSubprocessFailureHint(
        "missing --cluster",
        "Recognized safe failure reason: Cloudera Manager cluster name is not configured. "
        "Select or fix a configured CM cluster.",
        "impala.cm_cluster_missing",
        "Select or fix a configured CM source.",
        "Cloudera Manager cluster is not configured",
    ),
    SafeSubprocessFailureHint(
        "missing --service",
        "Recognized safe failure reason: Cloudera Manager Impala service is not configured. "
        "Select or fix a configured CM cluster.",
        "impala.cm_service_missing",
        "Select or fix a configured CM Impala service.",
        "Cloudera Manager Impala service is not configured",
    ),
    SafeSubprocessFailureHint(
        "--config-cluster requires local config clusters[]",
        "Recognized safe failure reason: cluster selection requires clusters[] in local config. "
        "Fix local config or choose a configured cluster.",
        "web.cluster_config_missing",
        "Fix local config clusters[] or choose a configured source.",
        "Cluster selection config is missing",
    ),
    SafeSubprocessFailureHint(
        "was not found in local config clusters[]",
        "Recognized safe failure reason: selected cluster was not found in local config. "
        "Choose an existing local config cluster.",
        "web.cluster_not_found",
        "Choose an existing local config source.",
        "Selected source was not found",
    ),
    SafeSubprocessFailureHint(
        "output directory exists and is not empty",
        "Recognized safe failure reason: batch output directory is not empty. "
        "Choose a fresh Query Doctor output directory or allow overwrite.",
        "web.output_dir_not_empty",
        "Choose a fresh Query Doctor output directory or allow overwrite.",
        "Batch output directory is not empty",
    ),
    SafeSubprocessFailureHint(
        "--out must point to a dedicated batch directory",
        "Recognized safe failure reason: batch output directory failed safety validation. "
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "web.output_dir_rejected",
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "Batch output directory was rejected",
    ),
    SafeSubprocessFailureHint(
        "--out path is too shallow",
        "Recognized safe failure reason: batch output directory failed safety validation. "
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "web.output_dir_rejected",
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "Batch output directory was rejected",
    ),
    SafeSubprocessFailureHint(
        "--out directory name must start with query-doctor-",
        "Recognized safe failure reason: batch output directory failed safety validation. "
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "web.output_dir_rejected",
        "Use a dedicated query-doctor-* directory under the system temp directory.",
        "Batch output directory was rejected",
    ),
)


def run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
    runner: Runner,
    env: dict[str, str] | None = None,
    cancel_check: CancelCheck | None = None,
) -> subprocess.CompletedProcess[str]:
    if runner is subprocess.run:
        return run_bounded_subprocess(
            cmd,
            cwd=cwd,
            timeout_sec=timeout_sec,
            env=env,
            cancel_check=cancel_check,
        )
    completed = runner(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if cancel_check is not None and cancel_check():
        return subprocess.CompletedProcess(cmd, WEB_CANCELLED_RETURN_CODE, stdout="", stderr="")
    return bound_completed_process_output(completed)


def run_bounded_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
    env: dict[str, str] | None,
    cancel_check: CancelCheck | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=os.name == "posix",
    )
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    output_threads = start_bounded_output_readers(process, stdout_buffer, stderr_buffer)
    deadline = time.monotonic() + timeout_sec
    cancelled = False
    while True:
        if cancel_check is not None and cancel_check():
            cancelled = True
            terminate_process_tree(process)
            break
        if process.poll() is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate_process_tree(process, force=True)
            wait_after_stop(process)
            join_output_threads(output_threads)
            raise subprocess.TimeoutExpired(cmd, timeout_sec)
        time.sleep(min(0.05, remaining))

    if cancelled:
        wait_after_stop(process)
    else:
        process.wait()
    join_output_threads(output_threads)
    return subprocess.CompletedProcess(
        cmd,
        WEB_CANCELLED_RETURN_CODE if cancelled else process.returncode,
        stdout=decode_bounded_output(stdout_buffer),
        stderr=decode_bounded_output(stderr_buffer),
    )


def run_cancellable_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
    env: dict[str, str] | None,
    cancel_check: CancelCheck,
) -> subprocess.CompletedProcess[str]:
    return run_bounded_subprocess(
        cmd,
        cwd=cwd,
        timeout_sec=timeout_sec,
        env=env,
        cancel_check=cancel_check,
    )


def start_bounded_output_readers(
    process: subprocess.Popen[bytes],
    stdout_buffer: bytearray,
    stderr_buffer: bytearray,
) -> tuple[threading.Thread, threading.Thread]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("subprocess output pipes were not configured")
    stdout_thread = threading.Thread(
        target=read_stream_bounded,
        args=(process.stdout, stdout_buffer),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stream_bounded,
        args=(process.stderr, stderr_buffer),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return stdout_thread, stderr_thread


def read_stream_bounded(stream: BinaryIO, buffer: bytearray) -> None:
    try:
        while True:
            chunk = stream.read(WEB_SUBPROCESS_READ_CHUNK_BYTES)
            if not chunk:
                return
            remaining = WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
    finally:
        stream.close()


def join_output_threads(threads: tuple[threading.Thread, threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=WEB_SUBPROCESS_STOP_GRACE_SEC)


def decode_bounded_output(buffer: bytearray) -> str:
    return bytes(buffer).decode("utf-8", errors="replace")


def bound_completed_process_output(
    completed: subprocess.CompletedProcess[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=bound_output_value(completed.stdout),
        stderr=bound_output_value(completed.stderr),
    )


def bound_output_value(value: object) -> str:
    if isinstance(value, bytes):
        return value[:WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES].decode("utf-8", errors="replace")
    if isinstance(value, str):
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES:
            return value
        return encoded[:WEB_SUBPROCESS_CAPTURE_LIMIT_BYTES].decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def terminate_process_tree(process: subprocess.Popen[object], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return


def wait_after_stop(process: subprocess.Popen[object]) -> None:
    try:
        process.wait(timeout=WEB_SUBPROCESS_STOP_GRACE_SEC)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process, force=True)
        process.wait()


def communicate_after_stop(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process, force=True)
        return process.communicate()


def effective_subprocess_env(
    settings: WebSettings,
    base_env: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, str]:
    effective = merge_kerberos_cache_env(
        os.environ if base_env is None else base_env,
        {"krb5ccname": settings.krb5ccname},
    )
    if settings.cm_username and not effective.get("CM_USERNAME"):
        effective["CM_USERNAME"] = settings.cm_username
    return effective


def preflight_web_metadata_batch(
    settings: WebSettings,
    *,
    runner: Runner = subprocess.run,
    base_env: dict[str, str] | os._Environ[str] | None = None,
) -> None:
    env = effective_subprocess_env(settings, base_env=base_env)
    if not metadata_configured(settings):
        raise WebError(
            "Metadata collection is not configured for this web session. Restart with metadata options or disable metadata in config.",
            title="Metadata collection is not configured",
            reason_code="impala.metadata_not_configured",
            stage="Checking metadata preflight",
            next_step=(
                "Restart with metadata coordinator settings, "
                "or run in fast mode with metadata disabled."
            ),
        )
    if settings.metadata_source == METADATA_SOURCE_HMS_POSTGRES:
        if not hms_metadata.driver_available():
            raise WebError(
                "Metadata preflight failed: the metastore database driver is not installed. Install query-doctor[postgres] or disable metadata in config.",
                title="Metadata driver is unavailable",
                reason_code="impala.metadata_driver_unavailable",
                stage="Checking metadata preflight",
                next_step="Install query-doctor[postgres] in the web server environment or disable metadata collection.",
            )
        return
    if not hs2_runner.driver_available():
        raise WebError(
            "Metadata preflight failed: the impyla metadata driver is not installed. Install query-doctor[impala] or disable metadata in config.",
            title="Metadata driver is unavailable",
            reason_code="impala.metadata_driver_unavailable",
            stage="Checking metadata preflight",
            next_step="Install query-doctor[impala] in the web server environment or disable metadata collection.",
        )
    krb5ccname = env.get("KRB5CCNAME", "")
    if krb5ccname and any(ord(ch) < 32 or ord(ch) == 127 for ch in krb5ccname):
        raise WebError(
            "Metadata preflight failed: Kerberos cache setting is invalid. Fix server environment or disable metadata in config.",
            title="Metadata Kerberos cache setting is invalid",
            reason_code="impala.metadata_kerberos_cache_invalid",
            stage="Checking metadata preflight",
            next_step="Fix the Kerberos cache setting visible to the web server or disable metadata collection.",
        )
    try:
        completed = run_subprocess(
            ["klist"],
            cwd=settings.repo_dir,
            timeout_sec=min(settings.timeout_sec, 30),
            runner=runner,
            env=env,
        )
    except OSError as exc:
        raise WebError(
            "Metadata preflight failed: klist is not available. Fix server Kerberos setup or disable metadata in config.",
            title="Metadata Kerberos preflight is unavailable",
            reason_code="impala.metadata_klist_unavailable",
            stage="Checking metadata preflight",
            next_step="Install Kerberos client tools for the web server environment or disable metadata collection.",
        ) from exc
    if completed.returncode != 0:
        raise WebError(
            "Metadata preflight failed: Kerberos cache is not available or expired. "
            "Renew the Kerberos ticket or disable metadata in config.",
            title="Metadata Kerberos ticket is unavailable",
            reason_code="impala.metadata_kerberos_ticket_unavailable",
            stage="Checking metadata preflight",
            next_step="Renew the Kerberos ticket for the web server environment or disable metadata collection.",
        )


def subprocess_failure_web_error(
    stage: str,
    completed: subprocess.CompletedProcess[str],
) -> WebError:
    hint = safe_subprocess_failure_hint_info(completed)
    reason_code = hint.reason_code if hint is not None else default_subprocess_reason_code(stage)
    next_step = hint.next_step if hint is not None else default_subprocess_next_step(completed)
    title = hint.title if hint is not None else default_subprocess_title(stage)
    return WebError(
        subprocess_failure_message(stage, completed),
        title=title,
        reason_code=reason_code,
        stage=stage,
        next_step=next_step,
        details=(SUBPROCESS_OUTPUT_HIDDEN_MESSAGE,),
    )


def subprocess_failure_message(stage: str, completed: subprocess.CompletedProcess[str]) -> str:
    message = (
        f"{stage} failed with exit code {completed.returncode}. {SUBPROCESS_OUTPUT_HIDDEN_MESSAGE}"
    )
    safe_hint = safe_subprocess_failure_hint(completed)
    if safe_hint:
        message += f" {safe_hint}"
    if completed.returncode == 2:
        message += (
            " Exit code 2 usually indicates command-line argument validation or "
            "local configuration validation failed."
        )
    return message


def safe_subprocess_failure_hint(completed: subprocess.CompletedProcess[str]) -> str | None:
    hint = safe_subprocess_failure_hint_info(completed)
    return None if hint is None else hint.message


def safe_subprocess_failure_hint_info(
    completed: subprocess.CompletedProcess[str],
) -> SafeSubprocessFailureHint | None:
    output = safe_subprocess_failure_search_text(completed)
    if not output:
        return None
    for hint in SAFE_SUBPROCESS_FAILURE_HINTS:
        if hint.pattern in output:
            return hint
    return None


def default_subprocess_reason_code(stage: str) -> str:
    normalized = " ".join(str(stage or "").casefold().split())
    if "recent scan" in normalized:
        return "impala.recent_scan_failed"
    if "single-query" in normalized or "collection" in normalized:
        return "impala.collection_failed"
    if "manual profile" in normalized:
        return "impala.manual_profile_analysis_failed"
    if "analyzer" in normalized:
        return "impala.analyzer_failed"
    if "report" in normalized:
        return "web.report_generation_failed"
    if "optimized query" in normalized or "optimizer" in normalized:
        return "web.optimizer_generation_failed"
    return "web.subprocess_failed"


def default_subprocess_title(stage: str) -> str:
    normalized = " ".join(str(stage or "Subprocess").split())
    return f"{normalized} failed"


def default_subprocess_next_step(completed: subprocess.CompletedProcess[str]) -> str:
    if completed.returncode == 2:
        return "Review the selected local configuration and request bounds, then retry."
    return (
        "Review the selected source, local configuration, credentials, and scan bounds, then retry."
    )


def safe_subprocess_failure_search_text(completed: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(
        (bound_output_value(completed.stdout), bound_output_value(completed.stderr))
    )
    return " ".join(combined.casefold().split())


def has_cm_credentials(
    env: dict[str, str] | os._Environ[str] | None = None,
    *,
    username: str | None = None,
) -> bool:
    env = os.environ if env is None else env
    token = (env.get("CM_TOKEN") or "").strip()
    effective_username = (username or env.get("CM_USERNAME") or "").strip()
    password = (env.get("CM_PASSWORD") or "").strip()
    return bool(token) or (bool(effective_username) and bool(password))
