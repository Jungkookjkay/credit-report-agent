"""Focused tests for deterministic, source-preserving normalization."""

import pandas as pd

from src.normalization import (
	normalize_devops_market_code,
	normalize_devops_records,
	normalize_finance_records,
	normalize_market_code,
	normalize_text,
	resolve_finance_client,
	tokenize_finance_market_code,
)


def test_text_normalization_handles_case_whitespace_and_nulls() -> None:
	assert normalize_market_code("  nymex  ") == "NYMEX"
	assert normalize_text(" New   York\nMarket ") == "NEW YORK MARKET"
	assert normalize_text(None) is None
	assert normalize_text(float("nan")) is None
	assert normalize_text("   ") is None


def test_finance_client_resolution_hierarchy_and_fallback_pattern() -> None:
	parent = resolve_finance_client(" client-002 ", "CLIENT-002-WD-09")
	assert parent.resolved_client == "CLIENT-002"
	assert parent.client_resolution_method == "ULTIMATE_PARENT"

	fallback = resolve_finance_client(" ", "CLIENT-002-WD-01")
	assert fallback.resolved_client == "CLIENT-002"
	assert fallback.client_resolution_method == "CUSTOMER_WD_FALLBACK"

	malformed = resolve_finance_client(None, "CLIENT-002-WD-01-extra")
	assert malformed.resolved_client is None
	assert malformed.client_resolution_method is None


def test_client002_prefix_is_explicit_scoped_and_leading_only() -> None:
	assert normalize_devops_market_code("client002_lifams", "CLIENT-002") == (
		"LIFAMS",
		True,
	)
	assert normalize_devops_market_code("client002_par", "CLIENT-002") == (
		"PAR",
		True,
	)
	assert normalize_devops_market_code("client002_lifpar", "CLIENT-002") == (
		"LIFPAR",
		True,
	)
	assert normalize_devops_market_code("client002_xetra", "CLIENT-002") == (
		"XETRA",
		True,
	)
	assert normalize_devops_market_code("lifams", "CLIENT-002") == (
		"LIFAMS",
		False,
	)
	assert normalize_devops_market_code("other_client002_lifams", "CLIENT-002") == (
		"OTHER_CLIENT002_LIFAMS",
		False,
	)
	assert normalize_devops_market_code("client003_lifams", "CLIENT-003") == (
		"CLIENT003_LIFAMS",
		False,
	)


def test_finance_market_code_tokenization_uses_observed_delimiters() -> None:
	newline_codes = tokenize_finance_market_code("LIFAMS\nLIFBRU\nLIFPAR\nOSLD")
	assert newline_codes.normalized_tokens == ("LIFAMS", "LIFBRU", "LIFPAR", "OSLD")
	assert newline_codes.tokenization_safe

	comma_codes = tokenize_finance_market_code("BATSEU, CHIXEU")
	assert comma_codes.normalized_tokens == ("BATSEU", "CHIXEU")
	assert comma_codes.tokenization_safe

	semicolon_codes = tokenize_finance_market_code("AMS; BRU; LIS; PAR")
	assert semicolon_codes.normalized_tokens == ("AMS", "BRU", "LIS", "PAR")


def test_supported_composite_delimiters_split_exact_market_tokens() -> None:
	slash_codes = tokenize_finance_market_code("CME\nCBOT\nCOMEX/NYMEX\nCMEC")
	assert slash_codes.normalized_tokens == ("CME", "CBOT", "COMEX", "NYMEX", "CMEC")
	assert slash_codes.tokenization_safe

	qualified_code = tokenize_finance_market_code("CME, NYMEX (incl. COMEX)")
	assert qualified_code.normalized_tokens == ("CME", "NYMEX (INCL. COMEX)")
	assert "COMEX" not in qualified_code.normalized_tokens
	assert not qualified_code.tokenization_safe
	assert "parenthetical qualifier" in (qualified_code.tokenization_note or "")


def test_dataframe_normalization_preserves_raw_finance_fields() -> None:
	finance = pd.DataFrame(
		[
			{
				"Ultimate Parent NHN": "CLIENT-002",
				"Customer WD": "CLIENT-002-WD-09",
				"Market Code": " CME\nCBOT ",
			},
			{
				"Ultimate Parent NHN": "",
				"Customer WD": "CLIENT-002-WD-01",
				"Market Code": "LIFAMS\nLIFBRU",
			},
			{
				"Ultimate Parent NHN": None,
				"Customer WD": "unrecognized-value",
				"Market Code": "LIFAMS",
			},
		]
	)
	original = finance.copy(deep=True)

	normalized = normalize_finance_records(finance)

	pd.testing.assert_frame_equal(finance, original)
	assert normalized["Ultimate Parent NHN"].tolist() == [
		"CLIENT-002",
		"",
		None,
	]
	assert normalized["Customer WD"].tolist() == [
		"CLIENT-002-WD-09",
		"CLIENT-002-WD-01",
		"unrecognized-value",
	]
	assert normalized["resolved_client"].tolist() == [
		"CLIENT-002",
		"CLIENT-002",
		None,
	]
	assert normalized["client_resolution_method"].tolist() == [
		"ULTIMATE_PARENT",
		"CUSTOMER_WD_FALLBACK",
		None,
	]
	assert normalized["normalized_market_tokens"].tolist() == [
		["CME", "CBOT"],
		["LIFAMS", "LIFBRU"],
		["LIFAMS"],
	]


def test_dataframe_devops_normalization_preserves_source_values() -> None:
	devops = pd.DataFrame(
		{
			"Client": ["CLIENT-002", "CLIENT-001"],
			"Market Code": ["client002_lifams", "nymex"],
		}
	)
	original = devops.copy(deep=True)

	normalized = normalize_devops_records(devops)

	pd.testing.assert_frame_equal(devops, original)
	assert normalized["Client"].tolist() == ["CLIENT-002", "CLIENT-001"]
	assert normalized["Market Code"].tolist() == ["client002_lifams", "nymex"]
	assert normalized["normalized_client"].tolist() == ["CLIENT-002", "CLIENT-001"]
	assert normalized["normalized_market_code"].tolist() == ["LIFAMS", "NYMEX"]
	assert normalized["client002_prefix_removed"].tolist() == [True, False]
