"""
Các schema Pydantic. Mỗi nhóm định nghĩa trong file riêng; file này chỉ re-export.
"""

from app.legacy_v1.schemas.business_guide import (
    BusinessGuide, BusinessGuideCreate, BusinessGuideUpdate,
)
from app.legacy_v1.schemas.training_document import (
    TrainingDocument, TrainingDocumentUpdate,
)
from app.legacy_v1.schemas.business_data_record import (
    BusinessDataRecord, BusinessDataRecordCreate, BusinessDataRecordUpdate,
)
from app.legacy_v1.schemas.video_resource import (
    VideoResource, VideoResourceUpdate,
)
