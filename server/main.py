"""Jarvis planning server: holds the OpenRouter key so the desktop client needs none.

Run locally:   uvicorn server.main:app --port 8000          (from the project root)
Settings:      server/.env or the host's environment variables, see server/.env.example
"""
import base64
import binascii
import hmac
import logging
import os
import struct
import threading
import types
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))  # before planner reads its settings; real env vars win

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from server import planner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis.server")

# Only these models may be requested: the key is the server owner's, so a client must not pick a $100/M model.
ALLOWED_MODELS = [m.strip() for m in os.getenv(
    "JARVIS_ALLOWED_MODELS", "openai/gpt-6-luna,anthropic/claude-haiku-5.5,google/gemini-2.5-flash").split(",") if m.strip()]
# Optional shared secret. When set, clients must send it as X-Jarvis-Token. It is baked into the client exe,
# so it keeps out strangers who find the URL, not someone who has the exe: cap spending with an OpenRouter key limit.
CLIENT_TOKEN = os.getenv("JARVIS_CLIENT_TOKEN", "")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

app = FastAPI(title="Jarvis server")
# ponytail: one plan at a time per server process (planner keeps per-request usage in a module-level counter).
# Fine for a few users; give planner per-request usage objects if several people work at the same moment.
_lock = threading.Lock()


class PlanRequest(BaseModel):
    image_base64: str = Field(min_length=1, max_length=10_000_000)  # a 1280x720 PNG is ~0.5 MB in base64
    prompt: str = Field(min_length=1, max_length=4000)
    model: str
    memory: str = Field("", max_length=20_000)
    history: list[str] = Field(default_factory=list, max_length=200)


def png_size(png: bytes) -> tuple[int, int]:
    """Width and height from the PNG header; the prompt needs them for the coordinate system."""
    if png[:8] != PNG_MAGIC or len(png) < 24:
        raise ValueError("image is not a PNG")
    return struct.unpack(">II", png[16:24])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/plan")
def make_plan(req: PlanRequest, x_jarvis_token: str | None = Header(None)):
    if CLIENT_TOKEN and not hmac.compare_digest(x_jarvis_token or "", CLIENT_TOKEN):
        raise HTTPException(401, "invalid or missing client token")
    if req.model not in ALLOWED_MODELS:
        raise HTTPException(400, f"model {req.model!r} is not allowed on this server; use one of: {', '.join(ALLOWED_MODELS)}")
    try:
        png = base64.b64decode(req.image_base64, validate=True)
        width, height = png_size(png)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, f"bad screenshot: {e}")
    shot = types.SimpleNamespace(png=png, width=width, height=height)
    with _lock:
        planner.USAGE.update(requests=0, input=0, output=0, cost=0.0)
        try:
            p = planner.plan(req.prompt, shot, req.history, req.memory, model=req.model)
        except ValueError as e:  # the model kept answering with something that is not a valid plan
            log.warning("invalid plan from %s: %s", req.model, e)
            raise HTTPException(502, f"the model did not return a valid plan: {e}")
        except Exception as e:  # OpenRouter down, out of credits, unknown model id...
            log.exception("planning failed")
            raise HTTPException(502, f"model provider error: {type(e).__name__}: {e}")
        usage = dict(planner.USAGE)
    log.info("plan for %s: %d actions, done=%s, $%.4f", req.model, len(p.actions), p.done, usage["cost"])
    return {**p.model_dump(), "usage": usage}
