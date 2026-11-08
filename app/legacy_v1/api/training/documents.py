"""
API Tài liệu đào tạo (Training Documents). Prefix: /api/v1/training/documents
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.legacy_v1.core.crud import apply_update
from app.legacy_v1.core.storage import delete_file, save_upload
from app.legacy_v1.database import get_db
from app.legacy_v1.models.training_document import TrainingDocument
from app.legacy_v1.schemas.training_document import (
    TrainingDocument as TrainingDocumentSchema,
    TrainingDocumentUpdate,
)

router = APIRouter()

SUBDIR = "training_documents"
NOT_FOUND = "Training document not found"
NON_NULLABLE = ("title", "is_active")


def _get_or_404(db: Session, document_id: int) -> TrainingDocument:
    document = db.get(TrainingDocument, document_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return document


@router.get("", response_model=List[TrainingDocumentSchema])
def list_training_documents(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """Danh sách tài liệu; lọc theo category và is_active (mặc định true)."""
    query = db.query(TrainingDocument)
    if category:
        query = query.filter(TrainingDocument.category == category)
    if is_active is not None:
        query = query.filter(TrainingDocument.is_active == is_active)
    return query.order_by(TrainingDocument.id).all()


@router.get("/{document_id}", response_model=TrainingDocumentSchema)
def get_training_document(document_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, document_id)


@router.post("", response_model=TrainingDocumentSchema, status_code=status.HTTP_201_CREATED)
async def create_training_document(
    title: str = Form(..., min_length=1, max_length=200),
    file_type: str = Form(..., min_length=1, max_length=50),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None, max_length=100),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload tài liệu (multipart/form-data) và tạo bản ghi."""
    file_path = await save_upload(file, SUBDIR)
    db_document = TrainingDocument(
        title=title,
        description=description,
        file_path=file_path,
        file_type=file_type,
        category=category,
    )
    db.add(db_document)
    db.commit()
    db.refresh(db_document)
    return db_document


@router.put("/{document_id}", response_model=TrainingDocumentSchema)
def update_training_document(
    document_id: int, document_update: TrainingDocumentUpdate, db: Session = Depends(get_db)
):
    """Cập nhật metadata từng phần (không thay file)."""
    document = _get_or_404(db, document_id)
    apply_update(document, document_update.model_dump(exclude_unset=True), NON_NULLABLE)
    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}")
def delete_training_document(document_id: int, db: Session = Depends(get_db)):
    """Xóa bản ghi và file trên đĩa."""
    document = _get_or_404(db, document_id)
    file_path = document.file_path
    db.delete(document)
    db.commit()
    delete_file(file_path)
    return {"message": "Training document deleted successfully"}
