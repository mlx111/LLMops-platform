"""Seed demo data into the database from JSON files."""

import json
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.models.dataset import Dataset, EvalCase

ROOT = Path(__file__).resolve().parent.parent.parent  # backend/../.. = project root
DEMO_DIR = ROOT / "demo_data"

FILES = [
    ("QA Cases", "qa", "qa_cases.json"),
    ("RAG Cases", "rag", "rag_cases.json"),
    ("Tool Calling Cases", "tool_calling", "tool_calling_cases.json"),
    ("Multi-turn Cases", "multi_turn", "multi_turn_cases.json"),
]


def seed(force: bool = False) -> dict[str, int]:
    """Seed demo data. Returns dict of dataset_name -> case_count.

    Skips datasets that already exist unless *force* is True.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        counts: dict[str, int] = {}
        for ds_name, case_type, filename in FILES:
            existing = db.query(Dataset).filter(Dataset.name == ds_name).first()
            if existing and not force:
                counts[ds_name] = (
                    db.query(EvalCase.id)
                    .filter(EvalCase.dataset_id == existing.id)
                    .count()
                )
                continue
            if existing and force:
                db.query(EvalCase).filter(EvalCase.dataset_id == existing.id).delete()
                dataset = existing
            else:
                dataset = Dataset(name=ds_name, description=f"Demo {case_type} cases", case_type=case_type)
                db.add(dataset)
                db.flush()

            file_path = DEMO_DIR / filename
            if not file_path.exists():
                continue
            with open(file_path, encoding="utf-8") as f:
                cases_data = json.load(f)

            for c in cases_data:
                c.pop("case_type", None)
                extra = c.pop("extra_metadata", None)
                case = EvalCase(dataset_id=dataset.id, case_type=case_type, **c)
                if extra:
                    case.extra_metadata = extra
                db.add(case)

            db.commit()
            counts[ds_name] = len(cases_data)
        return counts
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    counts = seed(force=force)
    print("Demo data seeded:")
    for name, count in counts.items():
        print(f"  {name}: {count} cases")
    print(f"Total: {sum(counts.values())} cases across {len(counts)} datasets")
