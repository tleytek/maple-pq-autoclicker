"""
Tiny multi-point auto clicker for Windows. No dependencies (pure ctypes).

Hotkeys (work while any window is focused):
  F6   record current mouse position as the next click point
  F7   start / stop clicking (cycles through all points in order)
  F8   clear all points
  F9   save points to points.json (also auto-saved on F6/F8)
  F10  quit

Failsafe: slam the mouse into the top-left corner (0,0) to stop clicking.

Usage:
  python autoclicker.py                  # 100 ms between clicks
  python autoclicker.py --interval 0.05  # faster
  python autoclicker.py --button right --clicks 2
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import random
import sys
import time

user32 = ctypes.windll.user32

# Make coordinates match real screen pixels on scaled (125%/150%) displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

VK = {"F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79}

MOUSEEVENTF = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def get_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move(x, y):
    user32.SetCursorPos(int(x), int(y))


def click(button="left"):
    down, up = MOUSEEVENTF[button]
    for flag in (down, up):
        inp = INPUT(type=0)  # INPUT_MOUSE
        inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.01)


class KeyEdge:
    """Detects a fresh key press (not held)."""
    def __init__(self):
        self.prev = {}

    def pressed(self, vk):
        down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        was = self.prev.get(vk, False)
        self.prev[vk] = down
        return down and not was


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=0.1,
                    help="seconds between points (default 0.1)")
    ap.add_argument("--jitter", type=float, default=0.0,
                    help="random extra delay 0..N seconds per click")
    ap.add_argument("--clicks", type=int, default=1,
                    help="clicks per point (default 1)")
    ap.add_argument("--button", choices=MOUSEEVENTF, default="left")
    ap.add_argument("--points", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "points.json"))
    args = ap.parse_args()

    points = []
    if os.path.exists(args.points):
        with open(args.points) as f:
            points = [tuple(p) for p in json.load(f)]

    def save():
        with open(args.points, "w") as f:
            json.dump(points, f)

    print(__doc__.split("Usage:")[0])
    print(f"Loaded {len(points)} point(s): {points}")

    keys = KeyEdge()
    running = False
    idx = 0
    next_click = 0.0

    while True:
        if keys.pressed(VK["F10"]):
            print("Bye.")
            return
        if keys.pressed(VK["F6"]):
            points.append(get_pos())
            save()
            print(f"Point {len(points)} added at {points[-1]}")
        if keys.pressed(VK["F8"]):
            points.clear()
            running = False
            save()
            print("Points cleared.")
        if keys.pressed(VK["F9"]):
            save()
            print(f"Saved to {args.points}")
        if keys.pressed(VK["F7"]):
            if not points:
                print("No points yet - hover and press F6 first.")
            else:
                running = not running
                idx = 0
                print("RUNNING" if running else "stopped")

        if running and get_pos() == (0, 0):
            running = False
            print("Failsafe (mouse at 0,0) - stopped.")

        now = time.perf_counter()
        if running and now >= next_click:
            x, y = points[idx]
            move(x, y)
            for _ in range(args.clicks):
                click(args.button)
            idx = (idx + 1) % len(points)
            next_click = now + args.interval + random.uniform(0, args.jitter)

        time.sleep(0.005)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
