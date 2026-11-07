"""
API routes for Video Resources in 3PL Logistics
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.video_resource import VideoResource
from app.schemas.video_resource import VideoResource as VideoResourceSchema, VideoResourceCreate, VideoResourceUpdate
import os
from pathlib import Path

router = APIRouter()

# Ensure upload directory exists
UPLOAD_DIR = Path("uploads/video_resources")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/video/resources/{resource_id}", response_model=VideoResourceSchema)
async def get_video_resource(resource_id: int, db: Session = Depends(get_db)):
    resource = db.query(VideoResource).filter(VideoResource.id == resource_id).first()
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video resource not found"
        )
    return resource

@router.get("/video/resources", response_model=list[VideoResourceSchema])
async def get_video_resources(
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: Session = Depends(get_db)
):
    query = db.query(VideoResource)
    
    if category:
        query = query.filter(VideoResource.category == category)
    
    if is_active is not None:
        query = query.filter(VideoResource.is_active == is_active)
        
    return query.all()

@router.post("/video/resources", response_model=VideoResourceSchema)
async def create_video_resource(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    duration: Optional[int] = Form(None),
    video_file: UploadFile = File(...),
    thumbnail_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    # Save video file
    video_extension = Path(video_file.filename).suffix
    video_name = f"{title.replace(' ', '_')}_{hash(video_file.filename)}.{video_extension}"
    video_path = UPLOAD_DIR / video_name
    
    with open(video_path, "wb") as buffer:
        content = await video_file.read()
        buffer.write(content)
    
    # Save thumbnail if provided
    thumbnail_path = None
    if thumbnail_file:
        thumbnail_extension = Path(thumbnail_file.filename).suffix
        thumbnail_name = f"{title.replace(' ', '_')}_thumbnail{thumbnail_extension}"
        thumbnail_path = UPLOAD_DIR / thumbnail_name
        
        with open(thumbnail_path, "wb") as buffer:
            content = await thumbnail_file.read()
            buffer.write(content)
    
    # Create video resource record
    db_resource = VideoResource(
        title=title,
        description=description,
        video_path=str(video_path),
        thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
        duration=duration,
        category=category
    )
    db.add(db_resource)
    db.commit()
    db.refresh(db_resource)
    return db_resource

@router.put("/video/resources/{resource_id}", response_model=VideoResourceSchema)
async def update_video_resource(
    resource_id: int, 
    resource_update: VideoResourceUpdate, 
    db: Session = Depends(get_db)
):
    resource = db.query(VideoResource).filter(VideoResource.id == resource_id).first()
    
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video resource not found"
        )
    
    update_data = resource_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(resource, key, value)
        
    db.commit()
    db.refresh(resource)
    return resource

@router.delete("/video/resources/{resource_id}")
async def delete_video_resource(resource_id: int, db: Session = Depends(get_db)):
    resource = db.query(VideoResource).filter(VideoResource.id == resource_id).first()
    
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video resource not found"
        )
    
    # Delete files from disk
    video_path = Path(resource.video_path)
    if video_path.exists():
        video_path.unlink()
    
    if resource.thumbnail_path:
        thumbnail_path = Path(resource.thumbnail_path)
        if thumbnail_path.exists():
            thumbnail_path.unlink()
    
    db.delete(resource)
    db.commit()
    return {"message": "Video resource deleted successfully"}