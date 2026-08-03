# Visual AI Agent

A Chrome MV3 extension + FastAPI backend that monitors browser activity,
captures periodic screenshots, and uses Google Gemini's vision API to
automatically label what the user is doing — producing a structured,
queryable timeline of activity.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Chrome Extension (MV3)                                             │
│                                                                     │
│  ┌──────────┐  ┌────────────┐  ┌───────────────┐  ┌─────────────┐ │
│  │ popup/   │  │ options/   │  │ content.js    │  │background.js│ │
│  │ toggle,  │  │ backend    │  │ click/scroll/ │  │ tab events, │ │
│  │ status   │  │ URL, interval│ │ keydown counts│  │ screenshots,│ │
│  │          │  │ blocklist  │  │ (no values!)  │  │ batch POST  │ │
│  └──────────┘  └────────────┘  └───────┬───────┘  └──────┬──────┘ │
│                                        │    messages      │        │
│                                        └──────────────────┘        │
└─────────────────────────────────────────────┬───────────────────────┘
                                              │ HTTP (POST /events,
                                              │        POST /screenshots)
                                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  FastAPI Backend (server/)                                          │
│                                                                     │
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────────┐ │
│  │ POST /events   │  │POST /screenshots │  │GET /sessions/{id}/  │ │
│  │ batch insert   │  │ save PNG to disk, │  │    timeline         │ │
│  │                │  │ insert row        │  │ merged events +     │ │
│  │                │  │                   │  │ screenshots +       │ │
│  │                │  │                   │  │ analysis, by time   │ │
│  └────────┬───────┘  └────────┬──────────┘  └─────────────────────┘ │
│           │                   │                                     │
│           ▼                   ▼                                     │
│  ┌──────────────────────────────────────┐                           │
│  │           PostgreSQL                 │                           │
│  │  sessions | events | screenshots    │                           │
│  │           | activity_analysis       │                           │
│  └──────────────────────────────────────┘                           │
│                                                                     │
│  ┌──────────────────────────────────────┐       ┌─────────────────┐│
│  │  Async Worker (asyncio background)   │──────▶│  Gemini API     ││
│  │  polls unanalyzed screenshots,       │◀──────│  (vision input) ││
│  │  writes activity_analysis rows       │       │  gemini-2.0-flash│
│  └──────────────────────────────────────┘       └─────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

