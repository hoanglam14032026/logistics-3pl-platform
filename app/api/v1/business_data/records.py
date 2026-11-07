"""
API routes for Business Data Records in 3PL Logistics
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.business_data_record import BusinessDataRecord
from app.schemas.business_data_record import BusinessDataRecord as BusinessDataRecordSchema, BusinessDataRecordCreate, BusinessDataRecordUpdate

router = APIRouter()

@router.get("/business-data/records/{record_id}", response_model=BusinessDataRecordSchema)
async def get_business_data_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(BusinessDataRecord).filter(BusinessDataRecord.id == record_id).first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business data record not found"
        )
    return record

@router.get("/business-data/records", response_model=list[BusinessDataRecordSchema])
async def get_business_data_records(
    record_type: Optional[str] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    query = db.query(BusinessDataRecord)
    
    if record_type:
        query = query.filter(BusinessDataRecord.record_type == record_type)
    
    if category:
        query = query.filter(BusinessDataRecord.category == category)
    
    if is_active is not None:
        query = query.filter(BusinessDataRecord.is_active == is_active)
        
    return query.all()

@router.post("/business-data/records", response_model=BusinessDataRecordSchema)
async def create_business_data_record(record: BusinessDataRecordCreate, db: Session = Depends(get_db)):
    db_record = BusinessDataRecord(**record.dict())
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record

@router.put("/business-data/records/{record_id}", response_model=BusinessDataRecordSchema)
async def update_business_data_record(
    record_id: int, 
    record_update: BusinessDataRecordUpdate, 
    db: Session = Depends(get_db)
):
    record = db.query(BusinessDataRecord).filter(BusinessDataRecord.id == record_id).first()
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business data record not found"
        )
    
    update_data = record_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(record, key, value)
        
    db.commit()
    db.refresh(record)
    return record

@router.delete("/business-data/records/{record_id}")
async def delete_business_data_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(BusinessDataRecord).filter(BusinessDataRecord.id == record_id).first()
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business data record not found"
        )
    
    db.delete(record)
    db.commit()
    return {"message": "Business data record deleted successfully"}