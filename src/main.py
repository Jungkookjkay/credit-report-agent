"""Runnable deterministic batch pipeline and local CreditResult snapshot publisher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import pandas as pd

if __package__:
	from .audit import Phase12Reconciliation, run_phase12_reconciliations
	from .excel_export import add_client_executive_summaries, export_phase10_workbooks
	from .ingestion import load_all_devops_files, load_devops_file, load_finance_file
	from .matching import match_sla_record
	from .models import SLARecord
	from .normalization import normalize_devops_records, normalize_finance_records
	from .processing import apply_composite_allocation_control, process_sla_records
	from .reporting import (
		build_client_totals,
		build_detailed_report,
		build_exceptions_report,
		build_executive_summary,
		reconcile_report_datasets,
	)
	from .snapshot_store import write_credit_results_snapshot
	from .validation import validate_devops, validate_finance
else:
	from audit import Phase12Reconciliation, run_phase12_reconciliations
	from excel_export import add_client_executive_summaries, export_phase10_workbooks
	from ingestion import load_all_devops_files, load_devops_file, load_finance_file
	from matching import match_sla_record
	from models import SLARecord
	from normalization import normalize_devops_records, normalize_finance_records
	from processing import apply_composite_allocation_control, process_sla_records
	from reporting import (
		build_client_totals,
		build_detailed_report,
		build_exceptions_report,
		build_executive_summary,
		reconcile_report_datasets,
	)
	from snapshot_store import write_credit_results_snapshot
	from validation import validate_devops, validate_finance


SUPPORTED_WORKBOOK_SUFFIXES = {".xlsx", ".xls", ".xlsm"}


class ReconciliationFailure(RuntimeError):
	"""Raised when a mandatory reconciliation fails; no result snapshot is published."""


def _discover_finance_file(finance_path: str | Path) -> Path:
	path = Path(finance_path).expanduser().resolve()
	if path.is_file():
		if path.suffix.lower() not in SUPPORTED_WORKBOOK_SUFFIXES:
			raise ValueError("Finance input must be an Excel workbook.")
		return path
	if not path.is_dir():
		raise FileNotFoundError("Finance path does not exist: {0}".format(path))
	files = sorted(
		candidate
		for candidate in path.rglob("*")
		if candidate.is_file()
		and candidate.suffix.lower() in SUPPORTED_WORKBOOK_SUFFIXES
		and not candidate.name.startswith("~$")
	)
	if len(files) != 1:
		raise ValueError(
			"Expected exactly one Finance workbook in {0}, found {1}. "
			"Pass --finance-file to select it explicitly.".format(path, len(files))
		)
	return files[0]


def _to_sla_record(row: pd.Series) -> SLARecord:
	row_number = row.get("source_row_number")
	return SLARecord(
		client=row.get("Client"),
		raw_market_code=row.get("Market Code"),
		market_name=row.get("Market Name"),
		market_description=row.get("Market Description"),
		sla_breach_pct=row.get("SLA Breach %"),
		source_file=row.get("source_file"),
		source_sheet=row.get("source_sheet"),
		source_row_number=int(row_number) if pd.notna(row_number) else None,
		filename_client_hint=row.get("filename_client_hint"),
		filename_month_hint=row.get("filename_month_hint"),
		workbook_month_hint=row.get("workbook_month_hint"),
		month_conflict=(
			None if pd.isna(row.get("month_conflict")) else bool(row.get("month_conflict"))
		),
		processing_run_id=row.get("processing_run_id"),
	)


def _require_controls_passed(name: str, controls: Any) -> None:
	if controls.passed:
		return
	failed = [
		"{0}: expected={1}; actual={2}; {3}".format(
			control.control,
			control.expected,
			control.actual,
			control.explanation,
		)
		for control in controls.controls
		if control.status != "PASS"
	]
	raise ReconciliationFailure(
		"{0} failed; no CreditResult snapshot was published. {1}".format(
			name,
			" | ".join(failed),
		)
	)


def publish_snapshot_after_controls(
	snapshot_path: str | Path,
	credit_results: Sequence[Any],
	phase12_controls: Phase12Reconciliation,
) -> str:
	"""Publish a lossless snapshot only after all Phase 12 controls pass."""
	_require_controls_passed("Phase 12", phase12_controls)
	return write_credit_results_snapshot(snapshot_path, credit_results)


def run_pipeline(
	*,
	finance_path: str | Path = "input/finance",
	devops_directory: str | Path = "input/devops",
	output_directory: str | Path = "output",
	snapshot_path: str | Path | None = None,
) -> Dict[str, Any]:
	"""Run deterministic phases and publish a snapshot only after reconciliations pass."""
	finance_file = _discover_finance_file(finance_path)
	finance_raw = load_finance_file(finance_file)
	devops_raw = load_all_devops_files(devops_directory)
	finance = normalize_finance_records(finance_raw)
	devops = normalize_devops_records(devops_raw)

	finance_exceptions = validate_finance(finance_raw)
	devops_exceptions = validate_devops(devops_raw)
	match_results = [
		match_sla_record(row, finance)
		for _, row in devops.iterrows()
	]
	sla_records = [_to_sla_record(row) for _, row in devops.iterrows()]
	normalized_markets = devops["normalized_market_code"].tolist()
	match_results = apply_composite_allocation_control(
		sla_records,
		match_results,
		normalized_markets,
	)
	credit_results = process_sla_records(
		sla_records,
		match_results,
		normalized_devops_markets=normalized_markets,
		source_exceptions=tuple(finance_exceptions + devops_exceptions),
	)

	detailed = build_detailed_report(credit_results)
	client_totals = build_client_totals(credit_results)
	executive = build_executive_summary(credit_results)
	exceptions = build_exceptions_report(credit_results)
	phase9_controls = reconcile_report_datasets(
		credit_results,
		detailed,
		client_totals,
		executive,
		exceptions,
	)
	if not phase9_controls.passed:
		failed_checks = [name for name, passed in phase9_controls.checks.items() if not passed]
		raise ReconciliationFailure(
			"Phase 9 dataset reconciliation failed: {0}".format(", ".join(failed_checks))
		)

	output_path = Path(output_directory).expanduser().resolve()
	output_path.mkdir(parents=True, exist_ok=True)
	export_phase10_workbooks(
		detailed,
		executive,
		client_totals,
		exceptions,
		output_path,
	)
	client_workbooks = add_client_executive_summaries(
		detailed,
		client_totals,
		output_path,
	)
	phase12_controls = run_phase12_reconciliations(
		sla_records,
		match_results,
		credit_results,
		detailed,
		client_totals,
		executive,
		exceptions,
		output_dir=output_path,
	)
	_require_controls_passed("Phase 12", phase12_controls)

	if not credit_results or not credit_results[0].processing_run_id:
		raise ValueError("No processing_run_id is available for the validated result snapshot.")
	if any(
		result.processing_run_id != credit_results[0].processing_run_id
		for result in credit_results
	):
		raise ValueError("CreditResults do not belong to one processing run.")
	if snapshot_path is None:
		snapshot_destination = (
			output_path
			/ "snapshots"
			/ credit_results[0].processing_run_id
			/ "credit-results.v1.json"
		)
	else:
		snapshot_destination = Path(snapshot_path).expanduser().resolve()
	checksum = publish_snapshot_after_controls(
		snapshot_destination,
		credit_results,
		phase12_controls,
	)
	return {
		"processing_run_id": credit_results[0].processing_run_id,
		"finance_file": str(finance_file),
		"devops_file_count": devops_raw["source_file"].nunique(),
		"sla_record_count": len(sla_records),
		"credit_result_count": len(credit_results),
		"phase9_reconciliation_passed": phase9_controls.passed,
		"phase12_control_count": len(phase12_controls.controls),
		"phase12_passed": phase12_controls.passed_count,
		"phase12_failed": phase12_controls.failed_count,
		"client_workbooks": [str(path) for path in client_workbooks.values()],
		"executive_workbook": str(output_path / "Executive_Report.xlsx"),
		"exceptions_workbook": str(output_path / "Exceptions.xlsx"),
		"snapshot_path": str(snapshot_destination),
		"snapshot_sha256": checksum,
	}


def _parse_args(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Run deterministic credit processing, reconciliations, reports, and local snapshot export."
	)
	parser.add_argument("--finance-file", default="input/finance", help="Finance workbook or directory containing exactly one workbook.")
	parser.add_argument("--devops-dir", default="input/devops", help="Directory from which DevOps workbooks are discovered.")
	parser.add_argument("--output-dir", default="output", help="Directory for generated Excel reports and default snapshots.")
	parser.add_argument("--snapshot-path", help="Optional explicit snapshot path; by default a unique run-versioned path is used.")
	return parser.parse_args(arguments)


def main(arguments: Optional[Sequence[str]] = None) -> int:
	"""CLI entry point. Errors fail the command and snapshots are gated on controls."""
	args = _parse_args(arguments)
	try:
		result = run_pipeline(
			finance_path=args.finance_file,
			devops_directory=args.devops_dir,
			output_directory=args.output_dir,
			snapshot_path=args.snapshot_path,
		)
	except Exception as error:
		print("Pipeline failed: {0}".format(error), file=sys.stderr)
		return 1
	print(json.dumps(result, indent=2, sort_keys=True))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())


__all__ = [
	"ReconciliationFailure",
	"main",
	"publish_snapshot_after_controls",
	"run_pipeline",
]