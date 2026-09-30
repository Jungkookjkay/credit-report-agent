"""Deterministic calculation rules selected through client configuration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

import pandas as pd
import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "client_rules.yaml"
DIVISION_PRECISION = 50


@dataclass(frozen=True)
class RuleResult:
	"""Calculation outcome independent of Finance matching status."""

	calculation_rule: str
	status: Literal["CALCULATED", "EXCEPTION"]
	applied_rate: Decimal | None = None
	calculated_credit: Decimal | None = None
	exception_code: str | None = None
	explanation: str | None = None


RuleFunction = Callable[[Any, Any, Decimal], RuleResult]


def _is_missing(value: Any) -> bool:
	if value is None or (isinstance(value, str) and not value.strip()):
		return True
	try:
		missing = pd.isna(value)
		return bool(missing) if not hasattr(missing, "__len__") else False
	except (TypeError, ValueError):
		return False


def _to_decimal(value: Any) -> tuple[Decimal | None, str | None]:
	if _is_missing(value):
		return None, "MISSING"
	if isinstance(value, bool):
		return None, "INVALID"
	try:
		number = Decimal(str(value))
	except (InvalidOperation, TypeError, ValueError):
		return None, "INVALID"
	if not number.is_finite():
		return None, "INVALID"
	return number, None


def _parse_breach(value: Any) -> tuple[Decimal | None, str | None]:
	breach, issue = _to_decimal(value)
	if issue is not None:
		return None, issue
	if breach is None or breach < 0 or breach > 1:
		return None, "INVALID"
	return breach, None


def _exception(
	rule: str,
	code: str,
	explanation: str,
	*,
	applied_rate: Decimal | None = None,
) -> RuleResult:
	return RuleResult(
		calculation_rule=rule,
		status="EXCEPTION",
		applied_rate=applied_rate,
		exception_code=code,
		explanation=explanation,
	)


def calculate_standard_credit(
	breach_pct: Any,
	base_credit: Any,
	standard_cap: Decimal = Decimal("0.20"),
) -> RuleResult:
	"""Apply min(raw decimal breach, cap) to the matched Finance Base Credit."""
	breach, breach_issue = _parse_breach(breach_pct)
	if breach_issue is not None:
		code = "MISSING_SLA_BREACH" if breach_issue == "MISSING" else "INVALID_SLA_BREACH"
		return _exception("STANDARD_CAPPED", code, "SLA Breach % is missing or invalid.")

	base, base_issue = _to_decimal(base_credit)
	if base_issue is not None:
		code = "MISSING_BASE_CREDIT" if base_issue == "MISSING" else "INVALID_BASE_CREDIT"
		return _exception("STANDARD_CAPPED", code, "Finance Base Credit is missing or invalid.")

	assert breach is not None and base is not None
	applied_rate = min(breach, standard_cap)
	with localcontext() as context:
		context.prec = DIVISION_PRECISION
		calculated_credit = base * applied_rate
	return RuleResult(
		calculation_rule="STANDARD_CAPPED",
		status="CALCULATED",
		applied_rate=applied_rate,
		calculated_credit=calculated_credit,
		explanation="Raw decimal SLA breach capped at the configured standard rate.",
	)


def calculate_client_004_credit(breach_pct: Any) -> RuleResult:
	"""Apply CLIENT-004's 1000 / raw decimal breach formula without a cap."""
	breach, breach_issue = _parse_breach(breach_pct)
	if breach_issue is not None:
		code = "MISSING_SLA_BREACH" if breach_issue == "MISSING" else "INVALID_SLA_BREACH"
		return _exception("CLIENT_004_SPECIAL", code, "SLA Breach % is missing or invalid.")

	assert breach is not None
	if breach == 0:
		return RuleResult(
			calculation_rule="CLIENT_004_SPECIAL",
			status="CALCULATED",
			calculated_credit=Decimal("0"),
			explanation="Zero breach produces zero credit; division is not performed.",
		)

	with localcontext() as context:
		context.prec = DIVISION_PRECISION
		credit = Decimal("1000") / breach
	return RuleResult(
		calculation_rule="CLIENT_004_SPECIAL",
		status="CALCULATED",
		calculated_credit=credit,
		explanation="1000 divided by the raw decimal SLA breach; no standard cap applies.",
	)


def load_rule_config(config_path: str | Path | None = None) -> dict[str, Any]:
	"""Load and minimally validate rule selection settings from YAML."""
	path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
	with path.open(encoding="utf-8") as config_file:
		config = yaml.safe_load(config_file)
	if not isinstance(config, dict):
		raise ValueError(f"Rule configuration must be a YAML mapping: {path}")
	if not isinstance(config.get("client_rules"), dict):
		raise ValueError("Rule configuration requires a client_rules mapping.")
	if not isinstance(config.get("default_rule"), str):
		raise ValueError("Rule configuration requires a default_rule name.")
	return config


def _normalize_rule_name(value: Any) -> str:
	return str(value).strip().upper()


def _selected_rule(client: Any, config: Mapping[str, Any]) -> str:
	client_rules = {
		_normalize_rule_name(name): _normalize_rule_name(rule)
		for name, rule in config["client_rules"].items()
	}
	normalized_client = _normalize_rule_name(client)
	return client_rules.get(normalized_client, _normalize_rule_name(config["default_rule"]))


def select_rule(client: Any, config_path: str | Path | None = None) -> str:
	"""Return the configured rule name; unknown clients use the documented default."""
	return _selected_rule(client, load_rule_config(config_path))


def _run_standard(breach_pct: Any, base_credit: Any, cap: Decimal) -> RuleResult:
	return calculate_standard_credit(breach_pct, base_credit, cap)


def _run_client_004(breach_pct: Any, _base_credit: Any, _cap: Decimal) -> RuleResult:
	return calculate_client_004_credit(breach_pct)


RULE_REGISTRY: dict[str, RuleFunction] = {
	"STANDARD_CAPPED": _run_standard,
	"CLIENT_004_SPECIAL": _run_client_004,
}
RULE_REQUIRES_FINANCE: dict[str, bool] = {
	"STANDARD_CAPPED": True,
	"CLIENT_004_SPECIAL": False,
}


def rule_requires_finance(rule_name: str) -> bool:
	"""Return whether the selected calculation rule requires a Finance match."""
	try:
		return RULE_REQUIRES_FINANCE[rule_name]
	except KeyError as error:
		raise ValueError(f"Unknown calculation rule: {rule_name}") from error


def calculate_credit(
	client: Any,
	breach_pct: Any,
	base_credit: Any = None,
	*,
	config_path: str | Path | None = None,
) -> RuleResult:
	"""Select and execute one calculation rule without performing any matching."""
	config = load_rule_config(config_path)
	rule_name = _selected_rule(client, config)
	try:
		rule = RULE_REGISTRY[rule_name]
	except KeyError as error:
		raise ValueError(f"Configured calculation rule is not registered: {rule_name}") from error

	if rule_name == "STANDARD_CAPPED":
		cap, issue = _to_decimal(config.get("standard_cap"))
		if issue is not None or cap is None or cap < 0:
			raise ValueError("standard_cap must be a finite, non-negative decimal value.")
	else:
		cap = Decimal("0.20")
	return rule(breach_pct, base_credit, cap)


__all__ = [
	"DIVISION_PRECISION",
	"RULE_REGISTRY",
	"RULE_REQUIRES_FINANCE",
	"RuleResult",
	"calculate_client_004_credit",
	"calculate_credit",
	"calculate_standard_credit",
	"load_rule_config",
	"rule_requires_finance",
	"select_rule",
]
