"""Primary-monitor screenshot, downscaled so the LLM's coordinates map back to real pixels."""
import ctypes
import io
from typing import NamedTuple

import mss
from PIL import Image, ImageDraw, ImageFont

try:  # must run before any window/pyautogui call, else Windows display scaling skews coordinates
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

MAX_SIDE = 1280  # smaller image = fewer image tokens and bytes per step; scale maps LLM px back to real px
GRID = 100  # grid step in LLM-space pixels


class Shot(NamedTuple):
    png: bytes
    width: int  # size of the image the LLM sees
    height: int
    scale: float  # real screen px = LLM px * scale


def draw_grid(img: Image.Image) -> Image.Image:
    """Semi-transparent GRID x GRID px lines, each intersection labelled "x,y" (LLM-space pixels)."""
    try:
        font = ImageFont.load_default(size=10)
    except TypeError:  # Pillow < 10.1 has no sized default font
        font = ImageFont.load_default()
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(0, img.width, GRID):
        d.line([(x, 0), (x, img.height)], fill=(255, 0, 0, 90))
    for y in range(0, img.height, GRID):
        d.line([(0, y), (img.width, y)], fill=(255, 0, 0, 90))
    for x in range(0, img.width, GRID):
        for y in range(0, img.height, GRID):
            label = f"{x},{y}"
            box = d.textbbox((x + 2, y + 1), label, font=font)
            d.rectangle(box, fill=(255, 255, 255, 150))
            d.text((x + 2, y + 1), label, fill=(200, 0, 0, 255), font=font)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def capture() -> Shot:
    with mss.mss() as sct:  # per-call instance: mss handles are thread-bound
        raw = sct.grab(sct.monitors[1])  # primary monitor
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    w, h = img.size
    img = draw_grid(img)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Shot(buf.getvalue(), w, h, raw.width / w)


def thumb(size: tuple[int, int] = (64, 36)) -> Image.Image:
    """Tiny grayscale snapshot of the primary monitor, only for cheap 'did the screen change?' comparisons."""
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX").convert("L").resize(size, Image.BILINEAR)
