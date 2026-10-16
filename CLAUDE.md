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
| `DATASET#<id>` | `META` | filename, uploaded_at, status (`RECEIVED`/`PROCESSING`/`COMPLETED`/`FAILED`/`DUPLICATE`), row counts, file hash, `duplicate_of`, `quarantine_key`, error message |
| `DATASET#<id>` | `STATS#OVERALL` | overall mean, median, pass rate, etc. |
| `DATASET#<id>` | `STATS#SUBJECT#<subject>#<term>` | per-subject/term stats |
| `DATASET#<id>` | `ANOMALY#<rank>` (5 digits, rank 1 = most important) | `method` (`zscore` / `term_jump` / `duplicate_id` / `isolation_forest`), student_id, subject, term, score, `pattern`, severity, details (the reason) |
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

All zip Lambdas share `CodeUri: src/` and differ only by handler (e.g. `gradeflow.entrypoints.process_handler.handler`).

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
- [x] Phase 1: MVP pipeline (`v0.1.0`)
- [x] Phase 2: Cleaning & statistics (`v0.2.0`)
- [x] Phase 3: Reliability (`v0.3.0`)
- [x] Phase 4: Orchestration (`v0.4.0`)
- [x] Phase 5: Anomaly detection (`v0.5.0`)
- [x] Phase 6: Analytics API (`v0.6.0`)
- [x] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)

**Current phase:** Phase 8 (not started, waiting for "next phase")

