"""Tests for the window logic in main.py. Run: python test_app.py

Uses a temporary settings/snapshot file and a fake SendInput, so nothing is
pressed or clicked and your saved settings are untouched. The watch-region
tests open a small window on screen and use it as the region.
"""
import gc
import json
import os
import tempfile
import time
import tkinter as tk

import clicker
import main
import screen
from test_clicker import FakeSend

TMP = tempfile.mkdtemp()
FAR = [-99999, -99999]  # click spot that's never on the app window (input is fake)
# Input is fake here, so an admin MapleStory being open mustn't block start.
clicker.game_blocks_input = lambda: False


def make_app(settings=None):
    # Several Tk roots in one process: free old Tk objects here, on the main
    # thread, before a background thread's GC pass can (Tcl crashes on that).
    gc.collect()
    stamp = time.perf_counter_ns()
    main.SETTINGS_FILE = os.path.join(TMP, f"settings_{stamp}.json")
    main.SNAPSHOT_FILE = os.path.join(TMP, f"snapshot_{stamp}.ppm")
    if settings is not None:
        with open(main.SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    return _open()


def _open():
    app = main.App()
    app.fake = FakeSend()
    app.spammer.send = app.fake
    app.hotkeys.stop()  # don't react to real F-keys during tests
    app.root.update()
    return app


def reopen(app):
    """Close and start the app again with the same settings/snapshot files."""
    app.quit()
    gc.collect()
    return _open()


def pump(app, seconds=0.0):
    end = time.time() + seconds
    while True:
        app.root.update()
        if time.time() >= end:
            break
        time.sleep(0.01)


def saved():
    with open(main.SETTINGS_FILE) as f:
        return json.load(f)


def watched_target(app, color="#203040"):
    """A small topmost window away from the app, used as the watched region."""
    app.root.geometry("+40+40")
    top = tk.Toplevel(app.root)
    top.overrideredirect(True)
    top.attributes("-topmost", True)
    top.geometry("160x60+900+300")
    canvas = tk.Canvas(top, width=160, height=60, highlightthickness=0, bg=color)
    canvas.pack()
    pump(app, 0.4)
    region = {"left": top.winfo_rootx() + 10, "top": top.winfo_rooty() + 10,
              "width": 140, "height": 40}
    return top, canvas, region


def ready_app(gap=0.02):
    """Key, click spot, region and snapshot all set: ready to start."""
    app = make_app({"click": FAR, "gap": gap, "hold": 0.005})
    top, canvas, region = watched_target(app)
    app._region_chosen(region)
    app.take_snapshot()
    assert app.snapshot is not None, app.status.get()
    return app, top, canvas


def wait_stopped(app, limit=1.0):
    t0 = time.perf_counter()
    while app.spammer.running and time.perf_counter() - t0 < limit:
        time.sleep(0.001)
    return time.perf_counter() - t0


# --------------------------------------------------------------------------- #
def test_load_settings_rejects_bad_values():
    path = os.path.join(TMP, "bad.json")
    with open(path, "w") as f:
        json.dump({"interact_vk": 0x20, "click": [1, "x"], "gap": 0.0001,
                   "hold": "fast"}, f)  # old interact key setting is ignored
    s = main.load_settings(path)
    assert s == clicker.DEFAULT_SETTINGS, s
    with open(path, "w") as f:
        f.write("not json")
    assert main.load_settings(path) == clicker.DEFAULT_SETTINGS
    with open(path, "w") as f:
        json.dump({"region": {"left": 1, "top": 2, "width": 3, "height": 50}}, f)
    assert "region" not in main.load_settings(path)  # too small
    good = {"click": [5, 6], "gap": 0.03,
            "region": {"left": -100, "top": 2, "width": 300, "height": 50}}
    with open(path, "w") as f:
        json.dump(dict(good, hold=0.015), f)  # old saved hold is ignored
    assert main.load_settings(path) == dict(good, hold=clicker.DEFAULT_SETTINGS["hold"])


def test_load_snapshot_must_fit_the_region():
    main.SNAPSHOT_FILE = os.path.join(TMP, "fit.ppm")
    screen.save_frame(screen.Frame(20, 10, bytes(800)), main.SNAPSHOT_FILE)
    assert main.load_snapshot({"left": 0, "top": 0, "width": 20, "height": 10}) is not None
    assert main.load_snapshot({"left": 0, "top": 0, "width": 21, "height": 10}) is None
    assert main.load_snapshot(None) is None


def test_fresh_start_asks_for_setup_and_refuses_to_run():
    app = make_app()
    try:
        status = app.status.get()
        for step in ("F2", "Select Region"):
            assert step in status, status
        assert "Interact" not in status and "interact" not in app.vars
        assert app.vars["region"].get() == "not set"
        assert app.vars["snapshot"].get() == "not taken"
        app.start()
        assert not app.spammer.running
        assert "Can't start" in app.status.get()
    finally:
        app.quit()


def test_f2_sets_click_spot():
    app = make_app()
    orig = clicker.get_pos
    try:
        clicker.get_pos = lambda: (1234, 567)
        app.events.put(("key", clicker.VK_F2))
        pump(app, 0.1)
        assert app.settings["click"] == [1234, 567], app.settings
        assert saved()["click"] == [1234, 567]
    finally:
        clicker.get_pos = orig
        app.quit()


def test_speed_hotkeys_save():
    app = make_app()
    try:
        g = app.settings["gap"]
        app.events.put(("key", clicker.VK_F4))
        pump(app, 0.1)
        assert app.settings["gap"] == clicker.faster(g)
        app.events.put(("key", clicker.VK_F3))
        app.events.put(("key", clicker.VK_F3))
        pump(app, 0.1)
        assert app.settings["gap"] == clicker.slower(clicker.slower(clicker.faster(g)))
        assert saved()["gap"] == app.settings["gap"]
    finally:
        app.quit()


def test_region_overlay_drag_returns_screen_coords():
    app = make_app()
    got = []
    try:
        top = main.select_region(app.root, got.append)
        pump(app, 0.3)
        canvas = top.winfo_children()[0]
        v = main.screen.virtual_screen()
        canvas.event_generate("<ButtonPress-1>", x=100, y=50)
        canvas.event_generate("<B1-Motion>", x=150, y=60)
        canvas.event_generate("<ButtonRelease-1>", x=103, y=52)  # too small: ignored
        pump(app, 0.05)
        assert got == []
        canvas.event_generate("<ButtonPress-1>", x=300, y=200)
        canvas.event_generate("<ButtonRelease-1>", x=100, y=150)  # dragged up-left
        pump(app, 0.05)
        assert got == [{"left": 100 + v["left"], "top": 150 + v["top"],
                        "width": 200, "height": 50}], got
        top2 = main.select_region(app.root, got.append)
        pump(app, 0.2)
        top2.event_generate("<Escape>")
        pump(app, 0.05)
        assert got[-1] is None
    finally:
        app.quit()


def test_frame_photo_scaling():
    app = make_app()
    try:
        small = main.screen.Frame(100, 10, bytes(4000))
        assert main.frame_photo(small).width() == 200
        mid = main.screen.Frame(200, 10, bytes(8000))
        assert main.frame_photo(mid).width() == 200
        wide = main.screen.Frame(900, 10, bytes(36000))
        assert main.frame_photo(wide).width() == 300
    finally:
        app.quit()


def test_snapshot_is_saved_shown_and_survives_a_restart():
    app = make_app({"click": FAR})
    try:
        app.take_snapshot()
        assert app.snapshot is None and "Select Region first" in app.status.get()
        top, canvas, region = watched_target(app)
        app._region_chosen(region)
        assert app.vars["region"].get() == f"140×40 at ({region['left']}, {region['top']})"
        assert "Take Snapshot" in app.status.get()
        app.take_snapshot()
        assert app.snapshot is not None and os.path.exists(main.SNAPSHOT_FILE)
        assert app.vars["snapshot"].get() == "saved"
        assert app.snapshot_photo.width() == 280  # small crops shown at 2x
        pump(app, 0.4)
        assert app.vars["match"].get() == "matches snapshot"
        canvas.create_text(70, 20, text="Changed", fill="white")
        pump(app, 0.4)
        assert app.vars["match"].get().startswith("different ("), app.vars["match"].get()
        app = reopen(app)
        assert app.snapshot is not None and app.vars["snapshot"].get() == "saved"
        assert app.snapshot_photo is not None
        # choosing a new region throws the old snapshot away
        app._region_chosen(dict(region, width=100))
        assert app.snapshot is None and not os.path.exists(main.SNAPSHOT_FILE)
        assert app.vars["snapshot"].get() == "not taken"
        app.start()
        assert not app.spammer.running and "Take Snapshot" in app.status.get()
        # (the target window closed with the first app instance)
    finally:
        app.quit()


def test_wont_start_unless_region_matches_snapshot():
    app, top, canvas = ready_app()
    try:
        item = canvas.create_text(70, 20, text="Wrong screen", fill="white")
        pump(app, 0.1)
        app.events.put(("key", clicker.VK_F1))  # F1 pressed on the wrong screen
        pump(app, 0.2)
        assert not app.spammer.running
        assert "doesn't match the snapshot" in app.status.get(), app.status.get()
        assert app.fake.log == []  # not a single key or click was sent
        canvas.delete(item)  # back on the right screen
        pump(app, 0.1)
        app.events.put(("key", clicker.VK_F1))
        pump(app, 0.2)
        assert app.spammer.running, app.status.get()
        assert app.vars["state"].get() == "RUNNING · watching"
        app.events.put(("key", clicker.VK_F1))
        pump(app, 0.1)
        assert not app.spammer.running and app.watcher is None
        log = app.log_text.get("1.0", "end")
        assert "Click timing (last" in log and "longest" in log, log[-300:]
        top.destroy()
    finally:
        app.quit()


def test_change_stops_clicking_fast_and_blocks_restart():
    app, top, canvas = ready_app()
    try:
        app.start()
        pump(app, 0.4)
        assert app.spammer.running, app.status.get()  # unchanged: keeps going
        assert app.spammer.cycles >= 5  # ~45 ms per sequence (5 x 5 ms + 20 ms pause)
        item = canvas.create_text(70, 20, text="Next", fill="white")
        top.update()
        took = wait_stopped(app)
        print(f"     stopped {took * 1000:.0f} ms after the change")
        assert not app.spammer.running and took < 0.15, took
        pump(app, 0.2)
        assert app.watcher is None
        assert app.vars["state"].get() == "Stopped (region changed)"
        assert "no longer matches the snapshot" in app.status.get(), app.status.get()
        assert app.fake.log[-1][1] == [("left", "up")]
        # still on the changed screen: start is refused (the snapshot is kept)
        sent = len(app.fake.log)
        app.start()
        assert not app.spammer.running and len(app.fake.log) == sent
        # back to the snapshot screen: runs again
        canvas.delete(item)
        top.update()
        pump(app, 0.1)
        app.start()
        assert app.spammer.running, app.status.get()
        app.stop()
        top.destroy()
    finally:
        app.quit()


def test_refuses_click_spot_on_its_own_window():
    app, top, canvas = ready_app()
    try:
        app.settings["click"] = [app.root.winfo_rootx() + 5, app.root.winfo_rooty() + 5]
        app.start()
        assert not app.spammer.running
        assert "on this window" in app.status.get()
        top.destroy()
    finally:
        app.quit()


def test_refuses_region_over_its_own_window():
    app = make_app({"click": FAR})
    try:
        pump(app, 0.1)
        region = {"left": app.root.winfo_rootx() + 5, "top": app.root.winfo_rooty() + 5,
                  "width": 50, "height": 20}
        app._region_chosen(region)
        app.take_snapshot()
        assert app.snapshot is None and "overlaps or touches this window" in app.status.get()
        app.snapshot = screen.Frame(50, 20, bytes(4000))  # e.g. window moved after
        app.start()
        assert not app.spammer.running
        assert "overlaps or touches this window" in app.status.get()
    finally:
        app.quit()


def test_region_just_beside_the_window_counts_as_overlap():
    app = make_app({"click": FAR})
    try:
        pump(app, 0.1)
        x1 = app.root.winfo_rootx() + app.root.winfo_width()
        y = app.root.winfo_rooty() + 10
        near = {"left": x1 + 5, "top": y, "width": 50, "height": 20}  # in the shadow
        app._region_chosen(near)
        app.take_snapshot()
        assert app.snapshot is None and "touches this window" in app.status.get()
        clear = dict(near, left=x1 + main.SHADOW_MARGIN + 5)
        app._region_chosen(clear)
        app.take_snapshot()
        assert app.snapshot is not None, app.status.get()
    finally:
        app.quit()


def test_capture_failure_stops_clicking():
    app, top, canvas = ready_app()
    try:
        app.start()
        assert app.spammer.running
        app._on_region_change(None, "screen capture failed: test")
        pump(app, 0.2)
        assert not app.spammer.running
        assert app.vars["state"].get() == "Stopped (can't see region)"
        top.destroy()
    finally:
        app.quit()


def test_refuses_when_the_game_blocks_input():
    app, top, canvas = ready_app()
    orig = clicker.game_blocks_input
    try:
        clicker.game_blocks_input = lambda: True
        app.start()
        assert not app.spammer.running and app.fake.log == []
        assert "run it as administrator" in app.status.get(), app.status.get()
        clicker.game_blocks_input = lambda: False
        app.start()
        assert app.spammer.running, app.status.get()
        app.stop()
        top.destroy()
    finally:
        clicker.game_blocks_input = orig
        app.quit()


def test_pause_row_and_sequence_rate():
    s = dict(clicker.DEFAULT_SETTINGS, gap=0.05)
    assert abs(main.click_seconds(s) - 0.085) < 1e-9  # 35 ms click + 50 ms pause
    assert main.fmt_speed(s) == "50 ms  (11.8 clicks / sec)"
    app = make_app({"gap": 0.05, "hold": 0.015})
    try:
        assert app.settings["hold"] == 0.035
        assert app.vars["speed"].get() == "50 ms  (11.8 clicks / sec)"
        app.change_speed(clicker.faster)
        assert app.vars["speed"].get().startswith("33 ms"), app.vars["speed"].get()
        assert "Pause: 33 ms" in app.status.get()
    finally:
        app.quit()


if __name__ == "__main__":
    clicker.set_dpi_aware()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        gc.collect()
        print("ok  ", name)
    print(f"{len(tests)} tests passed")
