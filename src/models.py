"""Audit-friendly data models for the credit report pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MatchStatus = Literal["MATCHED", "AMBIGUOUS", "UNMATCHED", "CANDIDATE", "INELIGIBLE"]
MatchConfidence = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class FinanceRecord:
    """A Finance source row with source values and lineage preserved.

    ``base_credit`` maps to the source column ``Monthly LCY 04/30/26``.
    Market codes remain raw; composite codes are not tokenized here.
    """

    ultimate_parent_nhn: Any | None = None
    customer_wd: Any | None = None
    currency: Any | None = None
    market_line_description: Any | None = None
    market_name: Any | None = None
    raw_market_code: Any | None = None
    base_credit: Any | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row_number: int | None = None
    processing_run_id: str | None = None


@dataclass
class SLARecord:
    """A DevOps SLA source row, retaining raw identifiers and month hints."""

    client: Any | None = None
    raw_market_code: Any | None = None
    market_name: Any | None = None
    market_description: Any | None = None
    sla_breach_pct: Any | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row_number: int | None = None
    filename_client_hint: str | None = None
    filename_month_hint: str | None = None
    workbook_month_hint: str | None = None
    month_conflict: bool | None = None
    processing_run_id: str | None = None


@dataclass
class MatchResult:
    """An auditable match outcome; this model performs no matching itself."""

    status: MatchStatus
    confidence: MatchConfidence | None
    match_method: str | None = None
    candidate_count: int = 0
    match_score: float | None = None
    matched_finance_record: FinanceRecord | None = None
    matched_finance_reference: str | None = None
    matched_finance_records: tuple[FinanceRecord, ...] = ()
    matched_finance_references: tuple[str, ...] = ()
    candidate_finance_records: tuple[FinanceRecord, ...] = ()
    candidate_finance_references: tuple[str, ...] = ()
    explanation: str | None = None
    exception_code: str | None = None


@dataclass
class CreditResult:
    """A place to record calculation inputs, outcome, and audit lineage.

    Optional values allow the result to represent exceptions and rule paths that
    do not require a Finance Base Credit, without inventing placeholder values.
    """

    client: Any | None = None
    devops_market: Any | None = None
    normalized_devops_market: Any | None = None
    matched_finance_market: Any | None = None
    matched_finance_reference: str | None = None
    customer_wd: Any | None = None
    match_status: MatchStatus | None = None
    match_confidence: MatchConfidence | None = None
    match_method: str | None = None
    match_score: float | None = None
    candidate_count: int = 0
    candidate_finance_records: tuple[FinanceRecord, ...] = ()
    candidate_finance_references: tuple[str, ...] = ()
    sla_breach_pct: Any | None = None
    applied_rate: Any | None = None
    base_credit: Any | None = None
    currency: Any | None = None
    calculation_rule: str | None = None
    calculated_credit: Any | None = None
    explanation: str | None = None
    status: str | None = None
    exception_code: str | None = None
    exception_codes: tuple[str, ...] = ()
    source_exceptions: tuple[ExceptionRecord, ...] = ()
    audit_notes: tuple[str, ...] = ()
    reporting_month: str | None = None
    reporting_month_status: str | None = None
    filename_month_hint: str | None = None
    workbook_month_hint: str | None = None
    month_conflict: bool | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row_number: int | None = None
    finance_source_file: str | None = None
    finance_source_sheet: str | None = None
    finance_source_row_number: int | None = None
    processing_run_id: str | None = None


@dataclass
class ExceptionRecord:
    """A structured exception linked to its source or related record."""

    exception_code: str
    description: str
    severity: str | None = None
    status: str | None = None
    client: Any | None = None
    market: Any | None = None
    source_file: str | None = None
    source_row_number: int | None = None
    relevant_record: FinanceRecord | SLARecord | MatchResult | CreditResult | None = None
    record_reference: str | None = None
    processing_run_id: str | None = None