"""Phase 8 orchestration for deterministic market-level credit results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
import re
from uuid import uuid4

import pandas as pd

if __package__:
    from .models import CreditResult, ExceptionRecord, FinanceRecord, MatchResult, SLARecord
    from .normalization import normalize_client, normalize_devops_market_code, normalize_market_code
    from .rules import RuleResult, calculate_credit, rule_requires_finance, select_rule
else:
    from models import CreditResult, ExceptionRecord, FinanceRecord, MatchResult, SLARecord
    from normalization import normalize_client, normalize_devops_market_code, normalize_market_code
    from rules import RuleResult, calculate_credit, rule_requires_finance, select_rule


_MONTH_PATTERN = re.compile(
    r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|"
    r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[-_ ]?(\d{2,4})",
    flags=re.IGNORECASE,
)
COMPOSITE_ALLOCATION_AMBIGUITY = "COMPOSITE_ALLOCATION_AMBIGUITY"
COMPOSITE_ALLOCATION_EXPLANATION = (
    "The Finance record contains this market within a composite market field, but "
    "the same Finance Base Credit corresponds to multiple DevOps markets. No "
    "allocation rule is provided, so the Base Credit was not reused and no "
    "authoritative credit was calculated."
)


def _parse_month_hint(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = _MONTH_PATTERN.search(value)
    if match is None:
        return None
    month = datetime.strptime(match.group(1)[:3].title(), "%b").month
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return year, month


def _resolved_month(sla_record: SLARecord, match_result: MatchResult) -> tuple[str | None, str]:
    conflict_flag = (
        not _is_missing(sla_record.month_conflict) and bool(sla_record.month_conflict)
    )
    if (
        conflict_flag
        or match_result.status == "INELIGIBLE"
        or match_result.exception_code == "MONTH_CONFLICT"
    ):
        return None, "UNRESOLVED"

    filename_month = _parse_month_hint(sla_record.filename_month_hint)
    workbook_month = _parse_month_hint(sla_record.workbook_month_hint)
    if filename_month is not None and workbook_month is not None and filename_month != workbook_month:
        return None, "UNRESOLVED"
    resolved = filename_month or workbook_month
    if resolved is None:
        return None, "UNRESOLVED"
    return f"{resolved[0]:04d}-{resolved[1]:02d}", "RESOLVED"


def _is_missing(value: object) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        missing = pd.isna(value)
        return bool(missing) if not hasattr(missing, "__len__") else False
    except (TypeError, ValueError):
        return False


def _finance_records(match_result: MatchResult) -> tuple[FinanceRecord, ...]:
    if match_result.matched_finance_records:
        return match_result.matched_finance_records
    if match_result.matched_finance_record is not None:
        return (match_result.matched_finance_record,)
    return ()


def _unique_matched_record(
    match_result: MatchResult,
    *,
    require_high_confidence: bool,
) -> FinanceRecord | None:
    if match_result.status != "MATCHED" or match_result.candidate_count != 1:
        return None
    if require_high_confidence and match_result.confidence != "HIGH":
        return None

    records = _finance_records(match_result)
    if len(records) != 1:
        return None
    if match_result.matched_finance_record not in (None, records[0]):
        return None
    return records[0]


def _finance_source_identity(
    record: FinanceRecord,
) -> tuple[str, str, int] | None:
    if (
        not isinstance(record.source_file, str)
        or not record.source_file.strip()
        or not isinstance(record.source_sheet, str)
        or not record.source_sheet.strip()
        or _is_missing(record.source_row_number)
    ):
        return None
    try:
        row_number = int(record.source_row_number)
    except (TypeError, ValueError, OverflowError):
        return None
    return record.source_file, record.source_sheet, row_number


def apply_composite_allocation_control(
    sla_records: Sequence[SLARecord],
    match_results: Sequence[MatchResult],
    normalized_devops_markets: Sequence[object | None],
) -> tuple[MatchResult, ...]:
    """Block reuse of one composite Finance row across distinct markets in a month."""
    allocations: dict[
        tuple[str, str, str, str, int], list[tuple[int, str]]
    ] = defaultdict(list)
    for index, (sla, match, supplied_market) in enumerate(
        zip(sla_records, match_results, normalized_devops_markets)
    ):
        if match.status != "MATCHED" or match.match_method != "COMPOSITE_CODE_MATCH":
            continue
        finance_records = _finance_records(match)
        if len(finance_records) != 1:
            continue
        finance_identity = _finance_source_identity(finance_records[0])
        if finance_identity is None:
            continue
        month, month_status = _resolved_month(sla, match)
        client = normalize_client(sla.client)
        if month_status != "RESOLVED" or month is None or client is None:
            continue
        if _is_missing(supplied_market):
            market, _ = normalize_devops_market_code(sla.raw_market_code, sla.client)
        else:
            market = normalize_market_code(supplied_market)
        if market is None:
            continue
        allocations[(client, month, *finance_identity)].append((index, market))

    adjusted = list(match_results)
    for uses in allocations.values():
        if len({market for _, market in uses}) <= 1:
            continue
        for index, _ in uses:
            match = adjusted[index]
            finance_records = _finance_records(match)
            candidate_records = match.candidate_finance_records or finance_records
            candidate_references = (
                match.candidate_finance_references
                or match.matched_finance_references
            )
            adjusted[index] = replace(
                match,
                status="AMBIGUOUS",
                confidence="LOW",
                matched_finance_record=None,
                matched_finance_reference=None,
                matched_finance_records=(),
                matched_finance_references=(),
                candidate_count=len(candidate_records),
                candidate_finance_records=candidate_records,
                candidate_finance_references=candidate_references,
                exception_code=COMPOSITE_ALLOCATION_AMBIGUITY,
                explanation=COMPOSITE_ALLOCATION_EXPLANATION,
            )
    return tuple(adjusted)


def _exception_identity(exception: ExceptionRecord) -> tuple[str | None, int | None]:
    return exception.source_file, exception.source_row_number


def _relevant_source_exceptions(
    sla_record: SLARecord,
    match_result: MatchResult,
    source_exceptions: Sequence[ExceptionRecord],
) -> tuple[ExceptionRecord, ...]:
    identities = {(sla_record.source_file, sla_record.source_row_number)}
    for record in (
        *_finance_records(match_result),
        *match_result.candidate_finance_records,
    ):
        identities.add((record.source_file, record.source_row_number))

    relevant = []
    for exception in source_exceptions:
        identity = _exception_identity(exception)
        if identity in identities:
            relevant.append(exception)
        elif exception.source_row_number is None and any(
            exception.source_file == source_file for source_file, _ in identities
        ):
            relevant.append(exception)
    return tuple(relevant)


def _append_code(codes: list[str], code: str | None) -> None:
    if code and code not in codes:
        codes.append(code)


def _match_exception_code(match_result: MatchResult) -> str | None:
    if match_result.exception_code:
        return match_result.exception_code
    return {
        "AMBIGUOUS": "AMBIGUOUS_MATCH",
        "UNMATCHED": "NO_FINANCE_MATCH",
        "INELIGIBLE": "MONTH_CONFLICT",
        "CANDIDATE": "LOW_CONFIDENCE_MATCH",
    }.get(match_result.status)


def _explanation(
    rule_result: RuleResult | None,
    match_result: MatchResult,
    source_exceptions: Sequence[ExceptionRecord],
    audit_notes: Sequence[str],
) -> str:
    parts = []
    if rule_result is not None and rule_result.explanation:
        parts.append(rule_result.explanation)
    if match_result.explanation:
        parts.append(match_result.explanation)
    parts.extend(exception.description for exception in source_exceptions)
    parts.extend(audit_notes)
    return " ".join(parts)


def process_sla_record(
    sla_record: SLARecord,
    match_result: MatchResult,
    *,
    normalized_devops_market: object | None = None,
    processing_run_id: str | None = None,
    source_exceptions: Sequence[ExceptionRecord] = (),
    config_path: str | None = None,
) -> CreditResult:
    """Process one SLA row using its existing MatchResult; never rematch it."""
    rule_name = select_rule(sla_record.client, config_path)
    run_id = processing_run_id or sla_record.processing_run_id or uuid4().hex
    relevant_exceptions = _relevant_source_exceptions(
        sla_record, match_result, source_exceptions
    )
    reporting_month, reporting_month_status = _resolved_month(sla_record, match_result)
    exception_codes: list[str] = []
    for exception in relevant_exceptions:
        _append_code(exception_codes, exception.exception_code)
    _append_code(exception_codes, _match_exception_code(match_result))

    requires_finance = rule_requires_finance(rule_name)
    finance_record = _unique_matched_record(
        match_result,
        require_high_confidence=requires_finance,
    )
    audit_notes: list[str] = []
    if not requires_finance and finance_record is None:
        audit_notes.append(
            "Currency is unknown: the selected rule does not specify a currency "
            "and no unique Finance record is matched."
        )

    base_result = {
        "client": sla_record.client,
        "devops_market": sla_record.raw_market_code,
        "normalized_devops_market": normalized_devops_market,
        "matched_finance_market": (
            finance_record.raw_market_code if finance_record is not None else None
        ),
        "matched_finance_reference": (
            match_result.matched_finance_reference
            if finance_record is not None
            else None
        ),
        "customer_wd": finance_record.customer_wd if finance_record is not None else None,
        "match_status": match_result.status,
        "match_confidence": match_result.confidence,
        "match_method": match_result.match_method,
        "match_score": match_result.match_score,
        "candidate_count": match_result.candidate_count,
        "candidate_finance_records": match_result.candidate_finance_records,
        "candidate_finance_references": match_result.candidate_finance_references,
        "sla_breach_pct": sla_record.sla_breach_pct,
        "base_credit": finance_record.base_credit if finance_record is not None else None,
        "currency": finance_record.currency if finance_record is not None else None,
        "calculation_rule": rule_name,
        "exception_codes": tuple(exception_codes),
        "source_exceptions": relevant_exceptions,
        "audit_notes": tuple(audit_notes),
        "reporting_month": reporting_month,
        "reporting_month_status": reporting_month_status,
        "filename_month_hint": sla_record.filename_month_hint,
        "workbook_month_hint": sla_record.workbook_month_hint,
        "month_conflict": sla_record.month_conflict,
        "source_file": sla_record.source_file,
        "source_sheet": sla_record.source_sheet,
        "source_row_number": sla_record.source_row_number,
        "finance_source_file": (
            finance_record.source_file if finance_record is not None else None
        ),
        "finance_source_sheet": (
            finance_record.source_sheet if finance_record is not None else None
        ),
        "finance_source_row_number": (
            finance_record.source_row_number if finance_record is not None else None
        ),
        "processing_run_id": run_id,
    }

    if match_result.status == "INELIGIBLE":
        code = _match_exception_code(match_result) or "INELIGIBLE"
        _append_code(exception_codes, code)
        base_result["exception_codes"] = tuple(exception_codes)
        return CreditResult(
            **base_result,
            status="INELIGIBLE",
            exception_code=code,
            explanation=_explanation(
                None, match_result, relevant_exceptions, audit_notes
            ),
        )

    if requires_finance and finance_record is None:
        code = _match_exception_code(match_result)
        if code is None:
            code = (
                "NON_UNIQUE_FINANCE_MATCH"
                if match_result.status == "MATCHED"
                else "UNSUITABLE_FINANCE_MATCH"
            )
        _append_code(exception_codes, code)
        base_result["exception_codes"] = tuple(exception_codes)
        return CreditResult(
            **base_result,
            status="EXCEPTION",
            exception_code=code,
            explanation=_explanation(
                None, match_result, relevant_exceptions, audit_notes
            ),
        )

    rule_result = calculate_credit(
        sla_record.client,
        sla_record.sla_breach_pct,
        finance_record.base_credit if finance_record is not None else None,
        config_path=config_path,
    )
    if rule_result.status == "EXCEPTION":
        _append_code(exception_codes, rule_result.exception_code)
        base_result["exception_codes"] = tuple(exception_codes)
        return CreditResult(
            **base_result,
            applied_rate=rule_result.applied_rate,
            calculated_credit=None,
            status="EXCEPTION",
            exception_code=rule_result.exception_code,
            explanation=_explanation(
                rule_result, match_result, relevant_exceptions, audit_notes
            ),
        )

    if requires_finance and _is_missing(finance_record.currency):
        code = "MISSING_CURRENCY"
        _append_code(exception_codes, code)
        base_result["exception_codes"] = tuple(exception_codes)
        return CreditResult(
            **base_result,
            applied_rate=rule_result.applied_rate,
            calculated_credit=None,
            status="EXCEPTION",
            exception_code=code,
            explanation=_explanation(
                rule_result,
                match_result,
                relevant_exceptions,
                (*audit_notes, "Finance currency is required for an authoritative credit."),
            ),
        )

    status = "ZERO_CREDIT" if rule_result.calculated_credit == 0 else "CALCULATED"
    base_result["exception_codes"] = tuple(exception_codes)
    return CreditResult(
        **base_result,
        applied_rate=rule_result.applied_rate,
        calculated_credit=rule_result.calculated_credit,
        status=status,
        explanation=_explanation(
            rule_result, match_result, relevant_exceptions, audit_notes
        ),
    )


def process_sla_records(
    sla_records: Iterable[SLARecord],
    match_results: Iterable[MatchResult],
    *,
    normalized_devops_markets: Iterable[object | None] | None = None,
    processing_run_id: str | None = None,
    source_exceptions: Sequence[ExceptionRecord] = (),
    config_path: str | None = None,
) -> list[CreditResult]:
    """Process aligned SLA and MatchResult sequences under one unique run ID."""
    slas = tuple(sla_records)
    matches = tuple(match_results)
    markets = (
        tuple(normalized_devops_markets)
        if normalized_devops_markets is not None
        else (None,) * len(slas)
    )
    if len(slas) != len(matches) or len(slas) != len(markets):
        raise ValueError(
            "SLA records, MatchResults, and normalized markets must have equal lengths."
        )

    matches = apply_composite_allocation_control(slas, matches, markets)
    run_id = processing_run_id or uuid4().hex
    return [
        process_sla_record(
            sla,
            match,
            normalized_devops_market=market,
            processing_run_id=run_id,
            source_exceptions=source_exceptions,
            config_path=config_path,
        )
        for sla, match, market in zip(slas, matches, markets)
    ]


__all__ = [
    "apply_composite_allocation_control",
    "process_sla_record",
    "process_sla_records",
]