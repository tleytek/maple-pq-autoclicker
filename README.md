# Maple PQ Autoclicker

A small always-on-top window for MapleStory party quests. It spams your
**interact** key and a **left click** on one spot, fast, until you stop it.

You set two things:

- **Interact key**: the key you use to talk / pick up / interact in game.
- **Click spot**: where on the screen to left-click.

## Download (easiest)

Grab **`MaplePQAutoclicker.exe`** from the
[latest release](https://github.com/tleytek/maple-pq-autoclicker/releases/latest)
and double-click it. Nothing else to install.

Windows SmartScreen may say "Windows protected your PC" because the exe isn't
code-signed. Click **More info → Run anyway**.

## Run from source

1. Install **Python 3** from <https://www.python.org/downloads/>.
   In the installer, tick **"Add python.exe to PATH"**.
2. Download this repo (green **Code** button → **Download ZIP**, then unzip),
   or `git clone` it.
3. Double-click **`run.bat`**. No packages to install.

To build the exe yourself, run **`build.bat`**. It writes
`dist\MaplePQAutoclicker.exe`.

## Usage

1. Click **Record Interact**, then press your interact key (Esc cancels).
2. Put the mouse where you want it to click and press **F2**.
3. Switch to the game and press **F1** to start. Press **F1** again to stop.

Each cycle taps the interact key, then left-clicks the spot. The mouse is
moved to the spot for every click, so you can't use it while it runs.

| Control | Action |
|---|---|
| Record Interact | Set the interact key (press it after clicking the button) |
| F2 | Set the click spot to where the mouse is now |
| F1 / Start | Start / stop |
| F3 / Slower | Longer interval (×1.5) |
| F4 / Faster | Shorter interval (÷1.5, down to 10 ms) |
| Always on top | Keep the window above the game |
| Show log | Show what happened, with times |

Tips:

- The interval is the time from one key + click to the next. Default 50 ms
  (20 per second). Change it while running.
- If the game ignores it, close the app and start it again with right-click →
  **Run as administrator** (needed when the game itself runs as admin).
- It won't start if the click spot is on its own window.
- Settings are saved in `%APPDATA%\MaplePQAutoclicker`.
- Automating input may be against the game's rules. Use at your own risk.

## Tests

```
python test_clicker.py
python test_app.py
```

They don't press keys or click: input is sent to a fake. One test moves the
cursor (no clicks) to check it lands on the exact pixel, then puts it back.
