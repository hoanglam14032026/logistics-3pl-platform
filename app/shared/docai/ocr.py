"""
OCR ảnh trang (Giai đoạn 9) – chạy cục bộ, không gửi ảnh ra ngoài.

- `tesseract` (mặc định, `DOCAI_OCR_ENGINE`): chương trình Tesseract của hệ điều hành (`DOCAI_TESSERACT_CMD`), ngôn ngữ
  `DOCAI_OCR_LANGS` (mặc định `vie+eng` – cần gói `tesseract-ocr-vie`). Gọi qua tiến trình con, đầu ra TSV: chữ, độ tin
  cậy, bbox từng từ.
- `paddle`: PaddleOCR (nhóm tùy chọn `ocr-paddle`), nạp model tải sẵn trong `DOCAI_PADDLE_MODEL_DIR` (`det/`, `rec/`) –
  tắt kiểm tra nguồn model, không tải từ Internet.
- `ollama`: model loại `ocr` trong danh mục `ai_models` (người dùng chọn `ocr_model_id`) – chỉ văn bản, không bbox.

Kết quả mỗi trang: `text` (theo dòng), `words` [{t, c (0–1), b: [x, y, w, h] px}], `mean_confidence`.
"""

import csv
import io
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


class OcrError(Exception):
    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class OcrResult:
    text: str
    words: list[dict] = field(default_factory=list)
    mean_confidence: float | None = None


def _tesseract(image: bytes) -> OcrResult:
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="docai-ocr-") as tmp:
        src = Path(tmp) / "page.png"
        src.write_bytes(image)
        cmd = [settings.docai_tesseract_cmd, str(src), "stdout", "-l", settings.docai_ocr_langs, "--psm", "3", "tsv"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=settings.docai_ocr_timeout_sec, check=False, env=_tess_env()
            )
        except FileNotFoundError as exc:
            raise OcrError(
                f"Không tìm thấy chương trình OCR '{settings.docai_tesseract_cmd}' – "
                "cài tesseract-ocr, tesseract-ocr-vie"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OcrError("OCR quá thời gian (DOCAI_OCR_TIMEOUT_SEC)", transient=True) from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise OcrError(f"Tesseract lỗi: {message[0][:200]}")
    return parse_tsv(proc.stdout.decode("utf-8", "replace"))


def _tess_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TESSDATA_PREFIX")}
    env["OMP_THREAD_LIMIT"] = "1"  # tesseract đa luồng trong worker nhiều tiến trình: chậm hơn
    return env


def parse_tsv(tsv: str) -> OcrResult:
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    words, confs = [], []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if row.get("level") != "5" or not text or conf < 0:
            continue
        key = tuple(int(row[k]) for k in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(text)
        bbox = [int(row[k]) for k in ("left", "top", "width", "height")]
        words.append({"t": text, "c": round(conf / 100, 3), "b": bbox})
        confs.append(conf / 100)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return OcrResult(text=text, words=words, mean_confidence=round(sum(confs) / len(confs), 3) if confs else None)


@lru_cache(maxsize=1)
def _paddle_engine():
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise OcrError(
            "Chưa cài PaddleOCR – pip install -r requirements-ocr-paddle-lock.txt (nhóm ocr-paddle)"
        ) from exc
    model_dir = get_settings().docai_paddle_model_dir
    det, rec = model_dir / "det", model_dir / "rec"
    if not det.is_dir() or not rec.is_dir():
        raise OcrError(f"Thiếu model PaddleOCR tải sẵn: {det} và {rec}")
    return PaddleOCR(
        text_detection_model_dir=str(det),
        text_recognition_model_dir=str(rec),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _paddle(image: bytes) -> OcrResult:
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(image)) as img:
        array = np.array(img.convert("RGB"))
    results = _paddle_engine().predict(array)
    words, confs, texts = [], [], []
    for res in results or []:
        data = res if isinstance(res, dict) else getattr(res, "json", {}).get("res", {})
        for text, score, box in zip(
            data.get("rec_texts", []), data.get("rec_scores", []), data.get("rec_boxes", []), strict=False
        ):
            x1, y1, x2, y2 = (int(v) for v in list(box)[:4])
            words.append({"t": text, "c": round(float(score), 3), "b": [x1, y1, x2 - x1, y2 - y1]})
            confs.append(float(score))
            texts.append(text)
    return OcrResult(
        text="\n".join(texts), words=words, mean_confidence=round(sum(confs) / len(confs), 3) if confs else None
    )


def ocr_image(image: bytes, engine: str, ollama_model: tuple[str | None, str] | None = None) -> OcrResult:
    """`ollama_model` = (endpoint, model_ref) khi engine = ollama."""
    if engine == "tesseract":
        return _tesseract(image)
    if engine == "paddle":
        return _paddle(image)
    if engine == "ollama" and ollama_model:
        from app.shared.ai_models import ollama

        endpoint, model_ref = ollama_model
        try:
            return OcrResult(text=ollama.transcribe(endpoint, model_ref, image).strip())
        except ollama.OllamaError as exc:
            raise OcrError(str(exc), transient=exc.transient) from exc
    raise OcrError(f"Động cơ OCR không hợp lệ: {engine}")
