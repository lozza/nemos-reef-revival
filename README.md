# Nemo's Reef Revival

An unofficial fan project for playing Nemo's Reef Android 1.8.1 on a server
you run at home. The project is in early development:
[what works and what is still missing](STATUS.md).

## Experimental desktop prototype

Windows developer builds of a separate desktop client have been tested;
Linux remains unverified. No public desktop build or desktop-source snapshot
is available yet. [Read the desktop prototype guide](https://github.com/lozza/nemos-reef-revival/blob/main/DESKTOP.md)
for current status, controls, saves and testing access.

## Start here

**1. Choose where to run the server.** Follow one guide:

| Your setup | Guide |
|---|---|
| Docker, Portainer or a NAS | [Docker setup](DOCKER.md) |
| OpenMediaVault with the Compose plugin | [OpenMediaVault setup](OMV.md) |
| A Windows PC, without Docker | [Windows setup](WINDOWS.md) |

**2. Connect the game.** Once the server is running, follow
[Patch and install the APK](PATCHING.md). This step is the same for every server.

You need your own original Android 1.8.1 APK and an Android device that supports
32-bit apps. Game files are not included. The server reads game definitions from
the original APK; a separate patched copy runs on your Android device.

## What's different from the original?

Small fish stay visible when you zoom out, and the final daily reward tap
now guarantees pearls because real-money purchases are unavailable. You can
also exchange sand dollars for algae, grow plants with lower feeding costs,
collect larger harvests from slow producers and earn pearls through story
milestones, level-ups and more affordable rare-fish feeding.
Existing players need the latest APK patch to use the exchange.
See [Changes from the original game](CHANGES.md) for the full list, including
event schedules and compatibility fixes.

## Other guides

| I want to… | Guide |
|---|---|
| Put Docker saves in my own folder or NAS share | [Shared folders](SHARES.md) |
| Back up, restore or move an existing reef | [Saves and backups](SAVES.md) |
| Fix a setup problem | [Troubleshooting](TROUBLESHOOTING.md) |
| Understand the current game features | [Game status and playing](STATUS.md) |
| See economy changes, pearl rewards and example budgets | [Revival economy](ECONOMY.md) |
| Work on the server or use an emulator | [Development](DEVELOPMENT.md) |

The server computer must stay on while you play. A NAS or mini PC can run it
without keeping your laptop on. Use your home network or a private VPN; leave
router port forwarding off because the original game protocol uses HTTP and
has limited authentication.

This project is independent of Disney and Pixar.
