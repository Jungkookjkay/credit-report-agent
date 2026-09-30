"""Deterministic normalized views that preserve all source values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


CLIENT_MARKET_PREFIX_RULES = {"CLIENT-002": "client002_"}
FINANCE_MARKET_CODE_DELIMITERS = re.compile(r"[\r\n,;]+")
APPROVED_CUSTOMER_WD_PATTERN = re.compile(r"^(CLIENT-[0-9]+)-WD-[0-9]+$", re.IGNORECASE)


@dataclass(frozen=True)
class FinanceClientResolution:
	"""Derived Finance client value and its source-field resolution method."""

	resolved_client: str | None
	client_resolution_method: str | None


@dataclass(frozen=True)
class FinanceMarketCodeTokens:
	"""Normalized Finance code tokens plus any tokenization limitation."""

	normalized_tokens: tuple[str, ...]
	tokenization_safe: bool
	tokenization_note: str | None = None


def normalize_text(value: Any) -> str | None:
	"""Trim, collapse whitespace, and uppercase a value without altering it."""
	if value is None or pd.isna(value):
		return None
	normalized = re.sub(r"\s+", " ", str(value).strip()).upper()
	return normalized or None


def normalize_client(value: Any) -> str | None:
	"""Return a case- and whitespace-normalized client identifier."""
	return normalize_text(value)


def normalize_market_code(value: Any) -> str | None:
	"""Return a case- and whitespace-normalized market code."""
	return normalize_text(value)


def resolve_finance_client(
	ultimate_parent_nhn: Any,
	customer_wd: Any,
) -> FinanceClientResolution:
	"""Resolve a derived client without changing either Finance source value."""
	normalized_parent = normalize_client(ultimate_parent_nhn)
	if normalized_parent is not None:
		return FinanceClientResolution(normalized_parent, "ULTIMATE_PARENT")

	normalized_customer = normalize_client(customer_wd)
	if normalized_customer is None:
		return FinanceClientResolution(None, None)

	match = APPROVED_CUSTOMER_WD_PATTERN.fullmatch(normalized_customer)
	if match is None:
		return FinanceClientResolution(None, None)
	return FinanceClientResolution(match.group(1), "CUSTOMER_WD_FALLBACK")


def normalize_devops_market_code(
	market_code: Any,
	client: Any,
) -> tuple[str | None, bool]:
	"""Normalize a DevOps market code and report whether the approved prefix applied.

	The ``client002_`` rule is scoped to CLIENT-002 and is matched only at the
	beginning of the source value. Other underscores and client prefixes remain.
	"""
	if market_code is None or pd.isna(market_code):
		return None, False

	raw_text = str(market_code).strip()
	normalized_client = normalize_client(client)
	prefix = CLIENT_MARKET_PREFIX_RULES.get(normalized_client)
	prefix_removed = bool(prefix and raw_text.casefold().startswith(prefix.casefold()))
	if prefix_removed and prefix is not None:
		raw_text = raw_text[len(prefix) :]
	return normalize_market_code(raw_text), prefix_removed


def tokenize_finance_market_code(value: Any) -> FinanceMarketCodeTokens:
	"""Split only documented composite delimiters and preserve ambiguous parts.

	Line breaks and commas are present in the supplied Finance data. Semicolons
	are included as a documented delimiter. Slash and parenthetical qualifiers
	remain inside their token and cause the result to carry a limitation note.
	"""
	if value is None or pd.isna(value):
		return FinanceMarketCodeTokens((), True)

	raw_text = str(value)
	parts = FINANCE_MARKET_CODE_DELIMITERS.split(raw_text)
	tokens = tuple(
		normalized
		for part in parts
		if (normalized := normalize_market_code(part)) is not None
	)

	limitations: list[str] = []
	if "/" in raw_text:
		limitations.append("slash retained inside token; not treated as a delimiter")
	if "(" in raw_text or ")" in raw_text:
		limitations.append("parenthetical qualifier retained inside token")

	return FinanceMarketCodeTokens(
		normalized_tokens=tokens,
		tokenization_safe=not limitations,
		tokenization_note="; ".join(limitations) or None,
	)


def normalize_finance_records(finance: pd.DataFrame) -> pd.DataFrame:
	"""Return Finance rows with derived client and market representations."""
	result = finance.copy(deep=True)
	resolutions = [
		resolve_finance_client(parent, customer)
		for parent, customer in zip(
			result.get("Ultimate Parent NHN", pd.Series(index=result.index, dtype=object)),
			result.get("Customer WD", pd.Series(index=result.index, dtype=object)),
		)
	]
	tokenizations = [
		tokenize_finance_market_code(value)
		for value in result.get("Market Code", pd.Series(index=result.index, dtype=object))
	]

	result["resolved_client"] = [item.resolved_client for item in resolutions]
	result["client_resolution_method"] = [
		item.client_resolution_method for item in resolutions
	]
	result["normalized_market_code"] = [
		normalize_market_code(value)
		for value in result.get("Market Code", pd.Series(index=result.index, dtype=object))
	]
	result["normalized_market_tokens"] = [list(item.normalized_tokens) for item in tokenizations]
	result["market_code_tokenization_safe"] = [
		item.tokenization_safe for item in tokenizations
	]
	result["market_code_tokenization_note"] = [
		item.tokenization_note for item in tokenizations
	]
	return result


def normalize_devops_records(devops: pd.DataFrame) -> pd.DataFrame:
	"""Return DevOps rows with normalized client and market-code representations."""
	result = devops.copy(deep=True)
	clients = result.get("Client", pd.Series(index=result.index, dtype=object))
	market_codes = result.get("Market Code", pd.Series(index=result.index, dtype=object))
	normalized_market_codes = [
		normalize_devops_market_code(code, client)
		for code, client in zip(market_codes, clients)
	]
	result["normalized_client"] = [normalize_client(client) for client in clients]
	result["normalized_market_code"] = [item[0] for item in normalized_market_codes]
	result["client002_prefix_removed"] = [item[1] for item in normalized_market_codes]
	return result


__all__ = [
	"CLIENT_MARKET_PREFIX_RULES",
	"FinanceClientResolution",
	"FinanceMarketCodeTokens",
	"normalize_client",
	"normalize_devops_market_code",
	"normalize_devops_records",
	"normalize_finance_records",
	"normalize_market_code",
	"normalize_text",
	"resolve_finance_client",
	"tokenize_finance_market_code",
]
