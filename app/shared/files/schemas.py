"""06 §2.6: API trả `file_id`, tên gốc, MIME, dung lượng, SHA-256, `download_url` – không trả đường dẫn trên server."""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.timeutil import UtcDatetime


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int = Field(validation_alias="id")
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: UtcDatetime

    @computed_field
    @property
    def download_url(self) -> str:
        return f"/api/v2/files/{self.file_id}"
