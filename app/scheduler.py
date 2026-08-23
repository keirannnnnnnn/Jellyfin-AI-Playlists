import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.database import get_setting, DB_PATH
from app.services.generator_service import run_smart_playlists

logger = logging.getLogger("jellyfin_playlists.scheduler")

scheduler = AsyncIOScheduler()
JOB_ID = "daily_smart_playlists_sync"


async def scheduled_sync_job():
    """Job executed by APScheduler on schedule."""
    logger.info("Executing scheduled smart playlist generation job...")
    try:
        result = await run_smart_playlists(trigger="scheduled", db_file=DB_PATH)
        logger.info(f"Scheduled sync completed with status: {result.get('status')}")
    except Exception as e:
        logger.error(f"Scheduled sync encountered unhandled error: {e}", exc_info=True)


def update_scheduler_job():
    """Configure or reschedule the daily cron job based on DB settings."""
    hour_str = get_setting("schedule_hour", "2")
    minute_str = get_setting("schedule_minute", "0")
    enabled_str = get_setting("schedule_enabled", "true").lower()

    try:
        hour = int(hour_str)
        minute = int(minute_str)
    except (ValueError, TypeError):
        hour, minute = 2, 0

    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)

    if enabled_str in ("true", "1", "yes"):
        trigger = CronTrigger(hour=hour, minute=minute)
        scheduler.add_job(
            scheduled_sync_job,
            trigger=trigger,
            id=JOB_ID,
            name="Daily Smart Playlist Generator",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(f"Scheduled daily sync job for {hour:02d}:{minute:02d} daily.")
    else:
        logger.info("Daily sync job is disabled in settings.")


def start_scheduler():
    """Start the in-process AsyncIOScheduler."""
    if not scheduler.running:
        scheduler.start()
        update_scheduler_job()
        logger.info("APScheduler started successfully.")


def stop_scheduler():
    """Stop the scheduler on app shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")


def get_scheduler_status() -> dict:
    """Return current scheduler state and next run timestamp for the UI."""
    job = scheduler.get_job(JOB_ID)
    next_run = None
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()

    return {
        "running": scheduler.running,
        "job_enabled": get_setting("schedule_enabled", "true").lower() in ("true", "1", "yes"),
        "hour": int(get_setting("schedule_hour", "2")),
        "minute": int(get_setting("schedule_minute", "0")),
        "next_run_time": next_run,
    }
