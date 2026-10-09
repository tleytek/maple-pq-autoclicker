"""Tests for screen.py. Run: python test_screen.py

The live test opens a small topmost window with known colours, captures it
and checks the pixels, so it needs a visible desktop.
"""
import time
import tkinter as tk

import clicker
import screen as s


def solid(w, h, rgb, alpha=255):
    r, g, b = rgb
    return s.Frame(w, h, bytes([b, g, r, alpha]) * (w * h))


def paint(frame, x, y, rgb):
    buf = bytearray(frame.bgra)
    i = (y * frame.width + x) * 4
    buf[i:i + 3] = bytes(reversed(rgb))
    return s.Frame(frame.width, frame.height, bytes(buf))


def test_changed_pixels_counts_pixels_not_bytes():
    a = solid(50, 20, (10, 20, 30))
    assert s.changed_pixels(a, a) == 0
    b = paint(a, 0, 0, (11, 20, 30))         # one channel of one pixel
    b = paint(b, 49, 19, (255, 255, 255))    # all channels of the last pixel
    b = paint(b, 7, 3, (10, 21, 30))
    assert s.changed_pixels(a, b) == 3, s.changed_pixels(a, b)
    assert s.changed_pixels(b, a) == 3
    # alpha differences are ignored
    assert s.changed_pixels(a, solid(50, 20, (10, 20, 30), alpha=0)) == 0
    assert s.changed_pixels(a, solid(50, 20, (0, 0, 0))) == 1000
    assert s.changed_pixels(a, solid(10, 10, (10, 20, 30))) == 1000  # size change


def test_changed_pixels_is_fast():
    a = solid(600, 200, (1, 2, 3))
    b = paint(a, 300, 100, (9, 9, 9))
    t = time.perf_counter()
    for _ in range(100):
        assert s.changed_pixels(a, b) == 1
    per = (time.perf_counter() - t) / 100
    assert per < 0.005, f"{per * 1000:.2f} ms per diff"


def test_blank_and_ppm():
    a = solid(4, 3, (200, 100, 50))
    assert a.is_blank()
    assert not paint(a, 2, 2, (0, 0, 0)).is_blank()
    ppm = a.ppm()
    assert ppm.startswith(b"P6 4 3 255 ")
    assert ppm[len(b"P6 4 3 255 "):] == bytes([200, 100, 50]) * 12


def test_region_is_valid():
    assert s.region_is_valid({"left": -10, "top": 0, "width": 5, "height": 5})
    assert not s.region_is_valid({"left": 0, "top": 0, "width": 4, "height": 50})
    assert not s.region_is_valid({"left": 0, "top": 0, "width": 50})
    assert not s.region_is_valid(None)
    assert not s.region_is_valid({"left": 0.5, "top": 0, "width": 50, "height": 50})


def test_live_capture_reads_and_detects_change():
    clicker.set_dpi_aware()
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry("200x100+300+300")
    c = tk.Canvas(root, width=200, height=100, highlightthickness=0, bg="#102030")
    c.pack()
    try:
        for _ in range(30):
            root.update()
            time.sleep(0.02)
        region = {"left": root.winfo_rootx() + 20, "top": root.winfo_rooty() + 20,
                  "width": 160, "height": 60}
        with s.Capturer() as cap:
            f1 = cap.grab(region)
            assert (f1.width, f1.height, len(f1.bgra)) == (160, 60, 160 * 60 * 4)
            assert f1.bgra[:3] == bytes([0x30, 0x20, 0x10]), f1.bgra[:4]
            assert f1.is_blank()
            # PhotoImage reads the PPM (used for the preview)
            img = tk.PhotoImage(data=f1.ppm(), format="PPM")
            assert img.get(0, 0) == (0x10, 0x20, 0x30), img.get(0, 0)
            # draw a 10x10 square inside the region
            c.create_rectangle(50, 40, 60, 50, fill="#ffffff", outline="")
            for _ in range(10):
                root.update()
                time.sleep(0.02)
            f2 = cap.grab(region)
            assert s.changed_pixels(f1, f2) == 100, s.changed_pixels(f1, f2)
            # grabbing is quick enough to poll every few ms
            t = time.perf_counter()
            for _ in range(50):
                cap.grab(region)
            per = (time.perf_counter() - t) / 50
            print(f"     capture {per * 1000:.2f} ms per frame (160x60)")
            assert per < 0.03
    finally:
        root.destroy()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} tests passed")
