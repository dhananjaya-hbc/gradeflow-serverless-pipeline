"""How items are addressed in the PipelineTable (single-table design).

Its own module so the read-only API can use the key layout without importing the writing
code, which pulls in pandas through the domain objects.
"""

DATASETS_GSI_PK = "DATASETS"  # all datasets share this GSI1 partition -> list by upload time


def dataset_pk(dataset_id: str) -> str:
    return f"DATASET#{dataset_id}"


def hash_pk(sha256: str) -> str:
    return f"HASH#{sha256}"
