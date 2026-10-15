"""Integration tests (moto) for the Analytics API's adapters: the reader and the upload signer."""

import base64
import json
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
import requests
from tests.integration.conftest import REGION, TABLE
from tests.integration.test_adapters import make_anomaly, make_meta, make_stats

from gradeflow.application.queries import BadRequest
from gradeflow.application.read_ports import Page, StatsRecords
from gradeflow.domain.anomalies import AnomalyMethod
from gradeflow.domain.dataset import DatasetStatus
from gradeflow.infrastructure.dynamodb_reader import DynamoDbDatasetReader
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_upload_signer import S3UploadSigner

MAX_BYTES = 50 * 1024 * 1024


@pytest.fixture
def repository(table) -> DynamoDbDatasetRepository:
    return DynamoDbDatasetRepository(TABLE)


@pytest.fixture
def reader(table) -> DynamoDbDatasetReader:
    return DynamoDbDatasetReader(TABLE)


def at(day: int) -> str:
    return f"2026-10-{day:02d}T10:00:00+00:00"


def cursor_of(key: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


# --- listing datasets ----------------------------------------------------------------


def test_datasets_are_listed_newest_first_without_table_plumbing(repository, reader) -> None:
    repository.save_meta(make_meta("old", uploaded_at=at(1)))
    repository.save_meta(make_meta("new", uploaded_at=at(14)))

    page = reader.list_datasets(limit=10, cursor=None)

    assert [item["dataset_id"] for item in page.items] == ["new", "old"]
    assert page.next_cursor is None
    assert not {"PK", "SK", "GSI1PK", "GSI1SK"} & set(page.items[0])
    assert page.items[0]["total_rows"] == 10 and isinstance(page.items[0]["total_rows"], int)
    assert page.items[0]["status"] == "COMPLETED"


def test_hash_claims_never_appear_in_the_listing(repository, reader) -> None:
    repository.save_meta(make_meta("ds-1"))
    repository.claim_file_hash("a" * 64, "ds-1")

    assert [i["dataset_id"] for i in reader.list_datasets(10, None).items] == ["ds-1"]


def test_an_empty_table_lists_nothing(reader) -> None:
    assert reader.list_datasets(10, None) == Page([], None)


def test_listing_pages_through_all_datasets(repository, reader) -> None:
    for day in range(1, 6):
        repository.save_meta(make_meta(f"d{day}", uploaded_at=at(day)))

    first = reader.list_datasets(2, None)
    second = reader.list_datasets(2, first.next_cursor)
    third = reader.list_datasets(2, second.next_cursor)

    assert [i["dataset_id"] for i in first.items] == ["d5", "d4"]
    assert [i["dataset_id"] for i in second.items] == ["d3", "d2"]
    assert [i["dataset_id"] for i in third.items] == ["d1"]
    assert (first.next_cursor is None, third.next_cursor) == (False, None)


def test_a_full_last_page_does_not_offer_an_empty_next_page(repository, reader) -> None:
    """Four datasets and a page size of two: the second page is the last, and says so."""
    for day in range(1, 5):
        repository.save_meta(make_meta(f"d{day}", uploaded_at=at(day)))

    second = reader.list_datasets(2, reader.list_datasets(2, None).next_cursor)

    assert [i["dataset_id"] for i in second.items] == ["d2", "d1"]
    assert second.next_cursor is None


def test_numbers_come_back_as_plain_int_and_float(repository, reader) -> None:
    repository.save_stats("ds-1", make_stats())
    records = reader.get_stats("ds-1")
    assert records.overall["mean"] == 58.25 and isinstance(records.overall["mean"], float)
    assert records.overall["count"] == 8 and isinstance(records.overall["count"], int)
    assert records.overall["class_averages"] == {"10-A": 60, "10-B": 56.5}  # whole numbers are int


# --- forged cursors ------------------------------------------------------------------

BAD_CURSORS = [
    "not base64 at all!!",
    base64.urlsafe_b64encode(b"[1, 2]").decode(),  # valid JSON, not an object
    base64.urlsafe_b64encode(b"{not json").decode(),
    cursor_of({"PK": "DATASET#x", "SK": "META"}),  # keys missing
    cursor_of({"PK": "DATASET#x", "SK": "META", "GSI1PK": "SOMETHING", "GSI1SK": "t"}),
    cursor_of({"PK": "HASH#abc", "SK": "META", "GSI1PK": "DATASETS", "GSI1SK": "t"}),
    cursor_of({"PK": "DATASET#x", "SK": "STATS#OVERALL", "GSI1PK": "DATASETS", "GSI1SK": "t"}),
]


@pytest.mark.parametrize("cursor", BAD_CURSORS)
def test_a_forged_listing_cursor_is_a_400(reader, cursor: str) -> None:
    with pytest.raises(BadRequest) as error:
        reader.list_datasets(10, cursor)
    assert (error.value.status, error.value.code) == (400, "invalid_cursor")


@pytest.mark.parametrize(
    "cursor",
    [
        "not base64 at all!!",
        cursor_of({"SK": "META"}),  # not an anomaly position
        cursor_of({"SK": "STATS#OVERALL"}),
        cursor_of({"PK": "DATASET#other", "SK": "ANOMALY#00001"}),  # may not name a partition
    ],
)
def test_a_forged_anomaly_cursor_is_a_400(reader, cursor: str) -> None:
    with pytest.raises(BadRequest) as error:
        reader.list_anomalies("ds-1", 10, cursor, None)
    assert error.value.code == "invalid_cursor"


# --- one dataset ---------------------------------------------------------------------


def test_get_meta_returns_the_record_or_none(repository, reader) -> None:
    repository.save_meta(make_meta("ds-1", status=DatasetStatus.FAILED, error_message="bad file"))

    meta = reader.get_meta("ds-1")

    assert meta["status"] == "FAILED" and meta["error_message"] == "bad file"
    assert meta["row_errors"] == ["row 10: score '150' must be a number 0-100"]
    assert not {"PK", "SK", "GSI1PK", "GSI1SK"} & set(meta)
    assert reader.get_meta("nope") is None


def test_stats_are_split_into_overall_and_groups(repository, reader) -> None:
    repository.save_meta(make_meta("ds-1"))
    repository.save_stats("ds-1", make_stats())
    repository.save_anomalies("ds-1", [make_anomaly(1)])

    records = reader.get_stats("ds-1")

    assert isinstance(records, StatsRecords)
    assert records.overall["student_count"] == 4 and records.overall["pass_rate"] == 62.5
    assert records.overall["top_students"][0]["student_id"] == "S00001"
    assert [(g["subject"], g["term"]) for g in records.groups] == [
        ("Maths", "T1"),
        ("Social Studies", "T2"),
    ]
    assert records.groups[0]["mean"] == 57.5
    assert "SK" not in records.overall and "PK" not in records.groups[0]


def test_a_dataset_without_stats_has_empty_records(repository, reader) -> None:
    repository.save_meta(make_meta("ds-1", status=DatasetStatus.PROCESSING))
    assert reader.get_stats("ds-1") == StatsRecords(None, [])


# --- anomalies -----------------------------------------------------------------------


def save_twelve(repository, dataset_id: str = "ds-1") -> None:
    """Twelve findings in rank order: odd ranks are term jumps, even ranks the forest."""
    repository.save_anomalies(
        dataset_id,
        [
            make_anomaly(
                n,
                method=AnomalyMethod.TERM_JUMP if n % 2 else AnomalyMethod.ISOLATION_FOREST,
                severity=50.5 - n,
            )
            for n in range(1, 13)
        ],
    )


def ranks(page: Page) -> list[int]:
    return [item["rank"] for item in page.items]


def test_anomalies_come_back_in_rank_order(repository, reader) -> None:
    save_twelve(repository)

    page = reader.list_anomalies("ds-1", 50, None, None)

    assert ranks(page) == list(range(1, 13))  # rank 10 after 9, not after 1
    assert page.next_cursor is None
    assert page.items[0]["method"] == "term_jump"
    assert page.items[0]["severity"] == 49.5 and isinstance(page.items[0]["severity"], float)
    assert not {"PK", "SK"} & set(page.items[0])


def test_anomalies_page_without_a_filter(repository, reader) -> None:
    save_twelve(repository)

    first = reader.list_anomalies("ds-1", 5, None, None)
    second = reader.list_anomalies("ds-1", 5, first.next_cursor, None)
    third = reader.list_anomalies("ds-1", 5, second.next_cursor, None)

    assert (ranks(first), ranks(second), ranks(third)) == (
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12],
    )
    assert third.next_cursor is None


