"""Focused tests for deterministic Phase 9 report datasets."""

from __future__ import annotations

from decimal import Decimal

from src.models import CreditResult, FinanceRecord
from src.reporting import (
    UNKNOWN_CURRENCY_BUCKET,
    build_client_totals,
    build_detailed_report,
    build_exceptions_report,
    build_executive_summary,
    explain_credit_result,
    reconcile_report_datasets,
)


def _result(
    *,
    client: str = "CLIENT-001",
    market: str = "mx",
    normalized: str = "MX",
    month: str | None = "2026-04",
    month_status: str = "RESOLVED",
    match_status: str = "MATCHED",
    confidence: str | None = "HIGH",
    match_method: str | None = "EXACT_CODE_MATCH",
    breach: object = Decimal("0.05"),
    rate: object = Decimal("0.05"),
    base: object = Decimal("2000"),
    currency: object = "EUR",
    rule: str = "STANDARD_CAPPED",
    credit: Decimal | None = Decimal("100"),
    status: str = "CALCULATED",
    exception_code: str | None = None,
    exception_codes: tuple[str, ...] = (),
    candidates: tuple[FinanceRecord, ...] = (),
    candidate_count: int = 0,
    audit_notes: tuple[str, ...] = (),
    source_row: int = 1,
) -> CreditResult:
    return CreditResult(
        client=client,
        devops_market=market,
        normalized_devops_market=normalized,
        matched_finance_market="MX" if match_status == "MATCHED" else None,
        customer_wd=f"{client}-WD-01" if match_status == "MATCHED" else None,
        match_status=match_status,
        match_confidence=confidence,
        match_method=match_method,
        candidate_count=candidate_count,
        candidate_finance_records=candidates,
        candidate_finance_references=tuple(
            f"finance.xlsx:Finance_Fines:{record.source_row_number}" for record in candidates
        ),
        sla_breach_pct=breach,
        applied_rate=rate,
        base_credit=base if match_status == "MATCHED" else None,
        currency=currency if match_status == "MATCHED" else currency,
        calculation_rule=rule,
        calculated_credit=credit,
        explanation="Phase 8 processing explanation.",
        status=status,
        exception_code=exception_code,
        exception_codes=exception_codes,
        audit_notes=audit_notes,
        reporting_month=month,
        reporting_month_status=month_status,
        filename_month_hint="APR26" if month else "MAY26",
        workbook_month_hint="April 2026" if month else "April 2026",
        month_conflict=month is None,
        source_file="devops.xlsx",
        source_sheet="SLA_Breaches",
        source_row_number=source_row,
        finance_source_file="finance.xlsx" if match_status == "MATCHED" else None,
        finance_source_sheet="Finance_Fines" if match_status == "MATCHED" else None,
        finance_source_row_number=10 if match_status == "MATCHED" else None,
        processing_run_id="run-1",
    )


def test_detailed_report_preserves_required_fields_raw_values_and_decimal_credit() -> None:
    result = _result(market="mx", normalized="MX", credit=Decimal("100.123456789"))
    detail = build_detailed_report([result]).iloc[0]

    assert detail["Reporting Month"] == "2026-04"
    assert detail["Client"] == "CLIENT-001"
    assert detail["DevOps Market"] == "mx"
    assert detail["Original Market Code"] == "mx"
    assert detail["Normalized DevOps Market"] == "MX"
    assert detail["Calculated Credit"] == Decimal("100.123456789")
    assert detail["DevOps Source Row"] == 1
    assert detail["Finance Source Row"] == 10


def test_ambiguous_and_unmatched_credit_stays_null_in_detailed_report() -> None:
    ambiguous = _result(
        match_status="AMBIGUOUS",
        confidence="LOW",
        credit=None,
        rate=None,
        base=None,
        exception_code="AMBIGUOUS_MATCH",
        exception_codes=("AMBIGUOUS_MATCH",),
    )
    unmatched = _result(
        market="unknown",
        match_status="UNMATCHED",
        confidence="LOW",
        credit=None,
        rate=None,
        base=None,
        exception_code="NO_FINANCE_MATCH",
        exception_codes=("NO_FINANCE_MATCH",),
        source_row=2,
    )

    detail = build_detailed_report([ambiguous, unmatched])

    assert detail["Calculated Credit"].tolist() == [None, None]
    assert detail["Base Credit"].tolist() == [None, None]


