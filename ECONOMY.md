# Algae exchange and mystery seeds

[All guides](README.md) · [Changes from the original game](CHANGES.md)

The Revival economy gives surplus sand dollars a use and makes mystery seeds
less demanding to grow. Real-money purchases remain unavailable.

**Updating an existing setup:** update the server, then make a new patched APK
with the latest [patcher](PATCHING.md) and install it over your current game.
Keep its app data. The exchange needs this app-side fix because the original
screen checks pearls regardless of the offer's currency. Older patched APKs
still receive the seed changes, but exchange offers stay hidden.

## Exchange sand dollars for algae

From **level 6**, tap the **+** beside your algae balance, then choose the
**Exchange** tab in Treasures Cave. You can buy up to **three packs per day**, in one purchase or
separately. Each pack costs two sand dollars per algae.

| Your level | Algae per pack | Sand dollars per pack | Daily maximum algae |
|---|---:|---:|---:|
| 6 | 120 | 240 | 360 |
| 10 | 200 | 400 | 600 |
| 20 | 400 | 800 | 1,200 |
| 38 | 760 | 1,520 | 2,280 |

A pack contains **20 × your level** algae and costs **40 × your level** sand
dollars. Three packs bought together use the whole daily allowance. The limit
resets at midnight UTC and is shared across devices using the same account.
Exchanges give no XP or pearls. Growing corals still supplies algae without
spending sand dollars.

The original game may show its old real-money shop notice the first time you
open Treasures Cave. The Revival server provides no real-money offers; these
exchanges use sand dollars. The separate Algae, Pearls and Sand Dollars purchase
tabs remain empty.

## Growing seeds

Seed purchase prices are unchanged. Feeding costs, waiting times and results
use these settings:

| Seed | Algae per feeding | Total algae | Wait per stage | Total waiting | Rare chance |
|---|---:|---:|---:|---:|---:|
| Mystery | 60 | 180 | 15 minutes | 45 minutes | 25% |
| Enigmatic | 150 | 450 | 20 minutes | 1 hour | 75% |
| Extraordinary | 10 | 30 | 5 minutes | 15 minutes | 100% |

The total waiting assumes you return to feed at each stage. After four Mystery
Seeds in a row produce ordinary plants, the next Mystery Seed guarantees a
rare plant. A rare result resets that count. Enigmatic and Extraordinary Seeds
do not affect the Mystery Seed guarantee.

Rarity is rolled first, then a plant is chosen from that group. Adding more
species therefore does not change the chance of a rare result.

Existing plants keep their identity and paid feeding stages. An unfinished
stage is shortened proportionally when its old timer is longer than the new
timer. Previously spent algae is not refunded, and grown plants are not rerolled.

## Server setting

These settings are enabled by default. Back up your save before updating.
Hosts can use the previous catalogue by selecting `classic`:

**Docker:** add this inside the `nemo` service, alongside `ports` and `volumes`,
then redeploy:

```yaml
    environment:
      NEMO_ECONOMY: classic
```

**Windows:** start the server from the project folder with:

```bat
python server/nemo_server.py --apk nemo.apk --data server/data --economy classic
```

Use `revival` in place of `classic` to switch back. The setting applies to the
whole server. Switching profiles keeps balances, existing plants, exchange
usage and the Mystery Seed guarantee count. Classic mode uses the old seed
feed costs and timers for future stages; it does not undo timers already
shortened by the Revival profile.

No pearl exchange or general production-rate increase is included in this
stage of the rebalance.
