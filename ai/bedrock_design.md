# Bedrock / AI Integration Design

## Status

| Area | Status |
|---|---|
| Deterministic Python engine, reports, and Phase 12 controls | Implemented and regression-tested locally without AWS |
| Phase 14 assistant system prompt | Implemented in `ai/system_prompt.md` |
| Bedrock model inference and authority-boundary proof of concept | Manually tested in Bedrock Playground |
| AgentCore Harness `credit_report_assistant` | Created manually; currently configured with Nova 2 Lite v1 and the authority/no-hallucination instructions |
| Local read-only Python adapters and JSON responses | Implemented in `src/agent_tools.py`; tested without AWS |
| Lossless CreditResult snapshot and checksum loader | Implemented in `src/snapshot_store.py` |
| Deterministic batch runner and reconciliation-gated snapshot output | Implemented in `src/main.py` |
| AgentCore Gateway Lambda target handler | Implemented locally in `src/lambda_handler.py`; requires AWS configuration to execute against S3 |
| AgentCore tool registration and structured exchange | Designed below; not connected to AgentCore |
| AgentCore-to-local-Python invocation | Not implemented; the Harness does not currently call this repository or its Python engine |
| Deployment/API, authentication, persistence, monitoring, and orchestration | Optional future productionization |

The Python engine, batch snapshot writer, and local read-only adapters remain usable without AWS. Finance market-code fields are tokenized on line breaks, commas, semicolons, and slashes; a normalized DevOps code must equal a complete normalized token, never a substring. One composite Finance source row can be a unique HIGH-confidence market match, but its Base Credit is not reused across distinct DevOps markets in the same client/month without an explicit allocation rule. Those affected results are `AMBIGUOUS` / LOW with `COMPOSITE_ALLOCATION_AMBIGUITY`; the Finance row remains a candidate, not an authoritative selection. Multiple eligible Finance source rows remain `AMBIGUOUS` / LOW as a separate condition, even when their normalized values are identical. The Lambda handler loads only a configured pinned S3 snapshot; `boto3` is isolated to the optional `requirements-lambda.txt` package and imported lazily. No Bedrock or AgentCore SDK package is required by the deterministic pipeline. AWS execution has not been deployed or exercised from this repository.

## Authority and Responsibility

The LLM is a conversational/orchestration and explanation layer, not the financial decision engine. Python and its deterministic outputs remain authoritative for input data, matching, confidence, Base Credit, breach, applied rate, calculation rule, Credit, currency, exception codes, and totals.

Raw Excel workbooks should not be sent to the LLM for authoritative financial reasoning. An approved read-only tool should retrieve an existing `CreditResult` or Phase 9 report result, serialize it, and return it to the Harness. The model may explain those fields but may not recompute or replace them.

## Target Architecture

```text
User
  |
  v
Amazon Bedrock AgentCore Harness
  |
  v
Approved read-only tool
  |
  v
Deterministic Python Credit Engine / persisted deterministic results
  |
  v
Structured JSON (authoritative fields and lineage)
  |
  v
Nova explanation
```

The local deterministic tool boundary is implemented, but it is not connected to AgentCore. The preferred data path is a serialized, already-processed `CreditResult` snapshot, not source workbooks. Python produces the financial result before any AI explanation is requested.

## Phase 15B Local Prerequisites and AWS Execution

### Persisted deterministic results

`src/snapshot_store.py` encodes `CreditResult` dataclasses (including Finance candidates, source exceptions, Decimal values, floats, tuples, nulls, and lineage) into canonical JSON. The document carries `schema_version: 1`, record/run counts, and a SHA-256 checksum. Loading verifies format, version, count, checksum, and approved model types before reconstructing objects. Local writes are atomic and refuse to overwrite an existing snapshot by default. No matching or calculation runs during loading.

The deterministic batch command writes a unique, local snapshot only after Phase 9 and all 14 Phase 12 controls pass. It also generates the existing reports. Current command:

```sh
python3 -m src.main \
  --finance-file input/finance/00_Anonymized_Finance_APR26.xlsx \
  --devops-dir input/devops \
  --output-dir output
```

