# Credit Report Agent — Architecture

## 1. Objective

Build a reliable, auditable and reproducible system that combines:

1. DevOps SLA breach data
2. Finance fee-schedule data

to calculate client Credit receivables and produce:

- detailed monthly Client Credit Reports
- an Executive Report by month and client
- an Exceptions Report
- an AI-enabled operational assistant

The system must prioritize:

1. correctness
2. auditability
3. deterministic financial calculations
4. explicit exception handling
5. reproducibility
6. extensibility
7. AI only where it adds appropriate value

---

# 2. Core Design Principle

The financial result MUST NOT be calculated by an LLM.

Use the following separation:

    Deterministic Python Engine
              |
              | produces structured, validated results
              v
        AI / Bedrock Layer
              |
              v
    explanation / investigation /
    operational assistance

Python is the source of truth for:

- ingestion
- validation
- normalization
- matching
- confidence assignment
- duplicate detection
- application of business rules
- credit calculation
- aggregation
- report generation

The AI layer may:

- explain results
- summarize results
- explain exceptions
- help operators investigate exceptions
- answer natural-language questions using deterministic outputs

The AI layer MUST NOT:

- invent missing values
- override deterministic calculations
- silently resolve ambiguous matches
- change a Base Credit
- change an SLA breach percentage
- change client-specific calculation rules
- approve a LOW-confidence financial match autonomously

The deterministic calculation engine must work completely without AWS Bedrock.

---

# 3. Supplied Inputs

The assessment supplies:

- 00_Anonymized_Finance_APR26.xlsx
- 01_Anonymized_DEVOPS_CLIENT-001_APR26.xlsx
- 02_Anonymized_DEVOPS_CLIENT-002_APR26.xlsx
- 04_Anonymized_DEVOPS_CLIENT-004_APR26.xlsx
- 02_Anonymized_DEVOPS_CLIENT-002_MAY26.xlsx

Do not hardcode the number of DevOps files.

The solution must discover and process multiple client/month files.

IMPORTANT:

Do not enrich the anonymized data externally.
Do not attempt to identify real clients or markets beyond what is required by the supplied assessment.

---

# 4. High-Level Architecture

                        INPUT FILES

               Finance             DevOps
                  |                   |
                  +---------+---------+
                            |
                            v
                    INGESTION LAYER
                            |
                            v
                    SCHEMA VALIDATION
                            |
                            v
                       NORMALIZATION
                            |
                            v
                     MATCHING ENGINE
                            |
              +-------------+-------------+
              |             |             |
              v             v             v
            HIGH        AMBIGUOUS      UNMATCHED
              |             |             |
              v             +------+------+
         RULE ENGINE               |
              |                    v
              |              EXCEPTION QUEUE
              v
      CREDIT CALCULATION
              |
              v
         AUDIT DATASET
              |
        +-----+--------+
        |              |
        v              v
    CLIENT REPORT   EXECUTIVE REPORT
        |
        +-------------------+
                            |
                            v
                     AI / BEDROCK
                  OPERATIONAL LAYER

---

# 5. Processing Pipeline

## Stage 1 — File Discovery

Discover:

- one Finance file
- all DevOps files

Do not require filenames for individual clients to be hardcoded.

Derive metadata such as:

- client
- reporting month

from reliable source fields and/or filenames.

If filename metadata conflicts with data inside a workbook, flag the conflict.

Do not silently choose one.

---

## Stage 2 — Ingestion

Use:

- Python
- pandas
- openpyxl where required

Load source data without modifying original files.

Preserve source lineage:

- source_file
- source_sheet if relevant
- source_row_number where practical
- processing_run_id

---

# 6. Validation Layer

Validate required schemas before calculations.

Examples of checks:

- required columns exist
- client identifier exists
- market identifier exists
- SLA Breach % is numeric
- Base Credit field is numeric when required
- currency exists when required
- invalid/null values
- exact duplicate rows
- potentially non-unique business keys
- invalid percentages
- malformed records

IMPORTANT:

Repeated business keys are NOT automatically duplicates.

Distinguish:

1. exact duplicate records
2. legitimate multiple records
3. ambiguous records

Never use drop_duplicates() blindly.

Invalid records must be retained in an exception dataset.

---

# 7. Normalization Layer

Normalize values for comparison while preserving original values.

Example normalization:

- trim leading/trailing whitespace
- normalize case
- normalize repeated whitespace
- safely handle null values
- normalize market-code delimiters where appropriate

Always preserve:

- original DevOps market
- original Finance market

Never overwrite source values.

Normalized values exist only for matching.

---

# 8. Matching Engine

Matching must be hierarchical.

Prefer deterministic and explainable matching over probabilistic matching.

Suggested hierarchy:

### Exact and composite-code matching

Resolve the client scope before evaluating market codes. Tokenize Finance market-code fields on the supported observed delimiters: line breaks, commas, semicolons, and slashes. Normalize each token and require exact token equality with the normalized DevOps market code; never use substring matching (`CME` does not match `CMEC`). Parenthetical qualifiers remain part of their token.

