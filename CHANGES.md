# Changes from the original game

[All guides](README.md) · [Game status](STATUS.md)

Nemo's Reef Revival uses the Android 1.8.1 game with a replacement server.
These are the changes we've made to how it plays and runs.

## Fish stay visible when you zoom out

Small ambient fish no longer fade away when you zoom out. You can see them
swimming around while viewing the whole reef.

This is part of the APK patcher. It changes the small fish's visibility, not
the number of fish in your reef.

## Pearls you can earn every day

Real-money pearl purchases are unavailable. To give players a reliable way
to earn pearls, the **third and final daily reward tap always gives pearls**.

The guaranteed amount is **2 pearls, plus 1 for every 10 player levels**:

| Player level | Pearls on the final daily tap |
|---|---|
| 1–9 | 2 |
| 10–19 | 3 |
| 20–29 | 4 |
| 30–39 | 5 |

The first two taps can also give pearls, alongside sand dollars, algae or XP.
Complete all three taps to receive the guaranteed pearl reward.

The ad-funded extra tap and the pearl-spending “get everything” option are
hidden. The three normal daily taps remain available. In-game pearl prices
and spending still apply.

## Spend sand dollars on algae

From level 6, the algae shop offers packs bought with sand dollars. Each pack
contains **20 × your level** algae and costs **40 × your level** sand dollars.
You can buy up to **three packs per account each day**; the limit resets at
midnight UTC. Exchanges award no XP or pearls.

Exchange offers require the latest APK patch as well as the server update.
The patch fixes the original screen's hardcoded pearl-balance check. Offers
remain hidden on older patched APKs.

## Mystery seeds take less waiting

Mystery, Enigmatic and Extraordinary Seeds now take **45 minutes, 1 hour and
15 minutes** to grow, with **180, 450 and 30 algae** needed across three feeds.
Their rare chances are **25%, 75% and 100%**. After four non-rare Mystery Seed
results in a row, the next Mystery Seed guarantees a rare plant.

Existing plants keep their identity and paid feeding stages. The server also
recovers missed seed-quest progress when a saved plant contains evidence of
the seed it grew from; an ornamental plant still cannot count as a rare one.

## A reef you can grow without purchases

Nursery growth now needs at most **25 × the plant's unlock level algae** and
takes at most **one hour** across its feeding stages. Cheaper or quicker plants
stay cheaper or quicker. Slow producers give larger harvests and wait at most
four hours. For example, Bamboo Coral gives 240 algae every four hours rather
than 50 each day. The short starter harvests remain available.

Main-story and Tank Gang milestones from level 6 award **two to four pearls**.
Level-ups give at least one pearl, or three at multiples of five. Rare-fish
feeding costs cap at **250 algae per pearl produced**, with waits capped at
24 hours. The recurring premium rare-seed sales prompt is disabled.

Existing saves keep their plants, balances and quest progress. Completed
eligible story milestones receive the added pearl bonus once on reconnecting.
Past XP and resource rewards are not paid again.

See [Revival economy](ECONOMY.md) for prices, limits and the host setting for
using the previous economy, or [balance examples](BALANCE.md) for the numbers.
These settings are a first balance pass and will need longer-term play feedback.

## Events come back

Seasonal events return each year around Christmas, Halloween, Valentine's
Day and Easter. Other time events rotate on weekends, from Friday to Monday,
for players at level 6 or above. Event timing is handled by the replacement
server rather than the old live-service calendar.

## Your server, your reef

You can run the server on Windows, Docker or a NAS, and keep your save files
yourself. Friends are added by their player name and must be using the same
server. There is no shared official player network.

## Fixes for the patched game

- **Settings:** fixed a crash when opening Settings while connected to a server
  by IP address.
- **Saving on newer Android versions:** the patched game uses its internal app
  storage for login details and caches, avoiding the old external-storage path.
- **Texture compatibility:** the patcher adds PNG copies of textures for
  devices that cannot use the original PowerVR format.
- **Large event task windows:** events with more than two objectives use the
  regular quest card. This fixes the crash after the opening conversation in
  “Let's go to the beach!” and keeps all objectives and rewards.

You can also add your own music files when patching; see
[patcher options](DEVELOPMENT.md#patcher-options).

## What to update

Fish visibility and Android compatibility fixes are included when you
[patch the APK](PATCHING.md). An older patched APK needs to be patched again
to pick up those changes; keep your app data when installing the update.

Production, growth costs, pearl rewards, seed settings, event schedules and quest fixes come from the
server and work with both Windows and Docker. Those changes do not need another
APK patch. The algae exchange also needs the updated APK patch described above.

## About the reconstruction

The original server is gone, so parts of its economy and progression have been
rebuilt from the information available. Some prices, timers and rewards may
differ from the original. That reconstruction is separate from the deliberate
changes listed above.

For features that are still missing or imperfect, see [Game status](STATUS.md).
