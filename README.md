# GradeFlow: Serverless Data Pipeline for Education Analytics

An event-driven AWS pipeline that validates, cleans, analyses and flags anomalies in
student results datasets, then serves the results through an API to a web dashboard.

> 🚧 Work in progress, being built phase by phase. See [Roadmap](#roadmap).

## How it works (target architecture)

```
Upload ─▶ S3 (raw) ─▶ SQS (+DLQ) ─▶ Trigger Lambda ─▶ Step Functions
                                                      Validate ─▶ Clean ─▶ Stats ─▶ Anomalies ─▶ Store
                                                                    │
                                        S3 (processed, Parquet) ◀───┴───▶ DynamoDB
                                                                              │
                                                     React dashboard ◀── API Gateway + Lambda
```

## What works today (v0.3.0)

```
Upload ─▶ S3 raw ─▶ SQS queue ─▶ Process Lambda ─┬─▶ S3 processed (Parquet)
          (uploads/)    │         (+ pandas layer) ├─▶ DynamoDB (META, STATS#, HASH#)
                        │                          └─▶ S3 quarantine (rejected files)
                        └─ after 3 failed receives ─▶ dead-letter queue (DLQ)
```

Upload a CSV or JSON file to `uploads/<dataset_id>/<filename>`. S3 sends an event to an SQS
queue, and the Process Lambda handles one upload at a time:

1. **Fingerprints** the file (SHA-256) and claims it in DynamoDB with a conditional write.
   If a *different* dataset already owns that hash, the upload is recorded as `DUPLICATE`
   (`duplicate_of` names the original) and nothing else happens. If the *same* dataset owns it,
   this is a queue retry and processing simply continues.
2. **Validates.** File-level problems (unreadable UTF-8, wrong extension, no rows, missing
   columns) → `FAILED`. Row-level problems are counted, and the first 20 are listed in
   `row_errors`. If fewer than `MinValidRatio` (default 80%) of rows are valid → `FAILED`.
   Every `FAILED` file is **copied to the quarantine bucket** (`quarantine/<id>/<filename>`).
3. **Cleans** the valid rows with pandas: trims text, normalises subjects (`maths` → `Maths`,
   `ict` → `ICT`), casts `year`/`score` to numbers, drops exact duplicates (after normalising).
4. **Writes Parquet** to `processed/<dataset_id>/data.parquet` in the processed bucket.
5. **Computes statistics**, deletes any old `STATS#` items of that dataset, and saves the new ones.
6. Saves the final `META` item. Status flow: `PROCESSING` → `COMPLETED`, `FAILED` or `DUPLICATE`.
   `COMPLETED` is written last, so a completed dataset always has its Parquet file and stats.

### Reliability

| Situation | What happens |
|---|---|
| Same file uploaded again under a new ID | `DUPLICATE`, nothing reprocessed. The hash claim is atomic, so simultaneous uploads cannot both win. |
| Same upload delivered twice by SQS (at-least-once) | Recognised as a retry (same ID owns the hash) and processed again safely. |
| Broken or mostly-invalid file | `FAILED` with an error message, original copied to the quarantine bucket. The message is consumed (no retries: retrying cannot fix a bad file). |
| Infrastructure error (S3 or DynamoDB failing, malformed message) | The Lambda raises, SQS retries, and after 3 receives the message goes to the **DLQ** (kept 14 days). |
| Re-processing an existing dataset ID | Old `STATS#` items are deleted before the new ones are written. |

A failed file also keeps its hash claim, so re-uploading the same broken file under a new ID
is reported as a `DUPLICATE` of the first one.

| Column | Rule |
|---|---|
| `student_id` | required, `S` + 5 digits (e.g. `S00123`) |
| `student_name`, `class`, `subject` | required, non-empty |
| `term` | one of `T1`, `T2`, `T3` |
| `year` | integer, 2000 to the upload year |
| `score` | number, 0 to 100 |

### DynamoDB items (table `PipelineTable`)

| PK | SK | Content |
|---|---|---|
| `DATASET#<id>` | `META` | status, filename, row counts (`total`/`valid`/`invalid`/`clean`), `duplicates_removed`, `row_errors`, `file_hash`, `processed_key`, `quarantine_key`, `duplicate_of`, `error_message` |
| `DATASET#<id>` | `STATS#OVERALL` | count, mean, median, std, min, max, pass rate, `student_count`, top 5 / bottom 5 students, class averages |
| `DATASET#<id>` | `STATS#SUBJECT#<subject>#<term>` | the same metrics for one subject and term |
| `HASH#<sha256>` | `HASH` | `dataset_id` that first uploaded this exact file (duplicate detection) |

Pass rate is the percentage of scores at or above `PassMark` (default 50). All numbers are
rounded to 2 decimals. Results for the default sample file (seed 42):

| | |
|---|---|
| Rows | 904 uploaded → 894 valid → 891 after removing 3 duplicates |
| Overall | mean 62.43, median 62, std 16.12, pass rate 78.34% |
| Students / classes | 60 students; `10-A` 62.11, `10-B` 62.75 |
| Subject/term items | 15 (5 subjects × 3 terms) |

## Code architecture

The Python code follows **clean architecture**. Dependencies only point inward:

| Layer | Folder | Contains | May depend on |
|---|---|---|---|
| Domain | `src/gradeflow/domain/` | Entities, pure rules (validation, stats, anomalies) | nothing (pandas allowed) |
| Application | `src/gradeflow/application/` | Use cases, ports (interfaces) | domain |
| Infrastructure | `src/gradeflow/infrastructure/` | boto3 adapters (S3, DynamoDB), config | application, domain |
| Entrypoints | `src/gradeflow/entrypoints/` | Thin Lambda handlers | all of the above |

Business logic is testable without AWS, and `tests/unit/test_architecture.py` fails the
build if an inner layer imports boto3 or an outer layer.

## Tech stack

Python 3.12 · AWS SAM · Lambda · S3 · SQS · Step Functions · DynamoDB · API Gateway ·
pandas · scikit-learn · pytest · moto · ruff · React + Vite + Recharts · GitHub Actions

## Local setup

Requirements: Python 3.12, git. For deploying you also need the AWS CLI and SAM CLI (Docker
is needed later, from Phase 5). See [docs/aws-setup.md](docs/aws-setup.md).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Generate sample data

All data is **synthetic**. The generator deliberately injects problems (missing values,
out-of-range scores, duplicates, inconsistent subject casing, bad IDs, score jumps) so the
pipeline has something to catch.

```bash
python scripts/generate_sample_data.py                 # data/student_results.csv
python scripts/generate_sample_data.py --format json   # data/student_results.json
python scripts/generate_sample_data.py --students 200 --seed 7
```

The same `--seed` always produces the same file, which makes results reproducible.

### Run checks

```bash
ruff check .                # lint
ruff format --check .       # formatting
pytest                      # all tests
pytest tests/unit           # fast, pure Python (domain, use cases, handler, config)
pytest tests/integration    # S3 + DynamoDB adapters against moto (fake AWS, no account needed)
```

## Deploy to AWS

Prerequisites: an AWS account set up as in [docs/aws-setup.md](docs/aws-setup.md), plus the
AWS CLI and SAM CLI (`brew install aws-sam-cli`). The stack deploys to `ap-south-1` by default
(see `samconfig.toml`).

```bash
aws sts get-caller-identity   # make sure you're using your IAM user, not root
sam validate
sam build
sam deploy                    # shows the change set, then asks for confirmation
```

Optional parameters, e.g. `sam deploy --parameter-overrides MinValidRatio=0.9 PassMark=40`.

pandas and pyarrow come from the **AWS SDK for pandas** managed Lambda layer (parameter
`PandasLayerArn`, default is the `ap-south-1` / Python 3.12 / arm64 layer). In another region,
pick the ARN from <https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html> and pass it with
`--parameter-overrides`.

### Try it

Paste only the code blocks. (zsh does not treat `#` as a comment when pasting, so no comments
are included inside them.)

```bash
export AWS_PAGER=""
out() { aws cloudformation describe-stacks --stack-name gradeflow --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET=$(out RawBucketName); PROCESSED=$(out ProcessedBucketName); QUARANTINE=$(out QuarantineBucketName)
TABLE=$(out PipelineTableName); QUEUE=$(out UploadQueueUrl); DLQ=$(out DeadLetterQueueUrl)
meta() { aws dynamodb get-item --table-name $TABLE --region ap-south-1 \
  --key "{\"PK\":{\"S\":\"DATASET#$1\"},\"SK\":{\"S\":\"META\"}}" \
  --query "Item.{status:status.S,dup_of:duplicate_of.S,clean:clean_rows.N,hash:file_hash.S,quarantine:quarantine_key.S,error:error_message.S}"; }
python scripts/generate_sample_data.py
```

**1. A good file is processed** (the first run is a cold start that loads pandas):

```bash
aws s3 cp data/student_results.csv s3://$BUCKET/uploads/demo-001/student_results.csv
sleep 20
meta demo-001
aws s3 ls s3://$PROCESSED/processed/demo-001/
aws dynamodb get-item --table-name $TABLE --region ap-south-1 \
  --key '{"PK":{"S":"DATASET#demo-001"},"SK":{"S":"STATS#OVERALL"}}' \
  --query "Item.{count:count.N,mean:mean.N,median:median.N,pass_rate:pass_rate.N,students:student_count.N}"
```

Expect `COMPLETED`, clean 891, a Parquet file, and mean 62.43 / pass rate 78.34 / 60 students.

**2. The same file under a new ID is skipped:**

```bash
aws s3 cp data/student_results.csv s3://$BUCKET/uploads/demo-002/student_results.csv
sleep 15
meta demo-002
aws s3 ls s3://$PROCESSED/processed/demo-002/
```

Expect `DUPLICATE` with `dup_of` = demo-001, and no Parquet file for demo-002.

**3. A broken file is quarantined:**

```bash
printf 'student_id,score\nS00001,50\n' > /tmp/broken.csv
aws s3 cp /tmp/broken.csv s3://$BUCKET/uploads/demo-003/broken.csv
sleep 15
meta demo-003
aws s3 ls s3://$QUARANTINE/quarantine/demo-003/
```

Expect `FAILED`, an error naming the missing columns, and the file in the quarantine bucket.

**4. A poison message ends up in the DLQ** (about 25 to 40 minutes: each retry waits for
the 12-minute visibility timeout):

```bash
aws sqs send-message --queue-url $QUEUE --message-body 'not json' --region ap-south-1
```

Later, check the DLQ and purge it afterwards:

```bash
aws sqs get-queue-attributes --queue-url $DLQ --region ap-south-1 \
  --attribute-names ApproximateNumberOfMessages --query Attributes
aws sqs purge-queue --queue-url $DLQ --region ap-south-1
```

Lambda logs: `sam logs -n ProcessFunction --stack-name gradeflow --region ap-south-1 --tail`

### Clean up

The buckets must be empty before CloudFormation can delete them:

```bash
aws s3 rm s3://$BUCKET --recursive
aws s3 rm s3://$PROCESSED --recursive
aws s3 rm s3://$QUARANTINE --recursive
sam delete --stack-name gradeflow --region ap-south-1
```

### Cost

At portfolio-demo volume this stays within the AWS Free Tier:

- Lambda runs on arm64 with 512 MB (pandas needs more memory).
- DynamoDB is on-demand, so it costs nothing when idle.
- Raw and quarantined files expire after 30 days.
- SQS adds a small request volume, well inside its free tier of 1 million requests a month.
- Logs are kept for 14 days.

### Limits

Lambda reads the whole file into memory, builds the Parquet file in memory too, and has a
15-minute ceiling (this function uses 512 MB and a 120 s timeout). The target is files up to about 50 MB. Much larger files would need chunked
processing, AWS Glue or Fargate, which are out of scope for this project.

## Roadmap

- [x] Phase 0: Project setup
- [x] Phase 1: MVP pipeline (`v0.1.0`)
- [x] Phase 2: Cleaning & statistics (`v0.2.0`)
- [x] Phase 3: Reliability: SQS, DLQ, idempotency, quarantine (`v0.3.0`)
- [ ] Phase 4: Orchestration with Step Functions (`v0.4.0`)
- [ ] Phase 5: Anomaly detection, rule-based + Isolation Forest (`v0.5.0`)
- [ ] Phase 6: Analytics API (`v0.6.0`)
- [ ] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)
