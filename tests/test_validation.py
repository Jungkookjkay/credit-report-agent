"""Focused tests for read-only Phase 4 source validation."""

import pandas as pd

from src.validation import validate_devops, validate_finance, validate_inputs
from src.normalization import resolve_finance_client


FINANCE_COLUMNS = {
    "Ultimate Parent NHN": "CLIENT-001",
    "Customer WD": "CLIENT-001-WD-01",
    "Currency": "USD",
    "Market Line Description": "Market service",
    "Market Name": "Example market",
    "Market Code": "MKT",
    "Monthly LCY 04/30/26": 100.0,
}
DEVOPS_COLUMNS = {
    "Client": "CLIENT-001",
    "Market Code": "mkt",
    "Market Name": "mkt",
    "Market Description": "April 2026 SLA market record",
    "SLA Breach %": 0.0,
}


def _finance_frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _devops_frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_zero_breach_is_valid_and_inputs_are_not_modified() -> None:
    frame = _devops_frame(DEVOPS_COLUMNS.copy())
    original = frame.copy(deep=True)

    exceptions = validate_devops(frame)

    assert not any(issue.exception_code == "INVALID_SLA_BREACH" for issue in exceptions)
    pd.testing.assert_frame_equal(frame, original)


def test_missing_and_out_of_range_sla_breaches_are_reported() -> None:
    rows = [
        {**DEVOPS_COLUMNS, "SLA Breach %": None},
        {**DEVOPS_COLUMNS, "SLA Breach %": "not numeric"},
        {**DEVOPS_COLUMNS, "SLA Breach %": -0.01},
        {**DEVOPS_COLUMNS, "SLA Breach %": 1.01},
        {**DEVOPS_COLUMNS, "SLA Breach %": 0.0},
    ]

    exceptions = validate_devops(_devops_frame(*rows))

    assert sum(issue.exception_code == "INVALID_SLA_BREACH" for issue in exceptions) == 4


def test_exact_duplicates_and_candidate_key_collisions_are_distinct() -> None:
    first = FINANCE_COLUMNS.copy()
    exact_copy = FINANCE_COLUMNS.copy()
    different_record_same_key = {
        **FINANCE_COLUMNS,
        "Market Name": "Different source market name",
        "Monthly LCY 04/30/26": 120.0,
    }
    frame = _finance_frame(first, exact_copy, different_record_same_key)
    original = frame.copy(deep=True)

    exceptions = validate_finance(frame)

    assert sum(issue.exception_code == "EXACT_DUPLICATE" for issue in exceptions) == 1
    assert sum(issue.exception_code == "NON_UNIQUE_FINANCE_KEY" for issue in exceptions) == 3
    pd.testing.assert_frame_equal(frame, original)


def test_missing_parent_is_reported_without_inference() -> None:
    row = {**FINANCE_COLUMNS, "Ultimate Parent NHN": "", "Customer WD": "CLIENT-001-WD-01"}

    exceptions = validate_finance(_finance_frame(row))

    issue = next(issue for issue in exceptions if issue.exception_code == "MISSING_FINANCE_PARENT")
    assert issue.relevant_record is not None
    assert issue.relevant_record.ultimate_parent_nhn == ""
    assert issue.relevant_record.customer_wd == "CLIENT-001-WD-01"
    resolved = resolve_finance_client(
        issue.relevant_record.ultimate_parent_nhn,
        issue.relevant_record.customer_wd,
    )
    assert resolved.resolved_client == "CLIENT-001"
    assert resolved.client_resolution_method == "CUSTOMER_WD_FALLBACK"
    assert issue.relevant_record.ultimate_parent_nhn == ""


def test_missing_base_credit_and_currency_are_reported() -> None:
    missing_base = {
        **FINANCE_COLUMNS,
        "Customer WD": "CLIENT-001-WD-01",
        "Monthly LCY 04/30/26": None,
        "Currency": None,
    }
    invalid_base = {
        **FINANCE_COLUMNS,
        "Customer WD": "CLIENT-001-WD-02",
        "Monthly LCY 04/30/26": "invalid",
    }

    exceptions = validate_finance(_finance_frame(missing_base, invalid_base))

    assert any(issue.exception_code == "MISSING_BASE_CREDIT" for issue in exceptions)
    assert any(issue.exception_code == "INVALID_BASE_CREDIT" for issue in exceptions)
    assert any(issue.exception_code == "MISSING_CURRENCY" for issue in exceptions)


def test_month_and_client_conflicts_are_reported_without_rewriting_hints() -> None:
    rows = [
        {
            **DEVOPS_COLUMNS,
            "source_file": "devops_apr.xlsx",
            "source_row_number": 1,
            "filename_client_hint": "CLIENT-001",
            "filename_month_hint": "APR26",
            "workbook_month_hint": "April 2026",
        },
        {
            **DEVOPS_COLUMNS,
            "Client": "CLIENT-002",
            "source_file": "devops_may.xlsx",
            "source_row_number": 1,
            "filename_client_hint": "CLIENT-002",
            "filename_month_hint": "MAY26",
            "workbook_month_hint": "April 2026",
        },
        {
            **DEVOPS_COLUMNS,
            "source_file": "devops_client_conflict.xlsx",
            "source_row_number": 1,
            "filename_client_hint": "CLIENT-009",
            "filename_month_hint": "APR26",
            "workbook_month_hint": "April 2026",
        },
    ]
    frame = _devops_frame(*rows)
    original = frame.copy(deep=True)

    exceptions = validate_devops(frame)

    month_issues = [issue for issue in exceptions if issue.exception_code == "MONTH_CONFLICT"]
    client_issues = [issue for issue in exceptions if issue.exception_code == "CLIENT_CONFLICT"]
    assert [issue.source_file for issue in month_issues] == ["devops_may.xlsx"]
    assert [issue.source_file for issue in client_issues] == ["devops_client_conflict.xlsx"]
    pd.testing.assert_frame_equal(frame, original)


def test_missing_required_columns_return_schema_exceptions() -> None:
    exceptions = validate_inputs(
        pd.DataFrame({"Customer WD": ["CLIENT-001-WD-01"]}),
        pd.DataFrame({"Client": ["CLIENT-001"]}),
    ).exceptions

    assert sum(issue.exception_code == "SCHEMA_ERROR" for issue in exceptions) > 0# Placeholder tests for validation

# TODO: add schema and data-quality validation test cases.
