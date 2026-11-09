"""
Danh mục loại chứng từ cho AI trích xuất (Giai đoạn 9): mỗi loại = **một file JSON** trong `app/shared/docai/schemas/`.

Thêm loại mới: thêm file `<code>.json` (không sửa mã lõi), khởi động lại API + worker. File sai cấu trúc → ứng dụng
không khởi động (báo rõ file, trường lỗi) – không chạy với schema hỏng.

Cấu trúc file (xem `party.json`):
    code, version, name, description, instructions (hướng dẫn thêm cho model),
    target: "party" | "service_contract" | "shipment" | "commercial_invoice" | null (chỉ trích xuất, duyệt),
    dual_approval (Giai đoạn 10): {"mode": "always"} | {"mode": "amount", "amount_field", "currency_field"},
    fields: [{key, label, type, required, description, max_length, values (enum), items (array), form_field}]
Kiểu trường: string, text, date (YYYY-MM-DD), datetime (ISO 8601, thiếu múi giờ → +07:00), number (thập phân, trả
dạng chuỗi), integer, boolean, enum (`values`), array (danh sách object, `items` một cấp).
`form_field`: tên trường trong body API tạo bản ghi nghiệp vụ (tự điền form); bỏ trống = chỉ hiển thị cho người duyệt.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.timeutil import VN_TZ

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
TARGETS = ("party", "service_contract", "shipment", "commercial_invoice")
FieldType = Literal["string", "text", "date", "datetime", "number", "integer", "boolean", "enum", "array"]


class SchemaFileError(RuntimeError):
    pass


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    label: str = Field(min_length=1, max_length=200)
    type: FieldType
    required: bool = False
    description: str | None = Field(None, max_length=1000)
    max_length: int | None = Field(None, gt=0, le=100000)
    values: list[str] | None = None
    items: list["FieldSpec"] | None = None
    form_field: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def _shape(self):
        if (self.type == "enum") != bool(self.values):
            raise ValueError(f"{self.key}: 'values' chỉ dùng và bắt buộc với type=enum")
        if (self.type == "array") != bool(self.items):
            raise ValueError(f"{self.key}: 'items' chỉ dùng và bắt buộc với type=array")
        if self.items and any(i.type == "array" for i in self.items):
            raise ValueError(f"{self.key}: array chỉ một cấp")
        if self.items:
            keys = [i.key for i in self.items]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{self.key}: trùng key trong items")
        return self


class DualApproval(BaseModel):
    """Duyệt 2 người (Giai đoạn 10). `always`: mọi job loại này. `amount`: khi trường số tiền ≥ ngưỡng của đồng tiền
    (`DOCAI_DUAL_APPROVAL_THRESHOLDS`; đồng tiền không có ngưỡng / thiếu số tiền → cần 2 người)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["always", "amount"]
    amount_field: str | None = None
    currency_field: str | None = None

    @model_validator(mode="after")
    def _fields(self):
        if (self.mode == "amount") != bool(self.amount_field and self.currency_field):
            raise ValueError("dual_approval: mode=amount cần amount_field và currency_field (và chỉ khi đó)")
        return self


class DocTypeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=4000)
    target: Literal["party", "service_contract", "shipment", "commercial_invoice"] | None = None
    dual_approval: DualApproval | None = None
    fields: list[FieldSpec] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique(self):
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Trùng key trường")
        forms = [f.form_field for f in self.fields if f.form_field]
        if len(forms) != len(set(forms)):
            raise ValueError("Trùng form_field")
        if forms and self.target is None:
            raise ValueError("form_field cần target (bảng nghiệp vụ đích)")
        dual = self.dual_approval
        if dual and dual.mode == "amount":
            by_key = {f.key: f for f in self.fields}
            if getattr(by_key.get(dual.amount_field), "type", None) not in ("number", "integer"):
                raise ValueError("dual_approval.amount_field phải là trường số")
            if getattr(by_key.get(dual.currency_field), "type", None) not in ("string", "enum"):
                raise ValueError("dual_approval.currency_field phải là trường chuỗi")
        return self

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)


def load_dir(directory: Path) -> dict[str, DocTypeSchema]:
    found: dict[str, DocTypeSchema] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            schema = DocTypeSchema.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as exc:
            raise SchemaFileError(f"Schema loại chứng từ lỗi – {path.name}: {exc}") from exc
        if schema.code != path.stem:
            raise SchemaFileError(f"{path.name}: 'code' ({schema.code}) phải trùng tên file")
        found[schema.code] = schema
    if not found:
        raise SchemaFileError(f"Không có schema loại chứng từ nào trong {directory}")
    return found


