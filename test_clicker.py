"""Tests for clicker.py. Run: python test_clicker.py

Nothing here presses keys or clicks: SendInput is replaced by a recorder.
The cursor-accuracy test moves the real cursor (no click) and puts it back.
"""
import random
import threading
import time

import clicker as c

clicker_settings = dict(c.DEFAULT_SETTINGS, interact_vk=0x20, click=[100, 200])


def decode(inp):
    if inp.type == c.INPUT_KEYBOARD:
        up = bool(inp.ki.dwFlags & c.KEYEVENTF_KEYUP)
        return ("key", inp.ki.wVk, "up" if up else "down")
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


def test_key_input_uses_scan_codes():
    space = c.key_input(0x20, True)
    assert space.type == c.INPUT_KEYBOARD
    assert space.ki.wScan == 0x39, hex(space.ki.wScan)  # hardware scan code for Space
    assert space.ki.dwFlags == c.KEYEVENTF_SCANCODE
    up = c.key_input(0x20, False)
    assert up.ki.dwFlags == c.KEYEVENTF_SCANCODE | c.KEYEVENTF_KEYUP
    arrow = c.key_input(0x26, True)  # Up arrow is an extended key
    assert arrow.ki.dwFlags & c.KEYEVENTF_EXTENDEDKEY
    assert c.ctypes.sizeof(c.INPUT) == 40  # x64 layout


def test_key_names():
    assert c.key_name(0x20) == "Space", c.key_name(0x20)
    assert c.key_name(0x5A) == "Z", c.key_name(0x5A)
    assert c.key_name(None) == "—"


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


KEY_DOWN, KEY_UP = [("key", 0x20, "down")], [("key", 0x20, "up")]


def test_spammer_sequence_is_interact_click_interact_pause():
    fake = FakeSend()
    s = dict(clicker_settings, gap=0.04, hold=0.01)
    sp = c.Spammer(s, send_fn=fake)
    sp.start()
    time.sleep(0.5)
    sp.stop()
    assert not sp.running
    batches = [b for _, b in fake.log]
    # one cycle: interact (down/up), click (move+down/up), interact (down/up)
    first = batches[:6]
    assert first[0] == KEY_DOWN and first[1] == KEY_UP, first
    assert first[2][0][0] == "move" and first[2][1] == ("left", "down"), first
    assert first[3] == [("left", "up")], first
    assert first[4] == KEY_DOWN and first[5] == KEY_UP, first
    move = first[2][0]
    assert (move[1], move[2]) == c.to_abs(100, 200)
    # the pattern repeats exactly
    full = (len(batches) - 1) // 6 * 6
    for i in range(0, full, 6):
        assert [b[-1] for b in batches[i:i + 6]] == [b[-1] for b in first], i
    # within a sequence: each press is held `hold`, with a `hold` gap between
    # steps, so the second interact press is clearly separate from the first
    times = [t for t, _ in fake.log]
    for i in range(0, 5):
        assert abs(times[i + 1] - times[i] - 0.01) < 0.004, (i, times[i + 1] - times[i])
    # ...then the pause: last key up -> next sequence's first key down
    pauses = [times[i + 6] - times[i + 5] for i in range(0, full - 6, 6)]
    assert pauses and all(abs(p - 0.04) < 0.004 for p in pauses), pauses
    # sequences are 5 holds + pause apart: 0.09 s -> ~5-6 in 0.5 s
    assert 5 <= sp.cycles <= 6, sp.cycles
    # always ends with key + button released
    assert batches[-1] == [("key", 0x20, "up"), ("left", "up")], batches[-1]


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
    # clicks are 5 holds + pause apart
    starts = [t for t, b in fake.log if t > mark + 0.1 and b[0][0] == "move"]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps and all(abs(g - 0.085) < 0.006 for g in gaps), gaps


def test_stop_mid_hold_releases():
    fake = FakeSend()
    s = dict(clicker_settings, gap=2.0, hold=1.0)
    sp = c.Spammer(s, send_fn=fake)
    sp.start()
    time.sleep(0.05)  # inside the key hold
    t0 = time.perf_counter()
    sp.stop()
    assert time.perf_counter() - t0 < 0.1
    assert fake.log[-1][1] == [("key", 0x20, "up"), ("left", "up")]


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
