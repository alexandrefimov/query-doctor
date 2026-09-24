import pytest

from query_doctor.recent.statement_identity import (
    error_class_from_status,
    normalize_statement_for_fingerprint,
    safe_error_class,
    statement_fingerprint,
)


def test_statements_that_differ_only_in_literals_share_a_fingerprint():
    first = statement_fingerprint(
        "SELECT region, SUM(amount) FROM sales.orders\n"
        "WHERE order_date >= '2026-09-01' AND store_id = 17 -- nightly report\n"
        "GROUP BY region;"
    )
    second = statement_fingerprint(
        "select region,  sum(amount) from SALES.ORDERS "
        'where order_date >= "2026-09-23" and store_id = 4.5e2 /* rerun */ group by region'
    )

    assert first == second
    assert first is not None and first.startswith("sf_")
    assert len(first) == len("sf_") + 24


def test_different_statement_shapes_get_different_fingerprints():
    assert statement_fingerprint("SELECT a FROM t1 WHERE x = 1") != statement_fingerprint(
        "SELECT a FROM t2 WHERE x = 1"
    )


def test_normalization_keeps_digits_inside_identifiers_and_quotes_inside_comments():
    assert (
        normalize_statement_for_fingerprint(
            "SELECT t1.c2 FROM `db1`.t1 -- it's 'quoted'\nWHERE s = 'a -- b' AND n = 10"
        )
        == "select t1.c2 from `db1`.t1 where s = ? and n = ?"
    )


def test_truncated_listing_statement_is_marked_as_prefix_fingerprint():
    statement = "SELECT col_a, col_b FROM warehouse.facts WHERE note = 'unterminated..."
    fingerprint = statement_fingerprint(statement)

    assert fingerprint is not None and fingerprint.startswith("sfp_")
    assert statement_fingerprint(statement[:-3]) != fingerprint
    assert len(fingerprint) == len("sfp_") + 24


@pytest.mark.parametrize("statement", [None, "", "   ", "-- only a comment", ";"])
def test_missing_or_empty_statement_has_no_fingerprint(statement):
    assert statement_fingerprint(statement) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ParseException: Syntax error in line 1:\nselect * frm secret_table", "ParseException"),
        (
            "AnalysisException: Could not resolve table reference: 'finance.salaries'",
            "AnalysisException",
        ),
        ("org.apache.impala.common.AnalysisException: Column/field reference", "AnalysisException"),
        (
            "Memory limit exceeded: Failed to allocate on host-1.example.com:27000",
            "memory_limit_exceeded",
        ),
        ("Cancelled", "cancelled"),
        ("Rejected query from pool root.etl: queue full", "admission_rejected"),
        ("Admission for query exceeded timeout 60000ms in pool root.adhoc.", "admission_timeout"),
        ("Query 1a2b:3c4d expired due to execution time limit of 1h", "execution_time_limit"),
        (
            "Query 1a2b:3c4d expired due to client inactivity (timeout is 10m)",
            "client_inactivity_timeout",
        ),
        ("Failed to open HDFS file hdfs://nn/warehouse/secret/part-0.parq", "other"),
        ("OK", None),
        ("EXCEPTION", None),
        ("finished", None),
        ("", None),
        (None, None),
    ],
)
def test_error_class_keeps_only_a_raw_free_label(status, expected):
    label = error_class_from_status(status)

    assert label == expected
    if label is not None:
        assert safe_error_class(label) == label
        for raw in ("secret", "finance", "hdfs", "host-1", "root.", "1a2b", "select"):
            assert raw not in label.lower()


@pytest.mark.parametrize(
    "value",
    ["ParseException: x", "secret_table", "sf_deadbeef", "Error", "memory limit exceeded", None],
)
def test_store_guard_rejects_values_outside_the_contract(value):
    assert safe_error_class(value) is None
