"""Focused tests for deterministic Phase 6 Finance matching."""

from __future__ import annotations

import pandas as pd

from src.matching import match_devops_records
from src.normalization import normalize_devops_records, normalize_finance_records


def _finance_row(
    market_code: str,
    *,
    parent: str | None = "CLIENT-001",
    customer: str = "CLIENT-001-WD-01",
    credit: float = 100.0,
    currency: str = "USD",
    source_row: int = 1,
) -> dict:
    return {
        "Ultimate Parent NHN": parent,
        "Customer WD": customer,
        "Currency": currency,
        "Market Line Description": f"Line {source_row}",
        "Market Name": f"Market {source_row}",
        "Market Code": market_code,
        "Monthly LCY 04/30/26": credit,
        "source_file": "finance.xlsx",
        "source_sheet": "Finance_Fines",
        "source_row_number": source_row,
    }


def _devops_row(
    market_code: str,
    *,
    client: str = "CLIENT-001",
    source_file: str = "devops.xlsx",
    source_row: int = 1,
    month_conflict: bool = False,
) -> dict:
    return {
        "Client": client,
        "Market Code": market_code,
        "Market Name": market_code,
        "Market Description": "April 2026 SLA market record",
        "SLA Breach %": 0.1,
        "source_file": source_file,
        "source_sheet": "SLA_Breaches",
        "source_row_number": source_row,
        "filename_client_hint": client,
        "filename_month_hint": "MAY26" if month_conflict else "APR26",
        "workbook_month_hint": "April 2026",
        "month_conflict": month_conflict,
    }


def _match(devops_rows: list[dict], finance_rows: list[dict]) -> pd.DataFrame:
    devops = normalize_devops_records(pd.DataFrame(devops_rows))
    finance = normalize_finance_records(pd.DataFrame(finance_rows))
    return match_devops_records(devops, finance)


def test_unique_exact_match_is_high_confidence() -> None:
    result = _match([_devops_row("nymex")], [_finance_row("NYMEX")]).iloc[0]

    assert result["matching_status"] == "MATCHED"
    assert result["match_confidence"] == "HIGH"
    assert result["match_method"] == "EXACT_CODE_MATCH"
    assert result["candidate_count"] == 1
    assert result["matched_finance_reference"] == "finance.xlsx:Finance_Fines:1"
    assert result["match_confidence"] in {"HIGH", "MEDIUM", "LOW"}


def test_case_and_whitespace_differences_still_produce_high_exact_match() -> None:
    result = _match(
        [_devops_row("  nYmEx  ")],
        [_finance_row(" NYMEX  ")],
    ).iloc[0]

    assert result["normalized_market_code"] == "NYMEX"
    assert result["matching_status"] == "MATCHED"
    assert result["match_confidence"] == "HIGH"
    assert result["match_method"] == "EXACT_CODE_MATCH"


def test_unique_composite_token_match_is_high_confidence() -> None:
    result = _match(
        [_devops_row("lifams")],
        [_finance_row("LIFAMS\nLIFBRU\nLIFPAR\nOSLD")],
    ).iloc[0]

    assert result["matching_status"] == "MATCHED"
    assert result["match_confidence"] == "HIGH"
    assert result["match_method"] == "COMPOSITE_CODE_MATCH"
    assert result["candidate_count"] == 1


def test_client002_prefix_leads_to_scoped_unique_candidate() -> None:
    result = _match(
        [_devops_row("client002_lifams", client="CLIENT-002")],
        [
            _finance_row(
                "LIFAMS\nLIFBRU",
                parent="CLIENT-002",
                customer="CLIENT-002-WD-01",
            )
        ],
    ).iloc[0]

    assert result["normalized_market_code"] == "LIFAMS"
    assert result["matching_status"] == "MATCHED"
    assert result["match_method"] == "COMPOSITE_CODE_MATCH"
    assert "prefix normalization was applied" in result["matching_explanation"]


def test_same_market_for_another_client_does_not_interfere() -> None:
    result = _match(
        [_devops_row("NYSE", client="CLIENT-001")],
        [_finance_row("NYSE", parent="CLIENT-002", customer="CLIENT-002-WD-01")],
    ).iloc[0]

    assert result["matching_status"] == "UNMATCHED"
    assert result["exception_code"] == "NO_FINANCE_MATCH"
    assert result["candidate_count"] == 0
    assert result["matched_finance_record"] is None


def test_zero_candidates_are_unmatched() -> None:
    result = _match([_devops_row("NOPE")], [_finance_row("OTHER")]).iloc[0]

    assert result["matching_status"] == "UNMATCHED"
    assert result["match_confidence"] == "LOW"
    assert result["exception_code"] == "NO_FINANCE_MATCH"
    assert result["candidate_count"] == 0


def test_multiple_distinct_candidates_are_ambiguous() -> None:
    result = _match(
        [_devops_row("HKFE")],
        [
            _finance_row("HKFE", customer="CLIENT-001-WD-01", credit=100, source_row=1),
            _finance_row("HKFE", customer="CLIENT-001-WD-02", credit=250, source_row=2),
        ],
    ).iloc[0]

    assert result["matching_status"] == "AMBIGUOUS"
    assert result["match_confidence"] == "LOW"
    assert result["exception_code"] == "AMBIGUOUS_MATCH"
    assert result["candidate_count"] == 2
    assert result["matched_finance_record"] is None
    assert len(result["candidate_finance_references"]) == 2


