# Patch and install the APK

[All guides](README.md)

Do this **after** setting up the [Docker](DOCKER.md) or [Windows](WINDOWS.md)
server. Patching makes a copy of the Android game that connects to your server.

These steps run on a Windows PC. Your Docker server can stay on your NAS.

## 1. Prepare the patcher

If you followed Windows setup, reuse your existing project folder and Python.

Otherwise:

1. Install [Python for Windows](https://www.python.org/downloads/windows/).
   Enable **Add python.exe to PATH** if offered.
2. [Download the server and patcher ZIP](https://github.com/lozza/nemos-reef-revival/archive/refs/heads/re-notes.zip)
   and **Extract All**.
3. Open the extracted folder containing `patch-apk.bat` and `patcher`.
4. Put your **original Android 1.8.1 APK** there and name it `nemo.apk`.

Then install the signing tools:

1. Install a Windows JRE from [Adoptium Temurin](https://adoptium.net/temurin/releases/),
   choosing the architecture for your PC.
2. [Download uber-apk-signer 1.3.0](https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar).
3. Put `uber-apk-signer-1.3.0.jar` in the project's **patcher** folder.

Signing lets Android install the modified APK. These tools belong on the
patching PC; they are not needed on a Docker host.

## 2. Check the server address

Use the address that passed your server's health check, **without `/healthz`**.

For example: `http://192.168.1.20:8080`.

For Docker, use the Docker host's IP and the **left-hand port** in
`"8080:8080"`. Use the NAS IP if the server runs on a NAS.
For Windows, use the PC's IPv4 address.

The phone must be able to reach that address. Check its `/healthz` page in
the phone's browser first.

## 3. Download the original music (required)

The original Android APK does **not** contain the background music. Complete
this step before patching so the installed game has its original soundtrack.

1. Open [Nemo's Reef — original soundtrack downloads](https://downloads.khinsider.com/game-soundtracks/album/nemos-reef-android-ios-gamerip-2012).
2. Download the four original tracks, **Music 0** through **Music 3**, as MP3
   files. Downloading them separately is fine.
3. Create a folder named **music** inside the project folder, next to
   `patch-apk.bat` and `nemo.apk`.
4. Name the files exactly as follows. Keep their real `.mp3` extension;
   do not name them `music0.mp3.mp3`.

| Original track | Required filename in the music folder |
|---|---|
| Music 0 (track 1) | `music0.mp3` |
| Music 1 (track 2) | `music1.mp3` |
| Music 2 (track 3) | `music2.mp3` |
| Music 3 (track 4) | `music3.mp3` |

Your folder should look like this:

```text
project-folder/
  nemo.apk
  patch-apk.bat
  patcher/
  music/
    music0.mp3
    music1.mp3
    music2.mp3
    music3.mp3
```

The patcher also accepts real Ogg files named `music0.ogg` through
`music3.ogg`; MP3 downloads do not need conversion. Keep one version of
each track in the folder, because Ogg files take priority over MP3 files
with the same number.

The Windows batch patcher picks up this folder automatically. A command-line
patch must include **`--music music`**, as shown below. Check that the patcher
prints **`adding 4 music track(s) from music`** before installing the result.
If fewer tracks are found, fix the filenames and rebuild the patched APK.

Adding files to the folder after patching does not update an already-built
APK. The tracks are packed into the patched game on the patching PC; they
do not need to be installed on the Docker host. Music downloads are separate
from this repository and do not include the game APK.

## 4. Make the patched copy

**If your server uses port 8080:** double-click **patch-apk.bat**, enter just
the IP address (for example `192.168.1.20`) and press Enter.

**If you chose another port:** open Command Prompt in the project folder and
run this, replacing the whole example address with yours:

```bat
python patcher/patch_apk.py nemo.apk http://192.168.1.20:8081 --debug-log --music music
```

Wait for `wrote signed nemo-private.apk`. Texture conversion can take a minute.
If it says **UNSIGNED**, fix the Java/signing-tool setup and run it again.

Keep the original `nemo.apk`. Docker continues to use that original file.

## 5. Install and play

Copy **nemo-private.apk** to your Android device, open it in Files/Downloads,
and choose **Install**. If prompted, allow that file app to install unknown apps.

The game requires Android support for **32-bit apps**. It cannot install on
a device that only runs 64-bit apps.

Keep the server running, then open the game. On a new installation, choose
your player name when asked.

**For the algae exchange:** download the latest project ZIP and patch the original
APK again. Update the server too. The exchange stays hidden on older patched
APKs; the server's seed changes work with those older APKs.

**Already playing?** Keep the existing app data and use the same signing setup
when installing an update. Do not uninstall or clear app data to fix an update:
see [installation troubleshooting](TROUBLESHOOTING.md#android-will-not-install-the-apk)
and [save protection](SAVES.md).

If the server IP or port changes, patch the original APK again with the new
address and install the updated copy.

[Emulator and music options](DEVELOPMENT.md#patcher-options) ·
[Connection problems](TROUBLESHOOTING.md#the-game-cannot-connect)
