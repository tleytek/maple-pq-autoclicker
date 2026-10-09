"""Maple PQ Autoclicker.

Spams your in-game "interact" key and a left click at a saved spot, fast.

1. Record Interact (button): press the key you use to interact in game.
2. F2: saves the mouse position as the spot to left-click.
3. Select Region: the part of the screen to watch.
4. Take Snapshot: saves what that region must look like. Clicking only
   starts while the region matches it, and stops as soon as it doesn't.
5. F1: start / stop.   F3 slower, F4 faster.
"""
import json
import os
import queue
import time
import tkinter as tk
from tkinter import ttk

import clicker
import screen

APP_NAME = "Maple PQ Autoclicker"
# Settings live in %APPDATA% so the app works the same from source or as a
# single .exe (which unpacks to a new temp folder every run).
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "MaplePQAutoclicker")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.ppm")


def load_snapshot(region):
    """The saved snapshot, if it fits the region (else None)."""
    if not region:
        return None
    frame = screen.load_frame(SNAPSHOT_FILE)
    if frame is None or (frame.width, frame.height) != (region["width"], region["height"]):
        return None
    return frame


def delete_snapshot():
    try:
        os.remove(SNAPSHOT_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Settings persistence
# --------------------------------------------------------------------------- #
def load_settings(path=None):
    settings = dict(clicker.DEFAULT_SETTINGS)
    try:
        with open(path or SETTINGS_FILE, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return settings
    if isinstance(data, dict):
        vk = data.get("interact_vk")
        if isinstance(vk, int) and 0 < vk < 256 and vk not in clicker.HOTKEYS:
            settings["interact_vk"] = vk
        pos = data.get("click")
        if (isinstance(pos, list) and len(pos) == 2
                and all(isinstance(v, int) for v in pos)):
            settings["click"] = pos
        if screen.region_is_valid(data.get("region")):
            settings["region"] = {k: data["region"][k] for k in ("left", "top", "width", "height")}
        for key, lo, hi in (("gap", clicker.MIN_GAP, clicker.MAX_GAP), ("hold", 0.001, 0.5)):
            v = data.get(key)
            if isinstance(v, (int, float)) and lo <= v <= hi:
                settings[key] = float(v)
    return settings


def save_settings(settings, path=None):
    path = path or SETTINGS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f)


def fmt_speed(gap):
    return f"{gap * 1000:.0f} ms  ({1 / gap:.1f} / sec)"


def fmt_region(r):
    return f"{r['width']}×{r['height']} at ({r['left']}, {r['top']})"


def frame_photo(frame, max_w=300):
    """Tk image of a captured frame, zoomed up if tiny / shrunk if wide."""
    img = tk.PhotoImage(data=frame.ppm(), format="PPM")
    if frame.width * 2 <= max_w:
        return img.zoom(2)
    if frame.width > max_w:
        return img.subsample(-(-frame.width // max_w))
    return img


# --------------------------------------------------------------------------- #
# Region selector (drag a box over a frozen screenshot)
# --------------------------------------------------------------------------- #
def select_region(master, on_done):
    """Open a fullscreen drag-to-select overlay. Calls on_done(region_or_None)."""
    virtual = screen.virtual_screen()
    with screen.Capturer() as cap:
        shot = cap.grab(virtual)

    top = tk.Toplevel(master)
    top.overrideredirect(True)
    top.attributes("-topmost", True)
    top.geometry(f"{virtual['width']}x{virtual['height']}+{virtual['left']}+{virtual['top']}")

    canvas = tk.Canvas(top, highlightthickness=0, cursor="crosshair")
    canvas.pack(fill=tk.BOTH, expand=True)
    photo = tk.PhotoImage(data=shot.ppm(), format="PPM")
    canvas.photo = photo  # keep a reference
    canvas.create_image(0, 0, image=photo, anchor="nw")
    hint = canvas.create_text(
        virtual["width"] // 2, 40,
        text="Drag a box around the text to watch  —  Esc / right-click to cancel",
        fill="yellow", font=("Consolas", 20, "bold"),
    )
    state = {"start": None, "rect": None, "done": False}

    def finish(region):
        if state["done"]:
            return
        state["done"] = True
        top.destroy()
        on_done(region)

    def on_press(e):
        state["start"] = (e.x, e.y)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

    def on_drag(e):
        if state["start"]:
            x0, y0 = state["start"]
            canvas.coords(state["rect"], x0, y0, e.x, e.y)

    def on_release(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        region = {
            "left": min(x0, e.x) + virtual["left"],
            "top": min(y0, e.y) + virtual["top"],
            "width": abs(e.x - x0),
            "height": abs(e.y - y0),
        }
        state["start"] = None
        if screen.region_is_valid(region):
            finish(region)
        else:
            canvas.itemconfig(hint, text="Box too small — drag again (Esc to cancel)")

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<ButtonPress-3>", lambda e: finish(None))
    top.bind("<Escape>", lambda e: finish(None))
    top.focus_force()
    top.grab_set()
    return top


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class App:
    BG = "#15171c"
    FG = "#e6e6e6"
    ACCENT = "#7CFC00"
    DIM = "#8a8f98"
    WARN = "#ffb347"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.configure(bg=self.BG)
        self.root.minsize(340, 260)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.settings = load_settings()
        self.spammer = clicker.Spammer(self.settings)
        self.events = queue.Queue()  # hotkey thread -> UI thread
        self.capturing_key = False
        self.running = True
        self.capturer = screen.Capturer()  # UI-thread capture for the preview
        self.preview_photo = None
        self.snapshot_photo = None
        self.snapshot = load_snapshot(self.settings.get("region"))
        self.match_px = None  # pixels the region currently differs from the snapshot
        self.watcher = None
        self.stop_reason = None  # shown in the State row after an auto-stop

        self._build_ui()
        self._show_snapshot()
        self.hotkeys = clicker.HotkeyPoller(lambda vk: self.events.put(("key", vk))).start()
        self._refresh()
        self._afters = {"poll": self.root.after(50, self._poll),
                        "tick": self.root.after(250, self._tick)}

        missing = self._missing()
        self.set_status("Ready. Press F1 to start." if not missing
                        else "Set up: " + " and ".join(missing) + ".")

    # ---- UI ------------------------------------------------------------- #
    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", padding=(8, 4))
        style.configure("TCheckbutton", background=self.BG, foreground=self.FG)
        style.map("TCheckbutton", background=[("active", self.BG)])

        pad = {"padx": 10}
        stats = tk.Frame(self.root, bg=self.BG)
        stats.pack(fill=tk.X, pady=(10, 4), **pad)

        self.vars = {}
        self.value_labels = {}
        rows = [
            ("state", "State"),
            ("interact", "Interact key"),
            ("click", "Click spot"),
            ("region", "Watch region"),
            ("snapshot", "Snapshot"),
            ("match", "Region now"),
            ("speed", "Interval"),
            ("cycles", "Cycles"),
        ]
        for i, (key, label) in enumerate(rows):
            tk.Label(stats, text=label, bg=self.BG, fg=self.DIM,
                     font=("Segoe UI", 10)).grid(row=i, column=0, sticky="w", pady=1)
            var = tk.StringVar(value="—")
            self.vars[key] = var
            big = key == "state"
            lbl = tk.Label(stats, textvariable=var, bg=self.BG, fg=self.FG,
                           font=("Consolas", 14 if big else 12, "bold" if big else "normal"))
            lbl.grid(row=i, column=1, sticky="e", pady=1)
            self.value_labels[key] = lbl
        stats.columnconfigure(1, weight=1)

        btns = tk.Frame(self.root, bg=self.BG)
        btns.pack(fill=tk.X, pady=(8, 4), **pad)
        self.record_btn = ttk.Button(btns, text="Record Interact", command=self.record_interact)
        self.record_btn.pack(side=tk.LEFT)
        self.start_btn = ttk.Button(btns, text="Start", command=self.toggle)
        self.start_btn.pack(side=tk.LEFT, padx=4)

        region_btns = tk.Frame(self.root, bg=self.BG)
        region_btns.pack(fill=tk.X, pady=(0, 4), **pad)
        ttk.Button(region_btns, text="Select Region", command=self.select_region
                   ).pack(side=tk.LEFT)
        ttk.Button(region_btns, text="Take Snapshot", command=self.take_snapshot
                   ).pack(side=tk.LEFT, padx=4)

        speed = tk.Frame(self.root, bg=self.BG)
        speed.pack(fill=tk.X, pady=(0, 8), **pad)
        ttk.Button(speed, text="Slower", command=lambda: self.change_speed(clicker.slower)
                   ).pack(side=tk.LEFT)
        ttk.Button(speed, text="Faster", command=lambda: self.change_speed(clicker.faster)
                   ).pack(side=tk.LEFT, padx=4)

        prev_frame = tk.Frame(self.root, bg=self.BG)
        prev_frame.pack(fill=tk.X, pady=(0, 6), **pad)
        for row, text in enumerate(("Snapshot:", "Now:")):
            tk.Label(prev_frame, text=text, bg=self.BG, fg=self.DIM,
                     font=("Segoe UI", 9)).grid(row=row, column=0, sticky="nw", pady=2)
        self.snapshot_view = tk.Label(prev_frame, bg="black", text="(not taken)",
                                      fg=self.DIM, font=("Segoe UI", 9))
        self.snapshot_view.grid(row=0, column=1, sticky="w", padx=6, pady=2)
        self.preview = tk.Label(prev_frame, bg="black", text="(no region)",
                                fg=self.DIM, font=("Segoe UI", 9))
        self.preview.grid(row=1, column=1, sticky="w", padx=6, pady=2)

        opts = tk.Frame(self.root, bg=self.BG)
        opts.pack(fill=tk.X, **pad)
        self.on_top = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Always on top", variable=self.on_top,
                        command=self._apply_on_top).pack(side=tk.LEFT)
        self._apply_on_top()
        self.show_log = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Show log", variable=self.show_log,
                        command=self._toggle_log).pack(side=tk.LEFT, padx=(12, 0))

        self.status = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self.status, bg=self.BG, fg=self.DIM,
                 font=("Segoe UI", 9), anchor="w", wraplength=380, justify="left",
                 ).pack(fill=tk.X, pady=(6, 8), **pad)

        # Log panel (hidden until "Show log" is ticked)
        self.log_frame = tk.Frame(self.root, bg=self.BG)
        self.log_text = tk.Text(self.log_frame, height=10, bg="#0d0e11", fg=self.FG,
                                font=("Consolas", 9), relief="flat", wrap="word",
                                state="disabled")
        scroll = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(self.root, text="F1 start/stop · F2 set click spot · F3 slower · F4 faster",
                 bg=self.BG, fg="#555", font=("Segoe UI", 8)).pack(side=tk.BOTTOM, pady=(0, 4))

    def _toggle_log(self):
        if self.show_log.get():
            self.log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        else:
            self.log_frame.pack_forget()
            self.root.geometry("")  # shrink back

    def log(self, text):
        t = self.log_text
        t.config(state="normal")
        t.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 500:
            t.delete("1.0", f"{lines - 500}.0")
        t.see("end")
        t.config(state="disabled")

    def set_status(self, text):
        self.status.set(f"{time.strftime('%H:%M:%S')}  {text}")
        self.log(text)

    def _apply_on_top(self):
        self.root.attributes("-topmost", self.on_top.get())

    def _refresh(self):
        s = self.settings
        running = self.spammer.running
        if running:
            self.vars["state"].set("RUNNING" + (" · watching" if self.watcher else ""))
        else:
            self.vars["state"].set(f"Stopped ({self.stop_reason})" if self.stop_reason
                                   else "Stopped")
        self.value_labels["state"].config(
            fg=self.ACCENT if running else self.WARN if self.stop_reason else self.FG)
        if self.capturing_key:
            self.vars["interact"].set("press a key…")
        else:
            self.vars["interact"].set(clicker.key_name(s["interact_vk"])
                                      if s["interact_vk"] else "not set")
        self.value_labels["interact"].config(
            fg=self.WARN if self.capturing_key or not s["interact_vk"] else self.FG)
        self.vars["click"].set(f"({s['click'][0]}, {s['click'][1]})" if s["click"]
                               else "not set — F2")
        self.value_labels["click"].config(fg=self.FG if s["click"] else self.WARN)
        self.vars["region"].set(fmt_region(s["region"]) if s.get("region") else "not set")
        self.value_labels["region"].config(fg=self.FG if s.get("region") else self.WARN)
        self.vars["snapshot"].set("saved" if self.snapshot else "not taken")
        self.value_labels["snapshot"].config(fg=self.FG if self.snapshot else self.WARN)
        if self.match_px is None:
            self.vars["match"].set("—")
            self.value_labels["match"].config(fg=self.DIM)
        elif self.match_px < screen.CHANGE_PIXELS:
            self.vars["match"].set("matches snapshot")
            self.value_labels["match"].config(fg=self.ACCENT)
        else:
            self.vars["match"].set(f"different ({self.match_px:,} px)")
            self.value_labels["match"].config(fg=self.WARN)
        self.vars["speed"].set(fmt_speed(s["gap"]))
        self.vars["cycles"].set(f"{self.spammer.cycles:,}")
        self.start_btn.config(text="Stop" if running else "Start")

    # ---- actions -------------------------------------------------------- #
    def _missing(self):
        out = []
        if not self.settings["interact_vk"]:
            out.append("click Record Interact")
        if not self.settings["click"]:
            out.append("put the mouse on the click spot and press F2")
        if not self.settings.get("region"):
            out.append("click Select Region")
        elif not self.snapshot:
            out.append("click Take Snapshot")
        return out

    def record_interact(self):
        if self.capturing_key:
            return
        self.stop()
        self.capturing_key = True
        self.record_btn.config(text="Press a key…")
        self.root.focus_force()
        self.root.bind("<KeyPress>", self._key_captured)
        self.set_status("Press the key you use to interact in game (Esc cancels).")
        self._refresh()

    def _key_captured(self, event):
        vk = event.keycode  # on Windows Tk's keycode is the virtual-key code
        if vk in clicker.HOTKEYS:
            self.set_status(f"{clicker.HOTKEYS[vk]} is a hotkey here — press a different key.")
            return
        self.root.unbind("<KeyPress>")
        self.capturing_key = False
        self.record_btn.config(text="Record Interact")
        if vk == clicker.VK_ESCAPE:
            self.set_status("Recording the interact key cancelled.")
        else:
            self.settings["interact_vk"] = vk
            save_settings(self.settings)
            self.set_status(f"Interact key set to {clicker.key_name(vk)}.")
        self._refresh()

    def record_click_spot(self):
        x, y = clicker.get_pos()
        self.settings["click"] = [x, y]
        save_settings(self.settings)
        self.set_status(f"Click spot set to ({x}, {y}).")
        self._refresh()

    def select_region(self):
        self.root.withdraw()
        # give the window time to disappear before the screenshot is taken
        self.root.after(250, lambda: select_region(self.root, self._region_chosen))

    def _region_chosen(self, region):
        self.root.deiconify()
        self._apply_on_top()
        if region:
            self.settings["region"] = region
            save_settings(self.settings)
            self._set_snapshot(None)  # an old snapshot doesn't fit a new region
            self.set_status(f"Watch region set: {fmt_region(region)}. Now get the game to "
                            "the screen clicking should run on and click Take Snapshot.")
        else:
            self.set_status("Region selection cancelled.")
        self._refresh()
        self._update_preview()

    def _set_snapshot(self, frame):
        self.snapshot = frame
        if frame is None:
            delete_snapshot()
            self.snapshot_photo = None
            self.snapshot_view.config(image="", text="(not taken)")
        else:
            screen.save_frame(frame, SNAPSHOT_FILE)
            self._show_snapshot()

    def _show_snapshot(self):
        if self.snapshot is None:
            return
        self.snapshot_photo = frame_photo(self.snapshot)
        self.snapshot_view.config(image=self.snapshot_photo, text="")

    def take_snapshot(self):
        region = self.settings.get("region")
        if not region:
            self.set_status("Click Select Region first.")
            return
        self.stop()
        if self._region_on_window():
            self.set_status("Can't take a snapshot: the region overlaps this window. "
                            "Move the window or select the region again.")
            return
        try:
            frame = self.capturer.grab(region)
        except OSError as e:
            self.set_status(f"Can't take a snapshot: {e}.")
            return
        self._set_snapshot(frame)
        self.stop_reason = None
        self.set_status("Snapshot saved. Clicking will only start while the region "
                        "looks like this, and stops as soon as it doesn't.")
        if frame.is_blank():
            self.log("Note: the snapshot is one solid colour. If the game is in exclusive "
                     "fullscreen, captures may be black; use windowed mode.")
        self._update_preview(frame)

    def _update_preview(self, frame=None):
        region = self.settings.get("region")
        if not region:
            self.preview_photo = None
            self.match_px = None
            self.preview.config(image="", text="(no region)")
            self._refresh()
            return
        try:
            frame = frame or self.capturer.grab(region)
        except OSError as e:
            self.match_px = None
            self.preview.config(image="", text=f"(can't capture: {e})")
            self._refresh()
            return
        self.match_px = (screen.changed_pixels(frame, self.snapshot)
                         if self.snapshot else None)
        self.preview_photo = frame_photo(frame)
        self.preview.config(image=self.preview_photo, text="")
        self._refresh()

    def _window_box(self):
        r = self.root
        return (r.winfo_rootx(), r.winfo_rooty(),
                r.winfo_rootx() + r.winfo_width(), r.winfo_rooty() + r.winfo_height())

    def _spot_on_window(self):
        x, y = self.settings["click"]
        x0, y0, x1, y1 = self._window_box()
        return x0 <= x < x1 and y0 <= y < y1

    def _region_on_window(self):
        g = self.settings["region"]
        x0, y0, x1, y1 = self._window_box()
        return (g["left"] < x1 and x0 < g["left"] + g["width"]
                and g["top"] < y1 and y0 < g["top"] + g["height"])

    def _spot_in_region(self):
        x, y = self.settings["click"]
        g = self.settings["region"]
        return (g["left"] <= x < g["left"] + g["width"]
                and g["top"] <= y < g["top"] + g["height"])

    def _on_region_change(self, frame, info):
        """Watcher thread: stop clicking right now, tell the UI after."""
        self.spammer.stop_event.set()
        self.events.put(("changed", frame, info))

    def start(self):
        if self.spammer.running:
            return
        if self.capturing_key:
            self.set_status("Finish recording the interact key first.")
            return
        missing = self._missing()
        if missing:
            self.set_status("Can't start: " + " and ".join(missing) + ".")
            return
        if self._spot_on_window():
            self.set_status("Can't start: the click spot is on this window. "
                            "Move the window or set the spot again with F2.")
            return
        region = self.settings["region"]
        if self._region_on_window():
            self.set_status("Can't start: the watch region overlaps this window. "
                            "Move the window or select the region again.")
            return
        try:
            now = self.capturer.grab(region)
        except OSError as e:
            self.set_status(f"Can't start: {e}.")
            return
        differ = screen.changed_pixels(now, self.snapshot)
        self._update_preview(now)
        if differ >= screen.CHANGE_PIXELS:
            self.set_status(f"Not started: the region doesn't match the snapshot "
                            f"({differ:,} pixels differ). Get the game back to the snapshot "
                            "screen, or click Take Snapshot again.")
            return
        self.stop_reason = None
        self.spammer.start()  # before the watcher, so an instant change still stops it
        self.watcher = screen.Watcher(region, self.snapshot, self._on_region_change).start()
        s = self.settings
        self.set_status(f"Running: {clicker.key_name(s['interact_vk'])} + left click at "
                        f"({s['click'][0]}, {s['click'][1]}) every {s['gap'] * 1000:.0f} ms, "
                        "stops when the region stops matching the snapshot. F1 to stop.")
        if self._spot_in_region():
            self.log("Note: the click spot is inside the watched region, so if a click "
                     "changes it, clicking stops right away.")
        self._refresh()

    def _stop_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    def stop(self):
        if not self.spammer.running:
            self._stop_watcher()
            return
        self._stop_watcher()
        self.spammer.stop()
        self.set_status(f"Stopped after {self.spammer.cycles:,} cycles.")
        self._refresh()

    def _region_changed(self, frame, info):
        watcher, self.watcher = self.watcher, None
        self.spammer.stop()
        if frame is None:
            self.stop_reason = "can't see region"
            self.set_status(f"Stopped: {info}. Clicking stops when the region can't be checked.")
        else:
            self.stop_reason = "region changed"
            self.set_status(f"Stopped: the region no longer matches the snapshot ({info:,} pixels) "
                            f"after {self.spammer.cycles:,} cycles.")
            self._update_preview(frame)
        if watcher:
            self.log(f"Checked the region {watcher.checks:,} times.")
        self._refresh()

    def toggle(self):
        self.stop() if self.spammer.running else self.start()

    def change_speed(self, fn):
        self.settings["gap"] = fn(self.settings["gap"])
        save_settings(self.settings)
        self.set_status(f"Interval: {fmt_speed(self.settings['gap'])}")
        self._refresh()

    def quit(self):
        self.running = False
        self.hotkeys.stop()
        self._stop_watcher()
        self.spammer.stop()
        for after_id in self._afters.values():
            self.root.after_cancel(after_id)
        self.capturer.close()
        self.root.destroy()

    # ---- event loop ------------------------------------------------------ #
    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "changed":
                    self._region_changed(event[1], event[2])
                    continue
                vk = event[1]
                if vk == clicker.VK_F1:
                    self.toggle()
                elif vk == clicker.VK_F2:
                    self.record_click_spot()
                elif vk == clicker.VK_F3:
                    self.change_speed(clicker.slower)
                elif vk == clicker.VK_F4:
                    self.change_speed(clicker.faster)
        except queue.Empty:
            pass
        if self.running:
            self._afters["poll"] = self.root.after(50, self._poll)

    def _tick(self):
        if not self.running:
            return
        self._refresh()
        if self.root.state() != "withdrawn":
            # while watching, show the watcher's latest frame instead of capturing again
            self._update_preview(self.watcher.latest if self.watcher else None)
        self._afters["tick"] = self.root.after(250, self._tick)

    def run(self):
        self.root.mainloop()


def main():
    clicker.set_dpi_aware()  # before Tk, so window and cursor coords agree
    App().run()


if __name__ == "__main__":
    main()
