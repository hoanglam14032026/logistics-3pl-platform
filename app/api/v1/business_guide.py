"""
API routes for Business Guide in 3PL Logistics
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.business_guide import BusinessGuide
from app.schemas.business_guide import BusinessGuide as BusinessGuideSchema, BusinessGuideCreate, BusinessGuideUpdate

router = APIRouter()

@router.get("/guide/{guide_id}", response_model=BusinessGuideSchema)
async def get_business_guide(guide_id: int, db: Session = Depends(get_db)):
    guide = db.query(BusinessGuide).filter(BusinessGuide.id == guide_id).first()
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business guide not found"
        )
    return guide

@router.get("/guide", response_model=list[BusinessGuideSchema])
async def get_business_guides(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    query = db.query(BusinessGuide)
    
    if category:
        query = query.filter(BusinessGuide.category == category)
    
    if is_active is not None:
        query = query.filter(BusinessGuide.is_active == is_active)
        
    return query.all()

@router.post("/guide", response_model=BusinessGuideSchema)
async def create_business_guide(guide: BusinessGuideCreate, db: Session = Depends(get_db)):
    db_guide = BusinessGuide(**guide.dict())
    db.add(db_guide)
    db.commit()
    db.refresh(db_guide)
    return db_guide

@router.put("/guide/{guide_id}", response_model=BusinessGuideSchema)
async def update_business_guide(
    guide_id: int, 
    guide_update: BusinessGuideUpdate, 
    db: Session = Depends(get_db)
):
    guide = db.query(BusinessGuide).filter(BusinessGuide.id == guide_id).first()
    
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business guide not found"
        )
    
    update_data = guide_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(guide, key, value)
        
    db.commit()
    db.refresh(guide)
    return guide

@router.delete("/guide/{guide_id}")
async def delete_business_guide(guide_id: int, db: Session = Depends(get_db)):
    guide = db.query(BusinessGuide).filter(BusinessGuide.id == guide_id).first()
    
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business guide not found"
        )
    
    db.delete(guide)
    db.commit()
    return {"message": "Business guide deleted successfully"}