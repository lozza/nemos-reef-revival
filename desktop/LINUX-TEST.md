Nemo's Reef — experimental Linux x86_64 test

For 64-bit Intel/AMD Linux PCs and Steam Deck Desktop Mode.
Built on Ubuntu 22.04; requires glibc 2.35 or newer, an X11/XWayland desktop,
and an OpenGL 2.1-compatible graphics driver. ARM and macOS are unsupported.

Extract the entire archive into a folder in your home directory. Keep it together.
In a terminal inside the extracted folder, run:

    ./Play\ Nemos\ Reef.sh

If needed, first run: chmod +x NemosReef "Play Nemos Reef.sh"
No Android, Wine, Docker or separate Python installation is required.
The private local game server starts automatically; it does not use your Docker server.

The local test archive includes your existing original nemo.apk and four music
tracks. These game assets are not included in the publicly built runtime.
For a runtime-only copy, place your original nemo.apk beside NemosReef and put
music0.ogg through music3.ogg (or .mp3) in the music folder.

On Ubuntu/Mint, if graphics or audio libraries are missing:
    sudo apt install libgl1 libglfw3 libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6 libstdc++6 libgcc-s1
For missing audio support, install your distribution's ALSA runtime package
(libasound2 on Ubuntu 22.04, libasound2t64 on Ubuntu 24.04).
On Wayland, enable XWayland or try an X11 session.

F11 or Alt+Enter: fullscreen. Escape: return to window.
Drag sideways to scroll the Nursery; click to select.
Initial loading can take a while; short stalls and other prototype issues remain.

Linux saves are separate from Windows saves:
    ~/.local/share/nemos-reef/work/play
or $XDG_DATA_HOME/nemos-reef/work/play when XDG_DATA_HOME is configured.
Do not run two copies using the same save folder.

If it fails, run the launcher from a terminal and keep its output. Also send:
    ~/.local/share/nemos-reef/work/launch-error.txt
and work/play/client-session.json if present (same base directory).
Include your distribution/version, graphics card and display session.

Verification: Linux native/packaging checks are run in CI without the original
APK. Actual game launch, graphics, audio, saving and performance on Linux still
need testing. Windows tests do not establish those Linux features as working.