By default the snapshot path is `output/snapshots/<processing_run_id>/credit-results.v1.json`. `--snapshot-path` may pin a different local location. An explicit S3 upload/publisher has not been implemented; publishing a snapshot to S3 is a separate deployment step and must use the validated output without recalculation.

### Gateway and Lambda

The supported target design is one AWS Lambda function with four AgentCore Gateway Lambda tools: `get_client_credit`, `get_client_exceptions`, `get_executive_summary`, and `get_credit_record`. Gateway invokes the Lambda directly as an MCP target; no API Gateway or AgentCore Runtime is required for this adapter. The Lambda event contains tool input properties. Gateway supplies the selected tool name in `context.client_context.custom["bedrockAgentCoreToolName"]`, prefixed by the Gateway target name; the local handler strips that prefix and dispatches through an allowlist.

The handler requires these environment variables and fails closed if any is absent:

- `CREDIT_RESULTS_S3_BUCKET`
- `CREDIT_RESULTS_S3_KEY`
- `CREDIT_RESULTS_S3_VERSION_ID` (must be a real, non-null S3 object version)

At invocation, Lambda calls `GetObject` for that exact S3 version, validates the snapshot checksum/schema, reconstructs `CreditResult` objects, then invokes the local `ReadOnlyCreditTools`. No raw Excel input is read by the tool Lambda. Results and formulas are not recomputed.

The Gateway tool schema should expose the four existing Python method inputs. Gateway Lambda target names are qualified in the Lambda context; keep tool names/schema aligned with the Harness custom-function names. The local handler returns the tool's structured JSON dictionary directly; a response envelope is not synthesized by the model.

### Deployment prerequisites and steps

No AWS resources were created or changed for these local prerequisites. Before a live connection:

1. Run the batch command and confirm all Phase 12 controls pass. Keep the generated snapshot immutable and record its S3 `VersionId` and SHA-256.
2. Create/use a private, versioned S3 bucket in `eu-north-1`. Upload only the validated JSON snapshot, not the source Excel workbooks. Use the account's approved encryption policy; if SSE-KMS is used, grant the Lambda role only the needed decrypt permission for that key.
3. Package `src/` and the Lambda dependencies from `requirements-lambda.txt` for the chosen supported Lambda Python runtime and architecture. Build Linux-compatible NumPy/Pandas wheels in a compatible build environment; do not copy macOS wheels into the Lambda package.
4. Create a Lambda function whose handler is `src.lambda_handler.lambda_handler`, configured with the three snapshot environment variables. Its execution role needs `s3:GetObjectVersion` limited to the approved bucket/key/version scope as applicable, KMS decrypt only if required, and CloudWatch Logs write permissions.
5. Create an AgentCore Gateway in `eu-north-1` with a Lambda-function target and the four tool schemas. The Gateway service role needs `lambda:InvokeFunction` scoped to that function. Keep the Gateway inbound authorizer IAM/SigV4 or the approved JWT provider; do not use `NONE` for production. The Harness caller needs permission to invoke the Gateway.
6. Connect `credit_report_assistant` to the Gateway MCP endpoint and bind its custom-function definitions to the exposed tools. This Harness-to-Gateway binding is not implemented in this repository and must be verified in the existing Harness configuration before deployment.

AWS's [Gateway Lambda target documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html) describes the tool schema and Lambda event/context contract. [Gateway setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building.html), [Lambda permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-prerequisites-permissions.html), and [inbound authorization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html) cover service-role and caller access. The official [Gateway quick start](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-quick-start.html) demonstrates adding a Lambda target without an API Gateway endpoint.

### Expected AWS services and cost drivers

- **Amazon Bedrock AgentCore Gateway:** managed MCP endpoint and Lambda target routing. The current pricing page lists $0.005 per 1,000 Gateway API invocations; optional semantic search/indexing is separately charged and is unnecessary for four fixed tools.
- **AWS Lambda:** request and execution-duration/GB-second charges. The free tier may apply depending on account eligibility and usage.
- **Amazon S3:** small snapshot storage plus PUT/GET request charges. Versioned objects retain prior snapshots until lifecycle/retention policy removes them.
- **AWS KMS:** optional key and request charges if customer-managed SSE-KMS encryption is selected.
- **Amazon CloudWatch Logs:** ingestion and retention charges for Lambda/Gateway diagnostics.
- **Amazon Bedrock inference:** separate input/output token charges for the configured EU inference profile/model.

