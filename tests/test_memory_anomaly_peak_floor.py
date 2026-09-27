from query_doctor.analyzer.models import OperatorFact
from query_doctor.analyzer.operator_views import operator_with_best_memory_ratio
from query_doctor.analyzer.thresholds import MIN_MEMORY_ANOMALY_PEAK_BYTES

MIB = 1024**2


def _scan(peak_bytes: float, estimated_bytes: float) -> OperatorFact:
    return OperatorFact(
        operator_id="00",
        operator_name="SCAN",
        peak_mem_bytes=peak_bytes,
        estimated_peak_mem_bytes=estimated_bytes,
    )


def test_memory_anomaly_floor_is_256_mib():
    assert MIN_MEMORY_ANOMALY_PEAK_BYTES == 256 * MIB


def test_small_peak_is_not_a_memory_anomaly_even_with_a_large_ratio():
    # A small scan estimated at 1 MB that peaks at 14 MB is noise, not an estimate problem.
    assert operator_with_best_memory_ratio(_scan(14 * MIB, 1 * MIB), 4.0) is None


def test_peak_at_the_floor_with_a_large_ratio_is_a_memory_anomaly():
    anomaly = operator_with_best_memory_ratio(_scan(256 * MIB, 16 * MIB), 4.0)

    assert anomaly is not None
    assert anomaly.peak_mem_bytes == 256 * MIB


def test_large_peak_below_the_ratio_is_not_a_memory_anomaly():
    assert operator_with_best_memory_ratio(_scan(1024 * MIB, 512 * MIB), 4.0) is None
