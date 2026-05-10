from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.run import EvalRun
from app.models.version import Version
from app.schemas.version import VersionCreate, VersionListOut, VersionOut, VersionUpdate

router = APIRouter(prefix="/api", tags=["versions"])


@router.get("/versions", response_model=VersionListOut)
def list_versions(
    version_type: str | None = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(Version)
    if version_type:
        q = q.filter(Version.version_type == version_type)
    total = q.count()
    items = q.order_by(Version.created_at.desc()).offset(skip).limit(limit).all()
    return VersionListOut(items=items, total=total)


@router.post("/versions", response_model=VersionOut)
def create_version(body: VersionCreate, db: Session = Depends(get_db)):
    v = Version(**body.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.get("/versions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, db: Session = Depends(get_db)):
    v = db.get(Version, version_id)
    if not v:
        raise HTTPException(404, "Version not found")
    return v


@router.put("/versions/{version_id}", response_model=VersionOut)
def update_version(version_id: int, body: VersionUpdate, db: Session = Depends(get_db)):
    v = db.get(Version, version_id)
    if not v:
        raise HTTPException(404, "Version not found")
    for k, val in body.model_dump(exclude_unset=True).items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    return v


@router.delete("/versions/{version_id}")
def delete_version(version_id: int, db: Session = Depends(get_db)):
    v = db.get(Version, version_id)
    if not v:
        raise HTTPException(404, "Version not found")

    # Check if any EvalRun references this version
    ref = db.query(EvalRun.id).filter(
        or_(
            EvalRun.prompt_version_id == version_id,
            EvalRun.model_version_id == version_id,
            EvalRun.retriever_version_id == version_id,
            EvalRun.agent_version_id == version_id,
        )
    ).first()
    if ref:
        raise HTTPException(
            409,
            f"Cannot delete version '{v.name}': it is referenced by Run #{ref[0]}. "
            "Remove the reference before deleting."
        )

    try:
        db.delete(v)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409,
            f"Cannot delete version '{v.name}': it is still referenced by an EvalRun. "
            "Remove the reference before deleting."
        )
    return {"ok": True}
