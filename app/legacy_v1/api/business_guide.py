"""
API Hướng dẫn nghiệp vụ (Business Guide). Prefix: /api/v1/guide
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.legacy_v1.core.crud import apply_update
from app.legacy_v1.database import get_db
from app.legacy_v1.models.business_guide import BusinessGuide
from app.legacy_v1.schemas.business_guide import (
    BusinessGuide as BusinessGuideSchema,
    BusinessGuideCreate,
    BusinessGuideUpdate,
)

router = APIRouter()

NOT_FOUND = "Business guide not found"
NON_NULLABLE = ("title", "category", "content", "is_active")


def _get_or_404(db: Session, guide_id: int) -> BusinessGuide:
    guide = db.get(BusinessGuide, guide_id)
    if not guide:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return guide


@router.get("", response_model=List[BusinessGuideSchema])
def list_business_guides(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """Danh sách hướng dẫn; lọc theo category và is_active (mặc định true)."""
    query = db.query(BusinessGuide)
    if category:
        query = query.filter(BusinessGuide.category == category)
    if is_active is not None:
        query = query.filter(BusinessGuide.is_active == is_active)
    return query.order_by(BusinessGuide.id).all()


@router.get("/{guide_id}", response_model=BusinessGuideSchema)
def get_business_guide(guide_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, guide_id)


@router.post("", response_model=BusinessGuideSchema, status_code=status.HTTP_201_CREATED)
def create_business_guide(guide: BusinessGuideCreate, db: Session = Depends(get_db)):
    db_guide = BusinessGuide(**guide.model_dump())
    db.add(db_guide)
    db.commit()
    db.refresh(db_guide)
    return db_guide


@router.put("/{guide_id}", response_model=BusinessGuideSchema)
def update_business_guide(
    guide_id: int, guide_update: BusinessGuideUpdate, db: Session = Depends(get_db)
):
    """Cập nhật từng phần: chỉ các trường có trong body được thay đổi."""
    guide = _get_or_404(db, guide_id)
    apply_update(guide, guide_update.model_dump(exclude_unset=True), NON_NULLABLE)
    db.commit()
    db.refresh(guide)
    return guide


@router.delete("/{guide_id}")
def delete_business_guide(guide_id: int, db: Session = Depends(get_db)):
    guide = _get_or_404(db, guide_id)
    db.delete(guide)
    db.commit()
    return {"message": "Business guide deleted successfully"}
