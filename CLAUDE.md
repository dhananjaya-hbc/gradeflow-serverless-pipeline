# GradeFlow: Serverless Data Pipeline for Education Analytics

> This file is the project brief for Claude Code. Read it fully before doing anything.

---

## 0. How to work with me (MOST IMPORTANT)

I am a university student building this project to **learn** and to use it as a **portfolio project** for Software Engineer / AI intern roles. I want to build it **step by step**, not all at once.

Follow these rules strictly:

1. **One phase at a time.** Only work on the current phase listed in the Progress Tracker (Section 12). Never implement features from later phases, even if it seems convenient.
2. **Plan first, then code.** At the start of each phase:
   - Summarize what you will build in this phase (files to create/change, AWS resources, tests).
   - Wait for my approval ("go" / "ok") before writing code.
3. **Small steps inside a phase.** Break each phase into small tasks. After each task, briefly tell me what you did and why.
4. **Explain as you go.** I am learning AWS and serverless. Briefly explain important decisions (why SQS, why this IAM permission, etc.). Keep explanations short and practical.
5. **Ask before anything that costs money or touches AWS.** Never run `sam deploy`, create AWS resources, or delete anything in AWS without asking me first. Tell me the exact command and let me run it or confirm.
6. **Test before finishing.** Every phase must end with:
   - Unit tests passing (`pytest`).
   - Clear manual verification steps I can follow (e.g. "upload this file, check this DynamoDB item").
7. **End-of-phase checklist.** When a phase is done:
   - Update the Progress Tracker (Section 12) in this file.
   - Update `README.md` with anything new (setup, usage, architecture).
   - Suggest a version tag (e.g. `v0.1.0`).
   - Then **stop** and wait for me to say "next phase".
8. **Never commit secrets.** No AWS keys, account IDs, or credentials in code. Use `.gitignore` and environment variables / SAM parameters.
9. **Keep it simple.** Prefer clear, readable code over clever code. No unnecessary frameworks.
10. If something in this spec is unclear or seems like a bad idea, tell me and suggest an alternative instead of guessing.

### Git workflow

- **One file per commit.** Never batch several files into one commit. Stage and commit each created/changed file on its own: `git add <file>` then `git commit -m "<message>"`.
- **Conventional commit messages:** `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `build:`, `ci:`.
- **Ask before committing.** At the end of a task, show me the list of planned commits (file → message) and wait for my OK.
- **My identity only.** Commits use my git identity. Do not add `Co-Authored-By` trailers.

### Clean architecture rules

The code under `src/gradeflow/` follows clean architecture. **Dependencies point inward only:**

```
entrypoints  ──▶  infrastructure  ──▶  application  ──▶  domain
(Lambda handlers)  (AWS adapters)      (use cases, ports)  (entities, rules)
```

- `domain/`: entities, value objects, pure business rules (validation, stats, anomaly detection). **No boto3, no AWS, no env vars.** pandas is allowed (pragmatic choice).
- `application/`: use cases and **ports** (`typing.Protocol` interfaces such as `DatasetRepository`, `FileStorage`). Depends only on `domain`.
- `infrastructure/`: adapters that implement ports with boto3 (DynamoDB, S3, Step Functions) plus config loading from env vars.
- `entrypoints/`: thin Lambda handlers (the composition root). Parse the event, build adapters, call a use case, and return the result. No business logic here.
- Add a port only when there is a real external dependency. Don't create an abstraction for everything.
- A test in `tests/unit/test_architecture.py` enforces that `domain` and `application` never import `boto3` or outer layers.

---

## 1. Project Summary

A serverless, event-driven data pipeline on AWS. When a dataset (CSV or JSON) is uploaded, the system automatically:

1. Validates the data
2. Cleans the data
3. Calculates statistics
4. Detects anomalies (rule-based + machine learning)
5. Stores the results
6. Serves the results through an Analytics API to a web dashboard

**Example use case:** An education company uploads `student_results.csv`. The system processes it and the dashboard shows subject averages, pass rates, top/bottom performers, and flagged anomalies (e.g. suspicious score jumps, duplicate records).

**Goals of the project:**
- Demonstrate real-world cloud engineering: event-driven design, queues, orchestration, IaC, monitoring, least-privilege security.
- Demonstrate applied ML: anomaly detection with Isolation Forest.
- Be fully reproducible: one command deploys the whole stack.
- Be cheap: stay within the AWS Free Tier as much as possible.

---

## 2. Architecture

```
            ┌──────────────┐
 Upload ──▶ │  S3 (raw)     │  (CSV / JSON)
            └──────┬───────┘
                   │ S3 Event Notification
                   ▼
            ┌──────────────┐      ┌──────────┐
            │  SQS queue    │ ───▶ │   DLQ    │ (failed messages)
            └──────┬───────┘      └──────────┘
                   │
                   ▼
            ┌──────────────┐
            │ Trigger Lambda│ (starts workflow, idempotency check)
            └──────┬───────┘
                   ▼
   ┌────────────── Step Functions state machine ───────────────┐
   │ Validate ─▶ Clean ─▶ Compute Stats ─▶ Detect Anomalies ─▶ Store │
   │     │                                                     │
   │     └─ invalid ─▶ Quarantine file + mark FAILED (+ SNS)    │
   └────────────────────────────────────────────────────────────┘
                   │
      ┌────────────┴─────────────┐
      ▼                          ▼
 S3 (processed, Parquet)    DynamoDB (metadata, stats, anomalies)
                                 │
                                 ▼
                    API Gateway + API Lambda
                                 │
                                 ▼
                 React Dashboard (S3 + CloudFront)

 Cross-cutting: CloudWatch (logs, metrics, alarms), IAM (least privilege)
