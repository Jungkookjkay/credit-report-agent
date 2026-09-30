"""Deterministic DevOps-to-Finance candidate matching for Phase 6."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pandas as pd

if __package__:
    from .models import FinanceRecord, MatchResult
else:
    from models import FinanceRecord, MatchResult


FINANCE_LINEAGE_COLUMNS = {
    "source_file",
    "source_sheet",
    "source_row_number",
    "processing_run_id",
    "filename_client_hint",
    "filename_month_hint",
    "workbook_month_hint",
    "month_conflict",
}
FINANCE_DERIVED_COLUMNS = {
    "resolved_client",
    "client_resolution_method",
    "normalized_market_code",
    "normalized_market_tokens",
    "market_code_tokenization_safe",
    "market_code_tokenization_note",
}
MATCH_RESULT_COLUMNS = (
    "matching_status",
    "match_confidence",
    "match_method",
    "candidate_count",
    "match_score",
    "matched_finance_record",
    "matched_finance_reference",
    "matched_finance_records",
    "matched_finance_references",
    "candidate_finance_records",
    "candidate_finance_references",
    "matching_explanation",
    "exception_code",
)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
        return bool(missing) if not hasattr(missing, "__len__") else False
    except (TypeError, ValueError):
        return False


def _row_tokens(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(token for token in value if isinstance(token, str))
    return ()


def _source_value(value: Any) -> Any:
    if _is_missing(value):
        return ("<NULL>",)
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, list):
        return tuple(_source_value(item) for item in value)
    try:
        hash(value)
    except TypeError:
        return (type(value).__name__, repr(value))
    return type(value).__name__, value


def _source_columns(finance: pd.DataFrame) -> tuple[str, ...]:
    return tuple(
        column
        for column in finance.columns
        if column not in FINANCE_LINEAGE_COLUMNS
        and column not in FINANCE_DERIVED_COLUMNS
    )


def _finance_record(row: pd.Series) -> FinanceRecord:
    row_number = row.get("source_row_number")
    if _is_missing(row_number):
        row_number = None
    else:
        row_number = int(row_number)
    return FinanceRecord(
        ultimate_parent_nhn=row.get("Ultimate Parent NHN"),
        customer_wd=row.get("Customer WD"),
        currency=row.get("Currency"),
        market_line_description=row.get("Market Line Description"),
        market_name=row.get("Market Name"),
        raw_market_code=row.get("Market Code"),
        base_credit=row.get("Monthly LCY 04/30/26"),
        source_file=row.get("source_file"),
        source_sheet=row.get("source_sheet"),
        source_row_number=row_number,
        processing_run_id=row.get("processing_run_id"),
    )


def _record_reference(row: pd.Series) -> str:
    source_file = row.get("source_file")
    source_sheet = row.get("source_sheet")
    source_row = row.get("source_row_number")
    if _is_missing(source_row):
        return f"{source_file}:{source_sheet}"
    return f"{source_file}:{source_sheet}:{int(source_row)}"


def _candidate_groups(
    candidates: pd.DataFrame,
    source_columns: tuple[str, ...],
) -> list[list[pd.Series]]:
    """Group identical source payloads without dropping their lineage rows."""
    grouped: OrderedDict[tuple[Any, ...], list[pd.Series]] = OrderedDict()
    for _, row in candidates.iterrows():
        signature = tuple(_source_value(row.get(column)) for column in source_columns)
        grouped.setdefault(signature, []).append(row)
    return list(grouped.values())


def _all_candidate_records(groups: list[list[pd.Series]]) -> tuple[FinanceRecord, ...]:
    return tuple(_finance_record(row) for group in groups for row in group)


def _all_candidate_references(groups: list[list[pd.Series]]) -> tuple[str, ...]:
    return tuple(_record_reference(row) for group in groups for row in group)


def _base_result(
    status: str,
    confidence: str | None,
    *,
    explanation: str,
    exception_code: str | None = None,
    match_method: str | None = None,
    candidate_groups: list[list[pd.Series]] | None = None,
) -> MatchResult:
    groups = candidate_groups or []
    return MatchResult(
        status=status,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        match_method=match_method,
        candidate_count=len(groups),
        candidate_finance_records=_all_candidate_records(groups),
        candidate_finance_references=_all_candidate_references(groups),
        explanation=explanation,
        exception_code=exception_code,
    )


def _matched_result(
    group: list[pd.Series],
    method: str,
    explanation: str,
) -> MatchResult:
    records = tuple(_finance_record(row) for row in group)
    references = tuple(_record_reference(row) for row in group)
    duplicate_note = ""
    if len(group) > 1:
        duplicate_note = (
            f" {len(group)} exact duplicate source rows represent one candidate; "
            "all source references are retained."
        )
    return MatchResult(
        status="MATCHED",
        confidence="HIGH",
        match_method=method,
        candidate_count=1,
        matched_finance_record=records[0] if len(records) == 1 else None,
        matched_finance_reference=references[0] if len(references) == 1 else None,
        matched_finance_records=records,
        matched_finance_references=references,
        candidate_finance_records=records,
        candidate_finance_references=references,
        explanation=explanation + duplicate_note,
    )


def match_sla_record(
    devops_row: pd.Series,
    finance: pd.DataFrame,
) -> MatchResult:
    """Match one normalized SLA row to Finance records in its parent-client scope."""
    month_conflict = devops_row.get("month_conflict", False)
    if not _is_missing(month_conflict) and bool(month_conflict):
        return _base_result(
            "INELIGIBLE",
            None,
            exception_code="MONTH_CONFLICT",
            explanation=(
                "Matching is pending because filename and workbook month metadata "
                "conflict; neither reporting month was selected."
            ),
        )

    devops_client = devops_row.get("normalized_client")
    market_code = devops_row.get("normalized_market_code")
    if _is_missing(devops_client) or _is_missing(market_code):
        return _base_result(
            "UNMATCHED",
            "LOW",
            exception_code="NO_FINANCE_MATCH",
            explanation="No match was attempted because normalized client or market is missing.",
        )

    client_scope = finance.loc[finance["resolved_client"] == devops_client]
    if client_scope.empty:
        return _base_result(
            "UNMATCHED",
            "LOW",
            exception_code="NO_FINANCE_MATCH",
            explanation=f"No Finance records exist in client scope {devops_client!r}.",
        )

    token_rows = client_scope.loc[
        client_scope["normalized_market_tokens"].map(
            lambda tokens: market_code in _row_tokens(tokens)
        )
    ]
    atomic_rows = token_rows.loc[
        token_rows["normalized_market_code"].eq(market_code)
        & token_rows["normalized_market_tokens"].map(
            lambda tokens: len(_row_tokens(tokens)) == 1
        )
    ]

    source_columns = _source_columns(finance)
    atomic_groups = _candidate_groups(atomic_rows, source_columns)
    prefix_used = bool(devops_row.get("client002_prefix_removed", False))
    prefix_note = (
        " The approved CLIENT-002 client002_ prefix normalization was applied."
        if prefix_used
        else ""
    )

    if len(atomic_groups) == 1:
        return _matched_result(
            atomic_groups[0],
            "EXACT_CODE_MATCH",
            "One unique Finance record has the exact normalized atomic market code."
            + prefix_note,
        )
    if len(atomic_groups) > 1:
        return _base_result(
            "AMBIGUOUS",
            "LOW",
            exception_code="AMBIGUOUS_MATCH",
            explanation=(
                "Multiple distinct Finance records in the correct client scope have "
                "the exact normalized atomic market code. No record was selected."
                + prefix_note
            ),
            candidate_groups=atomic_groups,
        )

    composite_rows = token_rows.loc[
        token_rows["normalized_market_tokens"].map(
            lambda tokens: len(_row_tokens(tokens)) > 1
        )
    ]
    composite_groups = _candidate_groups(composite_rows, source_columns)
    if len(composite_groups) == 1:
        return _matched_result(
            composite_groups[0],
            "COMPOSITE_CODE_MATCH",
            "One unique Finance record contains the exact normalized market token "
            "within its composite market-code field."
            + prefix_note,
        )
    if len(composite_groups) > 1:
        return _base_result(
            "AMBIGUOUS",
            "LOW",
            exception_code="AMBIGUOUS_MATCH",
            explanation=(
                "Multiple distinct Finance records in the correct client scope "
                "contain the exact normalized token in composite market-code fields. "
                "No record was selected."
                + prefix_note
            ),
            candidate_groups=composite_groups,
        )

    return _base_result(
        "UNMATCHED",
        "LOW",
        exception_code="NO_FINANCE_MATCH",
        explanation=(
            f"No Finance market-code token {market_code!r} exists in client scope "
            f"{devops_client!r}. Other client scopes were not searched."
            + prefix_note
        ),
    )


def match_devops_records(
    devops: pd.DataFrame,
    finance: pd.DataFrame,
) -> pd.DataFrame:
    """Return a copy of normalized DevOps rows annotated with match outcomes."""
    required_devops = {"normalized_client", "normalized_market_code"}
    required_finance = {
        "resolved_client",
        "normalized_market_code",
        "normalized_market_tokens",
    }
    missing_devops = sorted(required_devops.difference(devops.columns))
    missing_finance = sorted(required_finance.difference(finance.columns))
    if missing_devops or missing_finance:
        raise ValueError(
            "Matching requires Phase 5 normalized inputs; "
            f"missing DevOps columns={missing_devops}, Finance columns={missing_finance}."
        )

    results = [
        match_sla_record(row, finance)
        for _, row in devops.iterrows()
    ]
    result = devops.copy(deep=True)
    result["matching_status"] = [match.status for match in results]
    result["match_confidence"] = [match.confidence for match in results]
    result["match_method"] = [match.match_method for match in results]
    result["candidate_count"] = [match.candidate_count for match in results]
    result["match_score"] = [match.match_score for match in results]
    result["matched_finance_record"] = [match.matched_finance_record for match in results]
    result["matched_finance_reference"] = [match.matched_finance_reference for match in results]
    result["matched_finance_records"] = [match.matched_finance_records for match in results]
    result["matched_finance_references"] = [match.matched_finance_references for match in results]
    result["candidate_finance_records"] = [match.candidate_finance_records for match in results]
    result["candidate_finance_references"] = [match.candidate_finance_references for match in results]
    result["matching_explanation"] = [match.explanation for match in results]
    result["exception_code"] = [match.exception_code for match in results]
    return result


__all__ = ["MATCH_RESULT_COLUMNS", "match_devops_records", "match_sla_record"]