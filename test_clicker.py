"""Tests for clicker.py. Run: python test_clicker.py

Nothing here clicks: SendInput is replaced by a recorder.
The cursor-accuracy test moves the real cursor (no click) and puts it back.
"""
import random
import threading
import time

import clicker as c

clicker_settings = dict(c.DEFAULT_SETTINGS, click=[100, 200])


def decode(inp):
    assert inp.type == c.INPUT_MOUSE, "only mouse input is ever sent"
    f = inp.mi.dwFlags
    if f & c.MOVE:
        return ("move", inp.mi.dx, inp.mi.dy)
    return ("left", "down" if f & c.LEFT_DOWN else "up")


class FakeSend:
    def __init__(self):
        self.log = []  # (time, [decoded inputs])

    def __call__(self, *inputs):
        self.log.append((time.perf_counter(), [decode(i) for i in inputs]))
        return len(inputs)


def test_input_layout():
    assert c.ctypes.sizeof(c.INPUT) == 40  # x64 layout
    down = c.button_input(True)
    assert down.type == c.INPUT_MOUSE and down.mi.dwFlags == c.LEFT_DOWN


def test_to_abs_hits_pixel_centres():
    desk = (0, 0, 2560, 1440)
    assert c.to_abs(0, 0, desk) == (12, 22)
    assert c.to_abs(2559, 1439, desk) == (65523, 65513)
    # second monitor to the left of the primary
    assert c.to_abs(-1920, 0, (-1920, 0, 4480, 1440))[0] == 7


def test_speed_steps_are_clamped():
    g = 0.05
    for _ in range(50):
        g = c.faster(g)
    assert g == c.MIN_GAP
    for _ in range(50):
        g = c.slower(g)
    assert g == c.MAX_GAP


CLICK_UP = [("left", "up")]


def test_spammer_clicks_then_pauses():
    fake = FakeSend()
    s = dict(clicker_settings, gap=0.04, hold=0.01)
    sp = c.Spammer(s, send_fn=fake)
    sp.start()
    time.sleep(0.5)
    sp.stop()
    assert not sp.running
    batches = [b for _, b in fake.log]
    # one click: move+left down (atomic), then left up; nothing else
    down, up = batches[0], batches[1]
    assert down[0] == ("move",) + c.to_abs(100, 200) and down[1] == ("left", "down"), down
    assert up == CLICK_UP, up
    full = (len(batches) - 1) // 2 * 2
    for i in range(0, full, 2):
        assert batches[i] == down and batches[i + 1] == CLICK_UP, i
    times = [t for t, _ in fake.log]
    holds = [times[i + 1] - times[i] for i in range(0, full, 2)]
    pauses = [times[i + 2] - times[i + 1] for i in range(0, full - 2, 2)]
    assert all(abs(h - 0.01) < 0.004 for h in holds), holds
    assert pauses and all(abs(p - 0.04) < 0.004 for p in pauses), pauses
    # a click every hold + pause = 50 ms -> ~10 in 0.5 s
    assert 9 <= sp.cycles <= 11, sp.cycles
    assert batches[-1] == CLICK_UP  # always ends with the button released


def test_spammer_picks_up_live_changes():
    fake = FakeSend()
    s = dict(clicker_settings, gap=0.03, hold=0.005)
    sp = c.Spammer(s, send_fn=fake)
    sp.start()
    time.sleep(0.15)
    s["click"] = [300, 400]
    s["gap"] = 0.06
    mark = time.perf_counter()
    time.sleep(0.4)
    sp.stop()
    moves = [b[0] for t, b in fake.log if t > mark + 0.07 and b[0][0] == "move"]
    assert moves and all(m[1:] == c.to_abs(300, 400) for m in moves)
    # clicks are hold + pause apart
    starts = [t for t, b in fake.log if t > mark + 0.1 and b[0][0] == "move"]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps and all(abs(g - 0.065) < 0.005 for g in gaps), gaps


def test_stop_mid_hold_releases():
    fake = FakeSend()
    s = dict(clicker_settings, gap=2.0, hold=1.0)
    sp = c.Spammer(s, send_fn=fake)
    sp.start()
    time.sleep(0.05)  # inside the click hold
    t0 = time.perf_counter()
    sp.stop()
    assert time.perf_counter() - t0 < 0.1
    assert fake.log[-1][1] == CLICK_UP


def test_elevation_checks():
    import os
    assert c.process_elevated(os.getpid()) == c.is_admin()
    assert c.process_elevated(0) is None  # System Idle: can't be opened
    assert c.game_blocks_input("no window has this title 1f3a") is False


def test_hotkey_poller_fires_once_per_press():
    state = {c.VK_F1: False, c.VK_F2: False, c.VK_F3: False, c.VK_F4: False}
    got = []
    poll = c.HotkeyPoller(lambda vk: got.append(vk), interval=0.002,
                          is_down_fn=lambda vk: state[vk]).start()
    for vk in (c.VK_F1, c.VK_F2, c.VK_F1):
        state[vk] = True
        time.sleep(0.03)  # held for many polls
        state[vk] = False
        time.sleep(0.03)
    poll.stop()
    assert got == [c.VK_F1, c.VK_F2, c.VK_F1], got


def test_cursor_lands_on_exact_pixel():
    """Moves the real cursor (no clicks) and restores it."""
    c.set_dpi_aware()
    if not c.is_admin():
        fg = c.user32.GetForegroundWindow()
        pid = c.wt.DWORD()
        c.user32.GetWindowThreadProcessId(fg, c.ctypes.byref(pid))
        if c.process_elevated(pid.value):
            print("     skipped: an admin window has focus, so Windows blocks our input")
            return
    left, top, w, h = c._virtual_desktop()
    orig = c.get_pos()
    rng = random.Random(1)
    pts = [(left, top), (left + w - 1, top + h - 1)] + [
        (rng.randrange(left, left + w), rng.randrange(top, top + h)) for _ in range(200)]
    misses = []
    try:
        for x, y in pts:
            c.send(c.move_input(x, y))
            got = c.get_pos()
            if got != (x, y):
                misses.append(((x, y), got))
    finally:
        c.send(c.move_input(*orig))
    # a miss on a single point can be the user nudging the mouse
    assert len(misses) <= 2, misses[:5]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} tests passed")
