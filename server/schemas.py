from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EventCreate(BaseModel):
    session_id: str
    type: str
    url: str
    metadata: dict[str, Any]
    timestamp: datetime

class EventBatchCreate(BaseModel):
    events: list[EventCreate]

class ScreenshotCreate(BaseModel):
    session_id: str
    tab_url: str
    image_base64: str
    timestamp: datetime

class SessionResponse(BaseModel):
    id: str
    start_time: datetime
    last_event_time: datetime
    is_active: bool
    
    model_config = ConfigDict(from_attributes=True)
