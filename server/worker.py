"""
Async background worker that processes unanalyzed screenshots.

Polls for screenshots without an activity_analysis row, calls
vision_analyzer.analyze(), and stores the result.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

import models
from database import async_session_maker
from vision_analyzer import get_analyzer

logger = logging.getLogger(__name__)

# Configurable via environment or direct override
POLL_INTERVAL_SECONDS = 15
MAX_RETRY_DELAY = 300  # 5 minutes cap


async def process_one_screenshot(screenshot: models.Screenshot, analyzer) -> bool:
    """
    Analyze a single screenshot and write the result to the DB.

    Returns True on success, False on failure.
    """
    try:
        with open(screenshot.file_path, "rb") as f:
            image_bytes = f.read()
    except FileNotFoundError:
        logger.error("Screenshot file not found: %s (id=%s)", screenshot.file_path, screenshot.id)
        return False

    result = await analyzer.analyze(image_bytes)

    async with async_session_maker() as db:
        analysis = models.ActivityAnalysis(
            screenshot_id=screenshot.id,
            label=result["label"],
            description=result["description"],
            category=result["category"],
            confidence=result["confidence"],
            model_used=getattr(analyzer, "model_name", "unknown"),
            analyzed_at=datetime.now(timezone.utc),
        )
        db.add(analysis)
        await db.commit()

    logger.info("Analyzed screenshot %s -> label=%s", screenshot.id, result["label"])
    return True


async def get_unanalyzed_screenshots() -> list[models.Screenshot]:
    """Fetch screenshots that have no corresponding activity_analysis row."""
    async with async_session_maker() as db:
        stmt = (
            select(models.Screenshot)
            .outerjoin(models.ActivityAnalysis)
            .where(models.ActivityAnalysis.id.is_(None))
            .limit(10)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def worker_loop(analyzer=None):
    """
    Main worker loop. Polls for unanalyzed screenshots and processes them.
    Runs indefinitely until cancelled.
    """
    if analyzer is None:
        analyzer = get_analyzer()

    consecutive_errors = 0

    while True:
        try:
            screenshots = await get_unanalyzed_screenshots()

            if not screenshots:
                consecutive_errors = 0
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            for screenshot in screenshots:
                try:
                    success = await process_one_screenshot(screenshot, analyzer)
                    if success:
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1
                except Exception as e:
                    consecutive_errors += 1
                    delay = min(2 ** consecutive_errors, MAX_RETRY_DELAY)
                    logger.error(
                        "Error analyzing screenshot %s: %s. Retrying in %ds.",
                        screenshot.id, e, delay,
                    )
                    await asyncio.sleep(delay)

        except asyncio.CancelledError:
            logger.info("Worker loop cancelled, shutting down.")
            return
        except Exception as e:
            consecutive_errors += 1
            delay = min(2 ** consecutive_errors, MAX_RETRY_DELAY)
            logger.error("Worker loop error: %s. Retrying in %ds.", e, delay)
            await asyncio.sleep(delay)
