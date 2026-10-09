"""Tests for the window logic in main.py. Run: python test_app.py

Uses a temporary settings file and a fake SendInput, so nothing is pressed
or clicked and your saved settings are untouched.
"""
import json
import os
import tempfile
import time
import types

import clicker
import main
from test_clicker import FakeSend

TMP = tempfile.mkdtemp()


def make_app(settings=None):
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
    good = {"interact_vk": 0x20, "click": [5, 6], "gap": 0.03, "hold": 0.01}
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
        app.events.put(clicker.VK_F2)
        pump(app, 0.1)
        assert app.settings["click"] == list(far), app.settings
        assert saved()["click"] == list(far)
        app.events.put(clicker.VK_F1)
        pump(app, 0.3)
        assert app.spammer.running and app.vars["state"].get() == "RUNNING"
        app.events.put(clicker.VK_F1)
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
        app.events.put(clicker.VK_F4)
        pump(app, 0.1)
        assert app.settings["gap"] == clicker.faster(g)
        app.events.put(clicker.VK_F3)
        app.events.put(clicker.VK_F3)
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


if __name__ == "__main__":
    clicker.set_dpi_aware()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} tests passed")