def test_multiple_composite_candidates_are_ambiguous_without_first_candidate_selection() -> None:
    result = _match(
        [_devops_row("MX")],
        [
            _finance_row("MX\nMXX", customer="CLIENT-001-WD-01", source_row=1),
            _finance_row("MX\nMKT", customer="CLIENT-001-WD-02", source_row=2),
        ],
    ).iloc[0]

    assert result["matching_status"] == "AMBIGUOUS"
    assert result["match_confidence"] == "LOW"
    assert result["match_confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert result["exception_code"] == "AMBIGUOUS_MATCH"
    assert result["matched_finance_record"] is None
    assert result["matched_finance_records"] == ()
    assert result["candidate_count"] == 2
    assert len(result["candidate_finance_records"]) == 2


def test_distinct_duplicate_valued_finance_rows_remain_ambiguous_and_keep_lineage() -> None:
    duplicate_a = _finance_row("CME", source_row=1)
    duplicate_b = {**duplicate_a, "source_row_number": 2}
    result = _match([_devops_row("CME")], [duplicate_a, duplicate_b]).iloc[0]

    assert result["matching_status"] == "AMBIGUOUS"
    assert result["match_confidence"] == "LOW"
    assert result["exception_code"] == "AMBIGUOUS_MATCH"
    assert result["candidate_count"] == 2
    assert result["matched_finance_record"] is None
    assert result["matched_finance_reference"] is None
    assert result["candidate_finance_references"] == (
        "finance.xlsx:Finance_Fines:1",
        "finance.xlsx:Finance_Fines:2",
    )


def test_supported_composite_tokens_match_individually_at_high_confidence() -> None:
    composite = "CME\nCBOT\nCOMEX/NYMEX\nCMEC"
    results = _match(
        [_devops_row(code) for code in ("CME", "CBOT", "COMEX", "NYMEX")],
        [_finance_row(composite)],
    )

    assert results["matching_status"].tolist() == ["MATCHED"] * 4
    assert results["match_confidence"].tolist() == ["HIGH"] * 4
    assert results["match_method"].tolist() == ["COMPOSITE_CODE_MATCH"] * 4
    assert results["candidate_count"].tolist() == [1] * 4


def test_market_code_requires_exact_token_equality_not_substring() -> None:
    result = _match([_devops_row("CME")], [_finance_row("CMEC")]).iloc[0]

    assert result["matching_status"] == "UNMATCHED"
    assert result["exception_code"] == "NO_FINANCE_MATCH"
    assert result["candidate_count"] == 0


def test_client001_tse_remains_ambiguous_with_two_scoped_records() -> None:
    result = _match(
        [_devops_row("TSE", client="CLIENT-001")],
        [
            _finance_row("TSE", parent="CLIENT-001", source_row=1),
            _finance_row("TSE", parent="CLIENT-001", source_row=2),
            _finance_row("TSE", parent="CLIENT-002", customer="CLIENT-002-WD-01", source_row=3),
        ],
    ).iloc[0]

    assert result["matching_status"] == "AMBIGUOUS"
    assert result["match_confidence"] == "LOW"
    assert result["exception_code"] == "AMBIGUOUS_MATCH"
    assert result["candidate_count"] == 2
    assert result["candidate_finance_references"] == (
        "finance.xlsx:Finance_Fines:1",
        "finance.xlsx:Finance_Fines:2",
    )


def test_atomic_and_composite_records_are_both_counted_for_ambiguity() -> None:
    result = _match(
        [_devops_row("TSE")],
        [
            _finance_row("TSE", source_row=1),
            _finance_row("TSE\nNYSE", source_row=2),
        ],
    ).iloc[0]

    assert result["matching_status"] == "AMBIGUOUS"
    assert result["match_confidence"] == "LOW"
    assert result["candidate_count"] == 2


def test_customer_wd_fallback_supports_client_scoped_match() -> None:
    result = _match(
        [_devops_row("LIFAMS", client="CLIENT-002")],
        [
            _finance_row(
                "LIFAMS",
                parent="",
                customer="CLIENT-002-WD-09",
            )
        ],
    ).iloc[0]

    assert result["matching_status"] == "MATCHED"
    assert result["candidate_count"] == 1
    matched = result["matched_finance_record"]
    assert matched is not None
    assert matched.ultimate_parent_nhn == ""
    assert matched.customer_wd == "CLIENT-002-WD-09"


def test_month_conflict_is_ineligible_and_never_gets_a_match() -> None:
    result = _match(
        [
            _devops_row(
                "CME",
                client="CLIENT-001",
                source_file="02_Anonymized_DEVOPS_CLIENT-002_MAY26.xlsx",
                month_conflict=True,
            )
        ],
        [_finance_row("CME")],
    ).iloc[0]

    assert result["matching_status"] == "INELIGIBLE"
    assert result["exception_code"] == "MONTH_CONFLICT"
    assert result["match_confidence"] is None
    assert result["candidate_count"] == 0
    assert result["matched_finance_record"] is None


def test_null_month_conflict_flag_does_not_block_matching() -> None:
    row = _devops_row("CME")
    row["month_conflict"] = pd.NA
    result = _match([row], [_finance_row("CME")]).iloc[0]

    assert result["matching_status"] == "MATCHED"


def test_client004_finance_match_does_not_calculate_credit() -> None:
    result = _match(
        [_devops_row("HKEX", client="CLIENT-004")],
        [
            _finance_row(
                "HKEX",
                parent="CLIENT-004",
                customer="CLIENT-004-WD-01",
                source_row=1,
            )
        ],
    ).iloc[0]

    assert result["matching_status"] == "MATCHED"
    assert result["match_method"] == "EXACT_CODE_MATCH"
    assert "calculated_credit" not in result.index