**Notes / decisions log:**
- Project name **GradeFlow**, Python package `gradeflow`, repo folder `gradeflow-serverless-pipeline`.
- **Clean architecture** with 4 layers under `src/gradeflow/`; pandas allowed in domain (pragmatic), boto3 only in infrastructure.
- Local dev uses Python **3.12** (`python3.12 -m venv .venv`) to match the Lambda runtime.
- Phase 1 validation will use the stdlib `csv`/`json` modules (no pandas layer until Phase 2).
- Git: one file per commit, conventional messages, ask before committing, no co-author trailers.
- Folders from Section 9 are created only when a phase first puts a file in them (git doesn't track empty folders).
- Sample generator uses stdlib only; default seed 42 → 904 rows with 3 injected T1→T2 jumps.
- Phase 1 (deployed + verified 2026-09-28): stack `gradeflow` in `ap-south-1`. Sample CSV → `COMPLETED` 904/894/10; missing-columns CSV → `FAILED`.
- Phase 1 code: `domain/dataset.py` + `domain/validation.py` (pure, `current_year` passed in); `application/ports.py` (`FileStorage`, `DatasetRepository`) + `validate_dataset.py`; `infrastructure/config.py`, `s3_storage.py`, `dynamodb_repository.py`; `entrypoints/validate_handler.py`.
- `dataset_id` comes from the S3 key `uploads/<dataset_id>/<filename>`; other key shapes are logged and skipped. The "current year" for validation is the S3 event time's year.
- Subject casing is NOT a validation error (cleaning normalizes it in Phase 2). `row_errors` capped at 20 to keep the META item small.
- Raw bucket name `${StackName}-raw-${AccountId}-${Region}` is built with `!Sub`. The Lambda's S3ReadPolicy uses the same string (not `!Ref`) to avoid a bucket↔function circular dependency.
- Lambdas run on arm64, 256 MB, 60 s; log groups keep 14 days. `MinValidRatio` is a SAM parameter → env var `MIN_VALID_RATIO`.
- Adapters are built lazily with `@functools.cache` in the handler (one set per warm container); `process_event(event, use_case)` takes the use case as a parameter so tests can inject fakes.
- Phase 2 (deployed + verified 2026-10-13): sample CSV → `COMPLETED`, 904 total / 894 valid / 891 clean / 3 duplicates; `STATS#OVERALL` mean 62.43, pass rate 78.34, 60 students; 15 `STATS#SUBJECT#` items; Parquet at `processed/<id>/data.parquet`.
- Phase 2 code: `domain/cleaning.py` (pandas; subject acronyms ICT/IT/PE stay upper case), `domain/statistics.py` (frozen dataclasses, sample std ddof=1, ties ordered by student_id), `split_valid_rows` in validation; `application/process_dataset.py` (`ProcessDataset`, replaces `ValidateDataset`; saves META `PROCESSING` first and `COMPLETED` last); ports `ProcessedDataWriter` + `DatasetRepository.save_stats`; `infrastructure/s3_parquet_writer.py`; `process_handler.py` replaces `validate_handler.py`.
- Template: `ValidateFunction` renamed `ProcessFunction` (512 MB, 120 s) with the AWS SDK for pandas layer 3.17.1 (parameter `PandasLayerArn`, ap-south-1 arm64 version 31; local pyarrow pinned 25.0.1 to match its `<26` limit). New `ProcessedBucket` (no expiry), params `PassMark`, env vars `PROCESSED_BUCKET`, `PASS_MARK`.
- DynamoDB floats are stored as `Decimal(str(value))` (exact rounded values). Stats written with `batch_writer`. 0 valid rows is always `FAILED` even if `MinValidRatio=0`.
- Known gap for Phase 3: re-uploading the same `dataset_id` can leave stale `STATS#` items from the earlier upload; idempotency (file hash) addresses re-uploads.
- Phase 3 (deployed + verified 2026-10-14): re-upload as demo-003 → `COMPLETED` (claims the hash); same file as demo-004 → `DUPLICATE` of demo-003, no Parquet; broken file demo-005 → `FAILED` + copy at `quarantine/demo-005/broken.csv`; a non-JSON message sent to the queue failed 3 times and landed in the DLQ (~36 min after sending). Datasets from before Phase 3 (e.g. demo-001) have no hash claim until re-uploaded.
- Phase 3 code: `domain/fingerprint.py` (`sha256_hex`), `DatasetStatus.DUPLICATE` + META fields `file_hash`/`duplicate_of`/`quarantine_key`; `ProcessDataset` flow = read → hash → claim → validate/clean → Parquet → delete old stats → save stats → `COMPLETED`; ports `FileQuarantine`, `DatasetRepository.claim_file_hash` (returns owner or None) and `delete_stats`; adapters `S3Quarantine` (copy), conditional `put_item` claim, `QUARANTINE_BUCKET`; handler unwraps SQS bodies (S3 `s3:TestEvent` ignored, malformed body raises).
- Decisions: added status `DUPLICATE` (deviation from the original four statuses, approved by default); quarantine **copies** (no delete permission on the raw bucket); the same dataset_id owning the hash means an SQS retry, not a duplicate; a FAILED file keeps its hash claim (re-upload under a new ID = `DUPLICATE`); bad files are consumed as `FAILED`, infrastructure errors raise so SQS retries then uses the DLQ; stale `STATS#` items are deleted before re-writing stats (closes the Phase 2 gap).
- Template: `UploadQueue` (visibility 720 s = 6x Lambda timeout, maxReceiveCount 3) + `DeadLetterQueue` (14 days), queue policy limited to this bucket and account (bucket ARN built as a string), S3 notification → queue for `uploads/`, SQS event source batch size 1, `QuarantineBucket` (30-day expiry). DynamoDB permissions are an exact action list (PutItem, GetItem, Query, DeleteItem, BatchWriteItem).
- **Phase 8 IAM review TODO:** SAM attaches the AWS-managed `AWSLambdaSQSQueueExecutionRole` for the SQS event (covers all queues in the account); `S3WritePolicy`/`S3ReadPolicy` templates include a few actions the code does not use (e.g. `PutObjectAcl`, `PutLifecycleConfiguration`).
- SQS moves a message to the DLQ on the receive after `maxReceiveCount` is reached, so with a 12-minute visibility timeout a poison message takes ~36 minutes. For a faster demo, lower `VisibilityTimeout`, but keep it at least 6x the Lambda timeout in real use.
- Shell tip: zsh does not treat `#` as a comment when pasting, so README command blocks contain no inline comments.
- Phase 4 (deployed + verified 2026-10-14): good file (seed 7) → steps `Validate IsValid Clean Stats Store`, `SUCCEEDED`, `COMPLETED` 904/894/891, mean 64.07, median 64.5, pass rate 89.23, 60 students; duplicate → `DUPLICATE` and **no** execution started; bad file → `Validate IsValid MarkFailed NotifyFailure PipelineFailed`, `FAILED` + quarantined; hand-started run for a missing file → Validate made 4 attempts (2/4/8 s backoff), then `FAILED` with a readable `NoSuchKey` message and no quarantine; 25 MB file (525,004 rows, seed 11, 35,000 students) → `COMPLETED` 524,994 valid / 524,991 clean, mean 62.06, median 62.1, pass rate 80.83, 15 subject/term items, whole run 29 s.
- Phase 4 code: `application/pipeline_state.py` (`PipelineState` + `ValidationOutcome`/`CleaningOutcome`, JSON dicts) and `pipeline_steps.py` (`TriggerPipeline`, `ValidateStep`, `CleanStep`, `StatsStep`, `StoreStep`, `RejectDataset`) replace `ProcessDataset`; ports `DatasetArtifacts` (valid rows as JSON Lines under `work/<id>/`, final Parquet) and `WorkflowStarter`, plus `DatasetRepository.create_meta_if_absent`; infra `s3_artifacts.py`, `step_functions_starter.py`, one config loader per Lambda; six thin handlers (`trigger`, `validate`, `clean`, `stats`, `store`, `fail`); `statemachine/pipeline.asl.json`; `domain/statistics.py` gained `stats_to_dict`/`stats_from_dict`.
- Decisions: stats travel in the state payload (3.3 KB for the sample; 300 groups stay under 100 KB; limit is 256 KB); execution name is `<dataset_id>-<hash12>-<S3 event time>` (an SQS redelivery maps to the same run, a later re-upload starts a new one: deviation from the plan, which would have made it a no-op and mishandled upload A, B, A); Trigger starts the workflow first and only then calls `create_meta_if_absent`, so a redelivery can never turn `COMPLETED` back into `PROCESSING`; a bad file is a returned `valid=false`, not an exception (no pointless retries); `RejectDataset` quarantines only when `error` is absent (a crash does not quarantine); the ASL uses the direct Lambda-ARN `Resource` form so `ResultPath` merges results cleanly; step failures reach `MarkFailed` through Catch with `ResultPath` `$.error`; if recording the failure fails, a Pass state sets a fallback `$.failure` so the alert still sends; crash messages show only the Lambda `errorMessage`, not the stack trace; SQS visibility timeout is 180 s (6x the 30 s Trigger timeout); `AlertEmail` is an optional SAM parameter.
- Per-function IAM is pinned by `tests/unit/test_template.py` (e.g. Stats reads S3 only, Store has no S3, Validate/Clean have no DynamoDB). The AWS-managed `AWSLambdaSQSQueueExecutionRole` is still attached to the Trigger (all queues): tighten in Phase 8.
- Testing aids: `tests/unit/asl_emulator.py` runs the real ASL file against the real handlers on fakes (retries, catches, choice, ResultPath); `tests/unit/fakes.py` holds shared in-memory ports; `test_template.py` checks handler paths, env vars vs config loaders, ASL placeholders and permissions. Mutating the ASL/template made these fail, so they do catch regressions.
- **Memory sizing (measured, `CLAUDE.md` Section 11 allows >512 MB "when needed")**: about 1.3 MB per 1,000 rows plus about 200 MB baseline. Now Trigger 512, Validate 2048, Clean 2048, Stats 1024, Store/Fail 256. Peaks for the 25 MB file: Trigger 247, Validate 842, Clean 885, Stats 364 MB. A 50 MB file (~1M rows) is *estimated* to need ~1.5 to 1.6 GB in Clean but **has not been run**: 25 MB is the largest verified size. Store used 204 of 256 MB at idle (pandas import): raise it if its payload ever grows.
- Lessons: S3 hash claims persist, so test files must have new bytes (a broken file identical to an earlier one is correctly a `DUPLICATE`); `steps $(last_execution)` shows the newest run of *any* dataset (check it belongs to your test); zsh treats `$VAR:x` as a modifier (use `${VAR}`); a flaky connection can break large multipart uploads (use `AWS_RETRY_MODE=standard AWS_MAX_ATTEMPTS=10`, and fewer parallel streams via `aws configure set default.s3.max_concurrent_requests 2`).
- Phase 5 (deployed + verified 2026-10-15): the seed-42 sample re-uploaded with new bytes (`--seed 42 --year 2024`, because the same bytes are a duplicate) gave `COMPLETED`, steps `Validate IsValid Clean Stats Anomalies Store`, 9 anomalies stored in exactly the locally predicted order: 1 `duplicate_id` (S00045), 5 `term_jump` (S00040 and S00055 spikes, S00034 sustained), 3 `isolation_forest` (S00034 0.739, S00055 0.667, S00040 0.655). The container gave the identical result to the laptop and to a local `docker run`.
- Phase 5 code: `domain/anomalies.py` (3 rule detectors, `Anomaly`, `rank_anomalies`, jump patterns spike/sustained/latest), `domain/student_outliers.py` (4 per-student features, Isolation Forest, fixed seed, skip below 20 students), `application/anomaly_step.py` (`DetectAnomaliesStep`, kept in its own module so zip Lambdas never import scikit-learn: a subprocess test enforces it), `AnomalyOutcome` in the state (findings go to S3 `work/<id>/anomalies.jsonl`, never the 256 KB state), `StoreStep` writes `ANOMALY#` items (cap `MaxAnomalies`=500, META keeps the true `anomaly_count`), `entrypoints/anomalies_handler.py`, `docker/anomalies/` (Dockerfile on `public.ecr.aws/lambda/python:3.12`, arm64, exact library versions), `.dockerignore`.
- Decisions: one Anomalies step (container Lambda) runs rules + forest; `method` has 4 values (deviation from the spec's 2); a term-jump `pattern` was added after the question "a big change can be genuine" (spike ranks above sustained; the planted S00034 is 34 -> 77 -> 99, so it is `sustained` and ranks below the spikes: kept as is, not bent); ranking is method priority, then pattern, then severity (so the cap of 500 keeps data problems and spikes first); thresholds `ZScoreLimit`=3, `JumpThreshold`=30, `IfContamination`=0.05 are SAM parameters -> env vars; a test checks template defaults equal the code defaults. Class-relative jumps (subtract the class's average change) were offered and NOT built.
- **Measured reliability (80 generated files: 20 seeds x sizes 30/60/120/300):** rule term jumps found all 3 planted in 80/80, duplicate ID 80/80; Isolation Forest at 5%: 82% average recall and all 3 in 11/20 files for 60 students, 100% for 300 students; at 10% (60 students) 98% but ~49% precision. The forest is a second opinion; seed 42 is a lucky seed, not typical. Tests pin only what holds (>= 70% mean recall at 60 students; 100% at 300).
- Quirks found: pandas `.assign()` of a full-length Series onto an EMPTY selection resurrects every row (fixed by masking first); identical student profiles give tied forest scores so nobody is flagged (documented in a test); a group of n scores can never exceed |z| = (n-1)/sqrt(n), so groups of 10 or fewer never trigger z>3.
- Container facts: image 1.45 GB on disk, 322 MB compressed in ECR (free tier 500 MB for 12 months on a new account, else ~$0.03/month); `sam deploy` created the ECR repo (`resolve_image_repos = true`); `Runtime`/`CodeUri`/`Layers` moved off Globals onto the six zip functions (invalid for image functions). **Cold start**: the first start after a deploy hit Lambda's 10 s init limit (`INIT_REPORT Status: timeout`), Lambda re-ran init inside the invocation, the Anomalies state took 61 s (47 s loading libraries, 3 s real work); a warm run took 1.6 s for the whole pipeline (Anomalies 0.4 s). The 300 s timeout covers this: do not lower it. Memory used 374-379 MB on 904-row files; the 25 MB file was NOT run through Anomalies. The `joblib will operate in serial mode` warning is harmless (no shared memory on Lambda).
- Tightening done in task 6: `StoreStep` now REQUIRES the `anomalies` section and its artifacts, the Store config requires `PROCESSED_BUCKET` (a state machine that skipped Anomalies fails loudly and writes nothing). Store's S3 permission is `s3:GetObject` on `work/*` only (pinned by a test). Mutating the ASL/template made the new tests fail.
- **Phase 8 IAM TODO still open:** the AWS-managed `AWSLambdaSQSQueueExecutionRole` on the Trigger (all queues), and S3 policy templates with a few unused actions. New in Phase 8 scope: consider lowering image size (e.g. drop unused packages) if cold start matters.
- Phase 6 (deployed + verified 2026-10-15, API `https://<id>.execute-api.ap-south-1.amazonaws.com/v1`): all five endpoints checked with the real key against real data. `GET /datasets` newest first (15 datasets, paged 5 at a time, no duplicates, no `HASH#` items); `demo-030` detail 904 -> 894 -> 891, stats mean 62.43 / pass rate 78.34 / 60 students / 15 groups, 9 anomalies in rank order, `isolation_forest` filter gives S00034, S00055, S00040, paging pages 4/4/1 with a null last cursor; pre-Phase-5 `demo-020` shows 0 anomalies, failed `demo-014` shows FAILED with empty stats (200). Errors: 404 unknown dataset, 400 for bad limit/method/cursor/id/JSON/filename, 403 for no or wrong key. **CORS preflight without a key returns 200.** A file uploaded through `POST /uploads` and the signed form reached COMPLETED (8 anomalies). S3 itself refused an empty file (`EntityTooSmall`), proving the signed size range works; the API refused `.txt` before signing. Warm latency about 315 ms from the laptop, first call 719 ms.
- Phase 6 code: `application/read_ports.py` + `queries.py` (ListDatasets, GetDataset, GetStats, GetAnomalies, CreateUpload; plain dicts, `QueryError` carries status + code), `infrastructure/dynamodb_reader.py` (read-only, GSI1 listing, one-extra-item paging so no empty trailing page, method filter fills pages, strictly validated opaque cursors), `s3_upload_signer.py`, `table_keys.py`, `entrypoints/api_handler.py` (router, standard error body, CORS + no-store headers, 4 KB body cap, never leaks internals), `scripts/api_upload.py`, `tests/integration/conftest.py` (shared moto fixtures).
- Decisions: REST API + API key + usage plan (HTTP APIs have no built-in keys); pre-signed POST with a 1 byte to 50 MB range (a PUT cannot cap size); dataset ids are server-generated so nobody can overwrite another dataset; the API Lambda has NO pandas layer (subprocess tests enforce it for the handler, reader and signer); DynamoDB access is GetItem + Query only on the table and GSI1, S3 is PutObject on `uploads/*` only (pinned by tests, and a spy test proves the reader only calls query/get_item); an unfinished or failed dataset returns 200 with empty stats rather than an error; the S3 `RECEIVED` status is still never written (nothing exists between upload and the Trigger).
- Bugs found before they reached users: (1) requiring the API key on the whole API also required it on the CORS OPTIONS preflights, so every browser call would 403 (fixed by requiring the key per route; found by expanding the template with SAM's translator; pinned by a test); (2) `generate_presigned_post` with a default client returns the GLOBAL endpoint `bucket.s3.amazonaws.com`, which redirects outside us-east-1 and a browser cannot follow a redirect on a cross-origin POST (fixed with SigV4 + virtual addressing -> regional URL); (3) the reader imported key helpers from the repository, which pulled pandas/numpy/pyarrow into the API Lambda (fixed by `table_keys.py`); (4) cfn-lint W3005 on SAM-generated usage-plan resources (ignored for that single check at template level).
- **Open: Lambda concurrency.** The account's quota is 10 concurrent executions (AWS default 1000, adjustable, NO increase requested yet; command: `aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 100`). 60 simultaneous API requests give mostly HTTP 500 (Lambda refuses at 10; CloudWatch showed 52 throttles, 0 function errors). The usage plan (now 5 req/s, burst 10, 5,000/day) is best-effort and still let about 50 of 60 through, so it did NOT fix it; a realistic 8-request dashboard load gave 7 x 200 + 1 x 429. **Phase 7 dashboard must retry 429 and 500 with backoff.** The pipeline is protected by Step Functions retries (3) and SQS.
- Phase 7 reminders: the S3 raw bucket needs its own CORS rule for browser POST uploads (needs the dashboard origin); tighten `AllowedOrigin` from `*`; the API key shipped in JS is public (not real auth; Cognito is the later option); `v0.5.0` tag was created one commit early and may still need `git tag -f -a v0.5.0 bd5a4a3` + `git push --force origin v0.5.0`.
- Phase 7 (deployed + verified 2026-10-16, site served by CloudFront; address = stack output `DashboardUrl`): a file chosen in the browser (generator `--seed 77 --year 2024`) reached S3, ran Step Functions to `SUCCEEDED` and showed as `COMPLETED`: 904 total / 894 valid / 10 rejected / 891 clean / 3 duplicates removed, mean 69.25, pass rate 93.38, 60 students, 15 subject/term items, 10 anomalies (1 `duplicate_id` S00056, 3 `term_jump` S00052/S00019/S00036 all `sustained`, 3 `zscore` for S00049 whose planted -5 score is a data error, 3 `isolation_forest` for the same three jump students). From the command line: `index.html` 200 `no-cache`; `config.json` 200 `no-store` (key 40 chars, never printed); assets `max-age=31536000,immutable`; direct bucket access 403; HTTP redirects to HTTPS; HSTS, nosniff, frame deny, no-referrer and the CSP present; S3 preflight allows POST from the dashboard and from `http://localhost:5173` and refuses an unknown origin and PUT; the API answers 200 with the key and 403 without. **Not verified by me:** how it looks in a browser (the user viewed it and said it works nicely) and DevTools console (CSP is enforced; nobody reported a violation).
- Phase 7 code: `dashboard/` (React 19, Vite 8, TypeScript 7 strict, Recharts 3, React Router 7 with hash routes, Vitest 5 + Testing Library). `src/config.ts` (runtime `config.json`), `src/api/{client,context,types}` (retry + concurrency limiter), `src/lib/*` (charts, format, labels, anomalies, polling, upload, palette: pure and tested), `src/hooks/*` (`useDatasetList`, `useDatasetDetail`, `useAnomalies`, `useUpload`, `useTitle`), `src/components/*`, `src/pages/{Datasets,Upload,DatasetDetail,NotFound}Page.tsx`, `src/styles.css`. Real API responses are test fixtures in `src/test/fixtures/`. Infra: `DashboardBucket` (private), `DashboardOriginAccessControl`, `DashboardBucketPolicy`, `DashboardHeaders`, `DashboardDistribution`, a CORS rule on `RawBucket`, parameter `DashboardDevOrigin`, outputs `DashboardUrl`/`DashboardBucketName`/`DashboardDistributionId`. `scripts/deploy_dashboard.sh`. Tests: 223 Vitest, 638 pytest (`tests/unit/test_dashboard_hosting.py`, `test_deploy_dashboard_script.py` added).
- Decisions: hash routing, so CloudFront needs no rewrites and no custom error pages (a missing `config.json` stays a clear 403); runtime `config.json` written by the deploy script into `dist/` (never `public/`, mode 600, git-ignored), `config.json` is never cached; the deploy script runs the dashboard tests first, uploads assets, then config, then `index.html`, then clears `/index.html` and `/`; the API key in the browser is public by design (not auth; Cognito later); the client keeps at most 4 requests in flight and retries 429/5xx/network up to 4 attempts with backoff (the Lambda concurrency quota of 10 is the reason); list and detail pages poll every 5 s for 2 minutes, every 15 s until 10 minutes, then stop with a Refresh button, and pause while the tab is hidden (protects the 5,000/day API quota); uploads use `XMLHttpRequest` for progress and S3 errors are mapped to sentences; every chart has a "Show as table" version; the anomaly method filter is server-side and the student search only covers loaded rows (the page says so); the CSP is enforced and names `https://*.execute-api.<region>.amazonaws.com` because naming this stack's API would create the cycle API -> Lambda -> raw bucket CORS -> distribution -> headers policy -> API (a pytest cycle detector, which models SAM's implicit API -> function edge, pins this); the raw bucket CORS allows only POST from the deployed dashboard plus `DashboardDevOrigin` (default `http://localhost:5173`, empty switches it off, wildcards rejected by `AllowedPattern`); contrast of both themes and chart colours is pinned in `src/styles.test.ts` (this found two chart colours below 3:1, replaced).
- Lessons (test technique): React batches state updates inside one `act`, so a polling test must advance fake time in 1 s steps, one `act` each; `MemoryRouter` ignores a new `initialEntries` on rerender (navigate with a `Link`); vitest `css: false` makes `?raw` CSS imports empty (use `css: { include: [/styles\.css/] }`); a mocked module rejection made one page test fail even though every step passed (replaced by an API-level failure; the S3 failure path is covered in the hook tests); zsh does not word-split an unquoted `$VAR` and macOS `sed` has no `0,/re/`, so a first mutation run silently changed nothing and left mutations in the files (redone with real backups and md5 verification: always verify the restore).
- **Open after Phase 7:** the API's own CORS origin is still `*` (set `AllowedOrigin` to the dashboard address to tighten, which breaks `npm run dev` against the deployed API); the key in `config.json` is public; the JS bundle is 678 kB (202 kB gzipped, mostly Recharts; lazy-loading charts is possible); no CloudFront access logging or WAF; the Lambda concurrency quota increase is still not requested; the Phase 8 IAM TODOs above are still open.
