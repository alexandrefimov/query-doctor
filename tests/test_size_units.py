import pytest

from query_doctor.analyzer.scalars import fmt_bytes, parse_size_bytes
from query_doctor.impala.query_discovery import parse_size_bytes as parse_listing_size_bytes
from query_doctor.recent.query_optimization_score import (
    parse_size_bytes as parse_score_size_bytes,
)


# Impala prints binary sizes under decimal names: its "GB" is 1024**3 bytes.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("512 B", 512),
        ("1.00 KB", 1024),
        ("12.00 MB", 12 * 1024**2),
        ("11.43 GB", 11.43 * 1024**3),
        ("2.00 TB", 2 * 1024**4),
        ("3.00 GiB", 3 * 1024**3),
    ],
)
def test_profile_sizes_are_binary(value, expected):
    assert parse_size_bytes(value) == pytest.approx(expected)
    assert parse_score_size_bytes(value) == pytest.approx(expected)


def test_profile_and_listing_sizes_agree():
    assert parse_size_bytes("11.43 GB") == pytest.approx(
        parse_listing_size_bytes("11.43 GB"), abs=1
    )


def test_a_parsed_impala_size_prints_with_the_same_number():
    assert fmt_bytes(parse_size_bytes("11.43 GB")) == "11.43 GiB"
