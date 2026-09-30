"""Round-trip and integrity tests for versioned CreditResult snapshots."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import numpy as np
import pytest

from src.models import CreditResult, ExceptionRecord, FinanceRecord, MatchResult
from src.snapshot_store import (
	SNAPSHOT_SCHEMA_VERSION,
	SnapshotError,
	deserialize_credit_results,
	load_credit_results_snapshot,
	serialize_credit_results,
	write_credit_results_snapshot,
)


def _credit_result() -> CreditResult:
	finance = FinanceRecord(
		ultimate_parent_nhn="CLIENT-002",
		customer_wd="CLIENT-002-WD-09",
		currency="USD",
		raw_market_code="AMS\nBRU\nLIS\nPAR",
		base_credit=Decimal("7621.4016900"),
		source_file="finance.xlsx",
		source_sheet="Finance_Fines",
		source_row_number=1151,
		processing_run_id="run-a",
	)
	match = MatchResult(
		status="AMBIGUOUS",
		confidence="LOW",
		match_method=None,
		candidate_count=1,
		candidate_finance_records=(finance,),
		candidate_finance_references=("finance.xlsx:Finance_Fines:1151",),
		exception_code="AMBIGUOUS_MATCH",
		explanation="Candidate retained without selection.",
	)
	return CreditResult(
		client="CLIENT-002",
		devops_market="client002_par",
		normalized_devops_market="PAR",
		match_status=match.status,
		match_confidence=match.confidence,
		match_method=match.match_method,
		candidate_count=match.candidate_count,
		candidate_finance_records=match.candidate_finance_records,
		candidate_finance_references=match.candidate_finance_references,
		sla_breach_pct=np.float64(0.0254),
		applied_rate=None,
		base_credit=None,
		currency=None,
		calculation_rule="STANDARD_CAPPED",
		calculated_credit=None,
		status="EXCEPTION",
		exception_code="AMBIGUOUS_MATCH",
		exception_codes=("NON_UNIQUE_FINANCE_KEY", "AMBIGUOUS_MATCH"),
		source_exceptions=(
			ExceptionRecord(
				exception_code="NON_UNIQUE_FINANCE_KEY",
				description="Two Finance candidates share the candidate key.",
				source_file="finance.xlsx",
				source_row_number=1151,
				relevant_record=finance,
			),
		),
		reporting_month="2026-04",
		reporting_month_status="RESOLVED",
		filename_month_hint="APR26",
		workbook_month_hint="April 2026",
		month_conflict=False,
		source_file="devops.xlsx",
		source_sheet="SLA_Breaches",
		source_row_number=8,
		finance_source_file=None,
		finance_source_sheet=None,
		finance_source_row_number=None,
		processing_run_id="run-a",
	)


def test_snapshot_round_trip_preserves_creditresult_and_nested_lineage() -> None:
	original = _credit_result()

	restored = deserialize_credit_results(serialize_credit_results([original]))

	assert restored == (original,)
	assert isinstance(restored[0].sla_breach_pct, np.float64)
	assert restored[0].calculated_credit is None
	assert restored[0].candidate_finance_records[0].base_credit == Decimal("7621.4016900")
	assert restored[0].source_exceptions[0].relevant_record == original.candidate_finance_records[0]
	assert restored[0].source_file == "devops.xlsx"
	assert restored[0].source_sheet == "SLA_Breaches"
	assert restored[0].source_row_number == 8


def test_snapshot_preserves_client004_and_month_conflict_states() -> None:
	client004 = replace(
		_credit_result(),
		client="CLIENT-004",
		devops_market="mx",
		normalized_devops_market="MX",
		match_status="UNMATCHED",
		match_confidence="LOW",
		match_method=None,
		candidate_count=0,
		candidate_finance_records=(),
		candidate_finance_references=(),
		sla_breach_pct=Decimal("0.1301"),
		applied_rate=None,
		base_credit=None,
		currency=None,
		calculation_rule="CLIENT_004_SPECIAL",
		calculated_credit=Decimal("7686.3950807071483474250576479631053036126056879324"),
		status="CALCULATED",
		exception_code=None,
		exception_codes=("NO_FINANCE_MATCH", "CURRENCY_UNKNOWN"),
		source_exceptions=(),
		finance_source_file=None,
		finance_source_sheet=None,
		finance_source_row_number=None,
		source_file="client004.xlsx",
		source_row_number=1,
	)
	month_conflict = replace(
		_credit_result(),
		client="CLIENT-002",
		devops_market="nymex",
		match_status="INELIGIBLE",
		match_confidence=None,
		match_method=None,
		candidate_count=0,
		candidate_finance_records=(),
		candidate_finance_references=(),
		calculated_credit=None,
		status="INELIGIBLE",
		exception_code="MONTH_CONFLICT",
		exception_codes=("MONTH_CONFLICT",),
		source_exceptions=(),
		reporting_month=None,
		reporting_month_status="UNRESOLVED",
		filename_month_hint="MAY26",
		month_conflict=True,
		source_file="client002_may.xlsx",
		source_row_number=2,
	)

	restored = deserialize_credit_results(
		serialize_credit_results([client004, month_conflict])
	)

	assert restored == (client004, month_conflict)
	assert restored[0].match_status == "UNMATCHED"
	assert restored[0].match_confidence == "LOW"
	assert restored[0].status == "CALCULATED"
	assert restored[0].currency is None
	assert restored[1].match_status == "INELIGIBLE"
	assert restored[1].match_confidence is None
	assert restored[1].exception_code == "MONTH_CONFLICT"


def test_snapshot_encoding_is_deterministic_for_same_results() -> None:
	results = [_credit_result()]

	assert serialize_credit_results(results) == serialize_credit_results(results)


def test_snapshot_checksum_detects_modified_content() -> None:
	snapshot = bytearray(serialize_credit_results([_credit_result()]))
	needle = b"client002_par"
	index = snapshot.index(needle)
	snapshot[index : index + len(needle)] = b"client002_xxx"

	with pytest.raises(SnapshotError, match="checksum"):
		deserialize_credit_results(bytes(snapshot))


def test_snapshot_rejects_unsupported_schema_version() -> None:
	import hashlib
	import json

	snapshot = json.loads(serialize_credit_results([_credit_result()]).decode("utf-8"))
	snapshot["schema_version"] = SNAPSHOT_SCHEMA_VERSION + 1
	unsigned = {key: value for key, value in snapshot.items() if key != "sha256"}
	canonical = json.dumps(
		unsigned,
		ensure_ascii=False,
		sort_keys=True,
		separators=(",", ":"),
		allow_nan=False,
	).encode("utf-8")
	snapshot["sha256"] = hashlib.sha256(canonical).hexdigest()

	with pytest.raises(SnapshotError, match="schema version"):
		deserialize_credit_results(json.dumps(snapshot))


def test_snapshot_file_write_is_atomic_loadable_and_no_overwrite_by_default(tmp_path) -> None:
	path = tmp_path / "run-a" / "credit-results.v1.json"
	checksum = write_credit_results_snapshot(path, [_credit_result()])

	assert path.exists()
	assert len(checksum) == 64
	assert load_credit_results_snapshot(path) == (_credit_result(),)
	assert not list(path.parent.glob("*.tmp"))
	with pytest.raises(FileExistsError):
		write_credit_results_snapshot(path, [_credit_result()])