Each Finance source row contributes at most one candidate, even if it contains several explicit market-code tokens. If exactly one eligible source row in the client scope contains the token, return `MATCHED` / HIGH with `EXACT_CODE_MATCH` for an atomic field or `COMPOSITE_CODE_MATCH` for a composite field. If multiple eligible Finance source rows contain the token, return `AMBIGUOUS` / LOW and do not select a Base Credit. Distinct source rows remain distinct candidates even when their normalized values are identical.

### Composite Base Credit allocation

Token membership establishes whether a Finance row references a market; it does not establish that the row's single Base Credit can be allocated or reused for every referenced DevOps market. Before selecting Base Credit, batch processing groups composite-code matches by normalized client, resolved reporting month, and Finance source file/sheet/row. If that same source row corresponds to more than one distinct normalized DevOps market in the group, each affected result is `AMBIGUOUS` / LOW with primary exception `COMPOSITE_ALLOCATION_AMBIGUITY`. Retain the Finance row as a candidate for audit, but do not select its Base Credit or calculate standard credit. Multiple tokens by themselves do not trigger the control; zero-breach records are included. This is distinct from `AMBIGUOUS_MATCH`, where one DevOps market has multiple eligible Finance source rows.

### Level 3 — Approved alias mapping

Optional configuration:

    config/market_aliases.json

Aliases must be explicit and reviewable.

Do not have the LLM create aliases automatically.

If a unique approved alias resolves the record:

    confidence = MEDIUM or HIGH

The selected confidence policy must be documented.

### Level 4 — Candidate / fuzzy matching

Only use fuzzy matching if deterministic methods fail.

Fuzzy matching should generate candidates, NOT silently establish financially consequential matches.

Potential implementation:

rapidfuzz

The exact confidence thresholds must be configurable and documented.

Do not invent thresholds without documenting them as design assumptions.

### Ambiguous match

If multiple Finance records remain valid candidates and there is no documented deterministic field that resolves them:

    status = AMBIGUOUS
    confidence = LOW
    calculated_credit = null
    exception_code = AMBIGUOUS_MATCH

Do not select the first record.

### Unmatched

If no defensible Finance match exists:

    status = UNMATCHED
    calculated_credit = null
    exception_code = NO_FINANCE_MATCH

unless a client-specific calculation rule explicitly does not require Finance data.

---

# 9. Confidence Model

Every processed breached-market record must receive a matching status/confidence.

At minimum support:

- HIGH
- MEDIUM
- LOW

Also store:

- match_method
- match_score where applicable
- exception_code
- explanation

Confidence must describe the reliability of the MATCH, not confidence in the LLM.

Do not hide uncertain records.

---

# 10. Calculation Engine

## Standard Rule

For standard clients:

    Applied Rate = MIN(SLA Breach %, 20%)

    Calculated Credit =
        Base Credit × Applied Rate

The 20% value is a CAP.

Examples:

5% breach -> 5% applied
20% breach -> 20% applied
40% breach -> 20% applied

Base Credit comes from:

    Monthly LCY 04/30/26

in the matched Finance record.

Use the currency supplied by Finance.

Do not perform FX conversion.

---

## CLIENT-004

The assessment specifies:

    Calculated Credit = 1000 / breach

This rule applies only to CLIENT-004.

IMPORTANT:

The meaning/unit representation of "breach" in this formula must be handled explicitly and documented.

Do not silently guess whether the formula expects:

    5

or:

    0.05

Inspect the supplied values and create an explicit documented assumption.

Because the CLIENT-004 formula does not reference Base Credit, do not automatically assume that a Finance match is required for the arithmetic.

Keep these two concepts separate:

1. ability to match CLIENT-004 to Finance
2. ability to calculate its specified special formula

Any interpretation must be documented in the assumptions output.

---

# 11. Extensible Rule Engine

Do not scatter client-specific if-statements throughout the code.

Centralize calculation rules.

Example conceptual interface:

    calculate_credit(
        client,
        breach_pct,
        base_credit,
        context
    )

Prefer a rule registry or configuration-driven approach such as:

    STANDARD -> standard_credit_rule
    CLIENT-004 -> client_004_credit_rule

Future clients should be addable without redesigning the entire pipeline.

---

# 12. Auditability

For every SLA record processed, retain where applicable:

- processing_run_id
- source_file
- source_sheet
- source_row
- reporting_month
- client
- DevOps market original
- DevOps market normalized
- matched Finance market original
- Finance record identifier / row reference
- Customer WD
- match confidence
- match method
- match score
- SLA Breach %
- applied rate
- Base Credit
- currency
- calculation rule
- calculated credit
- exception code
- explanation
- processing timestamp

The objective is to answer:

"Exactly why did this client receive this amount?"

without rerunning or reverse-engineering the application.

---

# 13. Exception Framework

Use structured reason codes.

Examples:

- NO_FINANCE_MATCH
- AMBIGUOUS_MATCH
- LOW_CONFIDENCE_MATCH
- MISSING_BASE_CREDIT
- MISSING_CURRENCY
- INVALID_SLA_BREACH
- EXACT_DUPLICATE
- NON_UNIQUE_FINANCE_KEY
- SCHEMA_ERROR
- MONTH_CONFLICT
- CLIENT_CONFLICT
- SPECIAL_RULE_ASSUMPTION_REQUIRED

