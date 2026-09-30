"""Tests for the local read-only deterministic tool adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal

from src.agent_tools import (
	RECORD_ID_PREFIX,
	ReadOnlyCreditTools,
	deterministic_record_id,
)
from src.models import CreditResult, FinanceRecord


def _result(
	*,
	client: str = "CLIENT-001",
	row: int = 1,
	month: str | None = "2026-04",
	month_status: str = "RESOLVED",
	market: str = "mx",
	breach: object = Decimal("0.05"),
	match_status: str = "MATCHED",
	confidence: str | None = "HIGH",
	method: str | None = "EXACT_CODE_MATCH",
	rule: str = "STANDARD_CAPPED",
	base: object = Decimal("10000"),
	currency: str | None = "EUR",
	credit: Decimal | None = Decimal("500.00"),
	status: str = "CALCULATED",
	exception_codes: tuple[str, ...] = (),
	candidates: tuple[FinanceRecord, ...] = (),
	source_file: str | None = None,
) -> CreditResult:
	if source_file is None:
		source_file = (
			"02_Anonymized_DEVOPS_CLIENT-002_MAY26.xlsx"
			if month_status != "RESOLVED"
			else f"{client}_APR26.xlsx"
		)
	return CreditResult(
		client=client,
		devops_market=market,
		normalized_devops_market=market.upper(),
		matched_finance_market="MX" if match_status == "MATCHED" else None,
		customer_wd=f"{client}-WD-01" if match_status == "MATCHED" else None,
		match_status=match_status,
		match_confidence=confidence,
		match_method=method,
		candidate_count=len(candidates),
		candidate_finance_records=candidates,
		candidate_finance_references=tuple(
			f"finance.xlsx:Finance_Fines:{candidate.source_row_number}"
			for candidate in candidates
		),
		sla_breach_pct=breach,
		applied_rate=Decimal("0.05") if rule == "STANDARD_CAPPED" and status == "CALCULATED" else None,
		base_credit=base if match_status == "MATCHED" else None,
		currency=currency,
		calculation_rule=rule,
		calculated_credit=credit,
		status=status,
		exception_code=exception_codes[0] if exception_codes else None,
		exception_codes=exception_codes,
		audit_notes=("Currency is unknown: no unique Finance record matched.",)
		if rule == "CLIENT_004_SPECIAL" and currency is None
		else (),
		reporting_month=month,
		reporting_month_status=month_status,
		filename_month_hint="MAY26" if month_status != "RESOLVED" else "APR26",
		workbook_month_hint="April 2026",
		month_conflict=month_status != "RESOLVED",
		source_file=source_file,
		source_sheet="SLA_Breaches",
		source_row_number=row,
		finance_source_file="finance.xlsx" if match_status == "MATCHED" else None,
		finance_source_sheet="Finance_Fines" if match_status == "MATCHED" else None,
		finance_source_row_number=10 if match_status == "MATCHED" else None,
		processing_run_id="test-run",
	)


def _sample_results() -> list[CreditResult]:
	candidates = (
		FinanceRecord(
			customer_wd="CLIENT-002-WD-01",
			currency="EUR",
			raw_market_code="MX",
			base_credit=Decimal("1200"),
			source_file="finance.xlsx",
			source_sheet="Finance_Fines",
			source_row_number=21,
		),
		FinanceRecord(
			customer_wd="CLIENT-002-WD-02",
			currency="USD",
			raw_market_code="MX",
			base_credit=Decimal("1400"),
			source_file="finance.xlsx",
			source_sheet="Finance_Fines",
			source_row_number=22,
		),
	)
	return [
		_result(row=1, credit=Decimal("500.00")),
		_result(
			client="CLIENT-001",
			row=2,
			market="zro",
			breach=Decimal("0"),
			base=Decimal("2000"),
			credit=Decimal("0"),
			status="ZERO_CREDIT",
		),
		_result(
			client="CLIENT-002",
			row=3,
			market="mx",
			match_status="AMBIGUOUS",
			confidence="LOW",
			method=None,
			base=None,
			currency=None,
			credit=None,
			status="EXCEPTION",
			exception_codes=("AMBIGUOUS_MATCH",),
			candidates=candidates,
		),
		_result(
			client="CLIENT-002",
			row=4,
			market="chixca",
			match_status="UNMATCHED",
			confidence="LOW",
			method=None,
			base=None,
			currency=None,
			credit=None,
			status="EXCEPTION",
			exception_codes=("NO_FINANCE_MATCH",),
		),
		_result(
			client="CLIENT-004",
			row=5,
			market="mx",
			breach=Decimal("0.1301"),
			match_status="UNMATCHED",
			confidence="LOW",
			method=None,
			rule="CLIENT_004_SPECIAL",
			base=None,
			currency=None,
			credit=Decimal("7686.3950807071483474250576479631053036126056879324"),
			exception_codes=("NO_FINANCE_MATCH", "CURRENCY_UNKNOWN"),
		),
		_result(
			client="CLIENT-002",
			row=5,
			market="nymex",
			month=None,
			month_status="UNRESOLVED",
			match_status="INELIGIBLE",
			confidence=None,
			method=None,
			rule="STANDARD_CAPPED",
			base=None,
			currency=None,
			credit=None,
			status="INELIGIBLE",
			exception_codes=("MONTH_CONFLICT",),
		),
	]


def test_client_credit_returns_phase9_details_and_exact_decimal_strings() -> None:
	tools = ReadOnlyCreditTools(_sample_results())
	response = tools.get_client_credit("CLIENT-001", "2026-04")

	assert response["status"] == "OK"
	assert response["data"]["record_count"] == 2
	assert response["data"]["records"][0]["calculated_credit"] == "500.00"
	assert response["data"]["records"][0]["base_credit"] == "10000"
	assert response["data"]["records"][0]["sla_breach"] == "0.05"
	assert response["data"]["records"][0]["applied_rate"] == "0.05"
	assert response["data"]["records"][1]["calculated_credit"] == "0"
	assert response["data"]["records"][1]["calculation_status"] == "ZERO_CREDIT"
	assert json.loads(json.dumps(response))["data"]["records"][0]["currency"] == "EUR"


def test_ambiguous_standard_market_remains_low_with_null_credit_and_candidates() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_client_credit(
		"CLIENT-002", "2026-04"
	)
	ambiguous = next(
		record for record in response["data"]["records"]
		if record["match_status"] == "AMBIGUOUS"
	)

	assert ambiguous["match_confidence"] == "LOW"
	assert ambiguous["calculated_credit"] is None
	assert ambiguous["candidate_count"] == 2
	assert [candidate["customer_wd"] for candidate in ambiguous["candidate_finance_records"]] == [
		"CLIENT-002-WD-01",
		"CLIENT-002-WD-02",
	]


def test_client004_low_unmatched_calculated_and_unknown_currency_are_preserved() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_client_credit(
		"CLIENT-004", "2026-04"
	)
	record = response["data"]["records"][0]

	assert record["match_status"] == "UNMATCHED"
	assert record["match_confidence"] == "LOW"
	assert record["calculation_status"] == "CALCULATED"
	assert record["calculated_credit"] == "7686.3950807071483474250576479631053036126056879324"
	assert record["currency"] is None
	assert record["base_credit"] is None
	assert record["source_lineage"]["devops"]["row"] == 5
	assert record["source_lineage"]["finance"] is None


def test_month_conflict_is_pending_unresolved_not_assigned_may_or_april() -> None:
	tools = ReadOnlyCreditTools(_sample_results())
	response = tools.get_client_credit("CLIENT-002", "2026-05")

	assert response["status"] == "OK"
	assert response["data"]["records"] == []
	assert len(response["data"]["pending_or_unresolved"]) == 1
	pending = response["data"]["pending_or_unresolved"][0]
	assert pending["reporting_month"] is None
	assert pending["reporting_month_status"] == "UNRESOLVED"
	assert pending["match_status"] == "INELIGIBLE"
	assert pending["match_confidence"] is None
	assert pending["calculated_credit"] is None
	assert pending["exception_codes"] == ["MONTH_CONFLICT"]


def test_client_exceptions_preserve_occurrences_candidates_and_unique_count() -> None:
	tools = ReadOnlyCreditTools(_sample_results())
	response = tools.get_client_exceptions("CLIENT-002", "2026-04")

	assert response["status"] == "OK"
	assert response["data"]["unique_affected_record_count"] == 2
	assert response["data"]["exception_code_occurrence_count"] == 2
	ambiguous = next(
		item for item in response["data"]["exceptions"]
		if item["exception_code"] == "AMBIGUOUS_MATCH"
	)
	assert ambiguous["match_status"] == "AMBIGUOUS"
	assert ambiguous["match_confidence"] == "LOW"
	assert len(ambiguous["candidate_finance_records"]) == 2


def test_multiple_exception_codes_remain_two_occurrences_on_one_record() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_client_exceptions(
		"CLIENT-004", "2026-04"
	)

	assert response["data"]["unique_affected_record_count"] == 1
	assert response["data"]["exception_code_occurrence_count"] == 2
	assert {row["exception_code"] for row in response["data"]["exceptions"]} == {
		"NO_FINANCE_MATCH",
		"CURRENCY_UNKNOWN",
	}


def test_client_exceptions_returns_unresolved_month_conflict_by_filename_hint() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_client_exceptions(
		"CLIENT-002", "2026-05"
	)

	assert response["status"] == "OK"
	assert response["data"]["unique_affected_record_count"] == 1
	assert response["data"]["exceptions"][0]["reporting_month"] is None
	assert response["data"]["exceptions"][0]["exception_code"] == "MONTH_CONFLICT"
	assert response["data"]["exceptions"][0]["match_confidence"] is None


def test_executive_summary_uses_phase9_builder_and_keeps_currency_groups_separate() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_executive_summary("2026-04")
	groups = response["data"]["groups"]
	group_keys = {(group["Client"], group["Currency"]) for group in groups}

	assert response["status"] == "OK"
	assert response["human_review_required"] is True
	assert ("CLIENT-001", "EUR") in group_keys
	assert ("CLIENT-004", "UNKNOWN / UNSPECIFIED CURRENCY") in group_keys
	assert all(group["Currency"] != "USD" for group in groups)
	assert isinstance(
		next(group for group in groups if group["Client"] == "CLIENT-004")["Total Calculated Credit"],
		str,
	)


def test_executive_summary_keeps_may_conflicts_out_of_authoritative_groups() -> None:
	response = ReadOnlyCreditTools(_sample_results()).get_executive_summary("2026-05")

	assert response["status"] == "OK"
	assert response["data"]["groups"] == []
	assert response["data"]["pending_or_unresolved_record_count"] == 1
	assert response["data"]["pending_or_unresolved"][0]["reporting_month"] is None


def test_credit_record_id_is_stable_from_source_lineage_and_lookup_returns_context() -> None:
	result = _sample_results()[0]
	tools = ReadOnlyCreditTools([result])
	record_id = deterministic_record_id(result)
	copy_with_new_run = deepcopy(result)
	copy_with_new_run.processing_run_id = "another-run"

	assert record_id == deterministic_record_id(copy_with_new_run)
	assert record_id == tools.get_client_credit("CLIENT-001", "2026-04")["data"]["records"][0]["record_id"]
	response = tools.get_credit_record(record_id)
	assert response["status"] == "OK"
	assert response["data"]["calculated_credit"] == "500.00"
	assert "HIGH-confidence" in response["data"]["explanation"]


def test_missing_lineage_has_no_invented_record_id() -> None:
	result = _result(source_file=None)
	result.source_sheet = None
	result.source_row_number = None

	assert deterministic_record_id(result) is None
	response = ReadOnlyCreditTools([result]).get_client_credit("CLIENT-001", "2026-04")
	assert response["status"] == "OK"
	assert response["data"]["records"][0]["record_id"] is None


def test_duplicate_source_identity_returns_ambiguous_record_lookup() -> None:
	first = _sample_results()[0]
	second = deepcopy(first)
	second.devops_market = "different-market"
	tools = ReadOnlyCreditTools([first, second])
	record_id = deterministic_record_id(first)

	response = tools.get_credit_record(record_id)

	assert response["status"] == "INVALID_REQUEST"
	assert response["error"]["code"] == "AMBIGUOUS_RECORD_LOOKUP"
	assert response["data"] is None


def test_unknown_client_month_record_invalid_request_and_unavailable_results() -> None:
	tools = ReadOnlyCreditTools(_sample_results())
	assert tools.get_client_credit("CLIENT-999", "2026-04")["error"]["code"] == "UNKNOWN_CLIENT"
	assert tools.get_client_credit("CLIENT-001", "2099-12")["error"]["code"] == "UNKNOWN_MONTH"
	assert tools.get_client_credit(" ", "2026-04")["error"]["code"] == "INVALID_CLIENT"
	assert tools.get_client_credit("CLIENT-001", "April")["error"]["code"] == "INVALID_MONTH"
	assert tools.get_credit_record(RECORD_ID_PREFIX + "0" * 64)["error"]["code"] == "UNKNOWN_RECORD_ID"
	assert ReadOnlyCreditTools(None).get_executive_summary("2026-04")["error"]["code"] == "DETERMINISTIC_RESULTS_UNAVAILABLE"


def test_tools_do_not_mutate_underlying_credit_results_and_are_json_serializable() -> None:
	results = _sample_results()
	original = deepcopy(results)
	tools = ReadOnlyCreditTools(results)
	responses = [
		tools.get_client_credit("CLIENT-001", "2026-04"),
		tools.get_client_exceptions("CLIENT-002", "2026-04"),
		tools.get_executive_summary("2026-04"),
		tools.get_credit_record(deterministic_record_id(results[0])),
	]

	serialized = [json.dumps(response, allow_nan=False) for response in responses]

	assert len(serialized) == 4
	assert results == original


def test_source_float_money_values_are_serialized_as_decimal_strings() -> None:
	result = _result(
		row=9,
		base=1510.7065,
		breach=0.0527,
		credit=Decimal("79.61423255"),
	)
	result.candidate_finance_records = (
		FinanceRecord(
			customer_wd="CLIENT-001-WD-02",
			currency="EUR",
			raw_market_code="CME",
			base_credit=1510.7065,
		),
	)
	tools = ReadOnlyCreditTools([result])
	response = tools.get_client_credit("CLIENT-001", "2026-04")
	credit = response["data"]["records"][0]

	assert credit["base_credit"] == "1510.7065"
	assert credit["calculated_credit"] == "79.61423255"
	assert credit["candidate_finance_records"][0]["base_credit"] == "1510.7065"
	assert json.loads(json.dumps(response))["data"]["records"][0]["sla_breach"] == "0.0527"
