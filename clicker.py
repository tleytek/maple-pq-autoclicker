"""Input engine for maple-pq-autoclicker (pure ctypes, no UI).

Spams one keyboard key (the game's "interact" key) and a left click at a
saved screen position, as fast as the interval setting allows.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.windll.user32

VK_F1, VK_F2, VK_F3, VK_F4 = 0x70, 0x71, 0x72, 0x73
VK_ESCAPE = 0x1B
HOTKEYS = {VK_F1: "F1", VK_F2: "F2", VK_F3: "F3", VK_F4: "F4"}

MIN_GAP = 0.01   # shortest pause allowed between sequences: 10 ms
MAX_GAP = 2.0
SPEED_STEP = 1.5  # F3 / F4 change the interval by this factor
DEFAULT_SETTINGS = {
    "interact_vk": None,   # virtual-key code of the interact key
    "click": None,         # [x, y] screen pixel to left-click
    "gap": 0.05,           # pause after each interact/click/interact sequence
    "hold": 0.015,         # seconds each key / button is held down, and the
                           # gap between steps (so the game sees separate presses)
}

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOVE, ABSOLUTE, VIRTUALDESK = 0x0001, 0x8000, 0x4000
LEFT_DOWN, LEFT_UP = 0x0002, 0x0004
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
# Keys whose scan code needs the E0 (extended) prefix.
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PgUp..Down arrow
                0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90,       # Ins Del Win Apps / NumLk
                0xA3, 0xA5}                                     # RCtrl RAlt

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetKeyNameTextW.argtypes = [wt.LONG, wt.LPWSTR, ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def set_dpi_aware():
    """Use real screen pixels on scaled (125%/150%) displays. Call first."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()


GAME_TITLE = "MapleStory"
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND


def is_admin():
    """True when this program runs as administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def process_elevated(pid):
    """True/False whether a process runs as administrator; None if unknown."""
    ph = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not ph:
        return None
    try:
        tok = wt.HANDLE()
        if not advapi32.OpenProcessToken(ph, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            return None
        try:
            elevation, size = wt.DWORD(), wt.DWORD()
            if not advapi32.GetTokenInformation(tok, 20, ctypes.byref(elevation), 4,
                                                ctypes.byref(size)):  # TokenElevation
                return None
            return bool(elevation.value)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(ph)


def game_blocks_input(title=GAME_TITLE):
    """True when the game window is open, runs as administrator, and this
    program doesn't: Windows then silently drops all input we send to it."""
    if is_admin():
        return False
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return process_elevated(pid.value) is True


def get_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def key_name(vk):
    """Human-readable name for a virtual-key code, e.g. 'Space', 'Z'."""
    if vk is None:
        return "—"
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam = (scan << 16) | ((1 << 24) if vk in EXTENDED_VKS else 0)
    buf = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW(lparam, buf, 64):
        return buf.value.title() if len(buf.value) > 1 else buf.value.upper()
    return f"key 0x{vk:02X}"


# --------------------------------------------------------------------------- #
# Building inputs (pure functions, testable without sending anything)
# --------------------------------------------------------------------------- #
def _virtual_desktop():
    g = user32.GetSystemMetrics  # SM_X/YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN
    return g(76), g(77), g(78), g(79)


def to_abs(x, y, desktop=None):
    """Pixel -> 0..65535 absolute coords that land on exactly that pixel."""
    left, top, w, h = desktop or _virtual_desktop()
    # Aim at the pixel centre so rounding can't push it to a neighbour.
    nx = ((x - left) * 65536 + 32768) // w
    ny = ((y - top) * 65536 + 32768) // h
    return min(max(nx, 0), 65535), min(max(ny, 0), 65535)


def key_input(vk, down):
    """Keyboard INPUT sent as a hardware scan code (games often ignore VK-only input)."""
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    return inp


def move_input(x, y):
    nx, ny = to_abs(x, y)
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(nx, ny, 0, MOVE | ABSOLUTE | VIRTUALDESK, 0, 0)
    return inp


def button_input(down):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, 0, LEFT_DOWN if down else LEFT_UP, 0, 0)
    return inp


def send(*inputs):
    """Send inputs as one atomic batch."""
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def wait_until(deadline, stop):
    """Precise wait: sleep most of it, spin the last ~1.5 ms. False if stopped."""
    while True:
        if stop.is_set():
            return False
        left = deadline - time.perf_counter()
        if left <= 0:
            return True
        if left > 0.002:
            time.sleep(min(left - 0.0015, 0.01))


def faster(gap):
    return round(max(gap / SPEED_STEP, MIN_GAP), 4)


def slower(gap):
    return round(min(gap * SPEED_STEP, MAX_GAP), 4)


# --------------------------------------------------------------------------- #
# Spammer
# --------------------------------------------------------------------------- #
# One cycle: these steps in order, then a pause of settings["gap"].
SEQUENCE = ("interact", "click", "interact")


class Spammer:
    """Background thread: repeats SEQUENCE (interact key, left click on the
    saved spot, interact key), pausing `gap` seconds after each one, until
    stopped. Reads `settings` every cycle, so pause / position changes apply
    while it runs."""

    def __init__(self, settings, send_fn=None):
        self.settings = settings
        self.send = send_fn or send
        self.stop_event = threading.Event()
        self.thread = None
        self.cycles = 0

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.cycles = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(1)

    def _release(self, vk):
        self.send(key_input(vk, False), button_input(False))

    def _step(self, step, s):
        """Press and release one step. False if stopped during the hold."""
        if step == "interact":
            vk = s["interact_vk"]
            self.send(key_input(vk, True))
            ok = wait_until(time.perf_counter() + s["hold"], self.stop_event)
            if ok:
                self.send(key_input(vk, False))
        else:
            x, y = s["click"]
            self.send(move_input(x, y), button_input(True))  # move + press, atomic
            ok = wait_until(time.perf_counter() + s["hold"], self.stop_event)
            if ok:
                self.send(button_input(False))
        return ok

    def _run(self):
        vk = self.settings["interact_vk"]
        try:
            while not self.stop_event.is_set():
                s = self.settings
                vk = s["interact_vk"]
                for i, step in enumerate(SEQUENCE):
                    if i and not wait_until(time.perf_counter() + s["hold"], self.stop_event):
                        break  # short gap between steps
                    if not self._step(step, s):
                        break
                if self.stop_event.is_set():
                    break
                self.cycles += 1
                if not wait_until(time.perf_counter() + s["gap"], self.stop_event):
                    break
        finally:
            self._release(vk)  # never leave the key or button held down


class HotkeyPoller:
    """Polls F1-F4 globally (works while the game has focus) and calls
    on_press(vk) on each fresh press from a background thread."""

    def __init__(self, on_press, keys=tuple(HOTKEYS), interval=0.01, is_down_fn=None):
        self.on_press = on_press
        self.keys = keys
        self.interval = interval
        self.is_down = is_down_fn or is_down
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    def _run(self):
        prev = {vk: self.is_down(vk) for vk in self.keys}
        while not self.stop_event.wait(self.interval):
            for vk in self.keys:
                down = self.is_down(vk)
                if down and not prev[vk]:
                    self.on_press(vk)
                prev[vk] = down
