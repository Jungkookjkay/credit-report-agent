"""Tests for the deterministic batch entry point and snapshot publication gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.audit import Phase12Reconciliation, ReconciliationControl
from src.main import ReconciliationFailure, main, publish_snapshot_after_controls
from src.snapshot_store import load_credit_results_snapshot


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_is_not_published_when_phase12_controls_fail(tmp_path) -> None:
	snapshot_path = tmp_path / "must-not-publish.json"
	controls = Phase12Reconciliation(
		controls=(
			ReconciliationControl(
				control="test population control",
				expected="244",
				actual="243",
				status="FAIL",
				explanation="Synthetic failure for gate test.",
			),
		)
	)

	with pytest.raises(ReconciliationFailure, match="no CreditResult snapshot was published"):
		publish_snapshot_after_controls(snapshot_path, [], controls)
	assert not snapshot_path.exists()


def test_main_cli_runs_existing_pipeline_controls_and_publishes_local_snapshot(
	tmp_path,
	capsys,
) -> None:
	output_dir = tmp_path / "reports"
	snapshot_path = tmp_path / "snapshots" / "credit-results.v1.json"
	exit_code = main(
		[
			"--finance-file",
			str(ROOT / "input/finance/00_Anonymized_Finance_APR26.xlsx"),
			"--devops-dir",
			str(ROOT / "input/devops"),
			"--output-dir",
			str(output_dir),
			"--snapshot-path",
			str(snapshot_path),
		]
	)

	assert exit_code == 0
	command_summary = json.loads(capsys.readouterr().out)
	assert command_summary["sla_record_count"] == 244
	assert command_summary["credit_result_count"] == 244
	assert command_summary["phase12_control_count"] == 14
	assert command_summary["phase12_passed"] == 14
	assert command_summary["phase12_failed"] == 0
	assert snapshot_path.exists()
	assert len(load_credit_results_snapshot(snapshot_path)) == 244
	assert (output_dir / "CLIENT-001_APR26_Credit_Report.xlsx").exists()
	assert (output_dir / "Executive_Report.xlsx").exists()
	assert (output_dir / "Exceptions.xlsx").exists()