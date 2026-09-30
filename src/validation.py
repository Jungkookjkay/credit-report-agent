"""Read-only source-data validation for Finance and DevOps inputs."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

if __package__:
    from .models import ExceptionRecord, FinanceRecord, SLARecord
else:
    from models import ExceptionRecord, FinanceRecord, SLARecord


FINANCE_REQUIRED_COLUMNS = (
    "Ultimate Parent NHN",
    "Customer WD",
    "Currency",
    "Market Line Description",
    "Market Name",
    "Market Code",
    "Monthly LCY 04/30/26",
)
DEVOPS_REQUIRED_COLUMNS = (
    "Client",
    "Market Code",
    "Market Name",
    "Market Description",
    "SLA Breach %",
)
FINANCE_CANDIDATE_KEY_COLUMNS = ("Customer WD", "Market Code")
FINANCE_SOURCE_COLUMNS = (
    "Ultimate Parent NHN",
    "Customer WD",
    "Currency",
    "Market Line Description",
    "Market Name",
    "Market Code",
    "Monthly LCY 04/30/26",
)
LINEAGE_COLUMNS = {
    "source_file",
    "source_sheet",
    "source_row_number",
    "filename_client_hint",
    "filename_month_hint",
    "workbook_month_hint",
    "month_conflict",
    "processing_run_id",
}
MONTH_NAMES = (
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
)
MONTH_ABBREVIATIONS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


@dataclass
class ValidationReport:
    """Record counts and structured issues found without changing input rows."""

    finance_records_validated: int
    devops_records_validated: int
    exceptions: list[ExceptionRecord]

    @property
    def exception_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for exception in self.exceptions:
            counts[exception.exception_code] += 1
        return dict(sorted(counts.items()))

    @property
    def source_files_affected(self) -> list[str]:
        return sorted(
            {
                exception.source_file
                for exception in self.exceptions
                if exception.source_file is not None
            }
        )


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def _is_blank(value: Any) -> bool:
    return _is_null(value) or (isinstance(value, str) and not value.strip())


def _as_finite_number(value: Any) -> float | None:
    if _is_blank(value) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _month_key(value: Any) -> tuple[int, int] | None:
    """Parse a month/year hint for comparison only; never rewrite its source."""
    if _is_blank(value):
        return None
    month_pattern = "|".join((*MONTH_NAMES, *MONTH_ABBREVIATIONS))
    match = re.search(
        rf"({month_pattern})[-_ ]?(\d{{2,4}})",
        str(value),
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month = datetime.strptime(match.group(1)[:3].title(), "%b").month
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return month, year


def _comparison_value(value: Any) -> Any:
    """Create a hashable, null-aware comparison token without changing data."""
    if _is_null(value):
        return ("<NULL>",)
    try:
        hash(value)
    except TypeError:
        return (type(value).__name__, repr(value))
    return (type(value).__name__, value)


def _row_number(row: pd.Series | None) -> int | None:
    if row is None:
        return None
    value = row.get("source_row_number")
    number = _as_finite_number(value)
    return int(number) if number is not None else None


def _finance_record(row: pd.Series) -> FinanceRecord:
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
        source_row_number=_row_number(row),
        processing_run_id=row.get("processing_run_id"),
    )


def _sla_record(row: pd.Series) -> SLARecord:
    conflict = row.get("month_conflict")
    return SLARecord(
        client=row.get("Client"),
        raw_market_code=row.get("Market Code"),
        market_name=row.get("Market Name"),
        market_description=row.get("Market Description"),
        sla_breach_pct=row.get("SLA Breach %"),
        source_file=row.get("source_file"),
        source_sheet=row.get("source_sheet"),
        source_row_number=_row_number(row),
        filename_client_hint=row.get("filename_client_hint"),
        filename_month_hint=row.get("filename_month_hint"),
        workbook_month_hint=row.get("workbook_month_hint"),
        month_conflict=None if _is_null(conflict) else bool(conflict),
        processing_run_id=row.get("processing_run_id"),
    )


def _make_exception(
    code: str,
    description: str,
    *,
    row: pd.Series | None = None,
    kind: str | None = None,
    severity: str = "ERROR",
) -> ExceptionRecord:
    source_file = None if row is None else row.get("source_file")
    source_sheet = None if row is None else row.get("source_sheet")
    source_row_number = _row_number(row)
    record_reference = None
    relevant_record: FinanceRecord | SLARecord | None = None
    client = None
    market = None

    if row is not None:
        if kind == "finance":
            relevant_record = _finance_record(row)
            client = row.get("Ultimate Parent NHN")
            market = row.get("Market Code")
        elif kind == "devops":
            relevant_record = _sla_record(row)
            client = row.get("Client")
            market = row.get("Market Code")
        if source_file is not None and source_row_number is not None:
            record_reference = f"{source_file}:{source_sheet}:{source_row_number}"

    return ExceptionRecord(
        exception_code=code,
        description=description,
        severity=severity,
        status="OPEN",
        client=client,
        market=market,
        source_file=source_file,
        source_row_number=source_row_number,
        relevant_record=relevant_record,
        record_reference=record_reference,
        processing_run_id=None if row is None else row.get("processing_run_id"),
    )


def _schema_exceptions(
    frame: pd.DataFrame,
    required_columns: tuple[str, ...],
    kind: str,
) -> list[ExceptionRecord]:
    missing = [column for column in required_columns if column not in frame.columns]
    if not missing:
        return []

    source_file = (
        frame["source_file"].iloc[0]
        if "source_file" in frame and not frame.empty
        else None
    )
    return [
        ExceptionRecord(
            exception_code="SCHEMA_ERROR",
            description=f"{kind.title()} input is missing required column {column!r}.",
            severity="ERROR",
            status="OPEN",
            source_file=source_file,
        )
        for column in missing
    ]


def _raw_source_columns(frame: pd.DataFrame, expected: tuple[str, ...]) -> list[str]:
    """Return source fields, excluding ingestion-added lineage metadata."""
    expected_present = [column for column in expected if column in frame.columns]
    additional_source = [
        column
        for column in frame.columns
        if column not in LINEAGE_COLUMNS and column not in expected_present
    ]
    return expected_present + additional_source


def validate_finance(finance: pd.DataFrame) -> list[ExceptionRecord]:
    """Return Finance schema and row-level issues without modifying source rows."""
    exceptions = _schema_exceptions(finance, FINANCE_REQUIRED_COLUMNS, "Finance")
    if finance.empty:
        return exceptions

    for _, row in finance.iterrows():
        for column in ("Ultimate Parent NHN", "Customer WD", "Market Code"):
            if column not in finance.columns or not _is_blank(row.get(column)):
                continue
            if column == "Ultimate Parent NHN":
                code = "MISSING_FINANCE_PARENT"
                description = (
                    "Finance Ultimate Parent NHN is blank; it was not inferred "
                    "from Customer WD."
                )
            else:
                code = "MISSING_REQUIRED_IDENTIFIER"
                description = f"Finance required identifier {column!r} is missing."
            exceptions.append(
                _make_exception(code, description, row=row, kind="finance")
            )

        for column in ("Market Line Description", "Market Name"):
            if column in finance.columns and _is_blank(row.get(column)):
                exceptions.append(
                    _make_exception(
                        "MISSING_REQUIRED_VALUE",
                        f"Finance required value {column!r} is missing.",
                        row=row,
                        kind="finance",
                    )
                )

        if "Currency" in finance.columns and _is_blank(row.get("Currency")):
            exceptions.append(
                _make_exception(
                    "MISSING_CURRENCY",
                    "Finance Currency is missing.",
                    row=row,
                    kind="finance",
                )
            )

        base_column = "Monthly LCY 04/30/26"
        if base_column in finance.columns:
            base_value = row.get(base_column)
            if _is_blank(base_value):
                code = "MISSING_BASE_CREDIT"
                description = "Finance Base Credit is missing."
            elif _as_finite_number(base_value) is None:
                code = "INVALID_BASE_CREDIT"
                description = f"Finance Base Credit {base_value!r} is not a finite number."
            else:
                code = ""
                description = ""
            if code:
                exceptions.append(
                    _make_exception(code, description, row=row, kind="finance")
                )

    source_columns = _raw_source_columns(finance, FINANCE_SOURCE_COLUMNS)
    exact_seen: set[tuple[Any, ...]] = set()
    candidate_key_rows: dict[tuple[Any, ...], list[tuple[pd.Series, tuple[Any, ...]]]] = defaultdict(list)

    for _, row in finance.iterrows():
        source_signature = tuple(_comparison_value(row.get(column)) for column in source_columns)
        if source_signature in exact_seen:
            exceptions.append(
                _make_exception(
                    "EXACT_DUPLICATE",
                    "Finance source row exactly repeats a prior complete source record; "
                    "both rows remain present.",
                    row=row,
                    kind="finance",
                    severity="WARNING",
                )
            )
        else:
            exact_seen.add(source_signature)

        if not all(column in finance.columns for column in FINANCE_CANDIDATE_KEY_COLUMNS):
            continue
        key_values = [row.get(column) for column in FINANCE_CANDIDATE_KEY_COLUMNS]
        if any(_is_blank(value) for value in key_values):
            continue
        key = tuple(_comparison_value(value) for value in key_values)
        candidate_key_rows[key].append((row, source_signature))

    for rows in candidate_key_rows.values():
        if len({signature for _, signature in rows}) < 2:
            continue
        for row, _ in rows:
            exceptions.append(
                _make_exception(
                    "NON_UNIQUE_FINANCE_KEY",
                    "Different Finance source records share candidate key "
                    "(Customer WD, Market Code); no record was selected.",
                    row=row,
                    kind="finance",
                    severity="WARNING",
                )
            )

    return exceptions


def validate_devops(devops: pd.DataFrame) -> list[ExceptionRecord]:
    """Return DevOps schema and row-level issues without modifying source rows."""
    exceptions = _schema_exceptions(devops, DEVOPS_REQUIRED_COLUMNS, "DevOps")
    if devops.empty:
        return exceptions

    for _, row in devops.iterrows():
        for column in ("Client", "Market Code"):
            if column in devops.columns and _is_blank(row.get(column)):
                exceptions.append(
                    _make_exception(
                        "MISSING_REQUIRED_IDENTIFIER",
                        f"DevOps required identifier {column!r} is missing.",
                        row=row,
                        kind="devops",
                    )
                )

        for column in ("Market Name", "Market Description"):
            if column in devops.columns and _is_blank(row.get(column)):
                exceptions.append(
                    _make_exception(
                        "MISSING_REQUIRED_VALUE",
                        f"DevOps required value {column!r} is missing.",
                        row=row,
                        kind="devops",
                    )
                )

        if "SLA Breach %" in devops.columns:
            breach = row.get("SLA Breach %")
            numeric_breach = _as_finite_number(breach)
            if numeric_breach is None:
                description = f"SLA Breach % {breach!r} is missing or not a finite number."
            elif not 0 <= numeric_breach <= 1:
                description = (
                    f"SLA Breach % {breach!r} is outside the source decimal range [0, 1]."
                )
            else:
                description = ""
            if description:
                exceptions.append(
                    _make_exception(
                        "INVALID_SLA_BREACH",
                        description,
                        row=row,
                        kind="devops",
                    )
                )

        if "filename_client_hint" in devops.columns and "Client" in devops.columns:
            filename_client = row.get("filename_client_hint")
            workbook_client = row.get("Client")
            if not _is_blank(filename_client) and not _is_blank(workbook_client):
                if str(filename_client).strip().casefold() != str(workbook_client).strip().casefold():
                    exceptions.append(
                        _make_exception(
                            "CLIENT_CONFLICT",
                            f"Filename client {filename_client!r} conflicts with "
                            f"workbook Client {workbook_client!r}.",
                            row=row,
                            kind="devops",
                        )
                    )

        if "filename_month_hint" in devops.columns and "workbook_month_hint" in devops.columns:
            filename_month = _month_key(row.get("filename_month_hint"))
            workbook_month = _month_key(row.get("workbook_month_hint"))
            if (
                filename_month is not None
                and workbook_month is not None
                and filename_month != workbook_month
            ):
                exceptions.append(
                    _make_exception(
                        "MONTH_CONFLICT",
                        f"Filename month {row.get('filename_month_hint')!r} conflicts "
                        f"with workbook month {row.get('workbook_month_hint')!r}; "
                        "neither value was selected.",
                        row=row,
                        kind="devops",
                    )
                )

    return exceptions


def validate_inputs(finance: pd.DataFrame, devops: pd.DataFrame) -> ValidationReport:
    """Validate all loaded sources and return counts plus structured exceptions."""
    exceptions = validate_finance(finance)
    exceptions.extend(validate_devops(devops))
    return ValidationReport(
        finance_records_validated=len(finance),
        devops_records_validated=len(devops),
        exceptions=exceptions,
    )


__all__ = [
    "DEVOPS_REQUIRED_COLUMNS",
    "FINANCE_CANDIDATE_KEY_COLUMNS",
    "FINANCE_REQUIRED_COLUMNS",
    "ValidationReport",
    "validate_devops",
    "validate_finance",
    "validate_inputs",
]