def test_a_full_last_anomaly_page_does_not_offer_an_empty_next_page(repository, reader) -> None:
    save_twelve(repository)
    second = reader.list_anomalies(
        "ds-1", 6, reader.list_anomalies("ds-1", 6, None, None).next_cursor, None
    )
    assert ranks(second) == [7, 8, 9, 10, 11, 12] and second.next_cursor is None


def test_a_method_filter_returns_only_that_method_in_full_pages(repository, reader) -> None:
    """DynamoDB filters after reading, so a naive limit would give short pages."""
    save_twelve(repository)

    first = reader.list_anomalies("ds-1", 4, None, "term_jump")
    second = reader.list_anomalies("ds-1", 4, first.next_cursor, "term_jump")

    assert ranks(first) == [1, 3, 5, 7]  # four matches, although they span eight records
    assert ranks(second) == [9, 11]
    assert second.next_cursor is None
    assert {item["method"] for item in first.items + second.items} == {"term_jump"}


def test_a_filter_with_no_matches_is_an_empty_page(repository, reader) -> None:
    save_twelve(repository)
    assert reader.list_anomalies("ds-1", 5, None, "zscore") == Page([], None)


def test_anomalies_of_an_unknown_dataset_are_an_empty_page(reader) -> None:
    assert reader.list_anomalies("nope", 5, None, None) == Page([], None)


