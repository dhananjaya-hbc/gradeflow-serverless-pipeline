"""Generate SYNTHETIC student results with deliberate data-quality problems.

All names and IDs are fake. The output is used to exercise validation, cleaning,
statistics and anomaly detection.

Usage:
    python scripts/generate_sample_data.py
    python scripts/generate_sample_data.py --students 100 --format json --seed 7
"""

import argparse
import csv
import json
import random
from pathlib import Path

COLUMNS = ["student_id", "student_name", "class", "subject", "term", "year", "score"]
SUBJECTS = ["Maths", "Science", "English", "History", "ICT"]
TERMS = ["T1", "T2", "T3"]
CLASSES = ["10-A", "10-B"]
FIRST_NAMES = [
    "Amal", "Nimal", "Kasun", "Sachini", "Dilini", "Tharindu", "Ishara", "Ruwan",
    "Chamari", "Nadeesha", "Pasindu", "Hiruni", "Janith", "Oshadi", "Supun", "Malsha",
]  # fmt: skip
LAST_NAMES = [
    "Perera", "Silva", "Fernando", "Jayasinghe", "Bandara", "Wickramasinghe",
    "Gunawardena", "Rathnayake", "Herath", "Dissanayake",
]  # fmt: skip

Row = dict[str, object]


def make_students(count: int, rng: random.Random) -> list[dict[str, object]]:
    """Create fake students, each with a base ability used to generate scores."""
    return [
        {
            "student_id": f"S{i:05d}",
            "student_name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            "class": CLASSES[i % len(CLASSES)],
            "ability": rng.gauss(65, 12),
        }
        for i in range(1, count + 1)
    ]


def make_clean_rows(students: list[dict[str, object]], year: int, rng: random.Random) -> list[Row]:
    """Create one realistic score per student x subject x term."""
    subject_offset = {subject: rng.uniform(-8, 8) for subject in SUBJECTS}
    rows: list[Row] = []
    for student in students:
        for subject in SUBJECTS:
            for term in TERMS:
                score = student["ability"] + subject_offset[subject] + rng.gauss(0, 6)
                rows.append(
                    {
                        "student_id": student["student_id"],
                        "student_name": student["student_name"],
                        "class": student["class"],
                        "subject": subject,
                        "term": term,
                        "year": year,
                        "score": round(min(max(score, 0), 100), 1),
                    }
                )
    return rows


def _pick(rows: list[Row], used: set[int], count: int, rng: random.Random) -> list[int]:
    """Pick row indexes not already modified, so each problem is counted once."""
    choices = rng.sample([i for i in range(len(rows)) if i not in used], count)
    used.update(choices)
    return choices


def inject_term_jumps(rows: list[Row], used: set[int], count: int, rng: random.Random) -> list[str]:
    """Anomaly: score jumps by more than 30 points from T1 to T2 for one student/subject."""
    t1_indexes = [i for i, r in enumerate(rows) if r["term"] == "T1" and i not in used]
    notes = []
    for i in rng.sample(t1_indexes, count):
        t1 = rows[i]
        t2_index = next(
            j
            for j, r in enumerate(rows)
            if r["student_id"] == t1["student_id"]
            and r["subject"] == t1["subject"]
            and r["term"] == "T2"
        )
        low = round(rng.uniform(30, 45), 1)
        high = round(min(low + rng.uniform(35, 50), 100), 1)
        t1["score"], rows[t2_index]["score"] = low, high
        used.update({i, t2_index})
        notes.append(f"{t1['student_id']} {t1['subject']}: T1={low} -> T2={high}")
    return notes


def inject_problems(rows: list[Row], rng: random.Random) -> tuple[list[Row], dict[str, list[str]]]:
    """Apply deliberate data-quality problems and return the rows plus a report."""
    used: set[int] = set()
    report: dict[str, list[str]] = {"term_jump_anomalies": inject_term_jumps(rows, used, 3, rng)}

    report["out_of_range_scores"] = []
    for i, bad in zip(_pick(rows, used, 4, rng), [105, 120.5, 150, -5], strict=True):
        rows[i]["score"] = bad
        report["out_of_range_scores"].append(f"{rows[i]['student_id']} score={bad}")

    report["missing_values"] = []
    for i, field in zip(
        _pick(rows, used, 4, rng), ["score", "student_name", "subject", "term"], strict=True
    ):
        rows[i][field] = None
        report["missing_values"].append(f"{rows[i]['student_id']} missing {field}")

    report["bad_student_ids"] = []
    for i, bad in zip(_pick(rows, used, 2, rng), ["S123", "X00007"], strict=True):
        report["bad_student_ids"].append(f"{rows[i]['student_id']} -> {bad}")
        rows[i]["student_id"] = bad

    casing_indexes = _pick(rows, used, len(rows) // 10, rng)
    for i in casing_indexes:
        variant = rng.choice([str.lower, str.upper, lambda s: f"  {s.lower()} "])
        rows[i]["subject"] = variant(rows[i]["subject"])
    report["inconsistent_subject_casing"] = [f"{len(casing_indexes)} rows"]

    # Same student ID, different name (e.g. data-entry error or ID collision).
    original = rows[_pick(rows, used, 1, rng)[0]]
    clash = {**original, "student_name": "Fake Duplicate", "subject": "Maths", "term": "T3"}
    rows.append(clash)
    report["duplicate_id_different_name"] = [f"{clash['student_id']} as 'Fake Duplicate'"]

    duplicates = [dict(rows[i]) for i in _pick(rows, used, 3, rng)]
    rows.extend(duplicates)
    report["exact_duplicates"] = [
        f"{d['student_id']} {d['subject']} {d['term']}" for d in duplicates
    ]

    rng.shuffle(rows)
    return rows, report


def write_rows(rows: list[Row], path: Path, fmt: str) -> None:
    """Write rows as CSV (missing values = empty cell) or a JSON array (missing = null)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(rows, indent=2))
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--students", type=int, default=60, help="number of students")
    parser.add_argument("--year", type=int, default=2025, help="academic year of the results")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same file)")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--out", type=Path, help="default: data/student_results.<format>")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    out = args.out or Path("data") / f"student_results.{args.format}"

    rows = make_clean_rows(make_students(args.students, rng), args.year, rng)
    rows, report = inject_problems(rows, rng)
    write_rows(rows, out, args.format)

    print(f"Wrote {len(rows)} rows to {out}")
    print("Injected problems:")
    for problem, details in report.items():
        print(f"  {problem} ({len(details)}):")
        for detail in details:
            print(f"    - {detail}")


if __name__ == "__main__":
    main()
