"""Client side of planning: sends the screenshot and the command to the Jarvis server, gets a validated Plan back.
No API key lives on this side; the server holds it. The schema (Action, Plan, parse) is shared with the server."""
import base64
import functools
import logging
import os
import sys
import time

import httpx

import config  # noqa: F401  an optional .env next to the exe can still override the defaults below
from server.planner import Action, Plan, parse  # noqa: F401  re-exported for executor, gui and main

log = logging.getLogger("jarvis.vision")

# Baked into the exe: set it before running build.bat. Override at run time with `--server URL` or JARVIS_SERVER.
DEFAULT_SERVER = "https://your-jarvis-server.onrender.com"
# Must equal JARVIS_CLIENT_TOKEN on the server if the server sets one. JARVIS_TOKEN overrides it at run time.
CLIENT_TOKEN = ""
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"  # preselected in the start-up model picker
TIMEOUT = httpx.Timeout(120, connect=20)  # a sleeping free-tier server needs up to ~1 min to wake; a plan 5-30 s
RETRY_STATUS = {502, 503, 504}  # host/proxy hiccups; the server has already retried OpenRouter itself
ATTEMPTS = 2
USAGE = {"requests": 0, "input": 0, "output": 0, "cost": 0.0}  # running totals; main resets them for each command


class ServerError(RuntimeError):
    """Plain-language reason no plan came back; main shows it to the user as is."""


def _arg(name: str) -> str:
    """Value of `--name VALUE` or `--name=VALUE` on the command line, or ""."""
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return ""


def server_url() -> str:
    return (_arg("--server") or os.getenv("JARVIS_SERVER") or DEFAULT_SERVER).strip().rstrip("/")


def is_configured() -> bool:
    return "your-jarvis-server" not in server_url()


def current_model() -> str:
    return os.getenv("JARVIS_MODEL") or DEFAULT_MODEL


@functools.lru_cache(maxsize=1)
def _http() -> httpx.Client:
    """One client for the whole run: its pool keeps the TLS connection to the server alive between steps."""
    token = os.getenv("JARVIS_TOKEN") or CLIENT_TOKEN
    return httpx.Client(base_url=server_url(), timeout=TIMEOUT, headers={"X-Jarvis-Token": token} if token else {})


def warm_up() -> None:
    """Pings the server in the background at start-up, so a sleeping free instance is awake by the first command."""
    try:
        r = _http().get("/health")
        log.info("Server %s: HTTP %s", server_url(), r.status_code)
    except Exception as e:
        log.warning("Server %s not reachable yet: %s", server_url(), e)


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))[:500]
    except Exception:
        return r.text[:500]


def plan(prompt: str, shot, history: list[str] = (), memory: str = "") -> Plan:
    """history = the turns already executed (oldest first); memory = the model's notes from earlier turns."""
    url, model = server_url(), current_model()
    body = {"image_base64": base64.b64encode(shot.png).decode(), "prompt": prompt, "model": model,
            "memory": memory, "history": list(history)}
    log.info("Asking %s (%s)...", url, model)
    err = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = _http().post("/api/plan", json=body)
        except httpx.TimeoutException:
            err = ServerError(f"The Jarvis server at {url} did not answer in time. "
                              "A free server may still be waking up: try again in a minute.")
        except httpx.TransportError as e:
            err = ServerError(f"Cannot reach the Jarvis server at {url} ({type(e).__name__}). "
                              "Check your internet connection.")
        else:
            if r.status_code in RETRY_STATUS:
                err = ServerError(f"The Jarvis server is unavailable right now (HTTP {r.status_code}): {_detail(r)}")
            elif r.is_error:  # 4xx and other 5xx: retrying the same request will not help
                raise ServerError(f"The Jarvis server refused the request (HTTP {r.status_code}): {_detail(r)}")
            else:
                data = r.json()
                u = data.get("usage") or {}
                for k in USAGE:
                    USAGE[k] += u.get(k) or 0
                return Plan.model_validate(data)
        if attempt < ATTEMPTS:
            log.warning("%s; retrying in %d s", err, 2 * attempt)
            time.sleep(2 * attempt)
    raise err