def test_datasets_do_not_see_each_others_anomalies_even_with_a_borrowed_cursor(
    repository, reader
) -> None:
    save_twelve(repository, "ds-1")
    repository.save_anomalies(
        "ds-2", [make_anomaly(n, student_id=f"T{n:04d}") for n in range(1, 4)]
    )

    cursor_from_ds1 = reader.list_anomalies("ds-1", 2, None, None).next_cursor
    page = reader.list_anomalies("ds-2", 5, cursor_from_ds1, None)

    assert [item["student_id"] for item in page.items] == ["T0003"]  # ds-2's own, after rank 2


# --- the reader is read-only (the API Lambda's IAM policy relies on it) ----------------


class SpyResource:
    """Records which DynamoDB table operations are used."""

    def __init__(self, resource: Any, used: set[str]) -> None:
        self._resource, self._used = resource, used

    def Table(self, name: str) -> Any:  # noqa: N802 - boto3's own method name
        table, used = self._resource.Table(name), self._used

        class Recorder:
            def __getattr__(self, attribute: str) -> Any:
                if not attribute.startswith("_"):
                    used.add(attribute)
                return getattr(table, attribute)

        return Recorder()


def test_the_reader_only_ever_calls_query_and_get_item(table, repository) -> None:
    used: set[str] = set()
    spy = DynamoDbDatasetReader(TABLE, resource=SpyResource(boto3.resource("dynamodb"), used))
    repository.save_meta(make_meta("ds-1"))
    repository.save_stats("ds-1", make_stats())
    save_twelve(repository)

    spy.list_datasets(5, None)
    spy.get_meta("ds-1")
    spy.get_stats("ds-1")
    spy.list_anomalies("ds-1", 5, None, "term_jump")
    spy.list_anomalies("ds-1", 5, None, None)

    assert used == {"query", "get_item"}


# --- the upload signer ---------------------------------------------------------------

BUCKET = "gradeflow-raw-123456789012-ap-south-1"
KEY = "uploads/ds-20261015-1a2b3c4d/results.csv"


def policy_of(form) -> dict[str, Any]:
    return json.loads(base64.b64decode(form.fields["policy"]))


@pytest.fixture
def raw_bucket():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    return s3


def test_the_form_points_at_the_regional_endpoint(raw_bucket) -> None:
    """The global endpoint redirects for buckets outside us-east-1, which breaks a browser POST."""
    form = S3UploadSigner(BUCKET).create_upload(KEY, MAX_BYTES, 900)
    assert form.url == f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/"


def test_the_form_allows_exactly_one_key_and_a_size_range(raw_bucket) -> None:
    form = S3UploadSigner(BUCKET).create_upload(KEY, MAX_BYTES, 900)

    conditions = policy_of(form)["conditions"]
    assert ["content-length-range", 1, MAX_BYTES] in conditions  # not empty, not over 50 MB
    assert {"bucket": BUCKET} in conditions
    assert {"key": KEY} in conditions  # exactly this object, nothing else
    assert form.fields["key"] == KEY
    assert form.fields["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    assert form.fields["x-amz-signature"]


def test_the_form_expires_when_asked(raw_bucket) -> None:
    form = S3UploadSigner(BUCKET).create_upload(KEY, MAX_BYTES, 900)

    expires = datetime.strptime(policy_of(form)["expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )
    seconds_left = (expires - datetime.now(UTC)).total_seconds()
    assert 880 <= seconds_left <= 900


def test_each_key_gets_its_own_signature(raw_bucket) -> None:
    signer = S3UploadSigner(BUCKET)
    first = signer.create_upload(KEY, MAX_BYTES, 900)
    other = signer.create_upload("uploads/ds-other/results.csv", MAX_BYTES, 900)
    assert first.fields["x-amz-signature"] != other.fields["x-amz-signature"]
    assert first.fields["policy"] != other.fields["policy"]


def test_the_signed_form_can_actually_upload_a_file(raw_bucket) -> None:
    """Post the form the way a browser would (the file as the last field) and read it back."""
    form = S3UploadSigner(BUCKET).create_upload(KEY, MAX_BYTES, 900)

    response = requests.post(
        form.url,
        data=form.fields,
        files={"file": ("results.csv", b"student_id,score\n")},
        timeout=10,
    )

    assert response.status_code in (200, 201, 204)
    assert raw_bucket.get_object(Bucket=BUCKET, Key=KEY)["Body"].read() == b"student_id,score\n"
