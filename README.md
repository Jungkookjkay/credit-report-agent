# Credit Report Agent

## 1. Problem Statement

This prototype combines DevOps SLA breach records with Finance monthly fee-schedule data to identify service/SLA Credit receivables. It deterministically matches client and market records, assigns Finance-match confidence, calculates credits, retains exceptions, and produces client and cross-client Excel reports. This is a **service/SLA Credit receivable**, not a lending or credit-risk calculation.

## 2. Architecture Summary

`Excel Inputs` -> `Ingestion` -> `Validation` -> `Normalization` -> `Matching` -> `Rule Engine` -> `Credit Processing` -> `Audit / Reconciliation` -> `Reports` -> optional `Bedrock Assistant`

**Python is the source of truth for financial calculations and matching confidence.** AI does not calculate or approve financial Credits. The deterministic engine is independent of AWS Bedrock.

Implemented modules include `ingestion.py`, `validation.py`, `normalization.py`, `matching.py`, `rules.py`, `processing.py`, `reporting.py`, `excel_export.py`, `audit.py`, `snapshot_store.py`, `agent_tools.py`, `lambda_handler.py`, and the `main.py` batch entry point. AWS services are not required for local processing.

## 3. Setup Instructions

The repository does not pin a Python version. Its dependencies are declared in `requirements.txt`; validation was run with Python 3.9.6.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Dependencies: pandas, openpyxl, numpy, pytest, and PyYAML. `requirements-lambda.txt` adds boto3 and the adapter runtime dependencies for packaging the AWS Lambda target.

## 4. How to Run

Place the Finance workbook in `input/finance/` and DevOps workbooks in `input/devops/`. The ingestion functions load a specified Finance workbook and dynamically discover DevOps workbooks. Outputs are under `output/`.

Run the deterministic batch pipeline, generate the reports, run Phase 9/12 controls, and write a versioned local `CreditResult` snapshot only after every control passes:

```sh
python3 -m src.main \
	--finance-file input/finance/00_Anonymized_Finance_APR26.xlsx \
	--devops-dir input/devops \
	--output-dir output
```

By default, the snapshot is written under `output/snapshots/<processing_run_id>/credit-results.v1.json`. Pass `--snapshot-path` to choose another local path. Finance file, DevOps directory, output directory, and snapshot path can be supplied explicitly; a Finance directory must contain exactly one workbook. The command regenerates workbook files in the selected output directory.

## 5. How to Run Tests

```sh
python3 -m pytest -ra
```

The verified suite completed with **126 passed, 0 failed**. Tests cover formulas, matching, validation, CLIENT-004, processing, reporting, workbook controls, Phase 12 reconciliations, snapshot round-trips, Lambda dispatch, reproducibility, offline operation, and AI independence. This is the result for the tested repository state, not a guarantee for future changes.

## 6. Inputs Expected

The Finance workbook supplies client/market fee-schedule records, including `Monthly LCY 04/30/26` Base Credit and currency. DevOps workbooks supply client, market, month-description hints, and decimal SLA Breach % values. Multiple DevOps files are discovered dynamically.

Ingestion retains source file, sheet, and row lineage and does not modify source workbooks. Client and month hints are compared cautiously; conflicts are retained as exceptions, not silently resolved. In the supplied assessment data, the CLIENT-002 MAY26 filename conflicts with workbook content describing April.

## 7. Outputs Generated

The current `output/` directory contains:

- `CLIENT-001_APR26_Credit_Report.xlsx`
- `CLIENT-002_APR26_Credit_Report.xlsx`
- `CLIENT-004_APR26_Credit_Report.xlsx`
- `Executive_Report.xlsx`
- `Exceptions.xlsx`

Each client workbook has `Executive Summary`, `Credit Detail`, and `Notes & Methodology` sheets. The cross-client workbook keeps `Executive Summary`, `Client Totals`, and `Notes & Methodology`; `Exceptions.xlsx` includes `Exceptions` and `Pending Month Conflict`.

The **Client Executive Summary** is the concise client/CSM view. **Credit Detail** is the market-level calculation and audit view. **Executive Report** is the cross-client management view. **Exceptions Report** lists records requiring attention.

## 8. Matching Hierarchy

Matching is scoped to the resolved client before market candidates are considered. Finance market-code fields are tokenized on line breaks, commas, semicolons, and slashes; normalized DevOps codes must equal a complete normalized token. Substring matches are not used, so `CME` does not match `CMEC`.

Each eligible Finance source row is one candidate, even when several codes appear in that row or separate source rows have identical values. One eligible row produces a HIGH market match (`EXACT_CODE_MATCH` for a single atomic code or `COMPOSITE_CODE_MATCH` for a composite field). More than one eligible Finance source row for the client and token produces `AMBIGUOUS` / LOW; no candidate is selected.

Before selecting Base Credit, batch processing checks whether the same composite Finance source row is used by multiple distinct normalized DevOps markets for the same client and resolved reporting month. If so, each affected result becomes `AMBIGUOUS` / LOW with `COMPOSITE_ALLOCATION_AMBIGUITY`; the Finance row is retained as a candidate, but its Base Credit is not selected and no credit is calculated. Multiple tokens alone do not trigger this control, and it also applies to zero-breach rows.

No market aliases are approved in `config/market_aliases.yaml`, and alias/fuzzy matching is not currently used. The current assessment results use HIGH and LOW; no MEDIUM match results are present. MEDIUM is an allowed confidence category in the model for an explicitly approved policy, not a claim about current results.

