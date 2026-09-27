from query_doctor.analyzer.runtime_counters import extract_total_counter


def test_total_time_does_not_match_inactive_total_time():
    text = """
Execution Profile
  - InactiveTotalTime: 5s000ms
  - TotalTime: 2s000ms
"""

    counter = extract_total_counter(text, "TotalTime")

    assert counter is not None
    assert counter["ms"] == 2000.0


def test_total_bytes_sent_does_not_match_a_longer_counter_name():
    text = """
Execution Profile
  - TotalScanBytesSent: 9.00 GB
  - TotalBytesSent: 1.00 GB
"""

    counter = extract_total_counter(text, "TotalBytesSent")

    assert counter is not None
    assert counter["raw"] == "1.00 GB"
