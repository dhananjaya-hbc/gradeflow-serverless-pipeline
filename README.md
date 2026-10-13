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

## What works today (v0.2.0)

```
Upload ─▶ S3 raw ─(ObjectCreated, prefix uploads/)─▶ Process Lambda ─┬─▶ S3 processed (Parquet)
                                                  (+ pandas layer)   └─▶ DynamoDB (META + STATS#)
```

Upload a CSV or JSON file to `uploads/<dataset_id>/<filename>`. The Process Lambda:

1. **Validates.** File-level problems (unreadable UTF-8, wrong extension, no rows, missing
   columns) → status `FAILED` with an `error_message`. Row-level problems are counted, and the
   first 20 are listed in `row_errors`. If fewer than `MinValidRatio` (default 80%) of rows are
   valid, the dataset is `FAILED`.
2. **Cleans** the valid rows with pandas: trims text, normalises subjects (`maths` → `Maths`,
   `ict` → `ICT`), casts `year`/`score` to numbers, drops exact duplicates (after normalising).
3. **Writes Parquet** to `processed/<dataset_id>/data.parquet` in the processed bucket.
4. **Computes statistics** and saves them to DynamoDB.
5. Saves the final `META` item. Status flow: `PROCESSING` → `COMPLETED` (or `FAILED`).
   `COMPLETED` is written last, so a completed dataset always has its Parquet file and stats.

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
| `DATASET#<id>` | `META` | status, filename, row counts (`total`/`valid`/`invalid`/`clean`), `duplicates_removed`, `row_errors`, `processed_key`, `error_message` |
| `DATASET#<id>` | `STATS#OVERALL` | count, mean, median, std, min, max, pass rate, `student_count`, top 5 / bottom 5 students, class averages |
| `DATASET#<id>` | `STATS#SUBJECT#<subject>#<term>` | the same metrics for one subject and term |

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

```bash
export AWS_PAGER=""
out() { aws cloudformation describe-stacks --stack-name gradeflow --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET=$(out RawBucketName); PROCESSED=$(out ProcessedBucketName); TABLE=$(out PipelineTableName)

python scripts/generate_sample_data.py
aws s3 cp data/student_results.csv s3://$BUCKET/uploads/demo-001/student_results.csv
sleep 20   # the first run is a cold start that has to load pandas
```

```bash
# META: expect COMPLETED, clean 891, dups 3
aws dynamodb get-item --table-name $TABLE --region ap-south-1 \
  --key '{"PK":{"S":"DATASET#demo-001"},"SK":{"S":"META"}}' \
  --query "Item.{status:status.S,total:total_rows.N,valid:valid_rows.N,clean:clean_rows.N,dups:duplicates_removed.N,key:processed_key.S}"

# Parquet file
aws s3 ls s3://$PROCESSED/processed/demo-001/

# Overall stats: expect mean 62.43, pass_rate 78.34, students 60
aws dynamodb get-item --table-name $TABLE --region ap-south-1 \
  --key '{"PK":{"S":"DATASET#demo-001"},"SK":{"S":"STATS#OVERALL"}}' \
  --query "Item.{count:count.N,mean:mean.N,median:median.N,pass_rate:pass_rate.N,students:student_count.N}"

# Lambda logs
sam logs -n ProcessFunction --stack-name gradeflow --region ap-south-1 --tail
```

Reading the Parquet back in Python: `pd.read_parquet("s3://<processed bucket>/processed/demo-001/data.parquet")`
(needs `s3fs`), or download it with `aws s3 cp` and open it locally.

### Clean up

Both buckets must be empty before CloudFormation can delete them:

```bash
aws s3 rm s3://$BUCKET --recursive
aws s3 rm s3://$PROCESSED --recursive
sam delete --stack-name gradeflow --region ap-south-1
```

### Cost

At portfolio-demo volume this stays within the AWS Free Tier:

- Lambda runs on arm64 with 512 MB (pandas needs more memory).
- DynamoDB is on-demand, so it costs nothing when idle.
- Raw files expire after 30 days.
- Logs are kept for 14 days.

### Limits

Lambda reads the whole file into memory, builds the Parquet file in memory too, and has a
15-minute ceiling (this function uses 512 MB and a 120 s timeout). The target is files up to about 50 MB. Much larger files would need chunked
processing, AWS Glue or Fargate, which are out of scope for this project.

## Roadmap

- [x] Phase 0: Project setup
- [x] Phase 1: MVP pipeline (`v0.1.0`)
- [x] Phase 2: Cleaning & statistics (`v0.2.0`)
- [ ] Phase 3: Reliability: SQS, DLQ, idempotency, quarantine (`v0.3.0`)
- [ ] Phase 4: Orchestration with Step Functions (`v0.4.0`)
- [ ] Phase 5: Anomaly detection, rule-based + Isolation Forest (`v0.5.0`)
- [ ] Phase 6: Analytics API (`v0.6.0`)
- [ ] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)