## Setup

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Google Chrome
- A Google Gemini API key ([get one here](https://aistudio.google.com/apikey))

### Backend

```bash
# Clone the repo
git clone https://github.com/bharath-avl/neoflo.git
cd neoflo

# Set your Gemini API key
export GEMINI_API_KEY="your-key-here"

# Start Postgres + API via Docker Compose
docker-compose up --build

# The API will be available at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

**Without Docker** (local development):

```bash
cd server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start Postgres separately, then:
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/dbname"
export GEMINI_API_KEY="your-key-here"
uvicorn main:app --reload
```

### Chrome Extension

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `extension/` directory from this repo
5. The extension icon will appear in the toolbar
6. Click the icon → **Start Monitoring**
7. Open **Preferences** to configure:
   - Backend URL (default: `http://localhost:8000`)
   - Screenshot interval (default: 30 seconds)
   - Domain blocklist

### Running Tests

```bash
cd server
source venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest tests/ -v
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes (for analysis) | — | Google Gemini API key for vision analysis |
| `GEMINI_MODEL` | No | `gemini-2.5-flash-lite` | Gemini model to use. Google periodically retires models; update this if the default gets retired. See [supported models](https://ai.google.dev/gemini-api/docs/changelog). |
| `DATABASE_URL` | Yes (production) | `sqlite+aiosqlite:///./test.db` | Async database URL |

## Privacy & Consent Design

This tool is designed for **personal productivity tracking** with strong
privacy-by-default constraints:

### What IS tracked

- **Tab events**: which tab was activated, navigated to, or created (URL and
  title only)
- **Interaction counts**: that a click, scroll, or keydown occurred, and on
  which HTML element type (e.g., `<button>`, `<input>`)
- **Periodic screenshots**: a PNG of the visible tab, captured at a
  configurable interval (default 30s)

### What is NEVER tracked

- **Keystroke content**: we log that a `keydown` happened on an `<input>`, but
  never the key pressed or the field's value
- **Form input values**: no `value`, `textContent`, or `innerHTML` is ever read
- **Passwords**: no special handling needed because input values are never
  captured at all
- **Blocklisted domains**: if a domain appears on the user's blocklist, no
  events are logged and no screenshots are taken — the extension acts as if
  monitoring is off for that tab

### User controls

- **Pause toggle**: the popup provides a single-click toggle to pause/resume
  all monitoring. When paused, no events are logged, no screenshots are
  captured, and no data is sent to the backend
- **Domain blocklist**: configured in the options page, one domain per line.
  Matching is exact-domain or subdomain (e.g., `bank.com` blocks
  `bank.com` and `www.bank.com` but not `mobank.com`)
- **Configurable interval**: screenshot frequency is adjustable from 10s to 120s
- **Local-only backend**: all data stays on `localhost` by default. The backend
  URL is user-configurable but defaults to `http://localhost:8000`

### Session lifecycle

- A session starts when the user clicks **Start Monitoring**
- A session ends when the user clicks **Stop Monitoring** or closes the browser
- Server-side staleness: if no events arrive for 5+ minutes, the session is
  treated as inactive when queried (MV3 service workers can die silently)

## Sample Timeline Output

`GET /sessions/abc123def456/timeline`

```json
{
  "session_id": "abc123def456",
  "is_active": true,
  "timeline": [
    {
      "type": "event",
      "timestamp": "2026-08-03T10:00:01.000000+00:00",
      "data": {
        "id": "evt-001",
        "event_type": "navigation",
        "url": "https://github.com/bharath-avl/neoflo",
        "metadata": { "title": "bharath-avl/neoflo: Visual AI Agent" }
      }
    },
    {
      "type": "event",
      "timestamp": "2026-08-03T10:00:05.000000+00:00",
      "data": {
        "id": "evt-002",
        "event_type": "click",
        "url": "https://github.com/bharath-avl/neoflo",
        "metadata": { "tagName": "a" }
      }
    },
    {
      "type": "screenshot",
      "timestamp": "2026-08-03T10:00:30.000000+00:00",
      "data": {
        "id": "scr-001",
        "tab_url": "https://github.com/bharath-avl/neoflo",
        "file_path": "server/storage/abc123_1722675630_a1b2c3d4.png"
      },
      "analysis": {
        "label": "Browsing a GitHub repository",
        "description": "User is viewing the main page of a GitHub repository, reviewing the README and file structure.",
        "category": "coding",
        "confidence": 0.94,
        "model_used": "gemini-2.0-flash",
        "analyzed_at": "2026-08-03T10:00:45.000000+00:00"
      }
    },
    {
      "type": "event",
      "timestamp": "2026-08-03T10:00:50.000000+00:00",
      "data": {
        "id": "evt-003",
        "event_type": "scroll",
        "url": "https://github.com/bharath-avl/neoflo",
        "metadata": { "tagName": "html" }
      }
    },
    {
      "type": "screenshot",
      "timestamp": "2026-08-03T10:01:00.000000+00:00",
      "data": {
        "id": "scr-002",
        "tab_url": "https://github.com/bharath-avl/neoflo/blob/main/server/main.py",
        "file_path": "server/storage/abc123_1722675660_e5f6g7h8.png"
      },
      "analysis": {
        "label": "Reading Python source code",
        "description": "User is viewing a FastAPI application file on GitHub, scrolling through endpoint definitions.",
        "category": "coding",
        "confidence": 0.91,
        "model_used": "gemini-2.0-flash",
        "analyzed_at": "2026-08-03T10:01:15.000000+00:00"
      }
    }
  ]
}
```

## License

MIT
