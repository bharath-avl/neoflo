import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, index=True)
    start_time = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_event_time = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    events = relationship("Event", back_populates="session", cascade="all, delete")
    screenshots = relationship("Screenshot", back_populates="session", cascade="all, delete")

class Event(Base):
    __tablename__ = "events"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), index=True)
    type = Column(String, index=True)
    url = Column(String)
    metadata_ = Column("metadata", JSON)
    timestamp = Column(DateTime(timezone=True))
    
    session = relationship("Session", back_populates="events")

class Screenshot(Base):
    __tablename__ = "screenshots"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), index=True)
    file_path = Column(String)
    tab_url = Column(String)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    session = relationship("Session", back_populates="screenshots")
