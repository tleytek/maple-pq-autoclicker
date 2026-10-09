"""Tests for the window logic in main.py. Run: python test_app.py

Uses a temporary settings file and a fake SendInput, so nothing is pressed
or clicked and your saved settings are untouched.
"""
import gc
import json
import os
import tempfile
import time
import tkinter as tk
import types

import clicker
import main
from test_clicker import FakeSend

TMP = tempfile.mkdtemp()


def make_app(settings=None):
    # Several Tk roots in one process: free old Tk objects here, on the main
    # thread, before a background thread's GC pass can (Tcl crashes on that).
    gc.collect()
    main.SETTINGS_FILE = os.path.join(TMP, f"settings_{time.perf_counter_ns()}.json")
    if settings is not None:
        with open(main.SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    app = main.App()
    app.fake = FakeSend()
    app.spammer.send = app.fake
    app.hotkeys.stop()  # don't react to real F-keys during tests
    app.root.update()
    return app


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


def test_load_settings_rejects_bad_values():
    path = os.path.join(TMP, "bad.json")
    with open(path, "w") as f:
        json.dump({"interact_vk": clicker.VK_F1, "click": [1, "x"], "gap": 0.0001,
                   "hold": "fast"}, f)
    s = main.load_settings(path)
    assert s == clicker.DEFAULT_SETTINGS, s
    with open(path, "w") as f:
        f.write("not json")
    assert main.load_settings(path) == clicker.DEFAULT_SETTINGS
    with open(path, "w") as f:
        json.dump({"region": {"left": 1, "top": 2, "width": 3, "height": 50}}, f)
    assert "region" not in main.load_settings(path)  # too small
    good = {"interact_vk": 0x20, "click": [5, 6], "gap": 0.03, "hold": 0.01,
            "region": {"left": -100, "top": 2, "width": 300, "height": 50}}
    with open(path, "w") as f:
        json.dump(good, f)
    assert main.load_settings(path) == good


def test_fresh_start_asks_for_setup_and_refuses_to_run():
    app = make_app()
    try:
        assert "Record Interact" in app.status.get()
        assert app.vars["interact"].get() == "not set"
        app.start()
        assert not app.spammer.running
        assert "Can't start" in app.status.get()
    finally:
        app.quit()


def test_record_interact_key():
    app = make_app()
    try:
        app.record_interact()
        assert app.capturing_key and app.vars["interact"].get() == "press a key…"
        app._key_captured(types.SimpleNamespace(keycode=clicker.VK_F2))  # hotkey: refused
        assert app.capturing_key and app.settings["interact_vk"] is None
        app._key_captured(types.SimpleNamespace(keycode=0x20))
        assert not app.capturing_key
        assert app.settings["interact_vk"] == 0x20
        assert app.vars["interact"].get() == "Space"
        assert saved()["interact_vk"] == 0x20
        # Esc cancels without changing it
        app.record_interact()
        app._key_captured(types.SimpleNamespace(keycode=clicker.VK_ESCAPE))
        assert app.settings["interact_vk"] == 0x20 and not app.capturing_key
    finally:
        app.quit()


def test_f2_sets_click_spot_and_f1_toggles():
    app = make_app({"interact_vk": 0x5A, "gap": 0.02, "hold": 0.005})
    orig = clicker.get_pos
    try:
        far = (app.root.winfo_rootx() + app.root.winfo_width() + 50, 567)
        clicker.get_pos = lambda: far
        app.events.put(("key", clicker.VK_F2))
        pump(app, 0.1)
        assert app.settings["click"] == list(far), app.settings
        assert saved()["click"] == list(far)
        app.events.put(("key", clicker.VK_F1))
        pump(app, 0.3)
        assert app.spammer.running and app.vars["state"].get() == "RUNNING"
        app.events.put(("key", clicker.VK_F1))
        pump(app, 0.1)
        assert not app.spammer.running and app.vars["state"].get() == "Stopped"
        keys = [b for _, b in app.fake.log if b == [("key", 0x5A, "down")]]
        assert len(keys) >= 8, len(keys)
        assert "Stopped after" in app.status.get()
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


def test_refuses_click_spot_on_its_own_window():
    app = make_app({"interact_vk": 0x20})
    try:
        pump(app, 0.1)
        app.settings["click"] = [app.root.winfo_rootx() + 5, app.root.winfo_rooty() + 5]
        app.start()
        assert not app.spammer.running
        assert "on this window" in app.status.get()
    finally:
        app.quit()


def test_recording_key_stops_spamming():
    app = make_app({"interact_vk": 0x20, "click": [-99999, -99999]})
    try:
        app.start()
        assert app.spammer.running
        app.record_interact()
        assert not app.spammer.running and app.capturing_key
        app.start()
        assert not app.spammer.running
    finally:
        app.quit()


def test_region_select_save_preview_and_clear():
    app = make_app()
    try:
        assert app.vars["region"].get() == "not set (optional)"
        assert app.preview.cget("text") == "(no region)"
        region = {"left": 10, "top": 20, "width": 120, "height": 30}
        app._region_chosen(region)
        assert app.settings["region"] == region and saved()["region"] == region
        assert app.vars["region"].get() == "120×30 at (10, 20)"
        assert app.preview_photo is not None
        assert app.preview_photo.width() == 240  # small crops are shown at 2x
        app._region_chosen(None)  # cancel keeps the old one
        assert app.settings["region"] == region
        app.clear_region()
        assert "region" not in app.settings and "region" not in saved()
        assert app.preview.cget("text") == "(no region)"
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


def test_change_in_watched_region_stops_clicking_fast():
    app = make_app({"interact_vk": 0x20, "click": [-99999, -99999], "gap": 0.02,
                    "hold": 0.005})
    try:
        top, canvas, region = watched_target(app)
        app.settings["region"] = region
        app.start()
        assert app.spammer.running and app.watcher is not None
        pump(app, 0.4)
        assert app.spammer.running, app.status.get()  # unchanged: keeps going
        assert app.vars["state"].get() == "RUNNING · watching"
        cycles_before = app.spammer.cycles
        assert cycles_before >= 10
        canvas.create_text(70, 30, text="Next", fill="white")  # the "text" changes
        top.update()
        t0 = time.perf_counter()
        while app.spammer.running and time.perf_counter() - t0 < 1:
            time.sleep(0.001)
        took = time.perf_counter() - t0
        print(f"     stopped {took * 1000:.0f} ms after the change")
        assert not app.spammer.running and took < 0.15, took
        pump(app, 0.2)
        assert app.watcher is None
        assert app.vars["state"].get() == "Stopped (region changed)"
        assert "watched region changed" in app.status.get(), app.status.get()
        assert app.fake.log[-1][1] == [("key", 0x20, "up"), ("left", "up")]
        # starting again takes a new snapshot (the changed text is the new normal)
        app.start()
        pump(app, 0.3)
        assert app.spammer.running, app.status.get()
        app.stop()
        assert app.watcher is None and not app.spammer.running
        top.destroy()
    finally:
        app.quit()


def test_no_region_means_no_watching():
    app = make_app({"interact_vk": 0x20, "click": [-99999, -99999]})
    try:
        app.start()
        assert app.spammer.running and app.watcher is None
        assert app.vars["state"].get() == "RUNNING"
        app.stop()
    finally:
        app.quit()


def test_refuses_region_over_its_own_window():
    app = make_app({"interact_vk": 0x20, "click": [-99999, -99999]})
    try:
        pump(app, 0.1)
        app.settings["region"] = {"left": app.root.winfo_rootx() + 5,
                                  "top": app.root.winfo_rooty() + 5, "width": 50, "height": 20}
        app.start()
        assert not app.spammer.running
        assert "overlaps this window" in app.status.get()
    finally:
        app.quit()


def test_capture_failure_stops_clicking():
    app = make_app({"interact_vk": 0x20, "click": [-99999, -99999], "gap": 0.02})
    try:
        top, canvas, region = watched_target(app)
        app.settings["region"] = region
        app.start()
        assert app.spammer.running
        app._on_region_change(None, "screen capture failed: test")
        pump(app, 0.2)
        assert not app.spammer.running
        assert app.vars["state"].get() == "Stopped (can't see region)"
        top.destroy()
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
