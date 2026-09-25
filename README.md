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

Requirements: Python 3.12, git. (For deploying later: AWS CLI, SAM CLI, Docker. See
[docs/aws-setup.md](docs/aws-setup.md).)

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
ruff check .          # lint
ruff format --check . # formatting
pytest                # tests
```

## Roadmap

- [x] Phase 0: Project setup
- [ ] Phase 1: MVP pipeline (`v0.1.0`)
- [ ] Phase 2: Cleaning & statistics (`v0.2.0`)
- [ ] Phase 3: Reliability: SQS, DLQ, idempotency, quarantine (`v0.3.0`)
- [ ] Phase 4: Orchestration with Step Functions (`v0.4.0`)
- [ ] Phase 5: Anomaly detection, rule-based + Isolation Forest (`v0.5.0`)
- [ ] Phase 6: Analytics API (`v0.6.0`)
- [ ] Phase 7: Dashboard (`v0.7.0`)
- [ ] Phase 8: Observability & security (`v0.8.0`)
- [ ] Phase 9: CI/CD & release (`v1.0.0`)
