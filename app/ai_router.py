from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai_gateway import ask_logistics, embed_text
from app.core.db import get_db
from app.flows.operations import document_service as doc_service
from app.flows.operations import shipment_service
from app.flows.operations.document_router import _staff
from app.shared.identity.models import SysUser

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


@router.post("/ask")
def ask(req: AskRequest, actor: SysUser = Depends(_staff)):
    return {"answer": ask_logistics(req.prompt)}


@router.get("/track/{shipment_id:int}")
def track_shipment(shipment_id: int, actor: SysUser = Depends(_staff), db: Session = Depends(get_db)):
    shipment = shipment_service.get_shipment(db, shipment_id)
    docs = doc_service.list_documents(db, shipment_id)
    return {
        "shipment_id": shipment.id,
        "status": shipment.status,
        "documents": [{"doc_type": d.doc_type, "status": d.status} for d in docs],
    }


@router.post("/embed")
def embed(req: AskRequest, actor: SysUser = Depends(_staff)):
    return {"embedding": embed_text(req.prompt)}
