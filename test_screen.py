"""Tests for screen.py. Run: python test_screen.py

The live test opens a small topmost window with known colours, captures it
and checks the pixels, so it needs a visible desktop.
"""
import os
import tempfile
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
    b = paint(a, 0, 0, (60, 20, 30))         # one channel of one pixel
    b = paint(b, 49, 19, (255, 255, 255))    # all channels of the last pixel
    b = paint(b, 7, 3, (10, 70, 30))
    assert s.changed_pixels(a, b) == 3, s.changed_pixels(a, b)
    assert s.changed_pixels(b, a) == 3
    # alpha differences are ignored
    assert s.changed_pixels(a, solid(50, 20, (10, 20, 30), alpha=0)) == 0
    assert s.changed_pixels(a, solid(50, 20, (100, 120, 130))) == 1000
    assert s.changed_pixels(a, solid(10, 10, (10, 20, 30))) == 1000  # size change


def test_faint_shading_is_ignored_but_real_changes_count():
    """Every pair of channel values: shifts under 8 never count, 16+ always do."""
    for v in range(256):
        a = solid(1, 1, (v, 0, 0))
        for d in range(-20, 21):
            w = v + d
            if not 0 <= w <= 255:
                continue
            got = s.changed_pixels(a, solid(1, 1, (w, 0, 0)))
            if abs(d) < 8:
                assert got == 0, (v, w)
            elif abs(d) >= 16:
                assert got == 1, (v, w)
    # the real case: a window shadow darkening a 26 px strip by up to 5 levels
    base = solid(423, 84, (150, 180, 200))
    buf = bytearray(base.bgra)
    for y in range(84):
        for x in range(397, 423):
            i = (y * 423 + x) * 4
            for c in range(3):
                buf[i + c] -= 1 + (x - 397) % 5
    shaded = s.Frame(423, 84, bytes(buf))
    assert s.changed_pixels(base, shaded) == 0
    # ...while white text drawn on it still counts
    assert s.changed_pixels(base, changed_text(base)) == 100


def changed_text(frame):
    """Paint a 10x10 white block (stand-in for new text) into a frame."""
    for y in range(10):
        for x in range(10):
            frame = paint(frame, 20 + x, 20 + y, (255, 255, 255))
    return frame


def test_changed_pixels_is_fast():
    a = solid(600, 200, (1, 2, 3))
    b = paint(a, 300, 100, (99, 99, 99))
    t = time.perf_counter()
    for _ in range(100):
        assert s.changed_pixels(a, b) == 1
    per = (time.perf_counter() - t) / 100
    # two bucket grids per compare; still small next to a ~4 ms capture
    assert per < 0.010, f"{per * 1000:.2f} ms per diff"


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


def test_save_and_load_frame_round_trip():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "sub", "snap.ppm")
    a = paint(paint(solid(30, 7, (1, 2, 3), alpha=0), 5, 5, (250, 0, 9)), 29, 6, (9, 9, 9))
    s.save_frame(a, path)
    b = s.load_frame(path)
    assert (b.width, b.height) == (30, 7)
    assert s.changed_pixels(a, b) == 0  # alpha isn't stored; colours are exact
    assert b.ppm() == a.ppm()
    assert s.load_frame(os.path.join(d, "missing.ppm")) is None
    with open(path, "wb") as f:
        f.write(a.ppm()[:-5])  # truncated
    assert s.load_frame(path) is None
    with open(path, "wb") as f:
        f.write(b"garbage")
    assert s.load_frame(path) is None


def test_matches_uses_the_change_threshold():
    a = solid(50, 20, (10, 20, 30))
    assert s.matches(a, a)
    assert s.matches(changed(s.CHANGE_PIXELS - 1), a)
    assert not s.matches(changed(s.CHANGE_PIXELS), a)
    assert not s.matches(solid(10, 10, (10, 20, 30)), a)  # different size


class FakeCapturer:
    """Plays back a list of frames, repeating the last one."""
    def __init__(self, frames, delay=0.0):
        self.frames, self.delay, self.grabs = list(frames), delay, 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def grab(self, region):
        time.sleep(self.delay)
        self.grabs += 1
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


REGION = {"left": 0, "top": 0, "width": 50, "height": 20}


def run_watcher(frames, timeout=1.0, **kw):
    base = frames[0]
    got = []
    w = s.Watcher(REGION, base, lambda f, info: got.append((f, info)),
                  capturer=FakeCapturer(frames), interval=0, **kw).start()
    w.thread.join(timeout)
    w.stop()
    return w, got


def changed(n, base=None):
    f = base or solid(50, 20, (10, 20, 30))
    for i in range(n):
        f = paint(f, i % 50, i // 50, (255, 255, 255))
    return f


def test_watcher_ignores_small_noise_and_single_frame_flicker():
    base = solid(50, 20, (10, 20, 30))
    frames = [base, changed(5), base, changed(40), base] + [changed(3)] * 50
    w, got = run_watcher(frames, timeout=0.3)
    assert got == [] and not w.fired, got
    assert w.checks > 50


def test_watcher_fires_on_a_real_change():
    base = solid(50, 20, (10, 20, 30))
    new = changed(40)
    w, got = run_watcher([base, base, base, new, new, new])
    assert w.fired and len(got) == 1, got
    frame, n = got[0]
    assert frame is new and n == 40
    assert not w.running


def test_watcher_reacts_within_a_couple_of_frames():
    base = solid(50, 20, (10, 20, 30))
    cap = FakeCapturer([base] * 20 + [changed(40)], delay=0.004)
    got = []
    w = s.Watcher(REGION, base, lambda f, info: got.append(time.perf_counter()),
                  capturer=cap).start()
    w.thread.join(2)
    assert got and cap.grabs == 22, cap.grabs  # fired on the 2nd changed frame


def test_watcher_reports_capture_errors():
    class Broken(FakeCapturer):
        def grab(self, region):
            raise OSError("boom")
    got = []
    w = s.Watcher(REGION, solid(50, 20, (0, 0, 0)), lambda f, info: got.append((f, info)),
                  capturer=Broken([None])).start()
    w.thread.join(1)
    assert got and got[0][0] is None and "boom" in got[0][1], got


def test_watcher_stop_is_silent():
    base = solid(50, 20, (10, 20, 30))
    got = []
    w = s.Watcher(REGION, base, lambda f, info: got.append(info),
                  capturer=FakeCapturer([base])).start()
    time.sleep(0.05)
    w.stop()
    w.thread.join(1)
    assert not w.running and got == [] and not w.fired


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} tests passed")
