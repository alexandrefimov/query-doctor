from query_doctor.analyzer.models import OperatorFact
from query_doctor.analyzer.operator_views import operator_with_best_memory_ratio
from query_doctor.analyzer.thresholds import MIN_MEMORY_ANOMALY_PEAK_BYTES

MIB = 1024**2
GIB = 1024**3


def _operator(name: str, peak_bytes: float, estimated_bytes: float) -> OperatorFact:
    return OperatorFact(
        operator_id="00",
        operator_name=name,
        peak_mem_bytes=peak_bytes,
        estimated_peak_mem_bytes=estimated_bytes,
    )


def test_memory_anomaly_floor_is_256_mib():
    assert MIN_MEMORY_ANOMALY_PEAK_BYTES == 256 * MIB


def test_small_peak_is_not_a_memory_anomaly_even_with_a_large_ratio():
    # An operator estimated at 1 MB that peaks at 14 MB is noise, not an estimate problem.
    assert operator_with_best_memory_ratio(_operator("AGGREGATE", 14 * MIB, 1 * MIB), 4.0) is None


def test_peak_at_the_floor_with_a_large_ratio_is_a_memory_anomaly():
    anomaly = operator_with_best_memory_ratio(_operator("AGGREGATE", 256 * MIB, 16 * MIB), 4.0)

    assert anomaly is not None
    assert anomaly.peak_mem_bytes == 256 * MIB


def test_large_peak_below_the_ratio_is_not_a_memory_anomaly():
    assert operator_with_best_memory_ratio(_operator("HASH JOIN", 1 * GIB, 512 * MIB), 4.0) is None


def test_scan_memory_is_not_a_memory_anomaly():
    # Impala estimates a Parquet scan at about 80 MB whatever it reads; the real
    # peak follows I/O buffers and scanner threads, and no plan choice uses it.
    for name in ("SCAN", "HDFS SCAN", "KUDU SCAN"):
        assert operator_with_best_memory_ratio(_operator(name, 2.7 * GIB, 80 * MIB), 4.0) is None


def test_aggregate_and_join_memory_misses_stay_anomalies():
    for name in ("AGGREGATE", "HASH JOIN", "SORT", "ANALYTIC"):
        assert operator_with_best_memory_ratio(_operator(name, 2.5 * GIB, 120 * MIB), 4.0)
