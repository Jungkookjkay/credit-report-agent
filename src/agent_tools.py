"""Read-only local tool adapters for deterministic CreditResult data."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

if __package__:
	from .models import CreditResult, FinanceRecord
	from .reporting import (
		build_client_totals,
		build_detailed_report,
		build_exceptions_report,
		build_executive_summary,
	)
else:
	from models import CreditResult, FinanceRecord
	from reporting import (
		build_client_totals,
		build_detailed_report,
		build_exceptions_report,
		build_executive_summary,
	)


SOURCE_NAME = "deterministic_credit_engine"
RECORD_ID_PREFIX = "credit-record-v1-"
UNRESOLVED_MONTH = "UNRESOLVED"
MONTH_HINT_PATTERN = re.compile(
	r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[-_ ]?(\d{2}|\d{4})$",
	re.IGNORECASE,
)
MONTH_NUMBERS = {
	"JAN": 1,
	"FEB": 2,
	"MAR": 3,
	"APR": 4,
	"MAY": 5,
	"JUN": 6,
	"JUL": 7,
	"AUG": 8,
	"SEP": 9,
	"OCT": 10,
	"NOV": 11,
	"DEC": 12,
}
REVIEW_CODES = {
	"AMBIGUOUS_MATCH",
	"CLIENT_CONFLICT",
	"CURRENCY_UNKNOWN",
	"EXACT_DUPLICATE",
	"INVALID_SLA_BREACH",
	"LOW_CONFIDENCE_MATCH",
	"MISSING_BASE_CREDIT",
	"MISSING_CURRENCY",
	"MISSING_FINANCE_PARENT",
	"MONTH_CONFLICT",
	"NO_FINANCE_MATCH",
	"NON_UNIQUE_FINANCE_KEY",
	"SCHEMA_ERROR",
	"SPECIAL_RULE_ASSUMPTION_REQUIRED",
}

SourceKey = Tuple[str, str, int]


def deterministic_record_id(result: CreditResult) -> Optional[str]:
	"""Create a stable ID from verified DevOps source-file/sheet/row lineage."""
	if (
		not isinstance(result.source_file, str)
		or not result.source_file.strip()
		or not isinstance(result.source_sheet, str)
		or not result.source_sheet.strip()
		or result.source_row_number is None
	):
		return None
	try:
		row_number = int(result.source_row_number)
	except (TypeError, ValueError, OverflowError):
		return None
	if row_number < 1:
		return None

	identity = json.dumps(
		[result.source_file, result.source_sheet, row_number],
		ensure_ascii=False,
		separators=(",", ":"),
	)
	digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
	return RECORD_ID_PREFIX + digest


def _source_key(result: CreditResult) -> Optional[SourceKey]:
	record_id = deterministic_record_id(result)
	if record_id is None:
		return None
	return (
		str(result.source_file),
		str(result.source_sheet),
		int(result.source_row_number),
	)


def _json_value(value: Any) -> Any:
	"""Convert values to strict JSON-compatible primitives without rounding."""
	if value is None:
		return None
	if isinstance(value, Decimal):
		return format(value, "f") if value.is_finite() else None
	if isinstance(value, dict):
		return {str(key): _json_value(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [_json_value(item) for item in value]
	if isinstance(value, float) and not math.isfinite(value):
		return None
	try:
		missing = pd.isna(value)
		if isinstance(missing, bool) and missing:
			return None
		if getattr(missing, "shape", None) == () and bool(missing):
			return None
	except (TypeError, ValueError):
		pass
	if hasattr(value, "item"):
		try:
			return _json_value(value.item())
		except (TypeError, ValueError, AttributeError):
			pass
	if isinstance(value, (str, int, float, bool)):
		return value
	if hasattr(value, "isoformat"):
		return value.isoformat()
	return str(value)


def _decimal_json_string(value: Any) -> Optional[str]:
	"""Serialize financial/percentage numbers through their decimal text form."""
	if value is None:
		return None
	try:
		missing = pd.isna(value)
		if isinstance(missing, bool) and missing:
			return None
		if getattr(missing, "shape", None) == () and bool(missing):
			return None
	except (TypeError, ValueError):
		pass
	try:
		number = Decimal(str(value))
	except Exception:
		return None
	return format(number, "f") if number.is_finite() else None


def _month_from_hint(hint: Any) -> Optional[str]:
	if not isinstance(hint, str):
		return None
	match = MONTH_HINT_PATTERN.fullmatch(hint.strip())
	if match is None:
		return None
	month = MONTH_NUMBERS[match.group(1).upper()]
	year = int(match.group(2))
	if year < 100:
		year += 2000
	return "{0:04d}-{1:02d}".format(year, month)


def _is_valid_month(month: Any) -> bool:
	if month == UNRESOLVED_MONTH:
		return True
	if not isinstance(month, str) or re.fullmatch(r"\d{4}-\d{2}", month) is None:
		return False
	try:
		datetime.strptime(month, "%Y-%m")
		return True
	except ValueError:
		return False


def _source_lineage(result: CreditResult) -> Dict[str, Any]:
	return {
		"devops": {
			"file": result.source_file,
			"sheet": result.source_sheet,
			"row": result.source_row_number,
		},
		"finance": (
			{
				"file": result.finance_source_file,
				"sheet": result.finance_source_sheet,
				"row": result.finance_source_row_number,
			}
			if any(
				value is not None
				for value in (
					result.finance_source_file,
					result.finance_source_sheet,
					result.finance_source_row_number,
				)
			)
			else None
		),
	}


def _candidate_payload(record: FinanceRecord) -> Dict[str, Any]:
	return {
		"ultimate_parent": record.ultimate_parent_nhn,
		"customer_wd": record.customer_wd,
		"currency": record.currency,
		"market_code": record.raw_market_code,
		"base_credit": _decimal_json_string(record.base_credit),
		"source_lineage": {
			"file": record.source_file,
			"sheet": record.source_sheet,
			"row": record.source_row_number,
		},
	}


def _credit_payload(result: CreditResult) -> Dict[str, Any]:
	"""Serialize one result through the canonical Phase 9 detail builder."""
	detail = build_detailed_report([result]).iloc[0].to_dict()
	exception_text = detail.get("Exception Codes")
	exception_codes = (
		[code for code in str(exception_text).split("; ") if code]
		if exception_text is not None and not pd.isna(exception_text)
		else []
	)
	return {
		"record_id": deterministic_record_id(result),
		"client": detail.get("Client"),
		"reporting_month": detail.get("Reporting Month"),
		"reporting_month_status": detail.get("Reporting Month Status"),
		"filename_month_hint": detail.get("Filename Month Hint"),
		"workbook_month_hint": detail.get("Workbook Month Hint"),
		"devops_market": detail.get("DevOps Market"),
		"normalized_devops_market": detail.get("Normalized DevOps Market"),
		"matched_finance_market": detail.get("Matched Finance Market Code"),
		"customer_wd": detail.get("Customer WD"),
		"sla_breach": _decimal_json_string(detail.get("SLA Breach %")),
		"applied_rate": _decimal_json_string(detail.get("Applied Rate")),
		"base_credit": _decimal_json_string(detail.get("Base Credit")),
		"currency": detail.get("Currency"),
		"calculated_credit": _decimal_json_string(detail.get("Calculated Credit")),
		"calculation_rule": detail.get("Calculation Rule"),
		"calculation_status": detail.get("Calculation Status"),
		"match_status": detail.get("Match Status"),
		"match_confidence": detail.get("Match Confidence"),
		"match_method": detail.get("Match Method"),
		"candidate_count": detail.get("Candidate Count"),
		"candidate_finance_records": [
			_candidate_payload(candidate)
			for candidate in result.candidate_finance_records
		],
		"exception_code": detail.get("Exception Code"),
		"exception_codes": exception_codes,
		"explanation": detail.get("Explanation / Audit Note"),
		"currency_limitation": detail.get("Currency Limitation"),
		"source_lineage": _source_lineage(result),
		"processing_run_id": result.processing_run_id,
	}


def _response(
	status: str,
	data: Any = None,
	*,
	error_code: Optional[str] = None,
	error_message: Optional[str] = None,
	review_reasons: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
	return {
		"status": status,
		"source": SOURCE_NAME,
		"data": _json_value(data),
		"error": (
			{"code": error_code, "message": error_message}
			if error_code is not None
			else None
		),
		"human_review_required": bool(review_reasons),
		"review_reasons": list(review_reasons or ()),
	}


def _review_reasons(payloads: Sequence[Dict[str, Any]]) -> List[str]:
	reasons = set()
	for payload in payloads:
		for code in payload.get("exception_codes", []):
			if code in REVIEW_CODES:
				reasons.add(code)
	return sorted(reasons)


class ReadOnlyCreditTools:
	"""Read-only tool methods backed by an explicit in-memory result sequence.

	Pass ``None`` when deterministic results are unavailable; an empty sequence
	means results are available and contain no records.
	"""

	def __init__(self, credit_results: Optional[Sequence[CreditResult]]) -> None:
		self._available = credit_results is not None
		self._results = tuple(credit_results or ())
		self._by_record_id: Dict[str, List[CreditResult]] = defaultdict(list)
		self._by_source_key: Dict[SourceKey, List[CreditResult]] = defaultdict(list)
		for result in self._results:
			source_key = _source_key(result)
			if source_key is None:
				continue
			self._by_source_key[source_key].append(result)
			record_id = deterministic_record_id(result)
			if record_id is not None:
				self._by_record_id[record_id].append(result)

	def _unavailable(self) -> Optional[Dict[str, Any]]:
		if not self._available:
			return _response(
				"UNAVAILABLE",
				error_code="DETERMINISTIC_RESULTS_UNAVAILABLE",
				error_message="Authoritative deterministic results were not supplied.",
			)
		return None

	def _validate_client_month(self, client: Any, month: Any) -> Optional[Dict[str, Any]]:
		if not isinstance(client, str) or not client.strip():
			return _response(
				"INVALID_REQUEST",
				error_code="INVALID_CLIENT",
				error_message="client must be a non-empty string.",
			)
		if not _is_valid_month(month):
			return _response(
				"INVALID_REQUEST",
				error_code="INVALID_MONTH",
				error_message="month must use YYYY-MM or UNRESOLVED.",
			)
		return None

	def _known_clients(self) -> set:
		return {result.client for result in self._results if result.client is not None}

	def _known_months(self) -> set:
		months = set()
		for result in self._results:
			if result.reporting_month_status == "RESOLVED" and result.reporting_month:
				months.add(result.reporting_month)
			elif result.reporting_month_status != "RESOLVED":
				hint_month = _month_from_hint(result.filename_month_hint)
				if hint_month is not None:
					months.add(hint_month)
		return months

	def _month_matches(self, result: CreditResult, month: str) -> bool:
		if month == UNRESOLVED_MONTH:
			return result.reporting_month_status != "RESOLVED"
		if result.reporting_month_status == "RESOLVED":
			return result.reporting_month == month
		return _month_from_hint(result.filename_month_hint) == month

	def _request_results(self, client: str, month: str) -> List[CreditResult]:
		return [
			result for result in self._results
			if result.client == client and self._month_matches(result, month)
		]

	def get_client_credit(self, client: str, month: str) -> Dict[str, Any]:
		"""Return report-builder detail/totals for one client/month; no calculation."""
		unavailable = self._unavailable()
		if unavailable is not None:
			return unavailable
		invalid = self._validate_client_month(client, month)
		if invalid is not None:
			return invalid
		if client not in self._known_clients():
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_CLIENT",
				error_message="No deterministic results exist for this client.",
			)
		if month != UNRESOLVED_MONTH and month not in self._known_months():
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_MONTH",
				error_message="No deterministic results or month hints exist for this month.",
			)

		selected = self._request_results(client, month)
		if not selected:
			return _response(
				"NOT_FOUND",
				error_code="CLIENT_MONTH_NOT_FOUND",
				error_message="No deterministic records match this client/month request.",
			)
		resolved = [result for result in selected if result.reporting_month_status == "RESOLVED"]
		pending = [result for result in selected if result.reporting_month_status != "RESOLVED"]
		records = [
			_credit_payload(result)
			for result in resolved
		]
		pending_payloads = [_credit_payload(result) for result in pending]
		totals_frame = build_client_totals(resolved)
		if not totals_frame.empty:
			totals_frame = totals_frame.loc[totals_frame["Client"].eq(client)]
			if month != UNRESOLVED_MONTH:
				totals_frame = totals_frame.loc[totals_frame["Reporting Month"].eq(month)]
		totals = totals_frame.to_dict(orient="records")
		all_payloads = records + pending_payloads
		return _response(
			"OK",
			{
				"client": client,
				"requested_month": month,
				"records": records,
				"totals_by_currency": totals,
				"pending_or_unresolved": pending_payloads,
				"record_count": len(records),
				"pending_or_unresolved_count": len(pending_payloads),
			},
			review_reasons=_review_reasons(all_payloads),
		)

	def get_client_exceptions(self, client: str, month: str) -> Dict[str, Any]:
		"""Return Phase 9 exception occurrences and unique affected records."""
		unavailable = self._unavailable()
		if unavailable is not None:
			return unavailable
		invalid = self._validate_client_month(client, month)
		if invalid is not None:
			return invalid
		if client not in self._known_clients():
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_CLIENT",
				error_message="No deterministic results exist for this client.",
			)
		if month != UNRESOLVED_MONTH and month not in self._known_months():
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_MONTH",
				error_message="No deterministic results or month hints exist for this month.",
			)

		selected = self._request_results(client, month)
		if not selected:
			return _response(
				"NOT_FOUND",
				error_code="CLIENT_MONTH_NOT_FOUND",
				error_message="No deterministic records match this client/month request.",
			)
		exception_frame = build_exceptions_report(selected)
		by_source: Dict[SourceKey, List[CreditResult]] = defaultdict(list)
		for result in selected:
			source_key = _source_key(result)
			if source_key is not None:
				by_source[source_key].append(result)

		exception_rows = []
		for _, row in exception_frame.iterrows():
			source_key = (
				row.get("DevOps Source File"),
				row.get("DevOps Source Sheet"),
				row.get("DevOps Source Row"),
			)
			associated = by_source.get(source_key, [])
			if len(associated) != 1:
				record_payload = None
				record_id = None
				review_reason = "AMBIGUOUS_RECORD_LOOKUP" if associated else "SOURCE_LINEAGE_UNAVAILABLE"
			else:
				record_payload = _credit_payload(associated[0])
				record_id = record_payload["record_id"]
				review_reason = None
			exception_rows.append(
				{
					"record_id": record_id,
					"client": row.get("Client"),
					"reporting_month": row.get("Reporting Month"),
					"reporting_month_status": row.get("Reporting Month Status"),
					"market": row.get("Market"),
					"normalized_market": row.get("Normalized Market"),
					"exception_code": row.get("Exception Code"),
					"explanation": row.get("Explanation"),
					"calculation_status": row.get("Calculation Status"),
					"calculation_rule": row.get("Calculation Rule"),
					"calculated_credit": _decimal_json_string(row.get("Calculated Credit")),
					"currency": row.get("Currency"),
					"match_status": record_payload["match_status"] if record_payload else None,
					"match_confidence": record_payload["match_confidence"] if record_payload else None,
					"candidate_count": row.get("Candidate Count"),
					"candidate_finance_records": (
						record_payload["candidate_finance_records"] if record_payload else []
					),
					"exception_codes_for_record": (
						record_payload["exception_codes"] if record_payload else []
					),
					"source_lineage": record_payload["source_lineage"] if record_payload else None,
					"lookup_warning": review_reason,
				}
			)

		unique_keys = {
			_source_key(result) if _source_key(result) is not None else ("unindexed", index)
			for index, result in enumerate(selected)
			if any(
				row.get("DevOps Source File") == result.source_file
				and row.get("DevOps Source Sheet") == result.source_sheet
				and row.get("DevOps Source Row") == result.source_row_number
			for _, row in exception_frame.iterrows()
			)
		}
		if not exception_rows:
			return _response(
				"NOT_FOUND",
				error_code="NO_EXCEPTIONS",
				error_message="No exception records exist for this client/month.",
			)
		reasons = sorted(
			{
				str(row["exception_code"])
				for row in exception_rows
				if row["exception_code"] in REVIEW_CODES
			}
		)
		return _response(
			"OK",
			{
				"client": client,
				"requested_month": month,
				"unique_affected_record_count": len(unique_keys),
				"exception_code_occurrence_count": len(exception_rows),
				"exceptions": exception_rows,
			},
			review_reasons=reasons,
		)

	def get_executive_summary(self, month: str) -> Dict[str, Any]:
		"""Return Phase 9 executive groups for one resolved month, currencies separate."""
		unavailable = self._unavailable()
		if unavailable is not None:
			return unavailable
		if not _is_valid_month(month):
			return _response(
				"INVALID_REQUEST",
				error_code="INVALID_MONTH",
				error_message="month must use YYYY-MM or UNRESOLVED.",
			)
		if month != UNRESOLVED_MONTH and month not in self._known_months():
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_MONTH",
				error_message="No deterministic results or month hints exist for this month.",
			)

		resolved = [
			result for result in self._results
			if month != UNRESOLVED_MONTH
			and result.reporting_month_status == "RESOLVED"
			and result.reporting_month == month
		]
		pending = [
			result for result in self._results
			if result.reporting_month_status != "RESOLVED"
			and (
				month == UNRESOLVED_MONTH
				or _month_from_hint(result.filename_month_hint) == month
			)
		]
		summary = build_executive_summary(resolved)
		groups = summary.to_dict(orient="records")
		if not groups and not pending:
			return _response(
				"NOT_FOUND",
				error_code="NO_AUTHORITATIVE_SUMMARY",
				error_message="No authoritative executive summary exists for this month.",
			)
		return _response(
			"OK",
			{
				"reporting_month": None if month == UNRESOLVED_MONTH else month,
				"groups": groups,
				"pending_or_unresolved_record_count": len(pending),
				"pending_or_unresolved": [
					{
						"record_id": deterministic_record_id(result),
						"client": result.client,
						"reporting_month": None,
						"reporting_month_status": result.reporting_month_status,
						"filename_month_hint": result.filename_month_hint,
						"match_status": result.match_status,
						"match_confidence": result.match_confidence,
						"calculation_status": result.status,
						"exception_codes": _credit_payload(result)["exception_codes"],
						"source_lineage": _source_lineage(result),
					}
					for result in pending
				],
			},
			review_reasons=_review_reasons(
				[_credit_payload(result) for result in resolved + pending]
			),
		)

	def get_credit_record(self, record_id: str) -> Dict[str, Any]:
		"""Retrieve the exact deterministic record for a stable source-lineage ID."""
		unavailable = self._unavailable()
		if unavailable is not None:
			return unavailable
		if not isinstance(record_id, str) or not record_id.startswith(RECORD_ID_PREFIX):
			return _response(
				"INVALID_REQUEST",
				error_code="INVALID_RECORD_ID",
				error_message="record_id must be a credit-record-v1 source-lineage identifier.",
			)
		matches = self._by_record_id.get(record_id, [])
		if not matches:
			return _response(
				"NOT_FOUND",
				error_code="UNKNOWN_RECORD_ID",
				error_message="No deterministic result has this record_id.",
			)
		if len(matches) != 1:
			return _response(
				"INVALID_REQUEST",
				error_code="AMBIGUOUS_RECORD_LOOKUP",
				error_message="The source-lineage identifier maps to multiple results; no record was selected.",
				review_reasons=["AMBIGUOUS_RECORD_LOOKUP"],
			)
		payload = _credit_payload(matches[0])
		return _response(
			"OK",
			payload,
			review_reasons=_review_reasons([payload]),
		)


__all__ = ["ReadOnlyCreditTools", "deterministic_record_id"]