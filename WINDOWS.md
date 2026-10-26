# Windows setup

[All guides](README.md)

Run the server directly on a Windows PC, without Docker. The PC must stay on,
awake and connected while you play.

## 1. Install Python

Install [Python for Windows](https://www.python.org/downloads/windows/).
The server has been tested with Python 3.12 and 3.14.
If the installer offers **Add python.exe to PATH**, enable it.

Open Command Prompt and check:

```bat
python --version
```

The server uses Python's built-in libraries; there are no extra Python packages
to install. Java is needed later for APK patching, not for running this server.

## 2. Download the project

1. [Download the server and patcher ZIP](https://github.com/lozza/nemos-reef-revival/archive/refs/heads/re-notes.zip).
2. Right-click it and choose **Extract All**.
3. Open the extracted folder containing `start-server.bat`, `server` and `patcher`.
   This is your **project folder**.
4. Copy your original Android 1.8.1 APK into that folder and name it `nemo.apk`.

Use the linked ZIP: the source files are on the `re-notes` branch.

## 3. Start the server

Double-click **start-server.bat**. If Windows Firewall asks, allow access on
**Private networks**.

Keep that window open. Saved accounts and reefs go into `server\data`
inside the project folder.

## 4. Find your address and check it

In Command Prompt, run `ipconfig`. Find the **IPv4 Address** for your active
Wi-Fi or Ethernet adapter, for example `192.168.1.20`.

From a browser on the same home network, open:

```text
http://192.168.1.20:8080/healthz
```

Replace the example IP with yours. You should see `{"status":"ok"}`.
The `0.0.0.0` shown in the server window is not the address to enter on your phone.

**Next: [Patch and install the APK](PATCHING.md)** using your PC's IP and port 8080.

If 8080 is busy, close the server window and run this from the project folder,
replacing 8081 with a free port:

```bat
python server/nemo_server.py --apk nemo.apk --data server/data --port 8081
```

Use that port when checking and patching. Use this command to start the server
each time; the double-click launcher always uses 8080.

[Problems?](TROUBLESHOOTING.md) · [Backups and updates](SAVES.md)

For algae exchanges, seed settings and the previous-economy option, see
[Algae exchange and mystery seeds](ECONOMY.md).
