"""AgentCore Gateway Lambda target for read-only deterministic result tools."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Dict, Optional

if __package__:
	from .agent_tools import ReadOnlyCreditTools
	from .models import CreditResult
	from .snapshot_store import SnapshotError, deserialize_credit_results
else:
	from agent_tools import ReadOnlyCreditTools
	from models import CreditResult
	from snapshot_store import SnapshotError, deserialize_credit_results


TOOL_NAMES = {
	"get_client_credit",
	"get_client_exceptions",
	"get_executive_summary",
	"get_credit_record",
}


def _error_response(code: str, message: str, status: str = "UNAVAILABLE") -> Dict[str, Any]:
	return {
		"status": status,
		"source": "deterministic_credit_engine",
		"data": None,
		"error": {"code": code, "message": message},
		"human_review_required": False,
		"review_reasons": [],
	}


def _gateway_tool_name(context: Any) -> Optional[str]:
	client_context = getattr(context, "client_context", None)
	custom = getattr(client_context, "custom", None)
	if not isinstance(custom, Mapping):
		return None
	qualified_name = custom.get("bedrockAgentCoreToolName")
	if not isinstance(qualified_name, str) or not qualified_name:
		return None
	return qualified_name.rsplit("___", 1)[-1]


def dispatch_gateway_tool(
	event: Any,
	context: Any,
	credit_results: Optional[Sequence[CreditResult]],
) -> Dict[str, Any]:
	"""Dispatch one Gateway Lambda target event to an allowlisted local adapter."""
	if not isinstance(event, Mapping):
		return _error_response(
			"INVALID_TOOL_INPUT",
			"Gateway tool arguments must be a JSON object.",
			status="INVALID_REQUEST",
		)
	tool_name = _gateway_tool_name(context)
	if tool_name is None:
		return _error_response(
			"MISSING_TOOL_NAME",
			"AgentCore Gateway tool name is missing from Lambda context.",
			status="INVALID_REQUEST",
		)
	if tool_name not in TOOL_NAMES:
		return _error_response(
			"UNKNOWN_TOOL",
			"The requested read-only deterministic tool is not registered.",
			status="INVALID_REQUEST",
		)

	tools = ReadOnlyCreditTools(credit_results)
	if tool_name == "get_client_credit":
		return tools.get_client_credit(event.get("client"), event.get("month"))
	if tool_name == "get_client_exceptions":
		return tools.get_client_exceptions(event.get("client"), event.get("month"))
	if tool_name == "get_executive_summary":
		return tools.get_executive_summary(event.get("month"))
	return tools.get_credit_record(event.get("record_id"))


def _load_pinned_s3_snapshot() -> Sequence[CreditResult]:
	"""Load one explicitly pinned S3 object version; never discover or recalculate data."""
	bucket = os.environ.get("CREDIT_RESULTS_S3_BUCKET")
	key = os.environ.get("CREDIT_RESULTS_S3_KEY")
	version_id = os.environ.get("CREDIT_RESULTS_S3_VERSION_ID")
	if not bucket or not key or not version_id or version_id == "null":
		raise SnapshotError(
			"CREDIT_RESULTS_S3_BUCKET, KEY, and a non-null VERSION_ID are required."
		)
	try:
		import boto3
	except ImportError as error:
		raise SnapshotError("boto3 is required by the AWS Lambda snapshot loader.") from error

	response = boto3.client("s3").get_object(
		Bucket=bucket,
		Key=key,
		VersionId=version_id,
	)
	actual_version = response.get("VersionId")
	if actual_version != version_id:
		raise SnapshotError("S3 returned a different snapshot version than configured.")
	body_stream = response["Body"]
	try:
		body = body_stream.read()
	finally:
		body_stream.close()
	return deserialize_credit_results(body)


def make_lambda_handler(
	snapshot_loader: Callable[[], Optional[Sequence[CreditResult]]],
) -> Callable[[Any, Any], Dict[str, Any]]:
	"""Create a testable Lambda entry point around an injected read-only snapshot loader."""
	def handler(event: Any, context: Any) -> Dict[str, Any]:
		try:
			results = snapshot_loader()
		except SnapshotError as error:
			return _error_response("SNAPSHOT_UNAVAILABLE", str(error))
		except Exception:
			return _error_response(
				"SNAPSHOT_UNAVAILABLE",
				"The configured deterministic result snapshot could not be loaded.",
			)
		return dispatch_gateway_tool(event, context, results)

	return handler


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
	"""AWS Lambda entry point for an AgentCore Gateway Lambda target."""
	return make_lambda_handler(_load_pinned_s3_snapshot)(event, context)


__all__ = [
	"TOOL_NAMES",
	"dispatch_gateway_tool",
	"lambda_handler",
	"make_lambda_handler",
]