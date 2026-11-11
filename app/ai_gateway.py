import httpx

LLAMA_URL = "http://localhost:8080/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/api"

async def ask_logistics(prompt: str):
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(LLAMA_URL, json={
            "model": "Qwen3.8-27B",
            "messages": [
                {"role": "system", "content": "<|terse_on|><|think_low|> Bạn là trợ lý logistics 3PL."},
                {"role": "user", "content": prompt}
            ]
        })
        return r.json()["choices"][0]["message"]["content"]

async def ask_coder(prompt: str):
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(LLAMA_URL, json={
            "model": "Qwen3.8-27B",
            "messages": [
                {"role": "system", "content": "<|terse_on|><|think_medium|> Bạn là Senior Developer Python. Chỉ trả code."},
                {"role": "user", "content": prompt}
            ]
        })
        return r.json()["choices"][0]["message"]["content"]

async def read_bill_image(base64_image: str):
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OLLAMA_URL}/chat", json={
            "model": "qwen3-vl:8b",
            "messages": [{"role": "user", "content": "Đọc bill này, trả về JSON", "images": [base64_image]}],
            "stream": False
        })
        return r.json()["message"]["content"]

async def embed_text(text: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{OLLAMA_URL}/embeddings", json={
            "model": "nomic-embed-text",
            "prompt": text
        })
        return r.json()["embedding"]