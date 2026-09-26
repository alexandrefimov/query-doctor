from query_doctor.analyzer.operators import parse_operator_table_line, parse_operators
from query_doctor.analyzer.scalars import parse_size_bytes


# Impala 3.x ExecSummary: no #Inst column.
LEGACY_EXEC_SUMMARY = """ExecSummary:
Operator              #Hosts   Avg Time   Max Time    #Rows  Est. #Rows  Peak Mem  Est. Peak Mem  Detail
02:HASH JOIN               3  800.000ms    1s200ms    2.50M      10.00K  12.00 MB        2.00 MB  INNER JOIN, BROADCAST
|--05:EXCHANGE             3   10.000ms   15.000ms   10.00K      10.00K  64.00 KB       16.00 KB  BROADCAST
01:SCAN HDFS               3  300.000ms  900.000ms    5.00M       5.00M  32.00 MB      128.00 MB  demo.events
"""

# Impala 4.x and later ExecSummary: #Inst after #Hosts (IMPALA-4618).
MODERN_EXEC_SUMMARY = """ExecSummary:
Operator              #Hosts  #Inst   Avg Time   Max Time    #Rows  Est. #Rows  Peak Mem  Est. Peak Mem  Detail
F02:ROOT                   1      1   59.685us   59.685us                       4.01 MB        4.00 MB
02:HASH JOIN               3      3  800.000ms    1s200ms    2.50M      10.00K  12.00 MB        2.00 MB  INNER JOIN, BROADCAST
|--05:EXCHANGE             3      3   10.000ms   15.000ms   10.00K      10.00K  64.00 KB       16.00 KB  BROADCAST
|  F01:EXCHANGE SENDER     3      3    1.000ms    2.000ms                       8.00 KB            0 B
01:SCAN HDFS               3     12  300.000ms  900.000ms    5.00M       5.00M  32.00 MB      128.00 MB  demo.events
"""

EXPECTED = {
    ("02", "HASH JOIN"): (1200.0, 2.5e6, 1.0e4, "12.00 MB", "2.00 MB"),
    ("05", "EXCHANGE"): (15.0, 1.0e4, 1.0e4, "64.00 KB", "16.00 KB"),
    ("01", "SCAN"): (900.0, 5.0e6, 5.0e6, "32.00 MB", "128.00 MB"),
}


def _by_key(text):
    return {(op.operator_id, op.operator_name): op for op in parse_operators(text)}


def _assert_expected(ops):
    for key, (time_ms, actual, estimated, peak, est_peak) in EXPECTED.items():
        op = ops[key]
        assert op.time_ms == time_ms, key
        assert op.actual_rows == actual, key
        assert op.estimated_rows == estimated, key
        assert op.peak_mem_bytes == parse_size_bytes(peak), key
        assert op.estimated_peak_mem_bytes == parse_size_bytes(est_peak), key


def test_legacy_exec_summary_columns_are_read_by_header():
    ops = _by_key(LEGACY_EXEC_SUMMARY)

    _assert_expected(ops)
    assert ops[("02", "HASH JOIN")].join_kind == "INNER JOIN"


def test_modern_exec_summary_with_inst_column_is_read_by_header():
    ops = _by_key(MODERN_EXEC_SUMMARY)

    _assert_expected(ops)
    assert ops[("02", "HASH JOIN")].join_kind == "INNER JOIN"


def test_exec_summary_rows_without_header_infer_the_layout():
    legacy_row = "02:HASH JOIN  3  800.000ms  1s200ms  2.50M  10.00K  12.00 MB  2.00 MB  INNER JOIN"
    modern_row = "02:HASH JOIN  3  3  800.000ms  1s200ms  2.50M  10.00K  12.00 MB  2.00 MB  INNER JOIN"
    modern_row_without_detail = "02:HASH JOIN  3  3  800.000ms  1s200ms  2.50M  10.00K  12.00 MB  2.00 MB"

    for row in (legacy_row, modern_row, modern_row_without_detail):
        op = parse_operator_table_line(row)
        assert op is not None, row
        assert op.time_ms == 1200.0, row
        assert op.actual_rows == 2.5e6, row
        assert op.estimated_rows == 1.0e4, row
        assert op.peak_mem_bytes == parse_size_bytes("12.00 MB"), row
        assert op.estimated_peak_mem_bytes == parse_size_bytes("2.00 MB"), row


def test_exec_summary_tree_prefix_rows_are_table_rows():
    op = parse_operator_table_line(
        "|  |--05:EXCHANGE  3  3  10.000ms  15.000ms  10.00K  10.00K  64.00 KB  16.00 KB  BROADCAST"
    )

    assert op is not None
    assert (op.operator_id, op.operator_name) == ("05", "EXCHANGE")
    assert op.time_ms == 15.0
    assert op.actual_rows == 1.0e4
