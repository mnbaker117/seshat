"""Log viewer endpoint — serves recent application logs."""
from fastapi import APIRouter, Query

from app.discovery.log_buffer import get_log_lines

router = APIRouter(prefix="/api/discovery", tags=["logs"])


@router.get("/logs")
async def get_logs(lines: int = Query(500, ge=1, le=5000)):
    """Return the last N log lines from the in-memory ring buffer."""
    return {"lines": get_log_lines(lines)}
