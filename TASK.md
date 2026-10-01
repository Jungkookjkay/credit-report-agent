# Credit Report Agent — Implementation Task

## Role

You are acting as a senior Python engineer implementing a recruitment assessment prototype.

Read ARCHITECTURE.md completely before making any changes.

Treat ARCHITECTURE.md as the design authority.

Do not change business rules because you think another interpretation is more sensible.

Do not invent information missing from the supplied files.

If a requirement is ambiguous, surface it as:

    TODO / ASSUMPTION / QUESTION

rather than silently deciding.

---

# PHASE 0 — DO NOT CODE YET

Before writing implementation code:

1. Inspect every supplied Excel workbook.
2. Print/document:
   - filename
   - sheet names
   - dimensions
   - column names
   - inferred dtypes
   - first 5 rows
   - null counts
   - duplicate counts
   - unique clients
   - unique market codes
   - SLA percentage representation
   - Finance currency values
   - Finance Monthly LCY field
3. Compare DevOps schemas across files.
4. Identify how reporting month can reliably be determined.
5. Identify whether filenames and workbook contents disagree.
6. Analyze Finance key uniqueness.
7. Analyze candidate relationships between:
       client + market
   in DevOps and Finance.
8. Identify composite Finance market codes.
9. Identify ambiguous Finance matches.
10. Inspect CLIENT-004 carefully.

Create:

    analysis/data_profile.md

Document findings.

DO NOT modify input files.

STOP after this phase and present findings before implementing business logic if running interactively.

---

# PHASE 1 — CREATE PROJECT STRUCTURE

