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

## 3. Make the patched copy

**If your server uses port 8080:** double-click **patch-apk.bat**, enter just
the IP address (for example `192.168.1.20`) and press Enter.

**If you chose another port:** open Command Prompt in the project folder and
run this, replacing the whole example address with yours:

```bat
python patcher/patch_apk.py nemo.apk http://192.168.1.20:8081 --debug-log
```

Wait for `wrote signed nemo-private.apk`. Texture conversion can take a minute.
If it says **UNSIGNED**, fix the Java/signing-tool setup and run it again.

Keep the original `nemo.apk`. Docker continues to use that original file.

## 4. Install and play

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

[Emulator and optional music](DEVELOPMENT.md#patcher-options) ·
[Connection problems](TROUBLESHOOTING.md#the-game-cannot-connect)
