"""Shared analyzer threshold defaults."""

MEDIUM_DATA_MOVEMENT_BYTES = 1024**3
DEFAULT_LARGE_BYTES_THRESHOLD = 10 * 1024**3
# A memory estimate miss below this peak is noise: Impala 4.x estimates small
# scans at 1 MB, and a 14 MB peak there says nothing about admission or spill.
MIN_MEMORY_ANOMALY_PEAK_BYTES = 256 * 1024**2
