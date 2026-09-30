"""Focused tests for the deterministic Phase 7 calculation rules."""

from decimal import Decimal, localcontext

import pytest

from src.rules import (
	DIVISION_PRECISION,
	calculate_client_004_credit,
	calculate_credit,
	calculate_standard_credit,
	rule_requires_finance,
	select_rule,
)


def test_standard_rule_applies_raw_breach_below_cap() -> None:
	result = calculate_standard_credit("0.05", "10000")

	assert result.status == "CALCULATED"
	assert result.applied_rate == Decimal("0.05")
	assert result.calculated_credit == Decimal("500.00")


def test_standard_rule_converts_source_floats_via_string_representation() -> None:
	result = calculate_standard_credit(0.05, 10000.0)

	assert result.applied_rate == Decimal("0.05")
	assert result.calculated_credit == Decimal("500.000")


def test_standard_rule_at_cap() -> None:
	result = calculate_standard_credit("0.20", "10000")

	assert result.applied_rate == Decimal("0.20")
	assert result.calculated_credit == Decimal("2000.00")


def test_standard_rule_caps_breach_above_twenty_percent() -> None:
	result = calculate_standard_credit("0.35", "10000")

	assert result.applied_rate == Decimal("0.20")
	assert result.calculated_credit == Decimal("2000.00")


def test_standard_rule_caps_fifty_percent_source_breach() -> None:
	result = calculate_standard_credit("0.50", "10000")

	assert result.applied_rate == Decimal("0.20")
	assert result.calculated_credit == Decimal("2000.00")


def test_standard_rule_accepts_zero_breach() -> None:
	result = calculate_standard_credit("0", "10000")

	assert result.status == "CALCULATED"
	assert result.applied_rate == Decimal("0")
	assert result.calculated_credit == Decimal("0")


def test_standard_rule_preserves_negative_base_credit_sign() -> None:
	result = calculate_standard_credit("0.05", "-10000")

	assert result.calculated_credit == Decimal("-500.00")


def test_standard_rule_returns_controlled_result_for_missing_base_credit() -> None:
	result = calculate_standard_credit("0.05", None)

	assert result.status == "EXCEPTION"
	assert result.exception_code == "MISSING_BASE_CREDIT"
	assert result.calculated_credit is None


@pytest.mark.parametrize("breach", [None, "not-a-number", "-0.01", "1.01"])
def test_standard_rule_returns_controlled_result_for_missing_or_invalid_breach(
	breach: object,
) -> None:
	result = calculate_standard_credit(breach, "10000")

	assert result.status == "EXCEPTION"
	assert result.exception_code in {"MISSING_SLA_BREACH", "INVALID_SLA_BREACH"}
	assert result.calculated_credit is None


def test_standard_rule_rejects_invalid_base_credit() -> None:
	result = calculate_standard_credit("0.05", "unknown")

	assert result.status == "EXCEPTION"
	assert result.exception_code == "INVALID_BASE_CREDIT"


def test_client_004_uses_raw_positive_breach_below_twenty_percent() -> None:
	result = calculate_client_004_credit("0.10")

	assert result.status == "CALCULATED"
	assert result.calculated_credit == Decimal("10000")
	assert result.applied_rate is None


def test_client_004_has_no_standard_cap_above_twenty_percent() -> None:
	result = calculate_client_004_credit("0.50")

	assert result.calculated_credit == Decimal("2000")


def test_client_004_uses_approved_raw_decimal_example() -> None:
	result = calculate_client_004_credit("0.1301")
	with localcontext() as context:
		context.prec = DIVISION_PRECISION
		expected = Decimal("1000") / Decimal("0.1301")

	assert result.calculated_credit == expected


def test_client_004_zero_breach_returns_zero_without_division() -> None:
	result = calculate_client_004_credit("0")

	assert result.status == "CALCULATED"
	assert result.calculated_credit == Decimal("0")
	assert result.exception_code is None


def test_client_004_calculation_does_not_require_finance_base_credit() -> None:
	result = calculate_credit("CLIENT-004", "0.1301")

	assert result.status == "CALCULATED"
	assert result.calculated_credit is not None
	assert result.calculation_rule == "CLIENT_004_SPECIAL"


def test_rule_selection_uses_standard_for_normal_and_unknown_clients() -> None:
	assert select_rule("CLIENT-001") == "STANDARD_CAPPED"
	assert select_rule("CLIENT-NEW") == "STANDARD_CAPPED"


def test_rule_selection_uses_client_004_special_rule() -> None:
	assert select_rule("CLIENT-004") == "CLIENT_004_SPECIAL"


def test_finance_requirement_is_defined_by_rule_not_client() -> None:
	assert rule_requires_finance("STANDARD_CAPPED") is True
	assert rule_requires_finance("CLIENT_004_SPECIAL") is False


def test_dispatch_returns_selected_rule_and_decimal_result() -> None:
	result = calculate_credit("CLIENT-001", "0.05", "10000")

	assert result.calculation_rule == "STANDARD_CAPPED"
	assert result.calculated_credit == Decimal("500.00")
