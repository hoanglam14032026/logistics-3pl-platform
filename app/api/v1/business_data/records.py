"""
API Bản ghi dữ liệu kinh doanh (Business Data Records). Prefix: /api/v1/business-data/records
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.crud import apply_update
from app.database import get_db
from app.models.business_data_record import BusinessDataRecord
from app.schemas.business_data_record import (
    BusinessDataRecord as BusinessDataRecordSchema,
    BusinessDataRecordCreate,
    BusinessDataRecordUpdate,
)

router = APIRouter()

NOT_FOUND = "Business data record not found"
NON_NULLABLE = ("record_type", "title", "is_active")


def _get_or_404(db: Session, record_id: int) -> BusinessDataRecord:
    record = db.get(BusinessDataRecord, record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return record


@router.get("", response_model=List[BusinessDataRecordSchema])
def list_business_data_records(
    record_type: Optional[str] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """Danh sách bản ghi; lọc theo record_type, category, is_active (mặc định true)."""
    query = db.query(BusinessDataRecord)
    if record_type:
        query = query.filter(BusinessDataRecord.record_type == record_type)
    if category:
        query = query.filter(BusinessDataRecord.category == category)
    if is_active is not None:
        query = query.filter(BusinessDataRecord.is_active == is_active)
    return query.order_by(BusinessDataRecord.id).all()


@router.get("/{record_id}", response_model=BusinessDataRecordSchema)
def get_business_data_record(record_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, record_id)


@router.post("", response_model=BusinessDataRecordSchema, status_code=status.HTTP_201_CREATED)
def create_business_data_record(record: BusinessDataRecordCreate, db: Session = Depends(get_db)):
    db_record = BusinessDataRecord(**record.model_dump())
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record


@router.put("/{record_id}", response_model=BusinessDataRecordSchema)
def update_business_data_record(
    record_id: int, record_update: BusinessDataRecordUpdate, db: Session = Depends(get_db)
):
    """Cập nhật từng phần."""
    record = _get_or_404(db, record_id)
    apply_update(record, record_update.model_dump(exclude_unset=True), NON_NULLABLE)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{record_id}")
def delete_business_data_record(record_id: int, db: Session = Depends(get_db)):
    record = _get_or_404(db, record_id)
    db.delete(record)
    db.commit()
    return {"message": "Business data record deleted successfully"}
