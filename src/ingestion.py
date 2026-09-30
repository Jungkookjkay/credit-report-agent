"""Phase 2 ingestion layer.

This module intentionally does only raw file discovery and loading. It preserves
source data and lineage metadata without normalizing or resolving business
inconsistencies.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd


def _normalize_path(path: str | Path) -> Path:
    """Return a resolved Path object for the given input."""
    return Path(path).expanduser().resolve()


def _extract_filename_client_hint(path: Path) -> str | None:
    """Extract a client hint from filename metadata when present."""
    match = re.search(r"CLIENT[-_]?\d+", path.name, flags=re.IGNORECASE)
    if match:
        return match.group(0).upper()
    return None


def _extract_filename_month_hint(path: Path) -> str | None:
    """Extract month-like metadata from filename when present.

    Examples: APR26, MAY26, APR-26, MAY-2026.
    """
    name = path.name.upper()
    match = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[-_]?(\d{2,4})", name)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return None


def _extract_workbook_month_hint(df: pd.DataFrame) -> str | None:
    """Look for month-like strings in workbook values without changing source data."""
    for col in df.columns:
        if str(col).lower() == "market description":
            values = df[col].dropna().astype(str)
            for value in values:
                cleaned = value.strip()
                if not cleaned:
                    continue
                match = re.search(
                    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                if match:
                    return match.group(0)
                match = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[-_]?\d{2,4}", cleaned, flags=re.IGNORECASE)
                if match:
                    return match.group(0).upper()
    return None


def _month_key(value: str) -> tuple[int, int] | None:
    """Convert abbreviated or full month-year text to a comparable key."""
    match = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[-_ ]?(\d{2,4})",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month = datetime.strptime(match.group(1)[:3].title(), "%b").month
    year = int(match.group(2))
    if year < 100:
        year += 2000
    return month, year


def _add_lineage_columns(df: pd.DataFrame, source_file: str, source_sheet: str, filename_client_hint: str | None, filename_month_hint: str | None) -> pd.DataFrame:
    """Add lineage and filename-derived metadata without disturbing original columns."""
    result = df.copy()
    result["source_file"] = source_file
    result["source_sheet"] = source_sheet
    result["source_row_number"] = range(1, len(result) + 1)
    result["filename_client_hint"] = filename_client_hint
    result["filename_month_hint"] = filename_month_hint
    result["workbook_month_hint"] = _extract_workbook_month_hint(result)
    result["month_conflict"] = False
    if filename_month_hint and result["workbook_month_hint"].notna().any():
        filename_month = _month_key(filename_month_hint)
        workbook_month = _month_key(str(result["workbook_month_hint"].dropna().iloc[0]))
        result["month_conflict"] = (
            filename_month is not None
            and workbook_month is not None
            and filename_month != workbook_month
        )
    return result


def load_finance_file(file_path: str | Path) -> pd.DataFrame:
    """Load the finance workbook and preserve source columns + lineage metadata.

    The function deliberately preserves the original workbook values and does not
    remove duplicates, normalize market codes, or resolve any metadata conflict.
    """
    path = _normalize_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Finance file not found: {path}")

    workbook = pd.ExcelFile(path)
    sheet_names = workbook.sheet_names
    if not sheet_names:
        raise ValueError(f"No sheets found in finance file: {path}")

    selected_sheet = next(
        (name for name in sheet_names if "finance" in name.lower() or "fines" in name.lower()),
        sheet_names[0],
    )
    df = pd.read_excel(path, sheet_name=selected_sheet)
    filename_client_hint = _extract_filename_client_hint(path)
    filename_month_hint = _extract_filename_month_hint(path)
    result = _add_lineage_columns(
        df,
        source_file=path.name,
        source_sheet=selected_sheet,
        filename_client_hint=filename_client_hint,
        filename_month_hint=filename_month_hint,
    )
    return result


def discover_devops_files(directory: str | Path) -> list[Path]:
    """Discover DevOps workbook files without hardcoding any client or count."""
    base_dir = _normalize_path(directory)
    if not base_dir.exists():
        raise FileNotFoundError(f"DevOps directory not found: {base_dir}")

    candidates: list[Path] = []
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".xlsx", ".xls", ".xlsm"}:
            continue
        name = path.name.upper()
        if "FINANCE" in name:
            continue
        if "DEVOPS" in name or "CLIENT" in name or "SLA" in name:
            candidates.append(path)
    return candidates


def load_devops_file(file_path: str | Path) -> pd.DataFrame:
    """Load a single DevOps workbook and attach raw lineage metadata.

    The function preserves the original columns and source values exactly as read
    from the workbook. It also captures filename-derived hints and workbook-derived
    month hints for later validation. No normalization, matching, or filtering is
    applied here.
    """
    path = _normalize_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"DevOps file not found: {path}")

    workbook = pd.ExcelFile(path)
    sheet_names = workbook.sheet_names
    if not sheet_names:
        raise ValueError(f"No sheets found in DevOps file: {path}")

    selected_sheet = next(
        (name for name in sheet_names if "sla" in name.lower() or "breach" in name.lower()),
        sheet_names[0],
    )
    df = pd.read_excel(path, sheet_name=selected_sheet)
    filename_client_hint = _extract_filename_client_hint(path)
    filename_month_hint = _extract_filename_month_hint(path)
    result = _add_lineage_columns(
        df,
        source_file=path.name,
        source_sheet=selected_sheet,
        filename_client_hint=filename_client_hint,
        filename_month_hint=filename_month_hint,
    )
    return result


def load_all_devops_files(directory: str | Path) -> pd.DataFrame:
    """Load all discovered DevOps workbooks into a single raw dataset."""
    files = discover_devops_files(directory)
    if not files:
        raise FileNotFoundError(f"No DevOps files found in directory: {directory}")

    frames = [load_devops_file(path) for path in files]
    return pd.concat(frames, ignore_index=True, sort=False)


__all__ = [
    "discover_devops_files",
    "load_finance_file",
    "load_devops_file",
    "load_all_devops_files",
]
