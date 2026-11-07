"""
API routes for Training Documents in 3PL Logistics
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.training_document import TrainingDocument
from app.schemas.training_document import TrainingDocument as TrainingDocumentSchema, TrainingDocumentCreate, TrainingDocumentUpdate
import os
from pathlib import Path

router = APIRouter()

# Ensure upload directory exists
UPLOAD_DIR = Path("uploads/training_documents")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/training/documents/{document_id}", response_model=TrainingDocumentSchema)
async def get_training_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(TrainingDocument).filter(TrainingDocument.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training document not found"
        )
    return document

@router.get("/training/documents", response_model=list[TrainingDocumentSchema])
async def get_training_documents(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    query = db.query(TrainingDocument)
    
    if category:
        query = query.filter(TrainingDocument.category == category)
    
    if is_active is not None:
        query = query.filter(TrainingDocument.is_active == is_active)
        
    return query.all()

@router.post("/training/documents", response_model=TrainingDocumentSchema)
async def create_training_document(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    file_type: str = Form(...),
    category: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Save file
    file_extension = Path(file.filename).suffix
    file_name = f"{title.replace(' ', '_')}_{hash(file.filename)}.{file_extension}"
    file_path = UPLOAD_DIR / file_name
    
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    # Create document record
    db_document = TrainingDocument(
        title=title,
        description=description,
        file_path=str(file_path),
        file_type=file_type,
        category=category
    )
    db.add(db_document)
    db.commit()
    db.refresh(db_document)
    return db_document

@router.put("/training/documents/{document_id}", response_model=TrainingDocumentSchema)
async def update_training_document(
    document_id: int, 
    document_update: TrainingDocumentUpdate, 
    db: Session = Depends(get_db)
):
    document = db.query(TrainingDocument).filter(TrainingDocument.id == document_id).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training document not found"
        )
    
    update_data = document_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(document, key, value)
        
    db.commit()
    db.refresh(document)
    return document

@router.delete("/training/documents/{document_id}")
async def delete_training_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(TrainingDocument).filter(TrainingDocument.id == document_id).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Training document not found"
        )
    
    # Delete file from disk
    file_path = Path(document.file_path)
    if file_path.exists():
        file_path.unlink()
    
    db.delete(document)
    db.commit()
    return {"message": "Training document deleted successfully"}