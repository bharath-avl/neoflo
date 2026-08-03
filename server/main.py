import os
import uuid
import base64
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import List, Any

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from database import engine, Base, get_db
import models
import schemas
from worker import worker_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Start background worker
    worker_task = asyncio.create_task(worker_loop())
    logger.info("Vision analysis worker started.")

    yield

    # Shutdown: cancel worker
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Vision analysis worker stopped.")


app = FastAPI(title="Visual AI Agent Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)


async def get_or_create_session(session_id: str, db: AsyncSession, timestamp: datetime = None) -> models.Session:
    ts = timestamp or datetime.now(timezone.utc)
    result = await db.execute(select(models.Session).where(models.Session.id == session_id))
    session = result.scalars().first()
    
    if not session:
        session = models.Session(id=session_id, start_time=ts, last_event_time=ts)
        db.add(session)
    else:
        # Only update if the new timestamp is later
        if session.last_event_time.tzinfo is None:
            session.last_event_time = session.last_event_time.replace(tzinfo=timezone.utc)
            
        if ts > session.last_event_time:
            session.last_event_time = ts
            
    return session

@app.post("/events", response_model=dict)
async def create_events(batch: schemas.EventBatchCreate, db: AsyncSession = Depends(get_db)):
    if not batch.events:
        return {"status": "ok", "count": 0}
        
    latest_ts = None
    session_id = batch.events[0].session_id
    
    for event_data in batch.events:
        event_ts = event_data.timestamp
        if latest_ts is None or event_ts > latest_ts:
            latest_ts = event_ts
            
        db_event = models.Event(
            session_id=event_data.session_id,
            type=event_data.type,
            url=event_data.url,
            metadata_=event_data.metadata,
            timestamp=event_ts
        )
        db.add(db_event)
        
    await get_or_create_session(session_id, db, timestamp=latest_ts)
    await db.commit()
    
    return {"status": "ok", "count": len(batch.events)}

@app.post("/screenshots", response_model=dict)
async def create_screenshot(screenshot_data: schemas.ScreenshotCreate, db: AsyncSession = Depends(get_db)):
    # Save image to disk
    try:
        # Handle cases where "data:image/png;base64," prefix is present
        b64_data = screenshot_data.image_base64
        if "," in b64_data:
            b64_data = b64_data.split(",")[1]
            
        image_bytes = base64.b64decode(b64_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 data: {str(e)}")
        
    filename = f"{screenshot_data.session_id}_{int(screenshot_data.timestamp.timestamp())}_{uuid.uuid4().hex[:8]}.png"
    filepath = os.path.join(STORAGE_DIR, filename)
    
    with open(filepath, "wb") as f:
        f.write(image_bytes)
        
    # Save to db
    db_screenshot = models.Screenshot(
        session_id=screenshot_data.session_id,
        file_path=filepath,
        tab_url=screenshot_data.tab_url,
        timestamp=screenshot_data.timestamp
    )
    db.add(db_screenshot)
    
    await get_or_create_session(screenshot_data.session_id, db, timestamp=screenshot_data.timestamp)
    await db.commit()
    
    return {"status": "ok", "id": db_screenshot.id}

@app.get("/sessions/{session_id}", response_model=schemas.SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Session).where(models.Session.id == session_id))
    session = result.scalars().first()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # Check if session is active (no new events for 5+ mins means inactive)
    now = datetime.now(timezone.utc)
    last_event_tz = session.last_event_time
    if last_event_tz.tzinfo is None:
        last_event_tz = last_event_tz.replace(tzinfo=timezone.utc)
        
    is_active = (now - last_event_tz) < timedelta(minutes=5)
    
    return {
        "id": session.id,
        "start_time": session.start_time,
        "last_event_time": session.last_event_time,
        "is_active": is_active
    }


@app.get("/sessions/{session_id}/timeline")
async def get_timeline(session_id: str, db: AsyncSession = Depends(get_db)):
    """
    Return a merged timeline of events, screenshots, and their analysis
    for a given session, ordered by timestamp.
    """
    # Verify session exists
    sess_result = await db.execute(select(models.Session).where(models.Session.id == session_id))
    session = sess_result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch events
    events_result = await db.execute(
        select(models.Event)
        .where(models.Event.session_id == session_id)
        .order_by(models.Event.timestamp)
    )
    events = events_result.scalars().all()

    # Fetch screenshots with eagerly-loaded analysis
    screenshots_result = await db.execute(
        select(models.Screenshot)
        .options(selectinload(models.Screenshot.analysis))
        .where(models.Screenshot.session_id == session_id)
        .order_by(models.Screenshot.timestamp)
    )
    screenshots = screenshots_result.scalars().all()

    # Build timeline entries
    timeline: List[dict] = []

    for event in events:
        timeline.append({
            "type": "event",
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "data": {
                "id": event.id,
                "event_type": event.type,
                "url": event.url,
                "metadata": event.metadata_,
            },
        })

    for screenshot in screenshots:
        entry: dict[str, Any] = {
            "type": "screenshot",
            "timestamp": screenshot.timestamp.isoformat() if screenshot.timestamp else None,
            "data": {
                "id": screenshot.id,
                "tab_url": screenshot.tab_url,
                "file_path": screenshot.file_path,
            },
        }
        if screenshot.analysis:
            entry["analysis"] = {
                "label": screenshot.analysis.label,
                "description": screenshot.analysis.description,
                "category": screenshot.analysis.category,
                "confidence": screenshot.analysis.confidence,
                "model_used": screenshot.analysis.model_used,
                "analyzed_at": screenshot.analysis.analyzed_at.isoformat() if screenshot.analysis.analyzed_at else None,
            }
        timeline.append(entry)

    # Sort by timestamp
    def sort_key(item):
        ts = item.get("timestamp")
        if ts is None:
            return ""
        return ts

    timeline.sort(key=sort_key)

    return {
        "session_id": session_id,
        "is_active": (datetime.now(timezone.utc) - (session.last_event_time.replace(tzinfo=timezone.utc) if session.last_event_time.tzinfo is None else session.last_event_time)) < timedelta(minutes=5),
        "timeline": timeline,
    }