def test_client_totals_exclude_null_credit_but_include_valid_zero() -> None:
    results = [
        _result(credit=Decimal("100")),
        _result(credit=None, status="EXCEPTION", exception_code="NO_FINANCE_MATCH", source_row=2),
        _result(credit=Decimal("0"), status="ZERO_CREDIT", breach=Decimal("0"), rate=Decimal("0"), source_row=3),
    ]

    totals = build_client_totals(results)

    assert len(totals) == 1
    assert totals.iloc[0]["Total Calculated Credit"] == Decimal("100")
    assert totals.iloc[0]["Calculated Record Count"] == 2
    assert totals.iloc[0]["Zero Credit Count"] == 1
    assert totals.iloc[0]["Null Credit Record Count"] == 1


def test_client_totals_keep_currencies_separate() -> None:
    results = [
        _result(currency="EUR", credit=Decimal("100")),
        _result(currency="USD", credit=Decimal("200"), source_row=2),
    ]

    totals = build_client_totals(results)

    assert set(totals["Currency"]) == {"EUR", "USD"}
    amounts = dict(zip(totals["Currency"], totals["Total Calculated Credit"]))
    assert amounts == {"EUR": Decimal("100"), "USD": Decimal("200")}


def test_client_004_null_currency_uses_isolated_unknown_bucket() -> None:
    special = _result(
        client="CLIENT-004",
        rule="CLIENT_004_SPECIAL",
        currency=None,
        base=None,
        rate=None,
        credit=Decimal("7686.39"),
        exception_codes=("NO_FINANCE_MATCH",),
        audit_notes=("Currency is unknown: no unique Finance record matched.",),
    )
    usd = _result(
        client="CLIENT-004",
        rule="CLIENT_004_SPECIAL",
        currency="USD",
        credit=Decimal("10"),
        source_row=2,
    )

    totals = build_client_totals([special, usd])
    detail = build_detailed_report([special]).iloc[0]

    assert set(totals["Currency"]) == {UNKNOWN_CURRENCY_BUCKET, "USD"}
    unknown_total = totals.loc[totals["Currency"] == UNKNOWN_CURRENCY_BUCKET].iloc[0]
    assert unknown_total["Total Calculated Credit"] == Decimal("7686.39")
    assert "no authoritative currency" in unknown_total["Currency Limitation"]
    assert detail["Currency"] is None
    assert detail["Reporting Currency Bucket"] == UNKNOWN_CURRENCY_BUCKET
    assert detail["Currency Limitation"] == "Unknown / unspecified currency"


def test_exceptions_report_distinguishes_finance_exception_from_calculation_failure() -> None:
    special = _result(
        client="CLIENT-004",
        rule="CLIENT_004_SPECIAL",
        currency=None,
        base=None,
        rate=None,
        credit=Decimal("7686.39"),
        status="CALCULATED",
        exception_codes=("NO_FINANCE_MATCH", "CURRENCY_UNKNOWN"),
        audit_notes=("Currency is unknown: no unique Finance record matched.",),
    )

    report = build_exceptions_report([special])

    assert set(report["Exception Code"]) == {"NO_FINANCE_MATCH", "CURRENCY_UNKNOWN"}
    assert report["Calculation Status"].tolist() == ["CALCULATED", "CALCULATED"]
    assert report["Calculated Credit"].tolist() == [Decimal("7686.39"), Decimal("7686.39")]


def test_month_conflict_is_excluded_from_totals_and_retained_as_exception() -> None:
    pending = _result(
        client="CLIENT-002",
        market="nymex",
        month=None,
        month_status="UNRESOLVED",
        match_status="INELIGIBLE",
        confidence=None,
        credit=None,
        rate=None,
        status="INELIGIBLE",
        exception_code="MONTH_CONFLICT",
        exception_codes=("MONTH_CONFLICT",),
        source_row=12,
    )

    detail = build_detailed_report([pending]).iloc[0]
    totals = build_client_totals([pending])
    executive = build_executive_summary([pending])
    exceptions = build_exceptions_report([pending])

    assert detail["Reporting Month"] is None
    assert detail["Reporting Month Status"] == "UNRESOLVED"
    assert totals.empty
    assert executive.empty
    assert exceptions["Exception Code"].tolist() == ["MONTH_CONFLICT"]