Do not silently drop any relevant SLA record.

---

# 14. Reports

## Client Credit Report

Generate a detailed monthly report for each client.

Required assessment fields:

- Client
- Market from DevOps
- Matched market from Finance
- Customer WD
- Match confidence
- SLA Breach %
- Applied rate after 20% cap
- Base Credit
- Calculated Credit
- Explanation or exception
- Total Credit per client

Additional audit fields may be included.

For CLIENT-004, fields that do not apply because of the special formula should be explicitly represented as N/A or otherwise clearly explained rather than fabricated.

---

## Executive Report

Summarize Credit receivable by:

- reporting month
- client
- currency where necessary

Do NOT sum unlike currencies into a meaningless grand total.

Include useful operational measures such as:

- breached rows processed
- automatically resolved rows
- exception count
- HIGH/MEDIUM/LOW counts
- calculated credit

Keep the required executive output concise.

---

## Exceptions Report

Produce a separate exceptions sheet/file containing records requiring attention.

Include:

- client
- month
- market
- exception code
- candidate match(es), if applicable
- confidence
- reason
- recommended next step

AI recommendations, if later added, must be clearly distinguished from deterministic results.

---

# 15. Output Format

Use Excel for business-facing reports.

Suggested outputs:

    output/
        CLIENT-001_APR26_Credit_Report.xlsx
        CLIENT-002_APR26_Credit_Report.xlsx
        CLIENT-002_MAY26_Credit_Report.xlsx
        CLIENT-004_APR26_Credit_Report.xlsx
        Executive_Report.xlsx
        Exceptions.xlsx

Actual names should be generated dynamically from source metadata.

---

# 16. Testing

Use pytest.

At minimum test:

1. breach below 20%
2. breach exactly 20%
3. breach above 20%
4. zero breach
5. exact market match
6. case/whitespace normalization
7. composite market match
8. unmatched market
9. multiple Finance candidates
10. exact duplicate detection
11. missing Base Credit
12. missing currency
13. invalid SLA percentage
14. CLIENT-004 rule
15. repeat execution / idempotent output behavior
16. AI unavailable -> deterministic engine still succeeds

Where possible, tests should use small synthetic fixtures rather than relying entirely on assessment workbooks.

---

# 17. Logging

Use Python logging.

Log:

- run started
- files discovered
- rows ingested
- validation results
- matching summary
- exception counts
- calculations completed
- reports generated
- fatal errors

Do not log credentials or sensitive secrets.

---

# 18. Configuration

Keep configurable values outside core business logic where practical.

Possible configuration:

    config/
        settings.yaml
        client_rules.yaml
        market_aliases.yaml

Configuration may contain:

- cap = 0.20
- client rule mapping
- fuzzy thresholds
- input/output directories
- approved aliases

Do not over-engineer configuration if it makes the prototype harder to understand.

---

# 19. AWS Bedrock Layer

Bedrock is an OPTIONAL operational layer.

The deterministic Python solution must remain independently executable.

Conceptual design:

                  BEDROCK AGENT
                        |
             +----------+-----------+
             |                      |
             v                      v
      query_credit_report     explain_exception
             |                      |
             +----------+-----------+
                        |
                        v
              DETERMINISTIC OUTPUTS

The Bedrock layer should consume structured outputs from the Python engine.

Potential user questions:

- "What is CLIENT-001's April credit?"
- "Which clients have unresolved exceptions?"
- "Why was this market not automatically credited?"
- "Summarize the April credit report."
- "Which LOW-confidence matches require review?"

The model must not independently recompute authoritative financial values.

---

# 20. Human-in-the-Loop

Human approval is required before uncertain matching affects a financial result.

Examples:

HIGH deterministic match
    -> may proceed automatically

AMBIGUOUS / LOW
    -> exception
    -> human review
    -> approved mapping/rule
    -> deterministic recalculation

AI may recommend.

AI does not approve.

---

# 21. Production Evolution

The take-home implementation uses Excel files.

The design should allow future replacement of file ingestion with:

- S3
- database
- APIs
- scheduled workflows

without rewriting matching and calculation logic.

Conceptual production architecture:

    Source systems
          |
          v
    Scheduled ingestion
          |
          v
    Validation
          |
          v
    Matching
          |
          v
    Rule engine
          |
          v
    Credit results
          |
       +--+--+
       |     |
       v     v
    Reports  AI operational layer
          |
          v
    Human exception workflow

Do not build unnecessary cloud infrastructure solely for the assessment.

---

# 22. Non-Negotiable Principles

1. Never invent missing financial values.
2. Never silently discard relevant SLA rows.
3. Never silently resolve ambiguity.
4. Preserve source lineage.
5. Financial calculations are deterministic.
6. AI does not become the source of truth.
7. All assumptions must be explicit.
8. All client-specific rules must be traceable.
9. Core solution must work without Bedrock.
10. Prefer simple, explainable architecture over unnecessary complexity.