from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai_gateway import ask_logistics, embed_text, read_bill_image
from app.core.db import get_db
from app.flows.operations import document_service as doc_service
from app.flows.operations import shipment_service
from app.flows.operations.document_router import _staff
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/ai", tags=["ai"])

class AskRequest(BaseModel):
    prompt: str

class BillImageRequest(BaseModel):
    image_base64: str

@router.post("/ask")
async def ask(req: AskRequest, actor: SysUser = Depends(_staff)):
    return {"answer": ask_logistics(req.prompt)}

@router.get("/track/{shipment_id:int}")
async def track_shipment(shipment_id: int, actor: SysUser = Depends(_staff), db: Session = Depends(get_db)):
    shipment = shipment_service.get_shipment(db, shipment_id)
    docs = doc_service.list_documents(db, shipment_id)
    return {"shipment_id": shipment.id, "status": shipment.status, "documents": [{"doc_type": d.doc_type, "status": d.status, "ai_parsed": d.ai_parsed} for d in docs]}

@router.post("/read-bill")
async def read_bill(req: BillImageRequest, actor: SysUser = Depends(_staff)):
    return {"bill": read_bill_image(req.image_base64)}

@router.post("/read-bill-upload")
async def read_bill_upload(file: UploadFile = File(...), actor: SysUser = Depends(_staff)):
    if file.content_type not in ["image/jpeg", "image/png", "application/pdf"]:
        return {"error": "Chi nhan jpg, png, pdf"}
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return {"error": "File qua 20MB"}
    import base64
    return {"bill": read_bill_image(base64.b64encode(content).decode())}

@router.post("/embed")
async def embed(req: AskRequest, actor: SysUser = Depends(_staff)):
    return {"embedding": embed_text(req.prompt)}