Create:

    credit-report-agent/
    |
    |-- README.md
    |-- ARCHITECTURE.md
    |-- TASK.md
    |-- requirements.txt
    |-- .gitignore
    |
    |-- input/
    |   |-- finance/
    |   `-- devops/
    |
    |-- output/
    |
    |-- config/
    |   |-- settings.yaml
    |   |-- client_rules.yaml
    |   `-- market_aliases.yaml
    |
    |-- src/
    |   |-- __init__.py
    |   |-- config.py
    |   |-- models.py
    |   |-- ingestion.py
    |   |-- validation.py
    |   |-- normalization.py
    |   |-- matching.py
    |   |-- rules.py
    |   |-- reporting.py
    |   |-- audit.py
    |   `-- main.py
    |
    |-- tests/
    |   |-- test_normalization.py
    |   |-- test_matching.py
    |   |-- test_rules.py
    |   `-- test_validation.py
    |
    |-- analysis/
    |   `-- data_profile.md
    |
    `-- ai/
        |-- system_prompt.md
        `-- bedrock_design.md

Keep the structure pragmatic.

If a module has no meaningful responsibility, do not create unnecessary abstraction merely to satisfy this tree.

---

# PHASE 2 — INGESTION

Implement reusable functions to:

    load_finance_file(...)
    discover_devops_files(...)
    load_devops_file(...)
    load_all_devops_files(...)

Requirements:

- support multiple DevOps files
- preserve source filename
- preserve source row where practical
- preserve source sheet where relevant
- derive client/month carefully
- do not hardcode current clients
- do not modify source files

Use pathlib instead of fragile string paths.

---

# PHASE 3 — DATA MODELS

Define clear internal schemas.

Use either:

- dataclasses
- TypedDict
- pandas schema conventions

Avoid unnecessary dependencies.

Core conceptual entities:

    FinanceRecord
    SLARecord
    MatchResult
    CreditResult
    ExceptionRecord

Every MatchResult should support:

    status
    confidence
    match_method
    candidate_count
    match_score
    explanation

---

# PHASE 4 — VALIDATION

Implement validation for:

- required columns
- null identifiers
- numeric SLA values
- numeric Base Credit
- currency
- exact duplicates
- non-unique candidate keys
- month inconsistencies
- client inconsistencies

Never delete bad records silently.

Return validation issues in structured form.

---

# PHASE 5 — NORMALIZATION

Implement pure functions where possible.

Examples:

    normalize_client(...)
    normalize_market_code(...)
    tokenize_composite_market_code(...)

Normalization must:

- trim whitespace
- normalize case
- handle null safely
- preserve source value elsewhere

Do not use fuzzy matching here.

Write tests.

---

# PHASE 6 — MATCHING ENGINE

Implement matching as a staged pipeline.

Pseudo-logic:

    candidates = finance records for same client

    market_matches = Finance source rows in that client scope
        whose normalized atomic code or normalized composite token
        equals the normalized DevOps market code exactly

    if exactly one source row:
        return HIGH / EXACT_CODE_MATCH for an atomic code
        return HIGH / COMPOSITE_CODE_MATCH for a composite field

    if multiple source rows:
        return LOW / AMBIGUOUS_MATCH

Composite tokenization supports observed line breaks, commas,
semicolons, and slashes. Do not use substring matching or collapse
distinct source rows because their normalized values are identical.

Before selecting Base Credit, group composite matches by client,
resolved reporting month, and Finance source file/sheet/row. If one
composite Finance source row corresponds to multiple distinct normalized
DevOps markets in that group, return AMBIGUOUS / LOW with
COMPOSITE_ALLOCATION_AMBIGUITY, retain the Finance row as a candidate,
and do not calculate credit. Multiple tokens in one Finance row alone
are not ambiguous; zero-breach records still participate in this check.

    approved_alias_matches = alias configuration

    if exactly one:
        return configured confidence / ALIAS_MATCH

    optional fuzzy candidate generation

    if fuzzy result is sufficiently strong:
        DO NOT automatically treat it as financially authoritative
        unless the configured policy explicitly allows it

    otherwise:
        return LOW / NO_FINANCE_MATCH

Important:

Do not write:

    matches.iloc[0]

to resolve ambiguity.

Do not use an LLM for matching.

Do not use embeddings for matching.

Do not invent aliases.

Any alias mapping must come from config and be explicitly documented.

---

# PHASE 7 — RULE ENGINE

Implement standard rule:

    applied_rate = min(breach_rate, 0.20)
    credit = base_credit * applied_rate

Confirm whether source SLA percentages are represented as:

    5.27
or
    0.0527

and normalize exactly once.

Add explicit tests to prevent a 100x percentage error.

Implement CLIENT-004 as a separate rule:

    credit = 1000 / breach

Do NOT guess the unit convention.

Document the chosen interpretation based on source representation.

Centralize rule selection.

Suggested pattern:

    RULE_REGISTRY = {
        "STANDARD": calculate_standard_credit,
        "CLIENT-004": calculate_client_004_credit,
    }

The exact implementation may differ.

Avoid business-rule conditionals spread throughout the project.

---

# PHASE 8 — CREDIT PROCESSING

Implement orchestration such as:

    process_sla_record(...)
    process_client_month(...)
    run_credit_pipeline(...)

Each relevant SLA row must end in exactly one auditable outcome:

1. CALCULATED
2. EXCEPTION
3. NOT_APPLICABLE — only where clearly justified

Never disappear a row.

Generate a unique processing_run_id.

---

# PHASE 9 — REPORTING

Generate:

## Detailed Client Credit Reports

One report per client/month.

At minimum include:

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

Also include useful audit fields without overwhelming the business report.

## Executive Report

Aggregate by:

    month
    client
    currency

Include:

- total calculated credit
- number of breached rows
- calculated rows
- exception rows
- HIGH matches
- MEDIUM matches
- LOW matches

Do not aggregate different currencies into one monetary total.

## Exceptions Report

Include all:

- unmatched
- ambiguous
- missing-data
- duplicate
- LOW-confidence
- validation-error

records.

---

# PHASE 10 — EXCEL PRESENTATION

Use openpyxl after pandas export for light formatting.

Requirements:

- clear column headers
- freeze panes
- filters
- sensible column widths
- numeric formatting
- percentage formatting
- currency/amount formatting where appropriate
- readable exception information

Do not spend excessive effort on decorative styling.

Correctness and auditability matter more.

---

# PHASE 11 — TESTING

Implement pytest tests.

Required tests:

### Standard formula

    Base = 10000
    breach = 5%
    expected = 500

    Base = 10000
    breach = 20%
    expected = 2000

    Base = 10000
    breach = 50%
    expected = 2000

### Matching

- exact normalized match
- case difference
- whitespace difference
- unique composite match
- no match
- multiple exact matches
- multiple composite matches

### Validation

- missing Base Credit
- invalid SLA %
- missing currency
- exact duplicate
- non-unique Finance key

### CLIENT-004

Test the explicitly documented breach representation.

### Reproducibility

Running the same inputs twice must not change calculated results.

### AI independence

Core pipeline must have zero dependency on Bedrock.

---

# PHASE 12 — CONTROL RECONCILIATIONS

After processing, perform reconciliations.

At minimum verify:

    number of relevant SLA rows
    =
    calculated rows
    + exception rows
    + explicitly justified non-applicable rows

Also reconcile:

- client counts
- month counts
- report row counts
- total credit from detail vs executive aggregation

Raise/report reconciliation failures.

This is a mandatory control.

---

# PHASE 13 — README

Write README.md containing:

1. Problem statement
2. Architecture summary
3. Setup instructions
4. How to run
5. How to run tests
6. Inputs expected
7. Outputs generated
8. Matching hierarchy
9. Calculation rules
10. Assumptions
11. Known exceptions
12. Controls
13. AI component
14. Productionization approach

A reviewer should be able to clone the project and understand it without speaking to the author.

---

# PHASE 14 — AI SYSTEM PROMPT

Create:

    ai/system_prompt.md

The AI assistant should be instructed approximately as follows:

"You are an operational assistant for the Client Credit Report system.

The deterministic credit engine is the authoritative source for all financial values.

Never independently calculate or modify a client Credit when an authoritative calculated value exists.

Never invent missing Finance or DevOps values.

Clearly distinguish deterministic results from AI-generated explanations.

When a record is ambiguous, unmatched, LOW confidence, or otherwise exceptional, explain the issue and recommend human review rather than asserting an unsupported resolution.

You may:
- summarize reports
- explain calculations using supplied deterministic fields
- explain exception reason codes
- identify records requiring attention
- draft operational summaries

You may not:
- override calculation rules
- approve ambiguous matches
- invent market mappings
- modify source data
- authorize a financial credit."

Improve the wording as needed without weakening these controls.

---

# PHASE 15 — BEDROCK DESIGN

Create:

    ai/bedrock_design.md

Document an optional AWS Bedrock implementation.

Do NOT make the local solution depend on AWS.

Describe:

    User
      |
      v
    Bedrock Agent
      |
      v
    approved tool/function
      |
      v
    deterministic Python credit engine
      |
      v
    structured JSON result
      |
      v
    Bedrock explanation

Document possible tools such as:

    get_client_credit(client, month)
    get_client_exceptions(client, month)
    get_executive_summary(month)
    explain_credit_record(record_id)

The agent should read deterministic results rather than recompute them.

If implementing the AWS prototype, credentials must come from normal AWS credential mechanisms.

NEVER place:

- AWS access keys
- secret keys
- passwords
- account credentials

inside the repository.

---

# PHASE 16 — FINAL QUALITY GATE

Before considering implementation complete, answer these questions:

1. Did every relevant SLA row receive an outcome?
2. Can every calculated credit be traced to its source data?
3. Are ambiguous matches visible?
4. Are unmatched records visible?
5. Are duplicate conditions visible?
6. Are LOW-confidence records visible?
7. Is the 20% cap tested?
8. Is CLIENT-004 isolated to its own rule?
9. Can a new DevOps client/month file be processed without changing main.py?
10. Can a new client-specific rule be added cleanly?
11. Can the core solution run without AWS?
12. Are AI-generated statements clearly separated from deterministic results?
13. Are all assumptions documented?
14. Do detail totals reconcile to executive totals?
15. Are credentials absent from the repository?
16. Does pytest pass?

Do not claim completion until all applicable checks pass.

---

# DEVELOPMENT BEHAVIOR FOR COPILOT

Do not generate the entire project in one uncontrolled pass.

Work phase-by-phase.

Before each phase:

1. state what you observed
2. state what you intend to implement
3. identify assumptions
4. implement
5. run/test
6. report result

If source data contradicts ARCHITECTURE.md assumptions, STOP and surface the contradiction.

Do not alter source data to make tests pass.

Do not fabricate expected outputs.

Prefer readable code over clever code.

Use type hints and docstrings for important functions.

Keep functions small enough to test independently.

Avoid unnecessary frameworks.

The final solution should be understandable and defensible by the candidate during a technical panel.
