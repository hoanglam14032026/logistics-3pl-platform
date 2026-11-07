"""
API Tài nguyên video (Video Resources). Prefix: /api/v1/video/resources
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.crud import apply_update
from app.core.storage import delete_file, save_upload
from app.database import get_db
from app.models.video_resource import VideoResource
from app.schemas.video_resource import (
    VideoResource as VideoResourceSchema,
    VideoResourceUpdate,
)

router = APIRouter()

SUBDIR = "video_resources"
NOT_FOUND = "Video resource not found"
NON_NULLABLE = ("title", "is_active")


def _get_or_404(db: Session, resource_id: int) -> VideoResource:
    resource = db.get(VideoResource, resource_id)
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return resource


@router.get("", response_model=List[VideoResourceSchema])
def list_video_resources(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """Danh sách video; lọc theo category và is_active (mặc định true)."""
    query = db.query(VideoResource)
    if category:
        query = query.filter(VideoResource.category == category)
    if is_active is not None:
        query = query.filter(VideoResource.is_active == is_active)
    return query.order_by(VideoResource.id).all()


@router.get("/{resource_id}", response_model=VideoResourceSchema)
def get_video_resource(resource_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, resource_id)


@router.post("", response_model=VideoResourceSchema, status_code=status.HTTP_201_CREATED)
async def create_video_resource(
    title: str = Form(..., min_length=1, max_length=200),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None, max_length=100),
    duration: Optional[int] = Form(None, ge=0),
    video_file: UploadFile = File(...),
    thumbnail_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Upload video (bắt buộc) và thumbnail (tùy chọn), tạo bản ghi."""
    video_path = await save_upload(video_file, SUBDIR)
    thumbnail_path = None
    if thumbnail_file is not None and thumbnail_file.filename:
        thumbnail_path = await save_upload(thumbnail_file, SUBDIR)

    db_resource = VideoResource(
        title=title,
        description=description,
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        duration=duration,
        category=category,
    )
    db.add(db_resource)
    db.commit()
    db.refresh(db_resource)
    return db_resource


@router.put("/{resource_id}", response_model=VideoResourceSchema)
def update_video_resource(
    resource_id: int, resource_update: VideoResourceUpdate, db: Session = Depends(get_db)
):
    """Cập nhật metadata từng phần (không thay file)."""
    resource = _get_or_404(db, resource_id)
    apply_update(resource, resource_update.model_dump(exclude_unset=True), NON_NULLABLE)
    db.commit()
    db.refresh(resource)
    return resource


@router.delete("/{resource_id}")
def delete_video_resource(resource_id: int, db: Session = Depends(get_db)):
    """Xóa bản ghi, file video và thumbnail trên đĩa."""
    resource = _get_or_404(db, resource_id)
    paths = (resource.video_path, resource.thumbnail_path)
    db.delete(resource)
    db.commit()
    for p in paths:
        delete_file(p)
    return {"message": "Video resource deleted successfully"}
