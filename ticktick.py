"""Background TickTick poller. Open tasks due today (or overdue) that carry the TAG tag are offered to Jarvis
as commands. Opt-in by tag on purpose: the tag is the "automatable" marker, no LLM guessing.
Needs TICKTICK_TOKEN (OAuth2 access token from developer.ticktick.com; it expires, refresh it manually)."""
import json
import logging
import os
import threading
import urllib.request
from datetime import date, datetime

log = logging.getLogger("jarvis.ticktick")
API = "https://api.ticktick.com/open/v1"
TAG = os.getenv("TICKTICK_TAG", "jarvis").lower()
INTERVAL = int(os.getenv("TICKTICK_INTERVAL", "600"))  # seconds between polls


def _get(path: str):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + os.environ["TICKTICK_TOKEN"].strip()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def is_candidate(t: dict, today: date) -> bool:
    if t.get("status", 0) != 0 or TAG not in [x.lower() for x in t.get("tags") or []] or not t.get("dueDate"):
        return False
    due = datetime.fromisoformat(t["dueDate"]).astimezone().date()  # handles ".000" millis and "+0000" (3.11+)
    return due <= today


def candidates() -> list[dict]:
    today = date.today()
    out = []
    for pid in ["inbox"] + [p["id"] for p in _get("/project")]:  # inbox is not in the project list
        out += [t for t in _get(f"/project/{pid}/data").get("tasks", []) if is_candidate(t, today)]
    return out


def command(t: dict) -> str:
    return t["title"] + (f"\n{t['content']}" if t.get("content") else "")


def start(submit) -> None:
    """submit(command) -> bool: True if Jarvis accepted it (False when busy: retried next poll)."""
    seen: set[str] = set()  # ponytail: in-memory, tasks re-offered after a restart; persist ids if that annoys

    def loop() -> None:
        while True:
            try:
                for t in candidates():
                    if t["id"] not in seen and submit(command(t)):
                        seen.add(t["id"])
                        break  # one task at a time
            except Exception as e:
                log.warning("TickTick poll failed: %s", e)
            stop.wait(INTERVAL)

    stop = threading.Event()
    threading.Thread(target=loop, daemon=True, name="ticktick").start()
    log.info("TickTick polling every %ds for tasks tagged %r", INTERVAL, TAG)