def test_ambiguous_exception_retains_candidate_finance_details() -> None:
    candidates = (
        FinanceRecord(customer_wd="CLIENT-001-WD-01", raw_market_code="MX", base_credit=100, currency="EUR"),
        FinanceRecord(customer_wd="CLIENT-001-WD-02", raw_market_code="MX", base_credit=200, currency="EUR"),
    )
    ambiguous = _result(
        match_status="AMBIGUOUS",
        confidence="LOW",
        candidates=candidates,
        candidate_count=2,
        credit=None,
        rate=None,
        base=None,
        status="EXCEPTION",
        exception_code="AMBIGUOUS_MATCH",
        exception_codes=("AMBIGUOUS_MATCH",),
    )

    exception = build_exceptions_report([ambiguous]).iloc[0]

    assert exception["Candidate Count"] == 2
    assert exception["Candidate Customer WD(s)"] == "CLIENT-001-WD-01; CLIENT-001-WD-02"
    assert exception["Candidate Market Code(s)"] == "MX"
    assert exception["Candidate Base Credit(s)"] == "100; 200"
    assert exception["Currency/Currencies"] == "EUR"


def test_explanations_are_deterministic_and_template_based() -> None:
    composite = _result(
        match_method="COMPOSITE_CODE_MATCH",
        breach=Decimal("0.0527"),
        rate=Decimal("0.0527"),
    )
    capped = _result(breach=Decimal("0.7549"), rate=Decimal("0.20"))
    ambiguous = _result(
        match_status="AMBIGUOUS", confidence="LOW", credit=None,
        exception_code="AMBIGUOUS_MATCH", exception_codes=("AMBIGUOUS_MATCH",),
    )

    assert explain_credit_result(composite) == explain_credit_result(composite)
    assert "HIGH-confidence composite market match" in explain_credit_result(composite)
    assert "5.27%" in explain_credit_result(composite)
    assert "75.49% exceeded" in explain_credit_result(capped)
    assert "no authoritative Base Credit was selected" in explain_credit_result(ambiguous)


def test_executive_counts_and_reconciliation_match_detailed_results() -> None:
    candidates = (
        FinanceRecord(customer_wd="CLIENT-001-WD-01", raw_market_code="MX", base_credit=100, currency="EUR"),
        FinanceRecord(customer_wd="CLIENT-001-WD-02", raw_market_code="MX", base_credit=200, currency="EUR"),
    )
    results = [
        _result(credit=Decimal("100"), breach=Decimal("0.05")),
        _result(credit=Decimal("0"), status="ZERO_CREDIT", breach=Decimal("0"), rate=Decimal("0"), source_row=2),
        _result(
            match_status="AMBIGUOUS", confidence="LOW", credit=None, rate=None, base=None,
            status="EXCEPTION", exception_code="AMBIGUOUS_MATCH",
            exception_codes=("AMBIGUOUS_MATCH",), candidates=candidates, candidate_count=2,
            source_row=3,
        ),
        _result(
            client="CLIENT-004", rule="CLIENT_004_SPECIAL", currency=None, base=None, rate=None,
            credit=Decimal("200"), exception_codes=("NO_FINANCE_MATCH",),
            audit_notes=("Currency is unknown: no unique Finance record matched.",), source_row=4,
        ),
        _result(
            client="CLIENT-002", month=None, month_status="UNRESOLVED", match_status="INELIGIBLE",
            confidence=None, credit=None, rate=None, status="INELIGIBLE",
            exception_code="MONTH_CONFLICT", exception_codes=("MONTH_CONFLICT",), source_row=5,
        ),
    ]
    detailed = build_detailed_report(results)
    client_totals = build_client_totals(results)
    executive = build_executive_summary(results)
    exceptions = build_exceptions_report(results)

    eur = executive.loc[executive["Currency"] == "EUR"].iloc[0]
    unknown = executive.loc[executive["Currency"] == UNKNOWN_CURRENCY_BUCKET].iloc[0]
    assert eur["Total Market Records"] == 3
    assert eur["Breached Market Count"] == 2
    assert eur["Calculated Positive-Credit Count"] == 1
    assert eur["Zero-Credit Count"] == 1
    assert eur["Exception Count"] == 1
    assert eur["Ambiguous Count"] == 1
    assert eur["HIGH-Confidence Matched Count"] == 2
    assert eur["Total Calculated Credit"] == Decimal("100")
    assert unknown["Total Calculated Credit"] == Decimal("200")

    reconciliation = reconcile_report_datasets(
        results, detailed, client_totals, executive, exceptions
    )
    assert reconciliation.passed, reconciliation.checks
    assert reconciliation.details["processed_result_count"] == 5
    assert reconciliation.details["resolved_month_row_count"] == 4
    assert reconciliation.details["unresolved_month_row_count"] == 1