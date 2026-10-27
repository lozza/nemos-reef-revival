# Development and advanced tools

[All guides](README.md)

Source code and launchers live on the
[re-notes branch](https://github.com/lozza/nemos-reef-revival/tree/re-notes).
The default branch provides the public setup guides and Compose files.

## Experimental desktop work

See [the desktop prototype guide](DESKTOP.md) for tested Windows behavior,
unverified Linux support, developer launch instructions, controls and separate
saves. Desktop-source snapshots are not published on the current `re-notes`
branch. The standard server/patcher ZIP is for the Android workflow.

## Source and tests

The Python server uses the standard library. From a source checkout:

```sh
python -m unittest discover -s tests -v
```

Tests use synthetic assets; original game files are not included.

Key files:

- [server/nemo_server.py](https://github.com/lozza/nemos-reef-revival/blob/re-notes/server/nemo_server.py):
  HTTP server; accepts `--apk`, `--port`, `--data` and `--worlddef`.
- [server/worlddef.py](https://github.com/lozza/nemos-reef-revival/blob/re-notes/server/worlddef.py):
  reads game definitions from the original APK.
- [patcher/patch_apk.py](https://github.com/lozza/nemos-reef-revival/blob/re-notes/patcher/patch_apk.py):
  patches client URLs, textures and signing.
- [tools/build_story.py](https://github.com/lozza/nemos-reef-revival/blob/re-notes/tools/build_story.py):
  generates story data. Text keys remain in the story JSON; text is read from the APK.
- [Protocol notes](https://github.com/lozza/nemos-reef-revival/blob/re-notes/PROTOCOL.md)
  and [game-action notes](https://github.com/lozza/nemos-reef-revival/blob/re-notes/ACTIONS.md):
  reverse-engineering references.

## Economy profiles

The default `revival` profile enables the algae exchange, seed changes,
production/growth balance and new pearl rewards.
Use `--economy classic` or `NEMO_ECONOMY=classic` to select the previous
catalogue. See [the player and host guide](ECONOMY.md).

- `server/economy.py` defines seed rarity groups, the guarantee, timer migration,
  exchange prices, persisted UTC-day limits and request receipts.
- `server/economy_ui.py` generates corrected exchange screens from the host's
  own APK. No original artwork or compiled UI assets are included in the repo.
  The server supplies these two generated resources through the game's existing
  resource-download system. Classic mode supplies the original files for rollback.
- Exchange costs and rewards are server-defined. A successful exchange saves
  balances, quota and its receipt together; client-supplied rewards are ignored.
- The mystery-seed guarantee is account-wide and survives reconnects. Existing
  seed results and paid growing stages are retained.
- Production uses fixed native harvests. Nursery costs and pearl-fish feeding
  are capped; story milestone and future level rewards supply pearls. A one-time
  migration credits new pearl bonuses for completed eligible story milestones.
  No sand-dollar/pearl exchange is provided.

The patcher fixes the exchange flow's hardcoded pearl balance check and uses
the game API route `/nemo/e1/` to advertise this capability. Older routes still
work for normal play and the server-side economy, but receive no exchange offers.
The account API route stays unchanged. Server-side validation remains the
authority for prices, balances and daily limits.

## Local Docker builds

From the source checkout:

```sh
docker build -t nemos-reef:local .
```

In a development copy of Compose, set `image: nemos-reef:local` and remove
`pull_policy: always`. Keep your APK and data mounts.

The publication workflow tests the server and Docker persistence, builds
AMD64/ARM64 images and checks the published image. Publications have
`latest` and `sha-FULL_COMMIT_SHA` tags for version pinning.

## Patcher options

The patcher writes both legacy API URLs into the native client. Keep the
server base URL short (at most 29 characters); the patcher checks whether it fits.

**Android Studio emulator:** the existing tested setup uses an Android 11
(API 30) x86_64 image with ARM translation. From that emulator, the PC is
`10.0.2.2`. Use:

```sh
python patcher/patch_apk.py nemo.apk http://10.0.2.2:8080 --debug-log --emulator --music music
```

The emulator flag works around a GL-driver query that can otherwise crash
startup. It is not needed for normal Android devices.

**Original music — required setup step:** the original APK has no background
music. Follow [download and file-placement instructions](PATCHING.md#3-download-the-original-music-required)
before patching, using the linked original soundtrack. The patcher accepts
`music0` through `music3` in MP3 or Ogg format. Add `--music music` to manual
commands; the Windows batch patcher detects the folder automatically. Check
that all four tracks were added. Music files are not supplied by this repo.

## Economy checks

Run the automated checks with `python -m unittest discover -s tests -v`.
They use synthetic assets; no game files or saved reefs are required.

To reproduce the session model using your own original APK:

```sh
python tools/economy_report.py --apk nemo.apk --output BALANCE.md
```

The report compares the Classic and Revival catalogues without reading a save.
It documents its schedules, costs and pearl-income assumptions. Use copied saves
for device tests. The production balance uses native fixed harvests, not hidden
banked rewards; shorter production timers round XP down to avoid raising the
maximum XP rate.

## Android game logs

For an APK patched with `--debug-log`, install
[Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools),
enable USB debugging on the phone, connect it and accept its authorisation prompt.

From a terminal where `adb` is available:

```sh
adb logcat -c
adb logcat -s SDR:* AndroidRuntime:E DEBUG:* > nemo-log.txt
```

Reproduce the issue, then press Ctrl+C. Review the log before sharing it.

## Reset tools

These are destructive developer tools. Stop the server and
[back up first](SAVES.md).

- `reset-reef.bat` asks for confirmation and deletes **all saved reefs**,
  keeping accounts.
- `new-start.bat` asks for confirmation and deletes **all accounts and reefs**.
- `reef-report.bat` writes a report of saved fish/plant combinations.

Use these only for the Windows data directory they operate on. They are not
Docker migration or update steps.