There is no separate API Gateway cost in the recommended Lambda-target design. Exact cost depends on call volume, Lambda memory/duration, snapshot size/retention, inference tokens, region pricing, and free-tier eligibility. Consult the current [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/), [Lambda pricing](https://aws.amazon.com/lambda/pricing/), and [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for a regional estimate; no fixed monthly cost is assumed here.

### Live verification test

After deployment, pin the test Lambda to a known reconciled snapshot version, connect the Harness to Gateway, and ask for CLIENT-001 April credit. Verify that:

1. `tools/list` exposes `get_client_credit` through the Gateway.
2. A request for `get_client_credit` with `client="CLIENT-001"` and `month="2026-04"` produces exactly one Gateway `tools/call` and one Lambda invocation.
3. Lambda logs show the Gateway request/tool context and the configured snapshot version, without logging credentials or raw workbook contents.
4. The tool JSON reports the regenerated exact EUR total `702.960636375`; Nova explains it without changing it.
5. A market affected by `COMPOSITE_ALLOCATION_AMBIGUITY` remains LOW/AMBIGUOUS with null Credit and retains its Finance row as a candidate; an unavailable/malformed snapshot produces a fail-closed error rather than an inferred answer.

This is a proposed live acceptance test; it has not been run because no AWS resources have been deployed from this repository.

## Manually Validated AWS State

The following is the supplied manual proof-of-concept status, not a repository-managed deployment:

- Region: `eu-north-1` (Europe/Stockholm).
- Model tested successfully: Amazon Nova 2 Lite v1 through the EU system-defined inference profile.
- An alternative model was unavailable to the account. The architecture and tool contracts therefore remain model-agnostic; they do not depend on Nova-specific APIs or output behavior.
- Bedrock Playground testing confirmed inference works and the configured authority instructions are followed in the tested cases: without deterministic engine output the model did not provide an authoritative Credit; with supplied engine output it explained the result without changing it.
- Exploratory testing without engine output also showed why the boundary matters: the model suggested an incorrect interpretation of the Credit formula when allowed to reason independently. This is evidence for the architecture, not a general reliability judgment about a model.
- Amazon Agents Classic was not used.
- An AgentCore Harness named `credit_report_assistant` exists and currently uses Nova 2 Lite v1 with financial-authority/no-hallucination instructions. No deterministic tools are attached yet.

Region, model, and inference-profile selection belong in external deployment configuration. The current values above are deployment context only; do not add account IDs, resource ARNs, secrets, or environment-specific credentials to this repository.

## Common Tool Contract

The local adapter is `src.agent_tools.ReadOnlyCreditTools`. It consumes an explicitly supplied sequence of existing `CreditResult` objects and uses the existing Phase 9 builders. Each method returns a JSON-serializable Python dictionary and must not ask the model to calculate Credit, select Finance candidates, or assign confidence.

Implemented local signatures:

```python
ReadOnlyCreditTools(credit_results: Optional[Sequence[CreditResult]])
ReadOnlyCreditTools.get_client_credit(client: str, month: str) -> Dict[str, Any]
ReadOnlyCreditTools.get_client_exceptions(client: str, month: str) -> Dict[str, Any]
ReadOnlyCreditTools.get_executive_summary(month: str) -> Dict[str, Any]
ReadOnlyCreditTools.get_credit_record(record_id: str) -> Dict[str, Any]
deterministic_record_id(result: CreditResult) -> Optional[str]
```

Pass `None` when deterministic results are unavailable; an empty sequence means the result set is available but has no rows. `month` accepts canonical `YYYY-MM`; `UNRESOLVED` explicitly requests pre-match unresolved rows. This adapter is a local Python API, not an executable CLI or an AgentCore connection. A caller supplies the already-built results:

```python
from src.agent_tools import ReadOnlyCreditTools

tools = ReadOnlyCreditTools(credit_results)
response = tools.get_client_credit("CLIENT-001", "2026-04")
```

The local batch entry point is `src/main.py`; it regenerates reports, runs the Phase 9/12 controls, then publishes a local checksummed snapshot only when controls pass. Its command and options are documented in the repository README. It does not upload data to S3.

Use JSON-compatible values with these semantics:

- Monetary, breach, and rate values are serialized as decimal strings to preserve their source decimal text (for example, `"0.1301"`). Do not serialize through binary floats.
- Missing values remain JSON `null`; null Credit is not zero.
- Confidence is the existing Finance-match confidence, or null when matching was not attempted (such as pre-match `MONTH_CONFLICT`).
- Include lineage references when available.
- Carry both match status/confidence and calculation status; do not collapse them into a single success flag.

Common response envelope:

```json
{
  "status": "OK | NOT_FOUND | INVALID_REQUEST | UNAVAILABLE | DATA_UNAVAILABLE",
  "source": "deterministic_credit_engine",
  "data": {},
  "error": null,
  "human_review_required": false,
  "review_reasons": []
}
```

For non-`OK` responses, `data` is null and `error` contains a stable code and concise message. The adapter sets review flags from deterministic status/exception fields, not from LLM judgment.

Record IDs are generated as `credit-record-v1-<sha256>` over the canonical JSON encoding of `[source_file, source_sheet, source_row_number]`. The actual 244-row assessment set has 244 complete, unique lineage tuples. Results without all three source identity fields receive `record_id: null` and cannot be looked up by ID. If a supplied result collection contains duplicate IDs, `get_credit_record` returns `AMBIGUOUS_RECORD_LOOKUP` rather than selecting a result.

### `get_client_credit(client, month)`

- **Purpose:** Retrieve market-level Credit results and existing client/month/currency totals.
- **Inputs:** Required client identifier and reporting month in the agreed canonical month representation, such as `2026-04`.
- **Deterministic source:** The supplied `CreditResult` records, with detail/totals built using `build_detailed_report` and `build_client_totals`.
- **Data response:** `client`, `reporting_month`, `records[]`, `totals_by_currency[]`, and optionally `pending_or_unresolved[]`. Each record exposes `devops_market`, `normalized_devops_market`, `matched_finance_market`, `customer_wd`, `sla_breach`, `applied_rate`, `base_credit`, `currency`, `calculated_credit`, `calculation_rule`, `calculation_status`, `match_status`, `match_confidence`, `match_method`, `exception_code`, `exception_codes`, `explanation`, and `source_lineage` where present. Decimal fields are strings or null.
- **Errors/review:** Invalid identifiers/months return `INVALID_REQUEST`; no matching client/month returns `NOT_FOUND`; unavailable backing data returns `UNAVAILABLE` or `DATA_UNAVAILABLE`. Ambiguous/unmatched standard results, currency limitations, and unresolved month conflicts remain visible and may require human review.

Example record shape:

```json
{
  "client": "CLIENT-001",
  "reporting_month": "2026-04",
  "devops_market": "synthetic-market",
  "normalized_devops_market": "SYNTHETIC-MARKET",
  "matched_finance_market": "SYNTHETIC-FINANCE-CODE",
  "customer_wd": "CLIENT-001-WD-01",
  "sla_breach": "0.05",
  "applied_rate": "0.05",
  "base_credit": "10000",
  "currency": "EUR",
  "calculated_credit": "500.00",
  "calculation_rule": "STANDARD_CAPPED",
  "calculation_status": "CALCULATED",
  "match_status": "MATCHED",
  "match_confidence": "HIGH",
  "match_method": "EXACT_CODE_MATCH",
  "exception_codes": [],
  "source_lineage": {
    "devops": {"file": "devops.xlsx", "sheet": "SLA_Breaches", "row": 1},
    "finance": {"file": "finance.xlsx", "sheet": "Finance_Fines", "row": 2}
  }
}
```

### `get_client_exceptions(client, month)`

- **Purpose:** Retrieve existing exception records for a client/month.
- **Inputs:** Required client and month filter. Use `UNRESOLVED` to retrieve unresolved-month records without assigning April or May.
- **Deterministic source:** `build_exceptions_report` and the associated `CreditResult` candidate/source fields.
- **Data response:** `client`, requested `month`, `unique_affected_record_count`, `exception_code_occurrence_count`, and `exceptions[]`. Each exception includes source record reference, code, explanation, market, match status/confidence where applicable, calculation status, candidate count/details where already present (Customer WD, Finance market code, Base Credit, currency), and source lineage.
- **Errors/review:** Invalid filters return `INVALID_REQUEST`; no matching exceptions returns `NOT_FOUND` with no records; unavailable data returns `UNAVAILABLE`/`DATA_UNAVAILABLE`. Ambiguous Finance candidates and source-data conflicts normally require human review.

Multiple exception codes can refer to one SLA record. Preserve the unique-record count separately from code occurrences; neither the tool nor the model may treat code-row count as unique affected markets.

### `get_executive_summary(month)`

- **Purpose:** Retrieve the existing deterministic executive summary for a month.
- **Inputs:** Required reporting month.
- **Deterministic source:** `build_executive_summary` over resolved results for the requested month.
- **Data response:** `reporting_month` and `groups[]`, each grouped by `client` and `currency` and containing the supplied market/breach/calculation/exception counts and exact `total_calculated_credit` as a decimal string or null.
- **Errors/review:** Invalid month returns `INVALID_REQUEST`; no authoritative summary returns `NOT_FOUND`; unavailable summary data returns `UNAVAILABLE`/`DATA_UNAVAILABLE`. Unresolved `MONTH_CONFLICT` rows may be exposed as pending information but must not be included in authoritative monthly totals.

Currencies remain separate groups. `UNKNOWN / UNSPECIFIED CURRENCY` remains its own group and is never inferred as USD or converted. The tool retrieves totals; it does not reaggregate them in the LLM.

### `get_credit_record(record_id)`

- **Purpose:** Retrieve the complete deterministic record/context so AgentCore/Nova can explain it. This is a retrieval tool, not a calculation operation. This is the implemented local method name; no `explain_credit_record` alias currently exists.
- **Inputs:** Required stable source-lineage record identifier returned by `deterministic_record_id`.
- **Deterministic source:** Exact `CreditResult`, with its match fields, source exceptions, and candidate references as already retained.
- **Data response:** Complete record fields from `get_client_credit`, including match/calc statuses, all exception codes, candidates, source lineage, and applicable month-conflict hints. Preserve nulls.
- **Errors/review:** Malformed ID returns `INVALID_REQUEST`; unknown ID returns `NOT_FOUND`; unavailable result storage returns `UNAVAILABLE`/`DATA_UNAVAILABLE`. Ambiguous, unmatched, missing-data, and ineligible records retain their explicit review reasons.

If an external AgentCore schema must retain the design-time name `explain_credit_record(record_id)`, map it to local `get_credit_record(record_id)` as a retrieval-only adapter. The model produces the explanation after receiving the returned record.

## Local Invocation Examples

The following are response excerpts generated by invoking the local methods on the supplied assessment `CreditResult` objects. Record arrays are intentionally shortened in these examples; values shown are unmodified tool output.

### `get_client_credit("CLIENT-001", "2026-04")`

```json
{
  "status": "OK",
  "source": "deterministic_credit_engine",
  "error": null,
  "data": {
    "client": "CLIENT-001",
    "requested_month": "2026-04",
    "record_count": 56,
    "totals_by_currency": [
      {
        "Reporting Month": "2026-04",
        "Client": "CLIENT-001",
        "Currency": "EUR",
        "Total Calculated Credit": "702.960636375"
      }
    ],
    "records": [
      {
        "record_id": "credit-record-v1-46a69e35e5f70d2bd5cbbfbb1cb4615cf3c9db28d21e35c2ab29664402ffeb3e",
        "devops_market": "cbot",
        "matched_finance_market": null,
        "sla_breach": "0.0527",
        "applied_rate": null,
        "base_credit": null,
        "currency": null,
        "calculated_credit": null,
        "calculation_rule": "STANDARD_CAPPED",
        "calculation_status": "EXCEPTION",
        "match_status": "AMBIGUOUS",
        "match_confidence": "LOW",
        "match_method": "COMPOSITE_CODE_MATCH",
        "exception_code": "COMPOSITE_ALLOCATION_AMBIGUITY",
        "candidate_count": 1,
        "candidate_finance_records": [
          {
            "customer_wd": "CLIENT-001-WD-02",
            "currency": "EUR",
            "market_code": "CME\nCBOT\nCOMEX/NYMEX\nCMEC",
            "base_credit": "1510.7065",
            "source_lineage": {
              "file": "00_Anonymized_Finance_APR26.xlsx",
              "sheet": "Finance_Fines",
              "row": 36
            }
          }
        ],
        "explanation": "The Finance record contains this market within a composite market field, but the same Finance Base Credit corresponds to multiple DevOps markets. No allocation rule is provided, so the Base Credit was not reused and no authoritative credit was calculated."
      }
    ]
  }
}
```

### `get_client_exceptions("CLIENT-002", "2026-04")`

Excerpt: one of 44 exception-code occurrences affecting 39 unique SLA records.

```json
{
  "status": "OK",
  "source": "deterministic_credit_engine",
  "data": {
    "client": "CLIENT-002",
    "requested_month": "2026-04",
    "unique_affected_record_count": 39,
    "exception_code_occurrence_count": 44,
    "exceptions": [
      {
        "record_id": "credit-record-v1-7691c390441e0d2be69b9fb1a87da1a117e347dcd6ff42b7275c2cdfedcb7ad9",
        "market": "client002_par",
        "exception_code": "AMBIGUOUS_MATCH",
        "calculation_status": "EXCEPTION",
        "calculated_credit": null,
        "match_status": "AMBIGUOUS",
        "match_confidence": "LOW",
        "candidate_count": 3,
        "candidate_finance_records": [
          {
            "customer_wd": "CLIENT-002-WD-01",
            "currency": "USD",
            "market_code": "AMS\nBRU\nLIS\nPAR",
            "base_credit": "1092.727"
          },
          {
            "customer_wd": "CLIENT-002-WD-09",
            "currency": "USD",
            "market_code": "AMS\nBRU\nLIS\nPAR",
            "base_credit": "7621.40169"
          },
          {
            "customer_wd": "CLIENT-002-WD-09",
            "currency": "USD",
            "market_code": "AMS\nBRU\nLIS\nPAR",
            "base_credit": "11236.0"
          }
        ],
        "exception_codes_for_record": ["NON_UNIQUE_FINANCE_KEY", "AMBIGUOUS_MATCH"]
      }
    ]
  }
}
```

### `get_executive_summary("2026-04")`

The actual response contains six client/currency groups. Exact totals remain separate, including the unknown-currency bucket:

```json
{
  "status": "OK",
  "source": "deterministic_credit_engine",
  "data": {
    "reporting_month": "2026-04",
    "groups": [
      {"Client": "CLIENT-001", "Currency": "EUR", "Total Calculated Credit": "702.960636375"},
      {"Client": "CLIENT-001", "Currency": "UNKNOWN / UNSPECIFIED CURRENCY", "Total Calculated Credit": null},
      {"Client": "CLIENT-002", "Currency": "UNKNOWN / UNSPECIFIED CURRENCY", "Total Calculated Credit": null},
      {"Client": "CLIENT-002", "Currency": "USD", "Total Calculated Credit": "463.360430778"},
      {"Client": "CLIENT-004", "Currency": "UNKNOWN / UNSPECIFIED CURRENCY", "Total Calculated Credit": "21469.4194183928702104404702486177512721127279580188"},
      {"Client": "CLIENT-004", "Currency": "USD", "Total Calculated Credit": "0"}
    ],
    "pending_or_unresolved_record_count": 0,
    "pending_or_unresolved": []
  }
}
```

### `get_credit_record(record_id)`

Actual CLIENT-004 example: LOW / UNMATCHED Finance status coexists with a calculated special-rule Credit; Finance attributes and currency remain null.

```json
{
  "status": "OK",
  "source": "deterministic_credit_engine",
  "data": {
    "record_id": "credit-record-v1-65c1ddbbc17953674a8a5cb96841b11e61ec661286b75e776125cbfd793887a2",
    "client": "CLIENT-004",
    "reporting_month": "2026-04",
    "devops_market": "mx",
    "matched_finance_market": null,
    "customer_wd": null,
    "sla_breach": "0.1301",
    "applied_rate": null,
    "base_credit": null,
    "currency": null,
    "calculated_credit": "7686.3950807071483474250576479631053036126056879324",
    "calculation_rule": "CLIENT_004_SPECIAL",
    "calculation_status": "CALCULATED",
    "match_status": "UNMATCHED",
    "match_confidence": "LOW",
    "exception_codes": ["NO_FINANCE_MATCH", "CURRENCY_UNKNOWN"],
    "source_lineage": {
      "devops": {"file": "04_Anonymized_DEVOPS_CLIENT-004_APR26.xlsx", "sheet": "SLA_Breaches", "row": 1},
      "finance": null
    }
  },
  "error": null,
  "human_review_required": true,
  "review_reasons": ["CURRENCY_UNKNOWN", "NO_FINANCE_MATCH"]
}
```

These local outputs are returned by Python methods. They are not evidence of AgentCore connectivity; no AWS tool has been registered or invoked from this repository.

## Required Special States

The response schema must preserve, not flatten, these distinctions:

- **Standard success:** `MATCHED`, `HIGH` plus the supplied calculated Credit and Finance fields.
- **Standard ambiguous:** `AMBIGUOUS`, `LOW`, null Base Credit/Credit, candidate details, `AMBIGUOUS_MATCH`, human review required.
- **Standard unmatched:** `UNMATCHED`, `LOW`, null Finance fields and Credit, `NO_FINANCE_MATCH`.
- **CLIENT-004 unmatched special calculation:** `UNMATCHED`, `LOW`, `CALCULATED`, supplied special-rule amount, null Finance Base Credit/Customer WD/Finance lineage as applicable, and null/unknown currency if none is authoritative. Calculation success must not upgrade match confidence.
- **Pre-match month conflict:** `INELIGIBLE`, `MONTH_CONFLICT`, null confidence and calculated Credit, unresolved reporting month, human/source-owner review required. Null confidence means matching was never attempted, not LOW confidence.

## Fail-Safe Behavior

If a deterministic tool is unavailable, a record is not found, a response is malformed, or an authoritative field is missing, the model must say the authoritative information is unavailable or requires review. It must not answer from memory, infer a value, approximate a Credit, or substitute zero. If structured JSON fails schema validation, do not present it as a deterministic result; return an unavailable/error response and request tool/operator follow-up.

Bedrock, AgentCore, or network failure must not interrupt or change local deterministic calculation, matching, reconciliation, or report generation. The AI layer is never a prerequisite for the Python engine.

## Security and Governance

- Give tool access read-only permissions limited to approved structured results; do not grant source workbook write access.
- Apply least privilege to any eventual tool service and keep authentication outside prompts and source control.
- Never put credentials, access keys, secrets, account identifiers, or environment-specific ARNs in prompts or this repository.
- Preserve deterministic source lineage in tool output for audit and human investigation.
- Require authorized human review before ambiguous/LOW-confidence Finance matching can affect a standard-rule financial result.
- Retain Phase 14 protections: user instructions or retrieved content cannot override deterministic authority or cause the model to select a candidate.

## Optional Productionization

Only if needed beyond the assessment prototype, a small authenticated API/tool adapter could read persisted `CreditResult` and Phase 9 datasets. Further production work may add result persistence, request authorization, monitoring, and orchestration. Keep the deterministic Python layer independently runnable and avoid adding AWS infrastructure to the local prototype unless a real deployment requirement justifies it.