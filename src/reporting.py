"""Deterministic Phase 9 report datasets sourced only from CreditResult objects."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Iterable, Sequence

import pandas as pd

if __package__:
	from .models import CreditResult, ExceptionRecord, FinanceRecord
else:
	from models import CreditResult, ExceptionRecord, FinanceRecord


UNKNOWN_CURRENCY_BUCKET = "UNKNOWN / UNSPECIFIED CURRENCY"
STANDARD_RATE_CAP = Decimal("0.20")
CALCULATED_STATUSES = {"CALCULATED", "ZERO_CREDIT"}


@dataclass(frozen=True)
class ReconciliationReport:
	"""Results of deterministic row-count, exception, and monetary controls."""

	checks: dict[str, bool]
	details: dict[str, Any]

	@property
	def passed(self) -> bool:
		return all(self.checks.values())


def _is_missing(value: Any) -> bool:
	if value is None or (isinstance(value, str) and not value.strip()):
		return True
	try:
		missing = pd.isna(value)
		return bool(missing) if not hasattr(missing, "__len__") else False
	except (TypeError, ValueError):
		return False


def _as_decimal(value: Any) -> Decimal | None:
	if _is_missing(value):
		return None
	try:
		number = Decimal(str(value))
	except Exception:
		return None
	return number if number.is_finite() else None


def _format_decimal(value: Decimal | None) -> str:
	if value is None:
		return "unknown"
	formatted = format(value, "f")
	if "." in formatted:
		formatted = formatted.rstrip("0").rstrip(".")
	return formatted or "0"


def _format_percent(value: Decimal | None) -> str:
	if value is None:
		return "unknown"
	return f"{_format_decimal(value * Decimal('100'))}%"


def _resolved_month(result: CreditResult) -> str | None:
	if result.reporting_month_status != "RESOLVED" or not result.reporting_month:
		return None
	return result.reporting_month


def _currency_bucket(result: CreditResult) -> Any:
	return UNKNOWN_CURRENCY_BUCKET if _is_missing(result.currency) else result.currency


def _exception_codes(result: CreditResult) -> tuple[str, ...]:
	codes = list(result.exception_codes)
	if result.exception_code and result.exception_code not in codes:
		codes.append(result.exception_code)
	for exception in result.source_exceptions:
		if exception.exception_code not in codes:
			codes.append(exception.exception_code)
	if result.reporting_month_status != "RESOLVED":
		if "MONTH_CONFLICT" in codes:
			pass
		elif result.month_conflict:
			codes.append("MONTH_CONFLICT")
		else:
			codes.append("UNRESOLVED_REPORTING_MONTH")
	if any("currency is unknown" in note.lower() for note in result.audit_notes):
		if "CURRENCY_UNKNOWN" not in codes:
			codes.append("CURRENCY_UNKNOWN")
	if result.status in {"EXCEPTION", "INELIGIBLE"} and not codes:
		codes.append("CALCULATION_EXCEPTION")
	return tuple(codes)


def _has_exception(result: CreditResult) -> bool:
	return bool(_exception_codes(result)) or result.status in {"EXCEPTION", "INELIGIBLE"}


def _unique_text(values: Iterable[Any]) -> tuple[str, ...]:
	result = []
	for value in values:
		if _is_missing(value):
			continue
		text = str(value)
		if text not in result:
			result.append(text)
	return tuple(result)


def _join_values(values: Iterable[Any]) -> str | None:
	texts = _unique_text(values)
	return "; ".join(texts) if texts else None


def _candidate_records(result: CreditResult) -> tuple[FinanceRecord, ...]:
	return result.candidate_finance_records


def _source_lineage(result: CreditResult) -> str:
	devops = ":".join(
		str(value)
		for value in (result.source_file, result.source_sheet, result.source_row_number)
		if not _is_missing(value)
	)
	finance = ":".join(
		str(value)
		for value in (
			result.finance_source_file,
			result.finance_source_sheet,
			result.finance_source_row_number,
		)
		if not _is_missing(value)
	)
	if finance:
		return f"DevOps={devops}; Finance={finance}"
	return f"DevOps={devops}"


def explain_credit_result(result: CreditResult) -> str:
	"""Build a stable human-readable explanation from authoritative result fields."""
	codes = _exception_codes(result)
	if result.exception_code == "COMPOSITE_ALLOCATION_AMBIGUITY":
		explanation = result.explanation or (
			"The Finance record contains this market within a composite market field, "
			"but the same Finance Base Credit corresponds to multiple DevOps markets. "
			"No allocation rule is provided, so the Base Credit was not reused and no "
			"authoritative credit was calculated."
		)
	elif result.status == "INELIGIBLE" or "MONTH_CONFLICT" in codes:
		explanation = (
			"Reporting month is unresolved due to MONTH_CONFLICT; the row is "
			"ineligible and excluded from authoritative monthly totals."
		)
	elif result.calculation_rule == "CLIENT_004_SPECIAL":
		explanation = (
			"CLIENT-004 special rule applied using the raw SLA breach value. "
			"Finance matching is not required for this formula."
		)
		if _as_decimal(result.sla_breach_pct) == 0:
			explanation += " Zero breach produces zero credit without division."
		if _is_missing(result.currency):
			explanation += (
				" Currency is not authoritative because no Finance match supplied one."
			)
	elif result.match_status == "AMBIGUOUS" and result.calculated_credit is None:
		explanation = (
			"Multiple Finance records matched this client and market; no authoritative "
			"Base Credit was selected and no standard credit was calculated."
		)
	elif result.match_status == "UNMATCHED" and result.calculated_credit is None:
		explanation = (
			"No Finance market token matched within the correct client scope; "
			"no standard credit was calculated."
		)
	elif result.status == "ZERO_CREDIT":
		breach = _as_decimal(result.sla_breach_pct)
		if breach == 0:
			explanation = "Valid zero-breach result; applied rate and calculated credit are zero."
		else:
			explanation = (
				"Valid calculation returned zero credit from the matched Finance "
				"Base Credit and applied rate."
			)
	elif result.status == "EXCEPTION":
		explanation = result.explanation or (
			f"Calculation was not completed ({result.exception_code or 'unspecified exception'})."
		)
	elif result.calculation_rule == "STANDARD_CAPPED":
		breach = _as_decimal(result.sla_breach_pct)
		applied_rate = _as_decimal(result.applied_rate)
		if breach is not None and applied_rate is not None and breach > applied_rate:
			explanation = (
				f"SLA breach {_format_percent(breach)} exceeded the standard cap; "
				f"applied rate was capped at {_format_percent(applied_rate)}."
			)
		else:
			match_kind = (
				"composite market match"
				if result.match_method == "COMPOSITE_CODE_MATCH"
				else "market match"
			)
			cap_position = (
				"at the 20% cap"
				if breach == STANDARD_RATE_CAP
				else "below the 20% cap"
			)
			currency = "the matched Finance currency" if _is_missing(result.currency) else str(result.currency)
			explanation = (
				f"{result.match_confidence or 'Unknown'}-confidence {match_kind}. "
				f"SLA breach {_format_percent(breach)} was {cap_position}. "
				f"Credit calculated using the matched {currency} Base Credit."
			)
	else:
		explanation = result.explanation or "No calculation explanation is available."

	additional = list(result.audit_notes)
	additional.extend(exception.description for exception in result.source_exceptions)
	for note in additional:
		if note and note not in explanation:
			explanation = f"{explanation} {note}"
	return explanation


def build_detailed_report(results: Sequence[CreditResult]) -> pd.DataFrame:
	"""Build the market-level detail dataset without changing numeric values."""
	rows = []
	for result in results:
		codes = _exception_codes(result)
		candidates = _candidate_records(result)
		rows.append(
			{
				"Reporting Month": _resolved_month(result),
				"Reporting Month Status": result.reporting_month_status or "UNRESOLVED",
				"Filename Month Hint": result.filename_month_hint,
				"Workbook Month Hint": result.workbook_month_hint,
				"Client": result.client,
				"DevOps Market": result.devops_market,
				"Original Market Code": result.devops_market,
				"Normalized DevOps Market": result.normalized_devops_market,
				"Matched Finance Market Code": result.matched_finance_market,
				"Customer WD": result.customer_wd,
				"Match Status": result.match_status,
				"Match Confidence": result.match_confidence,
				"Match Method": result.match_method,
				"Candidate Count": result.candidate_count,
				"SLA Breach %": result.sla_breach_pct,
				"Applied Rate": result.applied_rate,
				"Base Credit": result.base_credit,
				"Currency": result.currency,
				"Reporting Currency Bucket": _currency_bucket(result),
				"Currency Limitation": (
					"Unknown / unspecified currency"
					if _is_missing(result.currency)
					and result.calculation_rule == "CLIENT_004_SPECIAL"
					and result.calculated_credit is not None
					else None
				),
				"Calculation Rule": result.calculation_rule,
				"Calculated Credit": result.calculated_credit,
				"Calculation Status": result.status,
				"Exception Code": result.exception_code,
				"Exception Codes": "; ".join(codes) if codes else None,
				"Explanation / Audit Note": explain_credit_result(result),
				"Candidate Customer WD(s)": _join_values(
					record.customer_wd for record in candidates
				),
				"Candidate Market Code(s)": _join_values(
					record.raw_market_code for record in candidates
				),
				"Candidate Base Credit(s)": _join_values(
					record.base_credit for record in candidates
				),
				"Candidate Currency/Currencies": _join_values(
					record.currency for record in candidates
				),
				"DevOps Source File": result.source_file,
				"DevOps Source Sheet": result.source_sheet,
				"DevOps Source Row": result.source_row_number,
				"Finance Source File": result.finance_source_file,
				"Finance Source Sheet": result.finance_source_sheet,
				"Finance Source Row": result.finance_source_row_number,
				"Processing Run ID": result.processing_run_id,
				"Source Lineage": _source_lineage(result),
			}
		)
	return pd.DataFrame(rows)


def _is_authoritative_month(result: CreditResult) -> bool:
	return _resolved_month(result) is not None


def _is_calculated(result: CreditResult) -> bool:
	return result.status in CALCULATED_STATUSES and result.calculated_credit is not None


def _exact_decimal_sum(values: Sequence[Any]) -> Decimal:
	decimals = [number for value in values if (number := _as_decimal(value)) is not None]
	if not decimals:
		return Decimal("0")
	min_exponent = min(number.as_tuple().exponent for number in decimals)
	max_adjusted = max(number.adjusted() for number in decimals if number != 0) if any(
		number != 0 for number in decimals
	) else 0
	carry_digits = len(str(len(decimals))) + 1
	precision = max(28, max_adjusted - min_exponent + 1 + carry_digits)
	with localcontext() as context:
		context.prec = precision
		return sum(decimals, Decimal("0"))


def _report_groups(results: Sequence[CreditResult]) -> dict[tuple[Any, Any, Any], list[CreditResult]]:
	groups: dict[tuple[Any, Any, Any], list[CreditResult]] = defaultdict(list)
	for result in results:
		month = _resolved_month(result)
		if month is None:
			continue
		groups[(month, result.client, _currency_bucket(result))].append(result)
	return groups


def _group_credit_values(group: Sequence[CreditResult]) -> tuple[list[CreditResult], Decimal | None]:
	calculated = [result for result in group if _is_calculated(result)]
	total = (
		_exact_decimal_sum([result.calculated_credit for result in calculated])
		if calculated
		else None
	)
	return calculated, total


def build_client_totals(results: Sequence[CreditResult]) -> pd.DataFrame:
	"""Aggregate only valid calculated values by resolved month/client/currency."""
	rows = []
	for (month, client, currency), group in sorted(
		_report_groups(results).items(), key=lambda item: tuple(str(x) for x in item[0])
	):
		calculated, total = _group_credit_values(group)
		currency_limited = currency == UNKNOWN_CURRENCY_BUCKET and any(
			result.calculation_rule == "CLIENT_004_SPECIAL" and _is_calculated(result)
			for result in calculated
		)
		rows.append(
			{
				"Reporting Month": month,
				"Client": client,
				"Currency": currency,
				"Total Market Records": len(group),
				"Calculated Record Count": len(calculated),
				"Positive Credit Count": sum(
					result.calculated_credit > 0 for result in calculated
				),
				"Zero Credit Count": sum(
					result.calculated_credit == 0 for result in calculated
				),
				"Null Credit Record Count": sum(
					result.calculated_credit is None for result in group
				),
				"Total Calculated Credit": total,
				"Currency Limitation": (
					"Special-rule amounts have no authoritative currency."
					if currency_limited
					else None
				),
			}
		)
	return pd.DataFrame(rows)


def build_executive_summary(results: Sequence[CreditResult]) -> pd.DataFrame:
	"""Build authoritative month/client/currency operational and credit totals."""
	rows = []
	for (month, client, currency), group in sorted(
		_report_groups(results).items(), key=lambda item: tuple(str(x) for x in item[0])
	):
		calculated, total = _group_credit_values(group)
		currency_limited = currency == UNKNOWN_CURRENCY_BUCKET and any(
			result.calculation_rule == "CLIENT_004_SPECIAL" and _is_calculated(result)
			for result in calculated
		)
		rows.append(
			{
				"Reporting Month": month,
				"Client": client,
				"Currency": currency,
				"Total Market Records": len(group),
				"Breached Market Count": sum(
					(breach := _as_decimal(result.sla_breach_pct)) is not None and breach > 0
					for result in group
				),
				"Calculated Positive-Credit Count": sum(
					result.calculated_credit > 0 for result in calculated
				),
				"Zero-Credit Count": sum(
					result.calculated_credit == 0 for result in calculated
				),
				"Exception Count": sum(_has_exception(result) for result in group),
				"Ambiguous Count": sum(result.match_status == "AMBIGUOUS" for result in group),
				"Unmatched Count": sum(result.match_status == "UNMATCHED" for result in group),
				"HIGH-Confidence Matched Count": sum(
					result.match_status == "MATCHED" and result.match_confidence == "HIGH"
					for result in group
				),
				"Total Calculated Credit": total,
				"Currency Limitation": (
					"Special-rule amounts have no authoritative currency."
					if currency_limited
					else None
				),
			}
		)
	return pd.DataFrame(rows)


def _exception_explanation(result: CreditResult, code: str) -> str:
	if code == "CURRENCY_UNKNOWN":
		return (
			"No authoritative currency is supplied by the special rule or a unique "
			"Finance match; amount is isolated in the unknown-currency bucket."
		)
	for exception in result.source_exceptions:
		if exception.exception_code == code:
			return exception.description
	if code == "UNRESOLVED_REPORTING_MONTH":
		return "No authoritative reporting month is available; the row is excluded from monthly totals."
	return explain_credit_result(result)


def build_exceptions_report(results: Sequence[CreditResult]) -> pd.DataFrame:
	"""Create one exception row per code, retaining candidate Finance details."""
	rows = []
	for result in results:
		codes = _exception_codes(result)
		if not codes and not _has_exception(result):
			continue
		candidates = _candidate_records(result)
		for code in codes or ("CALCULATION_EXCEPTION",):
			rows.append(
				{
					"Reporting Month": _resolved_month(result),
					"Reporting Month Status": result.reporting_month_status or "UNRESOLVED",
					"Client": result.client,
					"Market": result.devops_market,
					"Normalized Market": result.normalized_devops_market,
					"Exception Code": code,
					"Explanation": _exception_explanation(result, code),
					"Calculation Status": result.status,
					"Calculated Credit": result.calculated_credit,
					"Calculation Rule": result.calculation_rule,
					"Currency": result.currency,
					"Candidate Count": result.candidate_count,
					"Candidate Customer WD(s)": _join_values(
						record.customer_wd for record in candidates
					),
					"Candidate Market Code(s)": _join_values(
						record.raw_market_code for record in candidates
					),
					"Candidate Base Credit(s)": _join_values(
						record.base_credit for record in candidates
					),
					"Currency/Currencies": _join_values(
						record.currency for record in candidates
					),
					"DevOps Source File": result.source_file,
					"DevOps Source Sheet": result.source_sheet,
					"DevOps Source Row": result.source_row_number,
					"Finance Source File": result.finance_source_file,
					"Finance Source Sheet": result.finance_source_sheet,
					"Finance Source Row": result.finance_source_row_number,
					"Source Lineage": _source_lineage(result),
					"Processing Run ID": result.processing_run_id,
				}
			)
	return pd.DataFrame(rows)


def _result_reference(result: CreditResult) -> tuple[Any, Any, Any]:
	return result.source_file, result.source_sheet, result.source_row_number


def _money_by_group(results: Sequence[CreditResult]) -> dict[tuple[Any, Any, Any], Decimal]:
	amounts: dict[tuple[Any, Any, Any], list[Any]] = defaultdict(list)
	for result in results:
		month = _resolved_month(result)
		if month is not None and _is_calculated(result):
			amounts[(month, result.client, _currency_bucket(result))].append(
				result.calculated_credit
			)
	return {key: _exact_decimal_sum(values) for key, values in amounts.items()}


def reconcile_report_datasets(
	results: Sequence[CreditResult],
	detailed: pd.DataFrame,
	client_totals: pd.DataFrame,
	executive: pd.DataFrame,
	exceptions: pd.DataFrame,
) -> ReconciliationReport:
	"""Compare report row counts, exception coverage, and exact currency totals."""
	resolved = [result for result in results if _is_authoritative_month(result)]
	unresolved = [result for result in results if not _is_authoritative_month(result)]
	expected_exception_refs = {
		_result_reference(result) for result in results if _has_exception(result)
	}
	reported_exception_refs = {
		(row.get("DevOps Source File"), row.get("DevOps Source Sheet"), row.get("DevOps Source Row"))
		for _, row in exceptions.iterrows()
	} if not exceptions.empty else set()

	expected_money = _money_by_group(results)
	client_money = {
		(row["Reporting Month"], row["Client"], row["Currency"]): row["Total Calculated Credit"]
		for _, row in client_totals.iterrows()
		if not _is_missing(row["Total Calculated Credit"])
	} if not client_totals.empty else {}
	executive_money = {
		(row["Reporting Month"], row["Client"], row["Currency"]): row["Total Calculated Credit"]
		for _, row in executive.iterrows()
		if not _is_missing(row["Total Calculated Credit"])
	} if not executive.empty else {}

	executive_record_count = (
		int(executive["Total Market Records"].sum()) if not executive.empty else 0
	)
	if detailed.empty:
		currency_bucket_check = True
	else:
		currency_bucket_check = all(
			row["Reporting Currency Bucket"] == (
				UNKNOWN_CURRENCY_BUCKET if _is_missing(row["Currency"]) else row["Currency"]
			)
			for _, row in detailed.iterrows()
		)
	authoritative_exception_count = sum(_has_exception(result) for result in resolved)
	executive_exception_count = (
		int(executive["Exception Count"].sum()) if not executive.empty else 0
	)
	expected_counts = {
		"Breached Market Count": sum(
			(breach := _as_decimal(result.sla_breach_pct)) is not None and breach > 0
			for result in resolved
		),
		"Calculated Positive-Credit Count": sum(
			_is_calculated(result) and result.calculated_credit > 0 for result in resolved
		),
		"Zero-Credit Count": sum(
			_is_calculated(result) and result.calculated_credit == 0 for result in resolved
		),
		"Ambiguous Count": sum(result.match_status == "AMBIGUOUS" for result in resolved),
		"Unmatched Count": sum(result.match_status == "UNMATCHED" for result in resolved),
		"HIGH-Confidence Matched Count": sum(
			result.match_status == "MATCHED" and result.match_confidence == "HIGH"
			for result in resolved
		),
	}
	actual_counts = {
		column: int(executive[column].sum()) if not executive.empty else 0
		for column in expected_counts
	}
	checks = {
		"detailed_rows_match_processed_results": len(detailed) == len(results),
		"detailed_source_rows_reconcile": (
			detailed.groupby("DevOps Source File", dropna=False).size().to_dict()
			== pd.Series([result.source_file for result in results]).value_counts(dropna=False).to_dict()
			if not detailed.empty
			else not results
		),
		"resolved_and_unresolved_month_rows_accounted": len(resolved) + len(unresolved) == len(results),
		"executive_market_counts_match_authoritative_detail": executive_record_count == len(resolved),
		"executive_operational_counts_match_authoritative_detail": (
			actual_counts == expected_counts
			and executive_exception_count == authoritative_exception_count
		),
		"exception_records_reconcile_to_flagged_results": expected_exception_refs == reported_exception_refs,
		"client_totals_match_calculated_results": client_money == expected_money,
		"executive_totals_match_calculated_results": executive_money == expected_money,
		"unresolved_months_excluded_from_authoritative_summaries": executive_record_count == len(resolved),
		"unknown_currency_has_separate_bucket": currency_bucket_check,
		"none_credit_values_excluded_from_totals": client_money == expected_money and executive_money == expected_money,
	}
	details = {
		"processed_result_count": len(results),
		"detailed_row_count": len(detailed),
		"resolved_month_row_count": len(resolved),
		"unresolved_month_row_count": len(unresolved),
		"executive_market_record_count": executive_record_count,
		"flagged_result_count": len(expected_exception_refs),
		"exception_report_record_count": len(reported_exception_refs),
		"row_counts_by_source": detailed.groupby("DevOps Source File", dropna=False).size().to_dict()
		if not detailed.empty
		else {},
		"authoritative_money_groups": len(expected_money),
	}
	return ReconciliationReport(checks=checks, details=details)


__all__ = [
	"ReconciliationReport",
	"UNKNOWN_CURRENCY_BUCKET",
	"build_client_totals",
	"build_detailed_report",
	"build_exceptions_report",
	"build_executive_summary",
	"explain_credit_result",
	"reconcile_report_datasets",
]
