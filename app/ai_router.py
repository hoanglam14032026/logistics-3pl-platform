from fastapi import APIRouter
from pydantic import BaseModel
from app.ai_gateway import ask_logistics, ask_coder

router = APIRouter(prefix="/api/ai", tags=["ai"])

class AskRequest(BaseModel):
    prompt: str
    mode: str = "logistics"

@router.post("/ask")
async def ask_ai(req: AskRequest):
    if req.mode == "coder":
        answer = await ask_coder(req.prompt)
    else:
        answer = await ask_logistics(req.prompt)
    return {"answer": answer}