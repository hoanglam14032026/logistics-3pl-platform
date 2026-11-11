import httpx

LLAMA_URL = "http://localhost:8080/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api"
llama_url: str = "http://localhost:8080"
from app.core.config import get_settings
def ask_logistics(prompt: str) -> str:
    settings = get_settings()
    try:
        r = httpx.post(f"{settings.llama_url}/v1/chat/completions", json={"messages": [{"role": "user", "content": prompt}]}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Loi goi model Qwen27B: {e} - Kiem tra llama-server port 8080 da chay chua"

def ask_coder(prompt: str) -> str:
    settings = get_settings()
    try:
        r = httpx.post(f"{settings.llama_url}/v1/chat/completions", json={"messages": [{"role": "user", "content": prompt}]}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Loi goi model Qwen27B: {e} - Kiem tra llama-server port 8080 da chay chua"

def read_bill_image(image_base64: str) -> str:
    settings = get_settings()
    try:
        r = httpx.post(f"{settings.ollama_url}/api/chat", json={"model": "qwen3-vl:8b", "messages": [{"role": "user", "content": "Doc het thong tin tren anh Bill nay, tra ve JSON", "images": [image_base64]}]}, timeout=120)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except Exception as e:
        return f"Loi goi model VL: {e} - Kiem tra Ollama port 11434 da chay chua"

def embed_text(text: str) -> list[float]:
    settings = get_settings()
    try:
        r = httpx.post(f"{settings.ollama_url}/api/embeddings", json={"model": "nomic-embed-text", "prompt": text}, timeout=30)
        r.raise_for_status()
        return r.json()["embedding"]
    except Exception:
        return []