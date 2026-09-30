"""Focused tests for Phase 8 market-level credit processing."""

from decimal import Decimal

from src.models import ExceptionRecord, FinanceRecord, MatchResult, SLARecord
from src.processing import process_sla_record, process_sla_records


def _sla(
    market: str = "mx",
    *,
    client: str = "CLIENT-001",
    breach: object = "0.05",
    row: int = 7,
    source_file: str = "devops.xlsx",
    month_conflict: bool = False,
) -> SLARecord:
    return SLARecord(
        client=client,
        raw_market_code=market,
        market_name=market,
        market_description="April 2026 SLA market record",
        sla_breach_pct=breach,
        source_file=source_file,
        source_sheet="SLA_Breaches",
        source_row_number=row,
        filename_month_hint="MAY26" if "MAY26" in source_file else "APR26",
        workbook_month_hint="April 2026",
        month_conflict=month_conflict,
    )


def _finance(
    market: str = "MX",
    *,
    base: object = "10000",
    currency: object = "USD",
    row: int = 12,
) -> FinanceRecord:
    return FinanceRecord(
        ultimate_parent_nhn="CLIENT-001",
        customer_wd="CLIENT-001-WD-01",
        currency=currency,
        raw_market_code=market,
        base_credit=base,
        source_file="finance.xlsx",
        source_sheet="Finance_Fines",
        source_row_number=row,
    )


def _matched(finance: FinanceRecord, *, method: str = "EXACT_CODE_MATCH") -> MatchResult:
    reference = f"{finance.source_file}:{finance.source_sheet}:{finance.source_row_number}"
    return MatchResult(
        status="MATCHED",
        confidence="HIGH",
        match_method=method,
        candidate_count=1,
        matched_finance_record=finance,
        matched_finance_reference=reference,
        matched_finance_records=(finance,),
        matched_finance_references=(reference,),
        candidate_finance_records=(finance,),
        candidate_finance_references=(reference,),
    )


def test_high_confidence_exact_match_calculates_standard_credit() -> None:
    finance = _finance()
    result = process_sla_record(
        _sla(), _matched(finance), normalized_devops_market="MX", processing_run_id="run-1"
    )

    assert result.status == "CALCULATED"
    assert result.calculated_credit == Decimal("500.00")
    assert result.base_credit == "10000"
    assert result.currency == "USD"
    assert result.match_confidence == "HIGH"
    assert result.match_method == "EXACT_CODE_MATCH"


def test_high_confidence_composite_match_calculates_standard_credit() -> None:
    finance = _finance("LIFAMS\nLIFBRU")
    result = process_sla_record(
        _sla("lifams"),
        _matched(finance, method="COMPOSITE_CODE_MATCH"),
        normalized_devops_market="LIFAMS",
    )

    assert result.status == "CALCULATED"
    assert result.calculated_credit == Decimal("500.00")
    assert result.matched_finance_market == "LIFAMS\nLIFBRU"


def test_standard_rule_cap_is_applied_during_processing() -> None:
    result = process_sla_record(
        _sla(breach="0.35"), _matched(_finance()), processing_run_id="run-1"
    )

    assert result.applied_rate == Decimal("0.20")
    assert result.calculated_credit == Decimal("2000.00")


def test_zero_breach_with_valid_standard_match_is_zero_credit() -> None:
    result = process_sla_record(
        _sla(breach="0"), _matched(_finance()), processing_run_id="run-1"
    )

    assert result.status == "ZERO_CREDIT"
    assert result.applied_rate == Decimal("0")
    assert result.calculated_credit == Decimal("0")


def test_ambiguous_standard_match_is_not_calculated_and_keeps_candidates() -> None:
    candidates = (_finance("MX", row=12), _finance("MX", base="20000", row=13))
    match = MatchResult(
        status="AMBIGUOUS",
        confidence="LOW",
        candidate_count=2,
        candidate_finance_records=candidates,
        candidate_finance_references=("finance.xlsx:Finance_Fines:12", "finance.xlsx:Finance_Fines:13"),
        exception_code="AMBIGUOUS_MATCH",
        explanation="Two distinct Finance candidates remain.",
    )

    result = process_sla_record(_sla(), match, processing_run_id="run-1")

    assert result.status == "EXCEPTION"
    assert result.calculated_credit is None
    assert result.base_credit is None
    assert result.exception_code == "AMBIGUOUS_MATCH"
    assert result.exception_codes == ("AMBIGUOUS_MATCH",)
    assert result.candidate_count == 2
    assert result.candidate_finance_records == candidates


