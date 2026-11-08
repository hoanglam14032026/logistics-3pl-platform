"""Schema API danh mục khách hàng, đối tác (`/api/v2/md/parties`)."""

import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.shared.identity.schemas import Email
from app.shared.masterdata.models import PartyStatus, PartyType

_VN_TAX_RE = re.compile(r"^\d{10}(-\d{3})?$")


def _strip_upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _strip_or_none(value: object) -> object:
    # Chuỗi rỗng/khoảng trắng ở trường tùy chọn = không có giá trị.
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def check_tax_code(tax_code: str | None, country_code: str) -> None:
    """MST Việt Nam: 10 số, hoặc 13 số dạng `0101234567-001` (chi nhánh). Nước ngoài: không kiểm định dạng."""
    if tax_code is not None and country_code == "VN" and not _VN_TAX_RE.match(tax_code):
        raise ValueError("Mã số thuế Việt Nam gồm 10 số hoặc dạng 0101234567-001")


def _optional(max_length: int, pattern: str | None = None):
    """Chuỗi tùy chọn: bỏ khoảng trắng đầu cuối; rỗng → None; ràng buộc áp lên phần `str`."""
    return Annotated[
        Annotated[str, StringConstraints(max_length=max_length, pattern=pattern)] | None,
        BeforeValidator(_strip_or_none),
    ]


PartyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
# Chuẩn hóa: bỏ khoảng trắng, rỗng → None, rồi chữ hoa (validator "before" chạy từ phải sang trái).
TaxCode = Annotated[
    Annotated[str, StringConstraints(max_length=32, pattern=r"^[0-9A-Z][0-9A-Z./-]*$")] | None,
    BeforeValidator(_strip_upper),
    BeforeValidator(_strip_or_none),
]
CountryCode = Annotated[str, BeforeValidator(_strip_upper), StringConstraints(pattern=r"^[A-Z]{2}$")]


class _PartyFields(BaseModel):
    address: _optional(500) = None
    contact_name: _optional(200) = None
    phone: _optional(30, r"^[0-9+()./ -]+$") = None
    email: Annotated[Email | None, BeforeValidator(_strip_or_none)] = None
    note: _optional(4000) = None


class PartyCreate(_PartyFields):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "party_type": "customer",
                "name": "Công ty TNHH Xuất nhập khẩu Minh An",
                "tax_code": "0101234567",
                "country_code": "VN",
                "address": "12 Trần Hưng Đạo, Hoàn Kiếm, Hà Nội",
                "contact_name": "Nguyễn Thị Lan",
                "phone": "024 3826 1234",
                "email": "xnk@minhan.vn",
            }
        }
    )

    party_type: PartyType
    name: PartyName
    tax_code: TaxCode = None
    country_code: CountryCode = "VN"

    @model_validator(mode="after")
    def _tax(self):
        check_tax_code(self.tax_code, self.country_code)
        return self


class PartyUpdate(_PartyFields):
    """PATCH (JSON Merge Patch). `status` đổi qua `:archive` / `:restore`, không sửa trực tiếp (06 §1)."""

    model_config = ConfigDict(extra="forbid")

    party_type: PartyType | None = None
    name: PartyName | None = None
    tax_code: TaxCode = None
    country_code: CountryCode | None = None

    @model_validator(mode="after")
    def _not_null(self):
        for field in ("party_type", "name", "country_code"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Trường '{field}' không được null")
        return self


class PartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    party_type: PartyType
    name: str
    tax_code: str | None
    country_code: str
    address: str | None
    contact_name: str | None
    phone: str | None
    email: str | None
    note: str | None
    status: PartyStatus
    archived_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    row_version: int