HIGH denotes a unique deterministic Finance match. LOW denotes ambiguous, unmatched, or otherwise non-authoritative Finance matching. Confidence describes **Finance market-match reliability**, not calculation accuracy or AI confidence. Ambiguous candidates are never resolved by selecting the first Finance record.

## 9. Calculation Rules

For default clients:

```text
Applied Rate = min(SLA Breach %, 20%)
Calculated Credit = Base Credit x Applied Rate
```

Source breach values are decimal fractions, such as `0.05` for 5%; they are used as fractions, not multiplied by 100 before calculation. Base Credit is Finance `Monthly LCY 04/30/26`. Finance currency is retained and no FX conversion is performed.

CLIENT-004 uses its separate rule:

```text
Calculated Credit = 1000 / raw breach
```

The approved interpretation uses the raw stored decimal: `0.1301 -> 1000 / 0.1301`, not `1000 / 13.01`. Finance Base Credit is not required for this arithmetic. Zero breach produces zero without division. Where no authoritative Finance match supplies currency, currency remains unknown.

## 10. Assumptions / Assessment Decisions

- The standard 20% value is a cap, not a fixed rate.
- Finance currency is authoritative where a Finance match supplies it; currencies are not converted.
- Raw identifiers and source values are retained alongside normalized values.
- Only the observed leading `client002_` prefix is removed for CLIENT-002 matching; no generic underscore normalization is applied.
- Composite Finance market codes are tokenized on observed line-break, comma, semicolon, and slash delimiters. Matching uses exact normalized token equality, never substring matching; parenthetical qualifiers remain part of their token.
- A single Finance source row containing multiple market tokens remains one unique market candidate. It is not reused across multiple distinct DevOps markets without an explicit allocation rule; those results are `COMPOSITE_ALLOCATION_AMBIGUITY`.
- Multiple eligible Finance source rows remain `AMBIGUOUS`, including rows with identical normalized values. A single-market use of a composite row is not allocation-ambiguous.
- A missing Finance parent may use Customer WD fallback only for the approved `CLIENT-<digits>-WD-<digits>` pattern.
- The MAY26 filename/workbook month conflict remains unresolved and excluded from authoritative financial totals.
- CLIENT-004 calculation eligibility is separate from Finance matching; unsupported currency is never inferred.

## 11. Known Exceptions / Data Quality Findings

The supplied data includes the unresolved CLIENT-002 MAY26 month conflict, ambiguous and unmatched market candidates, exact duplicate/non-unique Finance conditions, a missing Finance parent with an approved Customer WD fallback case, and CLIENT-004 markets without Finance matches or authoritative currency. These conditions remain visible in result and exception data rather than being silently repaired.

## 12. Controls and Reconciliation

The reusable Phase 12 controls in `src/audit.py` compare expected and actual values with explicit PASS/FAIL status. The verified assessment run completed **14 controls: 14 passed, 0 failed**. Controls cover SLA outcomes, client/month populations, breached outcomes, confidence eligibility, detail and executive populations/totals, currencies and null/zero semantics, exception-code occurrences versus unique affected records, confidence propagation, workbook values, and source lineage.

Of 22 breached records across all supplied files, 19 authoritative April records were eligible for matching and all have HIGH/MEDIUM/LOW confidence. The other 3 breached CLIENT-002 MAY26 rows were `INELIGIBLE / MONTH_CONFLICT` before matching; they intentionally retain null confidence. This is not missing match data. The 66 MAY26 rows remain outside authoritative April or May totals.

## 13. AI Component

Bedrock is optional and is **not connected to the Python engine in this repository**. `src/agent_tools.py` provides local read-only adapters over supplied `CreditResult` objects. `src/snapshot_store.py` serializes and validates lossless, checksummed snapshots; `src/main.py` publishes a local snapshot only after Phase 12 controls pass; and `src/lambda_handler.py` is a Lambda target that reads a pinned S3 object version and dispatches the four approved tools. The local adapters and batch pipeline need no AWS credentials or network access. AWS deployment, S3 upload, Gateway configuration, and Harness connection have not been performed. The system prompt and integration design are in `ai/`; the manual Playground/AgentCore proof of concept is external to this repository.

## 14. Productionization Approach

The prototype could evolve to `source systems -> scheduled ingestion (S3/API/database) -> validation -> deterministic matching/rules -> persisted results -> reporting/API -> optional Bedrock assistant -> human exception workflow`. Production work would add orchestration, persistent audit storage, monitoring/logging, access controls, versioned configuration, and human approval for ambiguous matches. The take-home solution does not require cloud infrastructure.

## Verified Assessment Run

These totals describe only the supplied anonymized assessment files; they are not hardcoded business expectations.

| Dataset | Markets / rows | Breached | Calculated breached | Authoritative result |
|---|---:|---:|---:|---|
| CLIENT-001 APR | 56 | 7 | 2 | EUR `702.960636375` |
| CLIENT-002 APR | 66 | 8 | 2 | USD `463.360430778` |
| CLIENT-004 APR | 56 | 4 | 4 special-rule calculations | Currency unspecified: `21469.4194183928702104404702486177512721127279580188` |
| CLIENT-002 MAY26 | 66 | - | - | Unresolved `MONTH_CONFLICT`; excluded from authoritative totals |