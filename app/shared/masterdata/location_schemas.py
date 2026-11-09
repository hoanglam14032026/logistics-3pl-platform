"""Schema API danh mục địa điểm `/api/v2/md/locations` (Giai đoạn 16)."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from app.core.timeutil import UtcDatetime
from app.shared.masterdata.models import LOCATION_CODE_PATTERN, LocationType, PartyStatus
from app.shared.masterdata.schemas import CountryCode, _optional, _strip_upper

LocationCode = Annotated[str, BeforeValidator(_strip_upper), StringConstraints(pattern=LOCATION_CODE_PATTERN)]
LocationName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Latitude = Annotated[Decimal, Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)]


def _pair(obj, fields_set: set[str] | None = None):
    if (obj.latitude is None) != (obj.longitude is None):
        if fields_set is None or {"latitude", "longitude"} & fields_set:
            raise ValueError("Tọa độ cần đủ cả latitude và longitude (hoặc bỏ trống cả hai)")
    return obj


class LocationCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "code": "VNHPH",
                "name": "Cảng Hải Phòng",
                "location_type": "seaport",
                "country_code": "VN",
                "address": "Số 8A Trần Phú, Ngô Quyền, Hải Phòng",
                "latitude": "20.865139",
                "longitude": "106.683830",
            }
        },
    )

    code: LocationCode
    name: LocationName
    location_type: LocationType
    country_code: CountryCode = "VN"
    address: _optional(500) = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    party_id: int | None = Field(None, gt=0, description="Khách hàng / đối tác sở hữu (kho khách hàng, điểm giao)")

    @model_validator(mode="after")
    def _coordinates(self):
        return _pair(self)


class LocationUpdate(BaseModel):
    """PATCH (JSON Merge Patch). `code` không đổi sau khi tạo. Tọa độ sửa cùng nhau."""

    model_config = ConfigDict(extra="forbid")

    name: LocationName | None = None
    location_type: LocationType | None = None
    country_code: CountryCode | None = None
    address: _optional(500) = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    party_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _checks(self):
        for field in ("name", "location_type", "country_code"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Trường '{field}' không được null")
        if ("latitude" in self.model_fields_set) != ("longitude" in self.model_fields_set):
            raise ValueError("Sửa tọa độ cần gửi cả latitude và longitude")
        return _pair(self, self.model_fields_set)


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    location_type: LocationType
    country_code: str
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    party_id: int | None
    status: PartyStatus
    archived_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    row_version: int
