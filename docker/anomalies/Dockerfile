# Container-image Lambda for the Anomalies step (scikit-learn is too big for a zip Lambda).
# Build context is the repository root:
#   docker build --platform linux/arm64 -f docker/anomalies/Dockerfile -t gradeflow-anomalies .
FROM public.ecr.aws/lambda/python:3.12

# Dependencies first, so Docker reuses this slow layer when only our code changes.
COPY docker/anomalies/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Only the application code: no tests, data or dev tooling.
COPY src/gradeflow ${LAMBDA_TASK_ROOT}/gradeflow

CMD ["gradeflow.entrypoints.anomalies_handler.handler"]