@lru_cache
def all_schemas() -> dict[str, DocTypeSchema]:
    return load_dir(SCHEMA_DIR)


def get_schema(code: str) -> DocTypeSchema | None:
    return all_schemas().get(code)


# --- Chuẩn hóa giá trị theo kiểu trường ----------------------------------------------------------------------------


class InvalidValue(ValueError):
    pass


_DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"),  # yyyy-mm-dd
    re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$"),  # dd/mm/yyyy (Việt Nam)
)
_TRUE = {"true", "1", "yes", "có", "co", "x"}
_FALSE = {"false", "0", "no", "không", "khong"}


def _date(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    for i, pattern in enumerate(_DATE_PATTERNS):
        m = pattern.match(text)
        if m:
            y, mo, d = (m.group(1), m.group(2), m.group(3)) if i == 0 else (m.group(3), m.group(2), m.group(1))
            try:
                return date(int(y), int(mo), int(d)).isoformat()
            except ValueError as exc:
                raise InvalidValue(f"Ngày không hợp lệ: {text}") from exc
    raise InvalidValue(f"Ngày phải dạng YYYY-MM-DD hoặc DD/MM/YYYY: {text}")


def _datetime(value: Any) -> str:
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return f"{_date(text)}T00:00:00+07:00"
        except InvalidValue as exc:
            raise InvalidValue(f"Thời điểm phải dạng ISO 8601: {text}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.isoformat()


def _number(value: Any) -> str:
    if isinstance(value, bool):
        raise InvalidValue("Không phải số")
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal_sep == "," else ",", "").replace(",", ".")
    elif text.count(",") == 1 and len(text.split(",")[1]) != 3:
        text = text.replace(",", ".")  # 12,5 → 12.5
    else:
        text = text.replace(",", "")  # 1,500,000 → 1500000
        if text.count(".") > 1:
            text = text.replace(".", "")  # 1.500.000 → 1500000
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidValue(f"Không phải số: {value}") from exc
    if not number.is_finite():
        raise InvalidValue(f"Không phải số: {value}")
    return format(number.normalize(), "f") if number == number.to_integral() else format(number, "f")


def _scalar(spec: FieldSpec, value: Any) -> Any:
    kind = spec.type
    if kind in ("string", "text"):
        if isinstance(value, (dict, list)):
            raise InvalidValue("Phải là chuỗi")
        text = str(value).strip()
        if kind == "string":
            text = re.sub(r"\s+", " ", text)
        if not text:
            return None
        if spec.max_length and len(text) > spec.max_length:
            raise InvalidValue(f"Dài quá {spec.max_length} ký tự")
        return text
    if kind == "date":
        return _date(value)
    if kind == "datetime":
        return _datetime(value)
    if kind == "number":
        return _number(value)
    if kind == "integer":
        number = Decimal(_number(value))
        if number != number.to_integral():
            raise InvalidValue(f"Phải là số nguyên: {value}")
        return int(number)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise InvalidValue(f"Phải là true/false: {value}")
    if kind == "enum":
        text = str(value).strip()
        for allowed in spec.values or []:
            if allowed.lower() == text.lower():
                return allowed
        raise InvalidValue(f"Phải là một trong: {', '.join(spec.values or [])}")
    raise InvalidValue(f"Kiểu không hỗ trợ: {kind}")


def normalize(spec: FieldSpec, value: Any) -> Any:
    """Giá trị chuẩn hóa (JSON được) hoặc None (trống). Sai kiểu → `InvalidValue`."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if spec.type != "array":
        return _scalar(spec, value)
    if not isinstance(value, list):
        raise InvalidValue("Phải là danh sách")
    rows = []
    for i, row in enumerate(value):
        if not isinstance(row, dict):
            raise InvalidValue(f"Dòng {i + 1}: phải là object")
        unknown = set(row) - {item.key for item in spec.items or []}
        if unknown:
            raise InvalidValue(f"Dòng {i + 1}: trường lạ {', '.join(sorted(unknown))}")
        out = {}
        for item in spec.items or []:
            try:
                out[item.key] = normalize(item, row.get(item.key))
            except InvalidValue as exc:
                raise InvalidValue(f"Dòng {i + 1}, {item.key}: {exc}") from exc
        if all(v is None for v in out.values()):
            continue  # dòng trống
        missing = [item.key for item in spec.items or [] if item.required and out[item.key] is None]
        if missing:
            raise InvalidValue(f"Dòng {i + 1}: thiếu {', '.join(missing)}")
        rows.append(out)
    return rows or None
