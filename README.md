# Maple PQ Autoclicker

A small always-on-top window for MapleStory party quests. It spams a
**left click on one spot**, fast, until you stop it, but only while the
screen shows what you expect.

You set up:

- **Click spot**: where on the screen to left-click.
- **Watch region + snapshot**: a part of the screen (e.g. the NPC's text)
  and a saved picture of what it should look like. Clicking only starts
  while the region matches the snapshot, and stops as soon as it doesn't
  (within a few hundredths of a second). Pressing start on the wrong screen
  does nothing.

## Download (easiest)

Grab **`MaplePQAutoclicker.exe`** from the
[latest release](https://github.com/tleytek/maple-pq-autoclicker/releases/latest)
and double-click it. Nothing else to install. Windows asks to run it as
administrator: click **Yes** (see below for why).

Windows SmartScreen may say "Windows protected your PC" because the exe isn't
code-signed. Click **More info → Run anyway**.

## Run from source

1. Install **Python 3** from <https://www.python.org/downloads/>.
   In the installer, tick **"Add python.exe to PATH"**.
2. Download this repo (green **Code** button → **Download ZIP**, then unzip),
   or `git clone` it.
3. Double-click **`run.bat`** and click **Yes** when Windows asks for
   administrator. No packages to install.

To build the exe yourself, run **`build.bat`**. It writes
`dist\MaplePQAutoclicker.exe`.

## Usage

1. Put the mouse where you want it to click and press **F2**.
2. Click **Select Region** and drag a box around the text to watch.
3. With the game on the screen where clicking should run, click
   **Take Snapshot**. The **Snapshot** and **Now** previews show the saved
   picture and the live region; **Region now** says whether they match.
4. Switch to the game and press **F1** to start. Press **F1** again to stop.

If the region doesn't match the snapshot, F1 does nothing (the status line
says how many pixels differ). After it stops on a change, it won't start
again until the region matches the snapshot again. The snapshot is kept
between runs; take a new one whenever you want a different screen.

Each click: move to the spot and press, hold (default 35 ms), release,
then pause (default 30 ms). Both timers are adjustable.
Clicks follow a fixed schedule, so the rhythm stays even. The hold is
longer than one game frame, so the game never misses a click.
The mouse is moved to the spot for every click, so you can't use it while
it runs.

### Why administrator?

MapleStory runs as administrator, and Windows silently ignores clicks that
normal programs send to an administrator program. So the app
has to run as administrator too: the exe asks every time it opens, and
`run.bat` starts it that way. If it's ever started without admin while an
admin MapleStory is open, Start refuses and says so instead of clicking into
nothing.

| Control | Action |
|---|---|
| F2 | Set the click spot to where the mouse is now |
| Select Region | Pick the part of the screen to watch (drag a box; Esc cancels) |
| Take Snapshot | Save what the region must look like for clicking to run |
| F1 / Start | Start / stop |
| Hold: Shorter / Longer | Time between button down and up, 5 ms per click (5 ms to 1 s) |
| Pause: Shorter / Longer | Time between button up and the next down, 5 ms per click (10 ms to 2 s) |
| F3 / F4 | Longer / shorter pause (5 ms) |
| F5 / F6 | Longer / shorter hold (5 ms) |
| Always on top | Keep the window above the game |
| Show log | Show what happened, with times |

Tips:

- **Hold** is button down → up, **Pause** is button up → next down. The
  defaults (35 + 30 ms) make about 15 clicks per second; the **Rate** row
  shows it. Change either while running. A hold under ~17 ms (one game
  frame) turns orange: the game can miss clicks that short.
- When clicking stops, **Show log** lists the measured timing, e.g.
  `Click timing (last 120): every 65.0 ms on average, shortest 64.9 ms,
  longest 65.1 ms`. If shortest and longest are close but it still feels
  uneven, the unevenness is in the game, not the clicker.
- It won't start if the click spot is on its own window, or the watch region
  is on or right next to it (its shadow changes shade when you click
  between the app and the game).
- The region is compared pixel by pixel with the snapshot (no text reading,
  so the game's small font isn't a problem). Faint colour shifts (under
  8/255, like a window shadow or slight shading) are ignored. It matches
  when fewer than 10 pixels clearly differ. While running, it stops when
  10+ pixels differ in 2 captures in a row, so a single flicker doesn't
  count.
- Selecting a new region throws the old snapshot away.
- Keep the region tight around the text. Anything moving inside it (your
  character, mobs, animated backgrounds) also counts as a change.
- If the click spot is inside the region and clicking changes it (e.g. a
  button lights up), it stops right away.
- The game has to be in windowed or borderless mode: in exclusive fullscreen
  the capture is black and changes aren't seen.
- Settings and the snapshot (`snapshot.ppm`) are saved in
  `%APPDATA%\MaplePQAutoclicker`.
- Automating input may be against the game's rules. Use at your own risk.

## Tests

```
python test_clicker.py
python test_screen.py
python test_app.py
```

They don't click: input is sent to a fake. One test moves the
cursor (no clicks) to check it lands on the exact pixel, then puts it back.
The screen tests open small windows and capture them, so run them on a
visible desktop.
