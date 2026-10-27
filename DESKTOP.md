# Windows & Linux desktop prototype

[All guides](README.md)

**Experimental prototype — status checked 27 October 2026.**

This work runs the original Nemo's Reef ARM game engine in a desktop window,
using a compatibility layer and ARM emulation. Android, an Android emulator,
and a Java runtime are not used by the desktop client. It is still early
development and is not a finished native Windows/Linux port.

The [website countdown](https://nemos-reef-revival.pages.dev/) is counting down
to the Windows version. It does not announce a verified Linux release.

## Availability and platform status

| Platform | Current status |
|---|---|
| Windows | A developer test build has run the original reef, UI, pointer input, sound effects and opening gameplay. Fullscreen is implemented. |
| Linux | Intended target. No Linux build or gameplay session has been verified. |
| Public desktop downloads | No desktop installer, release asset or desktop source snapshot is published in this repository yet. |

The public `re-notes` branch currently contains the Android server and patcher.
Downloading that ZIP does not provide the desktop client described here.
Ask the maintainer in the [project Discord](https://discord.gg/mseaKJb5G)
whether a current experimental test build or desktop-source snapshot is
available. Do not treat an Android APK or the Docker image as a desktop game.

## What has been checked on Windows

- Original reef graphics, fish animation and opening dialogue.
- Mouse input, text entry, algae collection, Nursery selection and first plant
  placement, including saving rewards and plant positions.
- Fullscreen, correct game proportions, mouse mapping and returning to the
  original window size and position.
- Original sound effects from the APK.
- Daily-bonus recovery on a copy of an affected save: two remaining rewards
  revealed correctly, the counter reached zero, and the game returned to the
  reef. The XP already credited by the earlier crash was retained.
- 163 desktop tests and five daily-reward server tests passed for the
  27 October developer snapshot. These checks do not establish that all
  gameplay, menus or later quests work.

## How to try a provided Windows test build

These instructions apply **after the maintainer provides a desktop test
package**; there is no public download link to one yet.

1. Extract the whole package into a folder on your PC.
2. Keep `NemosReef.exe`, its `_internal` folder and your original Android 1.8.1
   APK named `nemo.apk` together. Keep the original APK unchanged.
3. Double-click **NemosReef.exe**. The self-contained Windows build does not
   require a separate Python or Android installation.
4. Allow several minutes for the first reef load. A responsive loading window
   does not mean the game has finished loading.
5. Use the mouse and controls below. Close the window normally when finished.

To start fullscreen from PowerShell in that folder:

```powershell
.\NemosReef.exe --fullscreen
```

## Controls

| Action | Control |
|---|---|
| Select buttons and objects | Left click |
| Drag an object or the reef where the original UI permits it | Hold the left mouse button and move |
| Edit a name or age field | Click the field, type, then press Enter |
| Toggle fullscreen | F11 or Alt+Enter |
| Leave fullscreen | Esc while the game window has keyboard focus |
| Exit | Close the game window |

Fullscreen preserves the game's proportions. Black bars appear when the
monitor has a different shape. Mouse input accounts for those bars and
display scaling.

## Server setup and saves

The normal desktop launcher starts **its own local server**, bound to
`127.0.0.1` on an automatically selected port. It stops that server when the
game closes. You do not need to start Docker for this launcher.

**It does not connect to your existing Docker, NAS or Android server by
default.** Its account and reef data are separate. Sharing saves or migrating
a reef between those setups has not been validated.

| Run type | Save location |
|---|---|
| Packaged Windows build | `%LOCALAPPDATA%\NemosReef\work\play\` |
| Developer source launcher | `desktop/work/play/` inside that source snapshot |

Inside the save folder, `client-data` holds the desktop client profile and
`server-data/nemo.db` holds the local server's accounts and reefs. Keep both
when backing up. Close the game before copying them.

New packaged Windows builds reuse the same default save location. Changing
the folder containing the executable does not create a new save by itself.
Source-development saves are separate from packaged Windows saves.

## Developer launch instructions

A desktop-source snapshot must contain `desktop/play.py`,
`desktop/requirements-graphics.txt` and `server/nemo_server.py`.
The commands below require that snapshot, not the current public server ZIP.

### Windows source snapshot

The tested development environment used Python 3.14 on Windows. Put your
original APK at the snapshot root as `nemo.apk`, then double-click
`play-desktop.bat`. It installs the pinned dependencies if needed.

Alternatively, from the snapshot root:

```powershell
python -m pip install --target desktop/.deps -r desktop/requirements-graphics.txt
python desktop/play.py
```

Add `--fullscreen` to the last command to start fullscreen.

### Linux: proposed developer steps, not verified

No Linux distribution, packaged build or gameplay run has been validated.
These are exploratory launch steps for a supplied desktop-source snapshot:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r desktop/requirements-graphics.txt
python desktop/play.py
```

Put the original `nemo.apk` at the snapshot root first. A graphical desktop,
working OpenGL compatibility driver, GLFW support and Python Tk support for
text input are required. Distribution-specific packages, audio and display
behavior still need testing. There is no verified Linux installer.

## Known limitations and performance

- **It is slow.** A measured Windows reef scene improved from roughly 2 FPS to
  3.27 FPS after a timer fix. This remains below comfortable gameplay speed.
  Frame rate varies with the scene and computer; this is not a benchmark
  promise for other machines or Linux.
- Loading can take several minutes. One Windows check including startup and
  120 original frame callbacks took about four minutes.
- Extended gameplay and later screens remain unverified; further crashes
  and compatibility problems may remain.
- Daily rewards previously crashed when the reconstructed server awarded
  XP without a displayable reward. The corrected developer build supplies
  supported currencies and rejects taps after the day's allowance is used.
  Older test builds may still contain that bug.
- Sound effects use the original assets, but the original APK has no
  background music files. Desktop background-music restoration is not
  verified. The [Android patcher's music setup](PATCHING.md#3-download-the-original-music-required)
  is a separate Android workflow.
- The public Android server's feature list is not a claim that those features
  have all been tested in this desktop client.

## Report a desktop bug

Use [GitHub Issues](https://github.com/lozza/nemos-reef-revival/issues)
or the project's dedicated desktop-prototype Discord channel once available.
Include the platform, test-build date, exact steps, the last screen or button,
and a screenshot or the error text. For performance reports, include the
scene and approximate frame rate.

Packaged Windows reports are normally here:

- `%LOCALAPPDATA%\NemosReef\work\play\client-session.json`
- `%LOCALAPPDATA%\NemosReef\work\launch-error.txt` after a reported failure

Review logs before sharing them: they can contain player identifiers,
signatures and private server addresses. Do not post account/save databases,
the original APK or music files in public bug reports.

[Android setup](PATCHING.md) · [Android/server status](STATUS.md) ·
[Project Discord](https://discord.gg/mseaKJb5G)