```

**Why each service:**
- **S3**: cheap, durable storage for raw and processed files; emits upload events.
- **SQS + DLQ**: buffers bursts of uploads, gives retries, and failed files are not lost.
- **Lambda**: runs the processing code with no servers to manage.
- **Step Functions**: splits processing into clear steps with per-step retries, error branches, and visual execution history.
- **DynamoDB**: fast key-value storage for dataset status, stats, and anomalies served to the API.
- **API Gateway**: HTTP API for the dashboard.
- **CloudWatch**: logs, metrics, dashboards, alarms.
- **IAM**: each function gets only the permissions it needs.

---

## 3. Tech Stack

| Area | Choice |
|---|---|
| Language (backend) | Python 3.12 |
| Architecture | Clean architecture (domain / application / infrastructure / entrypoints) |
| Infrastructure as Code | AWS SAM (`template.yaml`) |
| Data processing | pandas via the **AWS SDK for pandas** managed Lambda layer |
| ML | scikit-learn (Isolation Forest) in a **container-image Lambda** (too big for zip) |
| Lambda utilities | Powertools for AWS Lambda (Python): logging, metrics, tracing |
| Testing | pytest, moto (AWS mocks) |
| Linting / formatting | ruff |
| Frontend | React + Vite + Recharts |
| CI/CD | GitHub Actions |
| AWS region | `ap-south-1` (Mumbai) by default, configurable |

---

## 4. Dataset Schema

Primary input: `student_results.csv`

| Column | Type | Rules |
|---|---|---|
| `student_id` | string | required, format `S` + 5 digits (e.g. `S00123`) |
| `student_name` | string | required, non-empty |
| `class` | string | required (e.g. `10-A`) |
| `subject` | string | required, normalized to Title Case (`maths` → `Maths`) |
| `term` | string | required, one of `T1`, `T2`, `T3` |
| `year` | integer | required, 2000–current year |
| `score` | number | required, 0–100 |

JSON input: an array of objects with the same fields.

**Validation outcomes:**
- **File-level failure** (wrong columns, unreadable file, empty file) → dataset marked `FAILED`, file moved to quarantine bucket.
- **Row-level issues** (bad score, missing value) → row rejected, counted, and reported; processing continues if the valid-row ratio ≥ 80% (configurable).

**Sample data:** `scripts/generate_sample_data.py` must generate **synthetic** data only (never real student data), with deliberate problems: missing values, scores > 100, duplicates, inconsistent subject casing, and a few injected anomalies (sudden score jumps between terms).

---

## 5. Storage Design

### S3 buckets
- `raw-<suffix>`: uploads land here (`uploads/<dataset_id>/<filename>`)
- `processed-<suffix>`: cleaned data as Parquet (`processed/<dataset_id>/data.parquet`)
- `quarantine-<suffix>`: invalid files

### DynamoDB (single table: `PipelineTable`)

| PK | SK | Content |
|---|---|---|
| `DATASET#<id>` | `META` | filename, uploaded_at, status (`RECEIVED`/`PROCESSING`/`COMPLETED`/`FAILED`), row counts, file hash, error message |
| `DATASET#<id>` | `STATS#OVERALL` | overall mean, median, pass rate, etc. |
| `DATASET#<id>` | `STATS#SUBJECT#<subject>#<term>` | per-subject/term stats |
| `DATASET#<id>` | `ANOMALY#<n>` | anomaly type, student_id, details, score, method (`zscore` / `isolation_forest`) |
| `HASH#<sha256>` | `HASH` | dataset_id (for idempotency / duplicate upload detection) |

