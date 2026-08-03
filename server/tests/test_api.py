import base64
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base

# Setup test database (in-memory sqlite)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session


# Patch worker_loop so the lifespan doesn't spawn a real background worker
@pytest.fixture(autouse=True, scope="session")
def patch_worker():
    with patch("main.worker_loop", new_callable=AsyncMock):
        yield


# Must import app AFTER patching worker, but get_db is needed for override
from main import app, get_db

app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def prepare_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Ingestion tests (carried over from previous branch)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_events(client: AsyncClient):
    payload = {
        "events": [
            {
                "session_id": "test_session_1",
                "type": "click",
                "url": "http://example.com",
                "metadata": {"tagName": "button"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            {
                "session_id": "test_session_1",
                "type": "scroll",
                "url": "http://example.com",
                "metadata": {"tagName": "body"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }

    response = await client.post("/events", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["count"] == 2

    # Check session was created
    session_res = await client.get("/sessions/test_session_1")
    assert session_res.status_code == 200
    assert session_res.json()["id"] == "test_session_1"
    assert session_res.json()["is_active"] is True


@pytest.mark.asyncio
async def test_create_screenshot(client: AsyncClient):
    img_bytes = b"fakeimagecontent"
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    payload = {
        "session_id": "test_session_2",
        "tab_url": "http://example.com",
        "image_base64": f"data:image/png;base64,{img_b64}",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    response = await client.post("/screenshots", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "id" in data

    session_res = await client.get("/sessions/test_session_2")
    assert session_res.status_code == 200
    assert session_res.json()["id"] == "test_session_2"


# ---------------------------------------------------------------------------
# Timeline tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeline_merges_and_orders(client: AsyncClient):
    """Timeline should contain events and screenshots, sorted by timestamp."""
    session_id = "timeline_session"
    now = datetime.now(timezone.utc)

    # Insert 2 events at different times
    t1 = (now - timedelta(minutes=3)).isoformat()
    t2 = (now - timedelta(minutes=1)).isoformat()

    await client.post("/events", json={
        "events": [
            {"session_id": session_id, "type": "click", "url": "http://a.com",
             "metadata": {"tagName": "div"}, "timestamp": t1},
            {"session_id": session_id, "type": "scroll", "url": "http://a.com",
             "metadata": {"tagName": "body"}, "timestamp": t2},
        ]
    })

    # Insert a screenshot between the two event timestamps
    t_mid = (now - timedelta(minutes=2)).isoformat()
    img_b64 = base64.b64encode(b"pngdata").decode()
    await client.post("/screenshots", json={
        "session_id": session_id,
        "tab_url": "http://a.com",
        "image_base64": img_b64,
        "timestamp": t_mid,
    })

    # Fetch timeline
    resp = await client.get(f"/sessions/{session_id}/timeline")
    assert resp.status_code == 200
    timeline = resp.json()["timeline"]

    assert len(timeline) == 3
    # Verify order: t1 (event) < t_mid (screenshot) < t2 (event)
    assert timeline[0]["type"] == "event"
    assert timeline[1]["type"] == "screenshot"
    assert timeline[2]["type"] == "event"

    # Timestamps should be ascending
    timestamps = [item["timestamp"] for item in timeline]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_timeline_includes_analysis(client: AsyncClient):
    """If an analysis row exists for a screenshot, it shows up in the timeline."""
    import models
    session_id = "analysis_session"
    now = datetime.now(timezone.utc)

    # Create a screenshot via API
    img_b64 = base64.b64encode(b"pngdata").decode()
    resp = await client.post("/screenshots", json={
        "session_id": session_id,
        "tab_url": "http://b.com",
        "image_base64": img_b64,
        "timestamp": now.isoformat(),
    })
    screenshot_id = resp.json()["id"]

    # Manually insert an analysis row (simulating worker output)
    async with TestingSessionLocal() as db:
        analysis = models.ActivityAnalysis(
            id=str(uuid.uuid4()),
            screenshot_id=screenshot_id,
            label="Browsing documentation",
            description="User is reading Python docs",
            category="research",
            confidence=0.92,
            model_used="gemini-2.0-flash",
            analyzed_at=now,
        )
        db.add(analysis)
        await db.commit()

    # Fetch timeline
    resp = await client.get(f"/sessions/{session_id}/timeline")
    assert resp.status_code == 200
    timeline = resp.json()["timeline"]

    assert len(timeline) == 1
    entry = timeline[0]
    assert entry["type"] == "screenshot"
    assert "analysis" in entry
    assert entry["analysis"]["label"] == "Browsing documentation"
    assert entry["analysis"]["category"] == "research"
    assert entry["analysis"]["confidence"] == 0.92


@pytest.mark.asyncio
async def test_timeline_404_for_unknown_session(client: AsyncClient):
    resp = await client.get("/sessions/nonexistent/timeline")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Worker tests (mocked analyzer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_processes_unanalyzed_screenshot():
    """
    The worker should pick up a screenshot without an analysis row,
    call analyze(), and write the result to the DB.
    """
    import os
    import tempfile

    import models
    from worker import process_one_screenshot

    session_id = "worker_session"
    now = datetime.now(timezone.utc)

    # Create session + screenshot manually in DB
    async with TestingSessionLocal() as db:
        session = models.Session(id=session_id, start_time=now, last_event_time=now)
        db.add(session)
        await db.commit()

    # Write a fake image file
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"fake png bytes")
    tmp.close()

    screenshot_id = str(uuid.uuid4())
    async with TestingSessionLocal() as db:
        screenshot = models.Screenshot(
            id=screenshot_id,
            session_id=session_id,
            file_path=tmp.name,
            tab_url="http://test.com",
            timestamp=now,
        )
        db.add(screenshot)
        await db.commit()

    # Patch worker's session maker to use our test DB
    import worker
    original_maker = worker.async_session_maker
    worker.async_session_maker = TestingSessionLocal

    try:
        # Mock analyzer
        mock_analyzer = AsyncMock()
        mock_analyzer.model_name = "gemini-2.0-flash-mock"
        mock_analyzer.analyze.return_value = {
            "label": "Writing code",
            "description": "User is editing a Python file in VS Code",
            "category": "coding",
            "confidence": 0.88,
        }

        # Re-fetch screenshot object within a session so file_path is accessible
        async with TestingSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(models.Screenshot).where(models.Screenshot.id == screenshot_id)
            )
            sc = result.scalars().first()

        success = await process_one_screenshot(sc, mock_analyzer)
        assert success is True
        mock_analyzer.analyze.assert_awaited_once()

        # Verify analysis was written
        async with TestingSessionLocal() as db:
            result = await db.execute(
                select(models.ActivityAnalysis).where(
                    models.ActivityAnalysis.screenshot_id == screenshot_id
                )
            )
            analysis = result.scalars().first()
            assert analysis is not None
            assert analysis.label == "Writing code"
            assert analysis.category == "coding"
            assert analysis.confidence == 0.88
            assert analysis.model_used == "gemini-2.0-flash-mock"

    finally:
        worker.async_session_maker = original_maker
        os.unlink(tmp.name)
