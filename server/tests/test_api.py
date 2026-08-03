import pytest
import base64
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from main import app, get_db
from database import Base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Setup test database (in-memory sqlite)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

import pytest_asyncio

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
    assert session_res.json()["is_active"] == True

@pytest.mark.asyncio
async def test_create_screenshot(client: AsyncClient):
    # Dummy base64 image
    img_bytes = b"fakeimagecontent"
    img_b64 = base64.b64encode(img_bytes).decode('utf-8')
    
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
    
    # Check session was created
    session_res = await client.get("/sessions/test_session_2")
    assert session_res.status_code == 200
    assert session_res.json()["id"] == "test_session_2"