def test_unmatched_standard_record_is_not_calculated() -> None:
    match = MatchResult(
        status="UNMATCHED",
        confidence="LOW",
        exception_code="NO_FINANCE_MATCH",
        explanation="No Finance market candidate.",
    )

    result = process_sla_record(_sla(), match, processing_run_id="run-1")

    assert result.status == "EXCEPTION"
    assert result.calculated_credit is None
    assert result.base_credit is None
    assert result.exception_code == "NO_FINANCE_MATCH"


def test_month_conflict_is_ineligible_and_not_calculated() -> None:
    match = MatchResult(
        status="INELIGIBLE",
        confidence=None,
        exception_code="MONTH_CONFLICT",
        explanation="Filename and workbook month conflict.",
    )

    result = process_sla_record(
        _sla(month_conflict=True), match, processing_run_id="run-1"
    )

    assert result.status == "INELIGIBLE"
    assert result.calculated_credit is None
    assert result.exception_code == "MONTH_CONFLICT"
    assert result.reporting_month is None
    assert result.reporting_month_status == "UNRESOLVED"


def test_nonconflicting_month_hints_are_carried_as_resolved_month() -> None:
    result = process_sla_record(
        _sla(), _matched(_finance()), processing_run_id="run-1"
    )

    assert result.reporting_month == "2026-04"
    assert result.reporting_month_status == "RESOLVED"
    assert result.filename_month_hint == "APR26"
    assert result.workbook_month_hint == "April 2026"


def test_client_004_calculates_for_unmatched_record_without_finance_attributes() -> None:
    match = MatchResult(
        status="UNMATCHED",
        confidence="LOW",
        exception_code="NO_FINANCE_MATCH",
    )

    result = process_sla_record(
        _sla("mx", client="CLIENT-004", breach="0.1301"),
        match,
        normalized_devops_market="MX",
        processing_run_id="run-1",
    )

    assert result.status == "CALCULATED"
    assert result.calculated_credit == Decimal(
        "7686.3950807071483474250576479631053036126056879324"
    )
    assert result.match_status == "UNMATCHED"
    assert result.match_confidence == "LOW"
    assert result.base_credit is None
    assert result.currency is None
    assert result.customer_wd is None
    assert result.matched_finance_market is None
    assert result.finance_source_file is None
    assert result.audit_notes
    assert "Currency is unknown" in result.explanation


def test_client_004_zero_breach_returns_zero_even_without_finance_match() -> None:
    match = MatchResult(status="UNMATCHED", confidence="LOW", exception_code="NO_FINANCE_MATCH")

    result = process_sla_record(
        _sla(client="CLIENT-004", breach="0"), match, processing_run_id="run-1"
    )

    assert result.status == "ZERO_CREDIT"
    assert result.calculated_credit == Decimal("0")
    assert result.exception_code is None
    assert result.exception_codes == ("NO_FINANCE_MATCH",)


def test_client_004_month_conflict_remains_ineligible() -> None:
    match = MatchResult(status="INELIGIBLE", confidence=None, exception_code="MONTH_CONFLICT")

    result = process_sla_record(
        _sla(client="CLIENT-004", breach="0.10"), match, processing_run_id="run-1"
    )

    assert result.status == "INELIGIBLE"
    assert result.calculated_credit is None


def test_negative_base_credit_sign_is_preserved() -> None:
    result = process_sla_record(
        _sla(), _matched(_finance(base="-10000")), processing_run_id="run-1"
    )

    assert result.status == "CALCULATED"
    assert result.calculated_credit == Decimal("-500.00")


def test_missing_standard_base_credit_is_a_controlled_exception() -> None:
    result = process_sla_record(
        _sla(), _matched(_finance(base=None)), processing_run_id="run-1"
    )

    assert result.status == "EXCEPTION"
    assert result.calculated_credit is None
    assert result.exception_code == "MISSING_BASE_CREDIT"


def test_invalid_standard_breach_is_a_controlled_exception() -> None:
    result = process_sla_record(
        _sla(breach="invalid"), _matched(_finance()), processing_run_id="run-1"
    )

    assert result.status == "EXCEPTION"
    assert result.calculated_credit is None
    assert result.exception_code == "INVALID_SLA_BREACH"


def test_standard_calculation_requires_currency() -> None:
    result = process_sla_record(
        _sla(), _matched(_finance(currency=None)), processing_run_id="run-1"
    )

    assert result.status == "EXCEPTION"
    assert result.calculated_credit is None
    assert result.exception_code == "MISSING_CURRENCY"


