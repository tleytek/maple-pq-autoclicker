"""Screen capture and change detection for maple-pq-autoclicker (pure ctypes).

Grabs a screen region with GDI BitBlt (the same method mss uses, without the
dependency) and measures how much of it changed compared with a snapshot.
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
MIN_REGION_SIZE = 5  # pixels


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


def changed_pixels(a, b):
    """Number of pixels whose colour differs between two same-size frames.

    Done with big-integer XOR/OR so it runs at C speed (well under 1 ms for a
    typical dialog-sized region) instead of a Python loop per pixel.
    """
    if a.bgra == b.bgra:
        return 0
    if (a.width, a.height) != (b.width, b.height):
        return a.width * a.height
    n = len(a.bgra)
    diff = (int.from_bytes(a.bgra, "little") ^ int.from_bytes(b.bgra, "little")).to_bytes(n, "little")
    # OR the B, G and R differences together; alpha is ignored.
    any_ch = (int.from_bytes(diff[0::4], "little") | int.from_bytes(diff[1::4], "little")
              | int.from_bytes(diff[2::4], "little"))
    return (n // 4) - any_ch.to_bytes(n // 4, "little").count(0)
