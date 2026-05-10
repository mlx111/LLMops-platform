from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.dataset import Dataset, EvalCase
from app.schemas.dataset import (
    DatasetCreate, DatasetOut, DatasetListOut, DatasetUpdate,
    EvalCaseCreate, EvalCaseListOut, EvalCaseOut, EvalCaseUpdate,
    ImportCasesRequest,
)

router = APIRouter(prefix="/api", tags=["datasets"])


# ---------- Datasets ----------
@router.get("/datasets", response_model=DatasetListOut)
def list_datasets(
    case_type: str | None = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(Dataset)
    if case_type:
        q = q.filter(Dataset.case_type == case_type)
    total = q.count()
    items = q.order_by(Dataset.created_at.desc()).offset(skip).limit(limit).all()
    for d in items:
        d.case_count = db.query(func.count(EvalCase.id)).filter(EvalCase.dataset_id == d.id).scalar()
    return DatasetListOut(items=items, total=total)


@router.post("/datasets", response_model=DatasetOut)
def create_dataset(body: DatasetCreate, db: Session = Depends(get_db)):
    ds = Dataset(**body.model_dump())
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    ds.case_count = db.query(func.count(EvalCase.id)).filter(EvalCase.dataset_id == ds.id).scalar()
    return ds


@router.put("/datasets/{dataset_id}", response_model=DatasetOut)
def update_dataset(dataset_id: int, body: DatasetUpdate, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ds, k, v)
    db.commit()
    db.refresh(ds)
    return ds


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    db.delete(ds)
    db.commit()
    return {"ok": True}


# ---------- Cases ----------
@router.get("/datasets/{dataset_id}/cases", response_model=EvalCaseListOut)
def list_cases(
    dataset_id: int,
    case_type: str | None = Query(None),
    difficulty: str | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(EvalCase).filter(EvalCase.dataset_id == dataset_id)
    if case_type:
        q = q.filter(EvalCase.case_type == case_type)
    if difficulty:
        q = q.filter(EvalCase.difficulty == difficulty)
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return EvalCaseListOut(items=items, total=total)


@router.post("/datasets/{dataset_id}/cases", response_model=EvalCaseOut)
def create_case(dataset_id: int, body: EvalCaseCreate, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    case = EvalCase(dataset_id=dataset_id, **body.model_dump(exclude={"extra_metadata"}))
    if body.extra_metadata:
        case.extra_metadata = body.extra_metadata
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.put("/cases/{case_id}", response_model=EvalCaseOut)
def update_case(case_id: int, body: EvalCaseUpdate, db: Session = Depends(get_db)):
    case = db.get(EvalCase, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    updates = body.model_dump(exclude_unset=True)
    if "extra_metadata" in updates:
        case.extra_metadata = updates.pop("extra_metadata")
    for k, v in updates.items():
        setattr(case, k, v)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}")
def delete_case(case_id: int, db: Session = Depends(get_db)):
    case = db.get(EvalCase, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    db.delete(case)
    db.commit()
    return {"ok": True}


# ---------- Import ----------
@router.post("/datasets/{dataset_id}/import", response_model=EvalCaseListOut)
def import_cases(dataset_id: int, body: ImportCasesRequest, db: Session = Depends(get_db)):
    ds = db.get(Dataset, dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    cases = []
    for c in body.cases:
        case = EvalCase(dataset_id=dataset_id, **c.model_dump(exclude={"extra_metadata"}))
        if c.extra_metadata:
            case.extra_metadata = c.extra_metadata
        db.add(case)
        cases.append(case)
    db.commit()
    return EvalCaseListOut(items=cases, total=len(cases))
