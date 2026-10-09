"""Screen capture and change detection for maple-pq-autoclicker (pure ctypes).

Grabs a screen region with GDI BitBlt (the same method mss uses, without the
dependency) and measures how much of it changed compared with a snapshot.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import threading
import time

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
MIN_REGION_SIZE = 5  # pixels
CHANGE_PIXELS = 10   # this many pixels must differ from the snapshot...
CONFIRM_FRAMES = 2   # ...in this many frames in a row (ignores 1-frame flicker)
WATCH_INTERVAL = 0.003  # pause between captures (each capture takes a few ms)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


def region_is_valid(region):
    return (isinstance(region, dict)
            and all(isinstance(region.get(k), int) for k in ("left", "top", "width", "height"))
            and region["width"] >= MIN_REGION_SIZE and region["height"] >= MIN_REGION_SIZE)


def virtual_screen():
    g = user32.GetSystemMetrics  # SM_X/YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN
    return {"left": g(76), "top": g(77), "width": g(78), "height": g(79)}


class Frame:
    """A captured image: top-down BGRA bytes."""
    __slots__ = ("width", "height", "bgra")

    def __init__(self, width, height, bgra):
        self.width, self.height, self.bgra = width, height, bgra

    def ppm(self):
        """PPM (P6) bytes, which Tk's PhotoImage reads without Pillow."""
        rgb = bytearray(self.width * self.height * 3)
        rgb[0::3] = self.bgra[2::4]
        rgb[1::3] = self.bgra[1::4]
        rgb[2::3] = self.bgra[0::4]
        return b"P6 %d %d 255 " % (self.width, self.height) + bytes(rgb)

    def is_blank(self):
        """True when every pixel is the same colour (e.g. a black capture of
        a game in exclusive fullscreen)."""
        n = len(self.bgra) // 4
        return all(self.bgra[c::4].count(self.bgra[c]) == n for c in range(3))


def save_frame(frame, path):
    """Save as a .ppm image (opens in most image viewers, e.g. GIMP/IrfanView)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(frame.ppm())
    os.replace(tmp, path)


def load_frame(path):
    """Load a .ppm saved by save_frame. Returns None if missing or damaged."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    rgb = data[m.end():]
    if w < 1 or h < 1 or len(rgb) != w * h * 3:
        return None
    bgra = bytearray(b"\xff" * (w * h * 4))
    bgra[0::4] = rgb[2::3]
    bgra[1::4] = rgb[1::3]
    bgra[2::4] = rgb[0::3]
    return Frame(w, h, bytes(bgra))


class Capturer:
    """Reusable GDI capture. Create one per thread."""

    def __init__(self):
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.bitmap = None
        self.size = None
        self.buf = None
        self.bmi = BITMAPINFO()

    def _ensure(self, w, h):
        if self.size == (w, h):
            return
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
        self.bitmap = gdi32.CreateCompatibleBitmap(self.screen_dc, w, h)
        gdi32.SelectObject(self.mem_dc, self.bitmap)
        hdr = self.bmi.bmiHeader
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth, hdr.biHeight = w, -h  # negative height = top-down rows
        hdr.biPlanes, hdr.biBitCount, hdr.biCompression = 1, 32, BI_RGB
        self.buf = ctypes.create_string_buffer(w * h * 4)
        self.size = (w, h)

    def grab(self, region):
        w, h = region["width"], region["height"]
        self._ensure(w, h)
        if not gdi32.BitBlt(self.mem_dc, 0, 0, w, h, self.screen_dc,
                            region["left"], region["top"], SRCCOPY):
            raise OSError("screen capture failed (BitBlt)")
        if gdi32.GetDIBits(self.mem_dc, self.bitmap, 0, h, self.buf,
                           ctypes.byref(self.bmi), DIB_RGB_COLORS) != h:
            raise OSError("screen capture failed (GetDIBits)")
        return Frame(w, h, self.buf.raw)

    def close(self):
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.mem_dc:
            gdi32.DeleteDC(self.mem_dc)
            self.mem_dc = None
        if self.screen_dc:
            user32.ReleaseDC(None, self.screen_dc)
            self.screen_dc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# Colour tolerance. Each channel is bucketed in steps of 16 on two grids