GSI for listing datasets by upload time (e.g. `GSI1PK = DATASETS`, `GSI1SK = uploaded_at`).

---

## 6. Processing Logic

- **Validate:** schema check, type check, range check; produce valid/invalid row counts and a validation report.
- **Clean:** drop exact duplicates, trim whitespace, normalize subject names, handle missing values (drop rows missing required fields), write Parquet.
- **Statistics:** per subject + term: count, mean, median, std, min, max, pass rate (pass mark 50, configurable); top 5 and bottom 5 students overall; class averages.
- **Anomaly detection:**
  - Rule-based: z-score > 3 within subject/term; score jumps > 30 points between consecutive terms for the same student/subject; duplicate student IDs with different names.
  - ML: Isolation Forest on per-student features (mean score, std across subjects, term-to-term change). Store anomaly score and reason.
- **Store:** write stats and anomalies to DynamoDB, update dataset status.

All processing logic lives in `domain/` as **pure functions** (take data, return results) so it is easy to unit test without AWS. Use cases in `application/` orchestrate them through ports; Lambda handlers in `entrypoints/` are thin wrappers.

---

## 7. Analytics API

| Method | Path | Description |
|---|---|---|
| `POST` | `/uploads` | returns a pre-signed S3 URL for uploading a file |
| `GET` | `/datasets` | list datasets with status (newest first) |
| `GET` | `/datasets/{id}` | dataset metadata + validation report |
| `GET` | `/datasets/{id}/stats` | overall and per-subject stats |
| `GET` | `/datasets/{id}/anomalies` | list of anomalies |

JSON responses, proper HTTP status codes, CORS enabled for the dashboard. Protected with an API key (Cognito optional later).

---

## 8. Dashboard

React + Vite + Recharts, hosted on S3 + CloudFront.

Pages:
- **Upload**: select CSV/JSON, upload through pre-signed URL, show progress.
- **Datasets**: table of datasets with status badges, auto-refresh while processing.
- **Dataset detail**: summary cards (rows, pass rate, average), bar chart of subject averages, line chart of term trends, anomalies table with filters.

---

## 9. Repository Structure

```
gradeflow-serverless-pipeline/
├── CLAUDE.md                  # this file
├── README.md
├── pyproject.toml             # ruff + pytest config
├── requirements-dev.txt
├── template.yaml              # AWS SAM template
├── samconfig.toml
├── statemachine/
│   └── pipeline.asl.json
├── src/
│   └── gradeflow/             # one shared package, used by every Lambda
│       ├── domain/            # entities + pure rules (no AWS)
│       ├── application/       # use cases + ports (Protocols)
│       ├── infrastructure/    # boto3 adapters, config
│       └── entrypoints/       # thin Lambda handlers
├── docker/
│   └── anomalies/             # Dockerfile for the ML container Lambda (Phase 5)
├── tests/
│   ├── unit/                  # domain + application tests, no AWS
│   ├── integration/           # infrastructure adapter tests with moto
│   └── fixtures/
├── scripts/
│   └── generate_sample_data.py
├── dashboard/                 # React app
├── docs/
│   └── architecture.png
└── .github/workflows/
    └── ci.yml
```

All zip Lambdas share `CodeUri: src/` and differ only by handler (e.g. `gradeflow.entrypoints.validate_handler.handler`).

---

## 10. Implementation Phases

Each phase ends with a working, tested, tagged version.

### Phase 0: Project setup → no tag
- Create repo structure, `.gitignore`, `README.md` skeleton, `requirements-dev.txt`, ruff config, pytest config.
- Write `scripts/generate_sample_data.py` (synthetic data with deliberate problems).
- Give me a checklist for AWS setup: billing alarm, MFA, IAM user, AWS CLI + SAM CLI install and configure.
- **Done when:** sample CSV generates, `pytest` runs (even with 0 tests), repo is committed.

### Phase 1: MVP pipeline → `v0.1.0`
- SAM template: raw bucket, one Lambda triggered directly by S3 upload, DynamoDB table.
- Lambda validates schema and writes a `META` item with row counts and status.
- Unit tests for validation logic.
- **Done when:** uploading the sample CSV creates a correct `META` item in DynamoDB.

### Phase 2: Cleaning & statistics → `v0.2.0`
- Add AWS SDK for pandas layer, processed bucket.
- Cleaning + stats logic as pure functions with tests.
- Write Parquet to processed bucket, stats items to DynamoDB.
- **Done when:** stats in DynamoDB match expected values computed in tests.