def test_processing_preserves_devops_finance_lineage_and_normalized_code() -> None:
    result = process_sla_record(
        _sla(),
        _matched(_finance()),
        normalized_devops_market="MX",
        processing_run_id="run-42",
    )

    assert result.devops_market == "mx"
    assert result.normalized_devops_market == "MX"
    assert result.source_file == "devops.xlsx"
    assert result.source_sheet == "SLA_Breaches"
    assert result.source_row_number == 7
    assert result.finance_source_file == "finance.xlsx"
    assert result.finance_source_sheet == "Finance_Fines"
    assert result.finance_source_row_number == 12
    assert result.processing_run_id == "run-42"


def test_repeated_processing_with_same_run_id_is_deterministic() -> None:
    sla = _sla()
    match = _matched(_finance())

    first = process_sla_record(sla, match, normalized_devops_market="MX", processing_run_id="same")
    second = process_sla_record(sla, match, normalized_devops_market="MX", processing_run_id="same")

    assert first == second


def test_batch_processing_generates_one_run_id_for_all_results() -> None:
    records = [_sla(row=1), _sla(row=2)]
    matches = [_matched(_finance(row=11)), _matched(_finance(row=12))]

    results = process_sla_records(
        records,
        matches,
        normalized_devops_markets=["MX", "MX"],
    )

    assert len(results) == 2
    assert results[0].processing_run_id
    assert results[0].processing_run_id == results[1].processing_run_id


def test_batch_processing_assigns_exactly_one_outcome_to_every_sla_record() -> None:
    slas = [
        _sla(row=1),
        _sla(row=2),
        _sla(row=3),
        _sla(row=4, month_conflict=True),
    ]
    ambiguous = MatchResult(
        status="AMBIGUOUS",
        confidence="LOW",
        candidate_count=2,
        candidate_finance_records=(_finance(row=21), _finance(row=22)),
        exception_code="AMBIGUOUS_MATCH",
    )
    unmatched = MatchResult(
        status="UNMATCHED",
        confidence="LOW",
        exception_code="NO_FINANCE_MATCH",
    )
    ineligible = MatchResult(
        status="INELIGIBLE",
        confidence=None,
        exception_code="MONTH_CONFLICT",
    )
    results = process_sla_records(
        slas,
        [_matched(_finance(row=20)), ambiguous, unmatched, ineligible],
        normalized_devops_markets=["MX", "MX", "MX", "MX"],
        processing_run_id="phase-11-run",
    )

    assert len(results) == len(slas) == 4
    assert [result.source_row_number for result in results] == [1, 2, 3, 4]
    assert [result.status for result in results] == [
        "CALCULATED",
        "EXCEPTION",
        "EXCEPTION",
        "INELIGIBLE",
    ]
    assert results[1].calculated_credit is None
    assert results[2].calculated_credit is None


def test_source_exceptions_for_sla_and_matched_finance_are_retained() -> None:
    finance = _finance()
    source_exceptions = (
        ExceptionRecord(
            exception_code="MISSING_FINANCE_PARENT",
            description="Finance parent was resolved by approved fallback.",
            source_file="finance.xlsx",
            source_row_number=12,
        ),
        ExceptionRecord(
            exception_code="SOURCE_WARNING",
            description="DevOps source warning.",
            source_file="devops.xlsx",
            source_row_number=7,
        ),
        ExceptionRecord(
            exception_code="UNRELATED",
            description="A different row's exception.",
            source_file="devops.xlsx",
            source_row_number=99,
        ),
    )

    result = process_sla_record(
        _sla(), _matched(finance), source_exceptions=source_exceptions, processing_run_id="run-1"
    )

    assert [item.exception_code for item in result.source_exceptions] == [
        "MISSING_FINANCE_PARENT",
        "SOURCE_WARNING",
    ]
    assert "MISSING_FINANCE_PARENT" in result.exception_codes
    assert "SOURCE_WARNING" in result.exception_codes


def test_duplicate_equivalent_finance_rows_are_not_arbitrarily_selected() -> None:
    finance = _finance()
    duplicate = FinanceRecord(**{**finance.__dict__, "source_row_number": 13})
    match = MatchResult(
        status="MATCHED",
        confidence="HIGH",
        candidate_count=1,
        matched_finance_records=(finance, duplicate),
        candidate_finance_records=(finance, duplicate),
        matched_finance_references=("finance.xlsx:Finance_Fines:12", "finance.xlsx:Finance_Fines:13"),
    )

    result = process_sla_record(_sla(), match, processing_run_id="run-1")

    assert result.status == "EXCEPTION"
    assert result.exception_code == "NON_UNIQUE_FINANCE_MATCH"
    assert result.calculated_credit is None
    assert result.candidate_finance_records == (finance, duplicate)
    assert len(result.candidate_finance_records) == 2