"""
Record-and-loop mouse macro for Windows. No dependencies (pure ctypes).

Hotkeys (work while any window is focused):
  F2   start / stop RECORDING the spots you click (in order)
  F1   start / stop PLAYING them on repeat, as fast as the gap allows
  F3   slower (gap x1.5)      F4   faster (gap / 1.5)
  F10  quit

Your own click timing is NOT replayed: playback jumps spot-to-spot with a
fixed gap (default 100 ms). Each jump + press is one atomic input, so there
is zero delay between arriving and clicking. Drags are kept as drags.

Saved to macro.json next to this script (recording + gap setting).
Failsafe: shove the mouse into the top-left corner (0,0) to stop.

Usage:
  python macro.py                # use saved gap (default 100 ms)
  python macro.py --gap 0.01     # 10 ms between clicks
  python macro.py --hold 0.02    # hold each click 20 ms (default 30 ms)
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Make coordinates real screen pixels on scaled (125%/150%) displays.
try:
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
        raise OSError
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()

ctypes.windll.winmm.timeBeginPeriod(1)  # 1 ms sleep granularity

VK_F1, VK_F2, VK_F3, VK_F4, VK_F10 = 0x70, 0x71, 0x72, 0x73, 0x79
MIN_GAP = 0.005  # fastest allowed: 5 ms between clicks

MOVE, ABSOLUTE, VIRTUALDESK = 0x0001, 0x8000, 0x4000
BUTTON_FLAGS = {  # (down, up)
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
# Low-level hook messages -> (button, kind)
HOOK_MSGS = {
    0x0201: ("left", "down"), 0x0202: ("left", "up"),
    0x0204: ("right", "down"), 0x0205: ("right", "up"),
    0x0207: ("middle", "down"), 0x0208: ("middle", "up"),
}
LLMHF_INJECTED = 0x01
WH_MOUSE_LL = 14

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def get_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _virtual_desktop():
    # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def _to_abs(x, y):
    """Pixel -> 0..65535 absolute coords that land on exactly that pixel."""
    left, top, w, h = _virtual_desktop()
    # Aim at the centre of the pixel so rounding can't push it to a neighbour.
    nx = ((x - left) * 65536 + 32768) // w
    ny = ((y - top) * 65536 + 32768) // h
    return min(max(nx, 0), 65535), min(max(ny, 0), 65535)


def move_and_button(x, y, button=None, down=True):
    """Atomically move to (x, y) and (optionally) press/release a button."""
    nx, ny = _to_abs(x, y)
    n = 2 if button else 1
    arr = (INPUT * n)()
    arr[0].type = 0  # INPUT_MOUSE
    arr[0].mi = MOUSEINPUT(nx, ny, 0, MOVE | ABSOLUTE | VIRTUALDESK, 0, 0)
    if button:
        arr[1].type = 0
        arr[1].mi = MOUSEINPUT(0, 0, 0, BUTTON_FLAGS[button][0 if down else 1], 0, 0)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def release_all():
    for _, up in BUTTON_FLAGS.values():
        inp = INPUT(type=0)
        inp.mi = MOUSEINPUT(0, 0, 0, up, 0, 0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class KeyEdge:
    """Detects a fresh key press (not held)."""
    def __init__(self):
        self.prev = {}

    def pressed(self, vk):
        down = is_down(vk)
        was = self.prev.get(vk, False)
        self.prev[vk] = down
        return down and not was


class MouseRecorder:
    """Low-level mouse hook on its own thread; records real (non-injected) clicks."""
    def __init__(self):
        self.recording = False
        self.start = 0.0
        self.events = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.ok = False
        self._proc = HOOKPROC(self._callback)  # keep a reference!
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(2)

    def _callback(self, code, wparam, lparam):
        if code == 0 and self.recording and wparam in HOOK_MSGS:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not info.flags & LLMHF_INJECTED:
                t = time.perf_counter() - self.start
                b, kind = HOOK_MSGS[wparam]
                with self.lock:
                    self.events.append([t, b, kind, info.pt.x, info.pt.y])
                print(f"  {t:7.3f}s  {b:6} {kind:4} at ({info.pt.x}, {info.pt.y})")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self):
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        self.ok = bool(hook)
        self.ready.set()
        if not hook:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def begin(self):
        with self.lock:
            self.events = []
        self.start = time.perf_counter()
        self.recording = True

    def end(self):
        self.recording = False
        with self.lock:
            return [list(e) for e in self.events], time.perf_counter() - self.start


def to_actions(events):
    """Turn raw down/up events into an ordered list of clicks/drags.

    Each action: [button, x_down, y_down, x_up, y_up]. Timing is dropped on
    purpose: playback goes point-to-point as fast as the gap setting allows.
    """
    actions, open_downs = [], {}
    for _, b, kind, x, y in events:
        if kind == "down":
            open_downs[b] = len(actions)
            actions.append([b, x, y, x, y])
        elif b in open_downs:
            a = actions[open_downs.pop(b)]
            a[3], a[4] = x, y
    return actions


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


class Player:
    """Plays actions on repeat in a background thread."""
    def __init__(self, settings):
        self.settings = settings  # dict with "gap" and "hold" (seconds)
        self.stop = threading.Event()
        self.thread = None
        self.loops = 0

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, actions):
        self.stop.clear()
        self.loops = 0
        self.thread = threading.Thread(target=self._run, args=(actions,), daemon=True)
        self.thread.start()

    def halt(self):
        self.stop.set()
        if self.thread:
            self.thread.join(1)
        release_all()

    def _run(self, actions):
        t = time.perf_counter()
        while not self.stop.is_set():
            for b, x1, y1, x2, y2 in actions:
                if get_pos() == (0, 0):
                    print("Failsafe (mouse at 0,0) - stopped.")
                    self.stop.set()
                    break
                move_and_button(x1, y1, b, True)           # move + press, atomic
                if not wait_until(time.perf_counter() + self.settings["hold"], self.stop):
                    break
                move_and_button(x2, y2, b, False)          # (drag end) + release
                t = max(t + self.settings["gap"], time.perf_counter())
                if not wait_until(t, self.stop):
                    break
            else:
                self.loops += 1
        release_all()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gap", type=float, default=None,
                    help="seconds from one click to the next (default 0.1)")
    ap.add_argument("--hold", type=float, default=None,
                    help="seconds each button is held down (default 0.03)")
    ap.add_argument("--file", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "macro.json"))
    args = ap.parse_args()

    # events: [t_seconds, button, "down"/"up", x, y]
    events = []
    settings = {"gap": 0.1, "hold": 0.03}
    if os.path.exists(args.file):
        with open(args.file) as f:
            data = json.load(f)
        events = data["events"]
        settings.update(data.get("settings", {}))
    if args.gap is not None:
        settings["gap"] = args.gap
    if args.hold is not None:
        settings["hold"] = args.hold

    def save():
        with open(args.file, "w") as f:
            json.dump({"events": events, "settings": settings}, f)

    print(__doc__.split("Usage:")[0])

    rec = MouseRecorder()
    if not rec.ok:
        print("ERROR: could not install the mouse hook; recording won't work.")

    actions = to_actions(events)
    print(f"Loaded {len(actions)} click(s)." if actions
          else "No recording yet - press F2 to start recording.")
    print(f"Gap between clicks: {settings['gap'] * 1000:.0f} ms "
          f"(F3 slower / F4 faster)")

    keys = KeyEdge()
    player = Player(settings)
    recording = False

    while True:
        if keys.pressed(VK_F10):
            player.halt()
            print("Bye.")
            return

        if keys.pressed(VK_F2):
            if recording:
                events, _ = rec.end()
                actions = to_actions(events)
                recording = False
                save()
                print(f"Recording stopped: {len(actions)} click(s). Press F1 to play.")
            else:
                if player.running:
                    player.halt()
                rec.begin()
                recording = True
                print("RECORDING... click your spots, press F2 when done.")

        if keys.pressed(VK_F1):
            if player.running:
                player.halt()
                print(f"Stopped after {player.loops} loop(s).")
            elif recording:
                print("Stop recording with F2 first.")
            elif not actions:
                print("Nothing recorded - press F2 to record first.")
            else:
                player.start(actions)
                print("PLAYING on repeat... press F1 to stop.")

        for vk, factor in ((VK_F3, 1.5), (VK_F4, 1 / 1.5)):
            if keys.pressed(vk):
                g = min(max(settings["gap"] * factor, MIN_GAP), 5.0)
                settings["gap"] = round(g, 4)
                save()
                print(f"Gap between clicks: {settings['gap'] * 1000:.0f} ms")

        time.sleep(0.005)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        release_all()
        sys.exit(0)
