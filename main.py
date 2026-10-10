"""Maple PQ Autoclicker.

Spams a left click on a saved spot: click, pause, repeat.

1. F2: saves the mouse position as the spot to left-click.
2. Select Region: the part of the screen to watch.
3. Take Snapshot: saves what that region must look like. Clicking only
   starts while the region matches it, and stops as soon as it doesn't.
4. F1: start / stop.   F3 longer pause, F4 shorter pause.
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


SHADOW_MARGIN = 20  # px around this window kept clear of the watch region

ADMIN_HINT = ("Can't start: MapleStory runs as administrator, so Windows blocks this "
              "app's keys and clicks. Close this app and run it as administrator "
              "(right-click → Run as administrator).")


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
    if isinstance(data, dict):  # (an old "interact_vk" entry is simply ignored)
        pos = data.get("click")
        if (isinstance(pos, list) and len(pos) == 2
                and all(isinstance(v, int) for v in pos)):
            settings["click"] = pos
        if screen.region_is_valid(data.get("region")):
            settings["region"] = {k: data["region"][k] for k in ("left", "top", "width", "height")}
        # The hold is saved as "click_hold". An old "hold" entry (v1.4.0 and
        # earlier saved 15 ms there automatically) is ignored.
        hold = data.get("click_hold")
        if isinstance(hold, (int, float)) and clicker.MIN_HOLD <= hold <= clicker.MAX_HOLD:
            settings["hold"] = float(hold)
        for key, lo, hi in (("gap", clicker.MIN_GAP, clicker.MAX_GAP),):
            v = data.get(key)
            if isinstance(v, (int, float)) and lo <= v <= hi:
                settings[key] = float(v)
    return settings


def save_settings(settings, path=None):
    path = path or SETTINGS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(settings)
    data["click_hold"] = data.pop("hold")
    with open(path, "w") as f:
        json.dump(data, f)


def click_seconds(settings):
    """One click (button held `hold`) plus the pause."""
    return settings["hold"] + settings["gap"]


def fmt_ms(seconds):
    return f"{seconds * 1000:.0f} ms"


def fmt_speed(settings):
    return f"{1 / click_seconds(settings):.1f} clicks / sec"


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
        if clicker.game_blocks_input():
            self.set_status(ADMIN_HINT.replace("Can't start: ", ""))

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
            ("click", "Click spot"),
            ("region", "Watch region"),
            ("snapshot", "Snapshot"),
            ("match", "Region now"),
            ("hold", "Hold (down → up)"),
            ("gap", "Pause (up → down)"),
            ("speed", "Rate"),
            ("cycles", "Clicks"),
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
        self.start_btn = ttk.Button(btns, text="Start", command=self.toggle)
        self.start_btn.pack(side=tk.LEFT)

        region_btns = tk.Frame(self.root, bg=self.BG)
        region_btns.pack(fill=tk.X, pady=(0, 4), **pad)
        ttk.Button(region_btns, text="Select Region", command=self.select_region
                   ).pack(side=tk.LEFT)
        ttk.Button(region_btns, text="Take Snapshot", command=self.take_snapshot
                   ).pack(side=tk.LEFT, padx=4)

        timers = tk.Frame(self.root, bg=self.BG)
        timers.pack(fill=tk.X, pady=(0, 8), **pad)
        for row, (label, key, shorter, longer) in enumerate((
                ("Hold", "hold", clicker.shorter_hold, clicker.longer_hold),
                ("Pause", "gap", clicker.faster, clicker.slower))):
            tk.Label(timers, text=label, bg=self.BG, fg=self.DIM, width=6, anchor="w",
                     font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", pady=1)
            ttk.Button(timers, text="Shorter",
                       command=lambda k=key, f=shorter: self.change_timer(k, f)
                       ).grid(row=row, column=1, pady=1)
            ttk.Button(timers, text="Longer",
                       command=lambda k=key, f=longer: self.change_timer(k, f)
                       ).grid(row=row, column=2, padx=4, pady=1)

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

        tk.Label(self.root, text="F1 start/stop · F2 set click spot · F3/F4 longer/shorter pause",
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
        self.vars["hold"].set(fmt_ms(s["hold"]))
        self.value_labels["hold"].config(
            fg=self.WARN if s["hold"] < clicker.FRAME else self.FG)
        self.vars["gap"].set(fmt_ms(s["gap"]))
        self.vars["speed"].set(fmt_speed(s))
        self.vars["cycles"].set(f"{self.spammer.cycles:,}")
        self.start_btn.config(text="Stop" if running else "Start")

    # ---- actions -------------------------------------------------------- #
    def _missing(self):
        out = []
        if not self.settings["click"]:
            out.append("put the mouse on the click spot and press F2")
        if not self.settings.get("region"):
            out.append("click Select Region")
        elif not self.snapshot:
            out.append("click Take Snapshot")
        return out

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
            self.set_status("Can't take a snapshot: the region overlaps or touches this "
                            "window (its shadow changes shade). Move the window away "
                            "or select the region again.")
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
        """Region overlaps this window or its drop shadow (which changes
        shade when focus moves between this window and the game)."""
        g = self.settings["region"]
        x0, y0, x1, y1 = self._window_box()
        m = SHADOW_MARGIN
        return (g["left"] < x1 + m and x0 - m < g["left"] + g["width"]
                and g["top"] < y1 + m and y0 - m < g["top"] + g["height"])

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
        missing = self._missing()
        if missing:
            self.set_status("Can't start: " + " and ".join(missing) + ".")
            return
        if clicker.game_blocks_input():
            self.set_status(ADMIN_HINT)
            return
        if self._spot_on_window():
            self.set_status("Can't start: the click spot is on this window. "
                            "Move the window or set the spot again with F2.")
            return
        region = self.settings["region"]
        if self._region_on_window():
            self.set_status("Can't start: the watch region overlaps or touches this "
                            "window (its shadow changes shade). Move the window away "
                            "or select the region again.")
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
        self.set_status(f"Running: left click at ({s['click'][0]}, {s['click'][1]}), "
                        f"pause {s['gap'] * 1000:.0f} ms. Stops when the region stops "
                        "matching the snapshot. F1 to stop.")
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
        self.set_status(f"Stopped after {self.spammer.cycles:,} clicks.")
        self._log_timing()
        self._refresh()

    def _log_timing(self):
        n, mean, lo, hi = clicker.timing_stats(self.spammer.presses)
        if n:
            self.log(f"Click timing (last {n:,}): every {mean:.1f} ms on average, "
                     f"shortest {lo:.1f} ms, longest {hi:.1f} ms.")

    def _region_changed(self, frame, info):
        watcher, self.watcher = self.watcher, None
        self.spammer.stop()
        if frame is None:
            self.stop_reason = "can't see region"
            self.set_status(f"Stopped: {info}. Clicking stops when the region can't be checked.")
        else:
            self.stop_reason = "region changed"
            self.set_status(f"Stopped: the region no longer matches the snapshot ({info:,} pixels) "
                            f"after {self.spammer.cycles:,} clicks.")
            self._update_preview(frame)
        if watcher:
            self.log(f"Checked the region {watcher.checks:,} times.")
        self._log_timing()
        self._refresh()

    def toggle(self):
        self.stop() if self.spammer.running else self.start()

    def change_timer(self, key, fn):
        """key "hold" (down -> up) or "gap" (up -> next down); fn steps it 5 ms."""
        self.settings[key] = fn(self.settings[key])
        save_settings(self.settings)
        s = self.settings
        self.set_status(f"Hold {fmt_ms(s['hold'])}, pause {fmt_ms(s['gap'])} "
                        f"({fmt_speed(s)}).")
        if key == "hold" and s["hold"] < clicker.FRAME:
            self.log("Note: a hold shorter than one game frame (~17 ms) can be missed "
                     "by the game.")
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
                    self.change_timer("gap", clicker.slower)
                elif vk == clicker.VK_F4:
                    self.change_timer("gap", clicker.faster)
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
