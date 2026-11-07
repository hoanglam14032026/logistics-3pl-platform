"""
Các schema Pydantic. Mỗi nhóm định nghĩa trong file riêng; file này chỉ re-export.
"""

from app.schemas.business_guide import (
    BusinessGuide, BusinessGuideCreate, BusinessGuideUpdate,
)
from app.schemas.training_document import (
    TrainingDocument, TrainingDocumentUpdate,
)
from app.schemas.business_data_record import (
    BusinessDataRecord, BusinessDataRecordCreate, BusinessDataRecordUpdate,
)
from app.schemas.video_resource import (
    VideoResource, VideoResourceUpdate,
)
