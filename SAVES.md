# Saves, backups and moving servers

[All guides](README.md)

## Where is my save?

| Server setup | Location |
|---|---|
| Windows launcher | `server/data/nemo.db` in the project folder |
| Default Docker Compose | `nemo.db` inside Docker volume `nemos-reef_reef-data` |
| Docker with your own shared folder | `nemo.db` inside the data folder you chose |

The database holds all players, reefs and friendships on that server.
`requests.log` is a diagnostic log, not the reef database.

Keep the phone's app data and your APK signing setup too: the phone needs its
existing player identity to reconnect to the saved reef. A database backup
does not replace those.

## Back up

Stop the server before copying its database. Keep dated copies somewhere
separate from the server. Check the copy exists and is not empty before restarting.

**Windows:** close the server window, copy the entire `server/data` folder
to your backup location, then start the server again.

**Docker shared folder:** stop the game stack, copy the data folder to your
backup location, then start the stack again.

**Docker-managed volume:** stop the game stack in Portainer/OMV. From a
terminal with Docker access on that host, run the following in your backup
folder. Use a new filename for each backup:

```sh
docker run --rm --entrypoint cat --mount type=volume,source=nemos-reef_reef-data,target=/data,readonly ghcr.io/lozza/nemos-reef-revival:latest /data/nemo.db > nemo-backup.db
```

Check the command succeeded and the file is not empty, then start the stack.

Do not delete the save volume or use `docker compose down -v`: that removes
the default Docker saves. Only one server should write to a database at a time.

## Move from a Docker volume to a shared folder

1. Stop the old game stack and back up its database using the command above.
2. Prepare your new data folder using [Shared folders](SHARES.md).
3. Copy the backup into that data folder, naming it **nemo.db**.
   Stop if that folder already has a database; preserve both before deciding
   which save to use.
4. Give the copied file ownership `10001:10001`. With sudo, use
   `sudo chown 10001:10001 /your/data/path/nemo.db`.
   Without sudo, rerun the temporary helper from the shared-folder guide,
   changing its command to:
   `chown 10001:10001 /data /data/nemo.db && echo 'Nemo data folder ready'`.
5. Deploy the shared-folder Compose file using your new paths.

Keep the old volume and backup until you have checked your reef in the game.
Do not run the old and new stacks together.

## Move from Windows to Docker

1. Stop the Windows server and back up its entire `server/data` folder.
2. Set up Docker with [your own shared folder](SHARES.md), but leave the
   game server stopped until the save is copied.
3. Copy `nemo.db` from the backup into the new data folder.
4. Give that file ownership `10001:10001`, as described above.
5. Start Docker and check its health page.
6. If the server address changed, [patch the APK](PATCHING.md) for the new
   address and install it as an update, keeping the existing app data.

Keep the original backup. A fresh empty reef can mean the new server is reading
a different database or the phone is using a different player identity.

## Restore a backup

Stop the server. Preserve its current data folder/database separately, then
copy the chosen backup into the save location from the table above as
`nemo.db`. For Docker shared folders, check ownership as described above.

To restore into the default Docker volume, put the backup in a temporary
folder on the Docker host. With the game stack stopped, deploy the helper
below as a separate temporary stack. Replace the backup's source path:

```yaml
name: nemo-save-restore

services:
  restore:
    image: ghcr.io/lozza/nemos-reef-revival:latest
    user: "0:0"
    entrypoint: ["/bin/sh", "-c"]
    command:
      - "test ! -e /data/nemo.db && cp /backup/nemo.db /data/nemo.db && chown 10001:10001 /data/nemo.db"
    volumes:
      - type: bind
        source: /path/to/backup/nemo.db
        target: /backup/nemo.db
        read_only: true
        bind:
          create_host_path: false
      - reef-data:/data
    restart: "no"

volumes:
  reef-data:
    external: true
    name: nemos-reef_reef-data
```

This refuses to overwrite an existing database. If one exists, preserve it
and move it out of the volume first using your volume-management tool; do not
delete it blindly. Wait for the helper to exit successfully, remove its stack,
then start the game stack.

## Update the server

Back up first.

- **Portainer:** open the stack, choose **Update the stack**, enable
  **Re-pull image**, then deploy.
- **OMV Compose:** pull the image, then choose **Up**.
- **Docker terminal:** run `docker compose pull`, then `docker compose up -d`
  from your Compose folder.
- **Windows:** extract a fresh source ZIP into a new folder, close the old
  server, then copy the original `nemo.apk` and `server/data` folder into
  the new project before starting it. Keep the old folder as a backup.
  Keep your original patching folder/signing setup if patching again.

Ordinary server updates do not require patching the APK again unless the
server address changes or the update specifically requires a client change.

## Play the same reef on another device

Each device starts as its own player. To play an existing reef on a second
device (an emulator, a new tablet), start the game there once and pick any
name, for example `lozzareeves2`. Then, where the server runs:

- Docker: `docker exec <container> python server/link_device.py lozzareeves2 lozzareeves`
- Windows: in the project folder, `python server\link_device.py lozzareeves2 lozzareeves`

The second device then plays `lozzareeves`, and the spare `lozzareeves2`
player and its reef are deleted. Close and reopen the game on that device.
Run the tool without names to list the players.
