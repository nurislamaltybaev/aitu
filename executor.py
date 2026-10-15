"""Runs approved actions with pyautogui. Slam the mouse into any screen corner to abort (FAILSAFE)."""
import logging
import time

import pyautogui
import pyperclip
from PIL import ImageChops, ImageStat

import screen_capture
from vision_client import Action

log = logging.getLogger("jarvis.executor")
pyautogui.FAILSAFE = True
DELAY = 0.2  # seconds between actions; raise it if apps miss clicks/keys
START_DELAY = 0.1  # before the first action: lets focus return to the app after a Jarvis window closes

# pyautogui looks letters up in the keyboard layout that is active when it is imported. Under a Russian or
# Kazakh layout "v" and "a" do not exist there, so Ctrl+V / Ctrl+A went out as a junk key and nothing was pasted.
# Virtual-key codes of Latin letters and digits are the same on every layout: pin them.
pyautogui.platformModule.keyboardMapping.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


def _keys(*keys: str) -> list[str]:
    bad = [k for k in keys if k.lower() not in pyautogui.KEYBOARD_KEYS]
    if bad:  # pyautogui silently ignores unknown keys; better to stop loudly
        raise ValueError(f"unknown key(s): {bad}")
    return [k.lower() for k in keys]


def _paste(text: str) -> None:
    """pyautogui.write can't type non-ASCII (Kazakh, Cyrillic, emoji), so paste via clipboard.
    pyperclip, not tkinter: Tk's clipboard segfaults the whole process on long non-ASCII text."""
    try:
        old = pyperclip.paste()
    except Exception:  # clipboard holds an image/files, or is locked: nothing text to restore
        old = None
    time.sleep(0.2)
    try:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3 + len(text) / 10000)  # the target app reads the clipboard async; long text needs longer
    finally:
        if old is not None:
            pyperclip.copy(old)


def _type(text: str) -> None:
    """Always pastes: pyautogui.write sends key presses, so with a Russian/Kazakh keyboard layout active even
    ASCII text comes out as the wrong letters. A failed type must not abort the run: log it, the next
    screenshot shows the model nothing was typed."""
    try:
        _paste(text)
    except pyautogui.FailSafeException:
        raise  # the user's emergency stop
    except Exception:
        log.exception("typing failed (%d chars), continuing", len(text))


def wait_stable(max_s: float, poll: float = 0.5, tol: float = 1.0) -> None:
    """Smart wait: sleep up to max_s, but return early once the screen has changed and then held still for
    two polls (new window loaded). If nothing ever changes it waits the full max_s, so it is never shorter
    than the old fixed pause when the UI is slow to start. ponytail: whole-screen diff, so a playing video or
    ticking clock keeps it waiting to max_s; pass a region to thumb() if that bites."""
    end = time.monotonic() + max_s
    prev, changed, still = screen_capture.thumb(), False, 0
    while (left := end - time.monotonic()) > 0:
        time.sleep(min(poll, left))
        cur = screen_capture.thumb()
        if ImageStat.Stat(ImageChops.difference(prev, cur)).mean[0] > tol:
            changed, still = True, 0
        else:
            still += 1
        prev = cur
        if changed and still >= 2:
            return


def _step(a: Action, scale: float) -> None:
    if a.x is not None:
        x, y = round(a.x * scale), round(a.y * scale)
        if not pyautogui.onScreen(x, y):
            raise ValueError(f"({x},{y}) is off screen")
    if a.action == "click":
        pyautogui.moveTo(x, y, duration=0.3)
        pyautogui.click(clicks=a.clicks, button=a.button)
    elif a.action == "type":
        _type(a.text)
    elif a.action == "press":
        pyautogui.press(*_keys(a.key))
    elif a.action == "hotkey":
        pyautogui.hotkey(*_keys(*a.keys))
    elif a.action == "scroll":
        if a.x is not None:
            pyautogui.moveTo(x, y, duration=0.3)
        pyautogui.scroll(a.amount * 100)  # ~one notch is 100 on Windows
    elif a.action == "wait":
        wait_stable(a.seconds)


def run(actions: list[Action], scale: float) -> None:
    time.sleep(START_DELAY)
    for i, a in enumerate(actions, 1):
        log.info("[%d/%d] %s", i, len(actions), a.model_dump(exclude_defaults=True))
        _step(a, scale)
        time.sleep(DELAY)
