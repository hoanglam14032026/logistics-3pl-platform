"""
Các model CSDL. Mỗi model định nghĩa trong file riêng; file này chỉ re-export.
"""

from app.legacy_v1.models.business_guide import BusinessGuide
from app.legacy_v1.models.training_document import TrainingDocument
from app.legacy_v1.models.business_data_record import BusinessDataRecord
from app.legacy_v1.models.video_resource import VideoResource

__all__ = ["BusinessGuide", "TrainingDocument", "BusinessDataRecord", "VideoResource"]
