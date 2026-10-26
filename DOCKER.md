# Docker setup

[All guides](README.md)

For Docker, Portainer and compatible NAS systems. The prebuilt image supports
Linux AMD64 and ARM64. You do not need to build it or install Python or Java
on the server.

## 1. Put the original APK on your server

Copy your original Android 1.8.1 APK onto the computer **running Docker**.

For example, you might put it at `/media/nemo/nemo.apk`. Use your own actual
path. A Windows network address such as `\\NAS\share\nemo.apk` is not a Linux
server path.

The server reads item definitions, text and the starting reef from this file.
It does not run the Android app.

## 2. Copy the Compose file

This version lets Docker look after your saved reefs. **Change the APK
`source:` path.** If port 8080 is already used, choose a free port using
[the port-conflict instructions](TROUBLESHOOTING.md#port-is-already-allocated).

Want saves in your own visible folder instead? Use [Shared folders](SHARES.md)
in place of this step.

```yaml
name: nemos-reef

services:
  nemo:
    image: ghcr.io/lozza/nemos-reef-revival:latest
    pull_policy: always
    ports:
      - "8080:8080"
    volumes:
      - type: bind
        # Change this to your original APK's full path on the Docker host.
        source: /path/to/nemo.apk
        target: /game/nemo.apk
        read_only: true
        bind:
          create_host_path: false
      - reef-data:/data
    restart: unless-stopped

volumes:
  reef-data:
    name: nemos-reef_reef-data
```

[Copy the raw Compose file](https://raw.githubusercontent.com/lozza/nemos-reef-revival/main/compose.yaml).

Keep `target: /game/nemo.apk` unchanged: that is the path inside the container.
`read_only: true` lets the server read the APK without changing it.
`reef-data:/data` stores accounts and reefs in the Docker volume
`nemos-reef_reef-data`.

## 3. Start it

**Portainer:** open **Stacks → Add stack → Web editor**, name the stack
`nemos-reef`, paste your edited Compose file, then **Deploy the stack**.

**Docker terminal:** save your edited file as `compose.yaml`. Open a terminal
in that folder and run:

```sh
docker compose up -d
```

**OpenMediaVault:** follow [the OMV steps](OMV.md).

## 4. Check it works

Open this address in a browser on your home network, replacing the IP:

```text
http://YOUR-SERVER-IP:8080/healthz
```

You should see `{"status":"ok"}`. If you changed the host port, use that port
here too. Keep the server's address handy, without `/healthz`.

**Next: [Patch and install the APK](PATCHING.md).**

For updates, see [Saves and backups](SAVES.md#update-the-server).
For errors, see [Troubleshooting](TROUBLESHOOTING.md).

For algae exchanges, seed settings and the previous-economy option, see
[Revival economy](ECONOMY.md).