### Phase 3: Reliability → `v0.3.0`
- Route S3 events via SQS with a DLQ.
- Idempotency using file SHA-256 (`HASH#` items): duplicate uploads are skipped.
- Quarantine bucket for invalid files.
- **Done when:** re-uploading the same file does not reprocess it; a broken file ends up in quarantine with status `FAILED`.

### Phase 4: Orchestration → `v0.4.0`
- Split logic into separate Lambdas: validate, clean, stats, store.
- Step Functions state machine with retries, catch → failure branch, optional SNS email alert.
- Trigger Lambda: SQS → start execution.
- **Done when:** a full run is visible in the Step Functions console with all steps green; a bad file follows the failure branch.

### Phase 5: Anomaly detection → `v0.5.0`
- Rule-based detectors (z-score, term jump, duplicate IDs) with tests.
- Isolation Forest step as a container-image Lambda.
- Add anomalies step to the state machine.
- Document the method and results in README.
- **Done when:** injected anomalies from the sample generator are detected.

### Phase 6: Analytics API → `v0.6.0`
- API Gateway + API Lambda with all endpoints from Section 7, pre-signed upload URLs, CORS, API key.
- Tests for handlers.
- **Done when:** all endpoints return correct data via `curl`.

### Phase 7: Dashboard → `v0.7.0`
- React app with Upload, Datasets, Dataset detail pages.
- Hosting on S3 + CloudFront (add to SAM or separate template).
- **Done when:** I can upload a file from the browser and see results appear.

### Phase 8: Observability & security → `v0.8.0`
- Powertools structured logging and custom metrics (rows processed, anomalies found, failures).
- CloudWatch dashboard; alarms for Lambda errors and DLQ messages.
- Review all IAM policies for least privilege.
- **Done when:** the CloudWatch dashboard shows pipeline metrics and a forced failure triggers an alarm.

### Phase 9: CI/CD & release → `v1.0.0`
- GitHub Actions: ruff lint + pytest on every push/PR; `sam build` + `sam deploy` on push to `main` (using GitHub OIDC role, no stored keys).
- Final README: overview, architecture diagram, features, tech stack, setup & deploy steps, API docs, design decisions, cost estimate, screenshots, demo video link, future improvements.
- **Done when:** a push to `main` deploys automatically and the README is complete. Tag `v1.0.0` and create a GitHub Release.

---

## 11. Constraints & Standards

- **Cost:** stay within Free Tier where possible. Low memory Lambdas (128–512 MB unless needed), DynamoDB on-demand, S3 lifecycle rule to delete raw files after 30 days. Include `sam delete` instructions in README.
- **Lambda limits:** 15-minute timeout; target files up to ~50 MB. Document in README how larger files would be handled (chunking, AWS Glue, or Fargate); do not build it.
- **Code style:** type hints, docstrings on public functions, ruff clean, small functions.
- **Config:** thresholds (pass mark, valid-row ratio, z-score limit) as environment variables, not hard-coded. Loaded in `infrastructure/config.py` and passed inward.
- **Security:** least-privilege IAM, no public buckets (except dashboard via CloudFront OAC), no secrets in code.
- **Data:** synthetic data only.

---

## 12. Progress Tracker

Claude Code: update this section at the end of every phase.

- [x] Phase 0: Project setup
- [ ] Phase 1: MVP pipeline (`v0.1.0`)
- [ ] Phase 2: Cleaning & statistics (`v0.2.0`)
- [ ] Phase 3: Reliability (`v0.3.0`)
- [ ] Phase 4: Orchestration (`v0.4.0`)
- [ ] Phase 5: Anomaly detection (`v0.5.0`)
- [ ] Phase 6: Analytics API (`v0.6.0`)
- [ ] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)

**Current phase:** Phase 1 (not started, waiting for "next phase")

**Notes / decisions log:**
- Project name **GradeFlow**, Python package `gradeflow`, repo folder `gradeflow-serverless-pipeline`.
- **Clean architecture** with 4 layers under `src/gradeflow/`; pandas allowed in domain (pragmatic), boto3 only in infrastructure.
- Local dev uses Python **3.12** (`python3.12 -m venv .venv`) to match the Lambda runtime.
- Phase 1 validation will use the stdlib `csv`/`json` modules (no pandas layer until Phase 2).
- Git: one file per commit, conventional messages, ask before committing, no co-author trailers.
- Folders from Section 9 are created only when a phase first puts a file in them (git doesn't track empty folders).
- Sample generator uses stdlib only; default seed 42 → 904 rows with 3 injected T1→T2 jumps.