# offset by 8; a pixel only counts as changed when it lands in a different
# bucket on both grids. So a channel shift under 8 (of 255) is always
# ignored, 16+ is always counted, 8-15 depends on the exact values. That
# ignores faint shading (e.g. a window shadow that darkens when its window
# has focus) while text appearing or changing is far above it.
_GRID_A = bytes(v >> 4 for v in range(256))
_GRID_B = bytes((v + 8) >> 4 for v in range(256))
_NONZERO = bytes([0]) + bytes([1]) * 255


def _changed_bytes(a, b, grid, n):
    """One flag per channel byte: 1 where that channel changed bucket on this grid."""
    qa = int.from_bytes(a.translate(grid), "little")
    qb = int.from_bytes(b.translate(grid), "little")
    return int.from_bytes((qa ^ qb).to_bytes(n, "little").translate(_NONZERO), "little")


def changed_pixels(a, b):
    """Number of pixels whose colour clearly differs between two frames
    (faint shifts under 8/255 per channel are ignored, see above).

    Done with byte tables and big-integer XOR/AND so it runs at C speed
    (about a millisecond for a dialog-sized region), not a loop per pixel.
    """
    if a.bgra == b.bgra:
        return 0
    if (a.width, a.height) != (b.width, b.height):
        return a.width * a.height
    n = len(a.bgra)
    # per channel: changed on both grids; then per pixel: any of B, G, R
    both = (_changed_bytes(a.bgra, b.bgra, _GRID_A, n)
            & _changed_bytes(a.bgra, b.bgra, _GRID_B, n)).to_bytes(n, "little")
    any_ch = (int.from_bytes(both[0::4], "little") | int.from_bytes(both[1::4], "little")
              | int.from_bytes(both[2::4], "little"))  # alpha is ignored
    return (n // 4) - any_ch.to_bytes(n // 4, "little").count(0)


def matches(frame, snapshot, change_pixels=CHANGE_PIXELS):
    """True when the frame looks like the snapshot (fewer than change_pixels differ)."""
    return changed_pixels(frame, snapshot) < change_pixels


class Watcher:
    """Background thread: compares the region with `baseline` over and over
    and calls on_change(frame, changed) once it differs (or on_change(None, msg)
    if capturing fails, so the clicker never runs unwatched). Stops after
    firing once."""

    def __init__(self, region, baseline, on_change, change_pixels=CHANGE_PIXELS,
                 confirm=CONFIRM_FRAMES, interval=WATCH_INTERVAL, capturer=Capturer):
        self.region = dict(region)
        self.baseline = baseline
        self.on_change = on_change
        self.change_pixels = change_pixels
        self.confirm = confirm
        self.interval = interval
        self.capturer = capturer
        self.stop_event = threading.Event()
        self.latest = baseline  # most recent frame, for the preview
        self.checks = 0
        self.fired = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    @property
    def running(self):
        return self.thread.is_alive()

    def _fire(self, frame, info):
        self.fired = True
        self.stop_event.set()
        self.on_change(frame, info)

    def _run(self):
        hits = 0
        try:
            with self.capturer() as cap:
                while not self.stop_event.is_set():
                    frame = cap.grab(self.region)
                    self.latest = frame
                    self.checks += 1
                    n = changed_pixels(frame, self.baseline)
                    hits = hits + 1 if n >= self.change_pixels else 0
                    if hits >= self.confirm:
                        self._fire(frame, n)
                        return
                    self.stop_event.wait(self.interval)
        except Exception as e:  # can't see the region: stop rather than click blind
            if not self.stop_event.is_set():
                self._fire(None, f"screen capture failed: {e}")
