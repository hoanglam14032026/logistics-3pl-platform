"""
Các model CSDL. Mỗi model định nghĩa trong file riêng; file này chỉ re-export.
"""

from app.models.business_guide import BusinessGuide
from app.models.training_document import TrainingDocument
from app.models.business_data_record import BusinessDataRecord
from app.models.video_resource import VideoResource

__all__ = ["BusinessGuide", "TrainingDocument", "BusinessDataRecord", "VideoResource"]
