"""Offline tests for the AgentCore Gateway Lambda target handler."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from types import SimpleNamespace

from src.agent_tools import deterministic_record_id
from src.lambda_handler import (
	dispatch_gateway_tool,
	lambda_handler,
	make_lambda_handler,
)
from src.models import CreditResult
from src.snapshot_store import serialize_credit_results


def _result(
	*,
	client: str = "CLIENT-001",
	market: str = "cme",
	row: int = 1,
	status: str = "CALCULATED",
	match_status: str = "MATCHED",
	confidence: str | None = "HIGH",
	rule: str = "STANDARD_CAPPED",
	credit: Decimal | None = Decimal("500.00"),
	currency: str | None = "EUR",
	exceptions: tuple[str, ...] = (),
) -> CreditResult:
	return CreditResult(
		client=client,
		devops_market=market,
		normalized_devops_market=market.upper(),
		matched_finance_market="CME" if match_status == "MATCHED" else None,
		customer_wd="CLIENT-001-WD-01" if match_status == "MATCHED" else None,
		match_status=match_status,
		match_confidence=confidence,
		match_method="EXACT_CODE_MATCH" if match_status == "MATCHED" else None,
		sla_breach_pct=Decimal("0.05"),
		applied_rate=Decimal("0.05") if rule == "STANDARD_CAPPED" else None,
		base_credit=Decimal("10000") if match_status == "MATCHED" else None,
		currency=currency,
		calculation_rule=rule,
		calculated_credit=credit,
		status=status,
		exception_code=exceptions[0] if exceptions else None,
		exception_codes=exceptions,
		audit_notes=("Currency is unknown: no unique Finance record matched.",)
		if currency is None and rule == "CLIENT_004_SPECIAL"
		else (),
		reporting_month="2026-04",
		reporting_month_status="RESOLVED",
		filename_month_hint="APR26",
		workbook_month_hint="April 2026",
		month_conflict=False,
		source_file=f"{client}_APR26.xlsx",
		source_sheet="SLA_Breaches",
		source_row_number=row,
		finance_source_file="finance.xlsx" if match_status == "MATCHED" else None,
		finance_source_sheet="Finance_Fines" if match_status == "MATCHED" else None,
		finance_source_row_number=12 if match_status == "MATCHED" else None,
		processing_run_id="run-lambda-test",
	)


def _context(tool_name: str | None):
	return SimpleNamespace(
		client_context=SimpleNamespace(
			custom=(
				{"bedrockAgentCoreToolName": "CreditTools___" + tool_name}
				if tool_name is not None
				else {}
			)
		)
	)


def test_gateway_dispatch_invokes_client_credit_tool() -> None:
	results = [_result()]
	response = dispatch_gateway_tool(
		{"client": "CLIENT-001", "month": "2026-04"},
		_context("get_client_credit"),
		results,
	)

	assert response["status"] == "OK"
	assert response["data"]["records"][0]["calculated_credit"] == "500.00"
	assert json.dumps(response, allow_nan=False)


def test_gateway_dispatch_invokes_client_exceptions_tool() -> None:
	result = _result(
		client="CLIENT-002",
		market="par",
		row=4,
		status="EXCEPTION",
		match_status="AMBIGUOUS",
		confidence="LOW",
		credit=None,
		currency=None,
		exceptions=("AMBIGUOUS_MATCH",),
	)
	response = dispatch_gateway_tool(
		{"client": "CLIENT-002", "month": "2026-04"},
		_context("get_client_exceptions"),
		[result],
	)

	assert response["status"] == "OK"
	assert response["data"]["unique_affected_record_count"] == 1
	assert response["data"]["exceptions"][0]["calculated_credit"] is None


def test_gateway_dispatch_invokes_executive_summary_tool() -> None:
	response = dispatch_gateway_tool(
		{"month": "2026-04"},
		_context("get_executive_summary"),
		[_result()],
	)

	assert response["status"] == "OK"
	assert response["data"]["reporting_month"] == "2026-04"
	assert response["data"]["groups"][0]["Currency"] == "EUR"


def test_gateway_dispatch_invokes_credit_record_tool() -> None:
	result = _result()
	response = dispatch_gateway_tool(
		{"record_id": deterministic_record_id(result)},
		_context("get_credit_record"),
		[result],
	)

	assert response["status"] == "OK"
	assert response["data"]["calculated_credit"] == "500.00"


def test_gateway_tool_name_target_prefix_is_removed_and_unknown_tool_fails_closed() -> None:
	unknown_context = SimpleNamespace(
		client_context=SimpleNamespace(
			custom={"bedrockAgentCoreToolName": "CreditTools___delete_credit_record"}
		)
	)
	response = dispatch_gateway_tool({}, unknown_context, [_result()])

	assert response["status"] == "INVALID_REQUEST"
	assert response["error"]["code"] == "UNKNOWN_TOOL"
	assert response["data"] is None


def test_gateway_dispatch_missing_or_invalid_context_and_event_are_structured_errors() -> None:
	no_tool = SimpleNamespace(client_context=SimpleNamespace(custom={}))
	assert dispatch_gateway_tool({}, no_tool, [_result()])["error"]["code"] == "MISSING_TOOL_NAME"
	assert dispatch_gateway_tool([], _context("get_client_credit"), [_result()])["error"]["code"] == "INVALID_TOOL_INPUT"


def test_lambda_handler_loader_failure_returns_unavailable_without_fallback() -> None:
	def unavailable_loader():
		raise RuntimeError("private S3 detail must not be exposed")

	handler = make_lambda_handler(unavailable_loader)
	response = handler(
		{"client": "CLIENT-001", "month": "2026-04"},
		_context("get_client_credit"),
	)

	assert response["status"] == "UNAVAILABLE"
	assert response["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
	assert "private S3 detail" not in response["error"]["message"]
	assert response["data"] is None


def test_lambda_handler_requires_pinned_snapshot_configuration(monkeypatch) -> None:
	monkeypatch.delenv("CREDIT_RESULTS_S3_BUCKET", raising=False)
	monkeypatch.delenv("CREDIT_RESULTS_S3_KEY", raising=False)
	monkeypatch.delenv("CREDIT_RESULTS_S3_VERSION_ID", raising=False)
	response = lambda_handler(
		{"client": "CLIENT-001", "month": "2026-04"},
		_context("get_client_credit"),
	)

	assert response["status"] == "UNAVAILABLE"
	assert response["error"]["code"] == "SNAPSHOT_UNAVAILABLE"


def test_pinned_s3_snapshot_is_validated_before_tool_dispatch(monkeypatch) -> None:
	result = _result()
	snapshot = serialize_credit_results([result])
	calls = []

	class FakeBody:
		def read(self):
			return snapshot
		def close(self):
			pass

	class FakeS3:
		def get_object(self, **kwargs):
			calls.append(kwargs)
			return {"Body": FakeBody(), "VersionId": "version-abc"}

	class FakeBoto3:
		def client(self, name):
			assert name == "s3"
			return FakeS3()

	monkeypatch.setenv("CREDIT_RESULTS_S3_BUCKET", "credit-results-test")
	monkeypatch.setenv("CREDIT_RESULTS_S3_KEY", "snapshots/run-lambda-test/results.json")
	monkeypatch.setenv("CREDIT_RESULTS_S3_VERSION_ID", "version-abc")
	monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
	response = lambda_handler(
		{"client": "CLIENT-001", "month": "2026-04"},
		_context("get_client_credit"),
	)

	assert response["status"] == "OK"
	assert response["data"]["records"][0]["calculated_credit"] == "500.00"
	assert calls == [{
		"Bucket": "credit-results-test",
		"Key": "snapshots/run-lambda-test/results.json",
		"VersionId": "version-abc",
	}]


def test_malformed_pinned_s3_snapshot_fails_closed(monkeypatch) -> None:
	class FakeBody:
		def read(self):
			return b'{"broken":true}'
		def close(self):
			pass

	class FakeS3:
		def get_object(self, **_kwargs):
			return {"Body": FakeBody(), "VersionId": "version-abc"}

	class FakeBoto3:
		def client(self, _name):
			return FakeS3()

	monkeypatch.setenv("CREDIT_RESULTS_S3_BUCKET", "credit-results-test")
	monkeypatch.setenv("CREDIT_RESULTS_S3_KEY", "snapshot.json")
	monkeypatch.setenv("CREDIT_RESULTS_S3_VERSION_ID", "version-abc")
	monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
	response = lambda_handler(
		{"client": "CLIENT-001", "month": "2026-04"},
		_context("get_client_credit"),
	)

	assert response["status"] == "UNAVAILABLE"
	assert response["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
	assert response["data"] is None