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

## What works today (v0.1.0)

```
Upload ─▶ S3 raw bucket ─(ObjectCreated, prefix uploads/)─▶ Validate Lambda ─▶ DynamoDB META item
```

Upload a CSV or JSON file to `uploads/<dataset_id>/<filename>`. The Validate Lambda:

1. **File-level checks**: readable UTF-8, `.csv` or `.json`, at least one row, all required
   columns present. Any failure → status `FAILED` with an `error_message`.
2. **Row-level checks** (see the schema below). Bad rows are counted, and the first 20 are
   listed in `row_errors`.
3. **Decision**: status `COMPLETED` if at least `MinValidRatio` (default 80%) of the rows are
   valid, otherwise `FAILED`.
4. Writes one item to DynamoDB: `PK=DATASET#<dataset_id>`, `SK=META`.

| Column | Rule |
|---|---|
| `student_id` | required, `S` + 5 digits (e.g. `S00123`) |
| `student_name`, `class`, `subject` | required, non-empty |
| `term` | one of `T1`, `T2`, `T3` |
| `year` | integer, 2000 to the upload year |
| `score` | number, 0 to 100 |

Example META item for the default sample file (904 rows, 10 deliberately broken):

```json
{
  "PK": "DATASET#demo-001", "SK": "META",
  "status": "COMPLETED", "filename": "student_results.csv",
  "total_rows": 904, "valid_rows": 894, "invalid_rows": 10,
  "row_errors": ["row 46: student_id 'S123' must be 'S' + 5 digits", "..."],
  "uploaded_at": "2026-09-28T18:56:38.685000+00:00",
  "GSI1PK": "DATASETS", "GSI1SK": "2026-09-28T18:56:38.685000+00:00"
}
```

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

Optional: `sam deploy --parameter-overrides MinValidRatio=0.9` changes the valid-row threshold.

### Try it

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name gradeflow --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='RawBucketName'].OutputValue" --output text)
TABLE=$(aws cloudformation describe-stacks --stack-name gradeflow --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='PipelineTableName'].OutputValue" --output text)

python scripts/generate_sample_data.py
aws s3 cp data/student_results.csv s3://$BUCKET/uploads/demo-001/student_results.csv

aws dynamodb get-item --table-name $TABLE --region ap-south-1 \
  --key '{"PK":{"S":"DATASET#demo-001"},"SK":{"S":"META"}}'

sam logs -n ValidateFunction --stack-name gradeflow --region ap-south-1 --tail   # Lambda logs
```

### Clean up

The bucket must be empty before CloudFormation can delete it:

```bash
aws s3 rm s3://$BUCKET --recursive
sam delete --stack-name gradeflow --region ap-south-1
```

### Cost

At portfolio-demo volume this stays within the AWS Free Tier:

- Lambda runs on arm64 with 256 MB.
- DynamoDB is on-demand, so it costs nothing when idle.
- Raw files expire after 30 days.
- Logs are kept for 14 days.

### Limits

Lambda reads the whole file into memory and has a 15-minute ceiling (this function uses a
60 s timeout). The target is files up to about 50 MB. Much larger files would need chunked
processing, AWS Glue or Fargate, which are out of scope for this project.

## Roadmap

- [x] Phase 0: Project setup
- [x] Phase 1: MVP pipeline (`v0.1.0`)
- [ ] Phase 2: Cleaning & statistics (`v0.2.0`)
- [ ] Phase 3: Reliability: SQS, DLQ, idempotency, quarantine (`v0.3.0`)
- [ ] Phase 4: Orchestration with Step Functions (`v0.4.0`)
- [ ] Phase 5: Anomaly detection, rule-based + Isolation Forest (`v0.5.0`)
- [ ] Phase 6: Analytics API (`v0.6.0`)
- [ ] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)
