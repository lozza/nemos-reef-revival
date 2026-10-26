# Revival economy

[All guides](README.md) · [Changes from the original game](CHANGES.md)

Grow your reef, complete quests and attract rare fish to earn what you need.
The Revival balance reduces growth bottlenecks and provides pearls through play.
Real-money purchases remain unavailable.

## Plants and harvests

- Nursery plants need at most **25 × their unlock level** algae across all
  feeding stages. Already cheaper plants stay cheaper. Equal feeding stages
  can make the total one or two algae below that ceiling.
- Nursery growth takes at most **one hour** in total, assuming prompt feeding.
  Shorter growth times stay shorter. Mystery seeds use their own rules below.
- Producers with waits of 15 minutes or more give larger harvests. Their waits
  cap at **four hours**. Starter plants with short timers keep their rewards.
- A plant still holds one ready harvest. It does not accumulate days of output.

For example, Bamboo Coral gives **240 algae every four hours**, instead of 50
every day. Barrel Sponges give **800 sand dollars every four hours**, instead
of 250 daily. Their total algae growth costs fall from 450 to 300 and from
4,500 to 498 respectively. Purchase prices and unlock levels are unchanged.

Where a production timer gets shorter, XP per harvest is reduced and rounded
down so its maximum XP per hour does not increase. Upgraded Patches retain
their fourfold harvest.

## Earning pearls

- The existing final daily reward tap guarantees **2 + floor(level / 10)** pearls.
  The first two taps can also award pearls.
- Ordinary level-ups give at least **one pearl**, with **three at every fifth
  level**. Larger pre-existing rewards are retained.
- Main story and Tank Gang milestones from level 6 give **two pearls**, rising
  to three from level 20 and four from level 40. Side quests and repeating
  minigames do not receive this bonus. Rewarded story quests from level 6 also
  give at least **10 × their unlock level algae** and **20 × their level sand dollars**.
- Pearl-producing rare fish cost at most **250 algae per pearl produced** and
  take at most **24 hours**. Previously cheaper feeding costs stay cheaper.

Rare fish still have to be attracted. A four-pearl Blue Bicolor Angelfish now
costs 1,000 algae per feeding rather than 450,000. Rushing a pearl-producing
fish costs one more pearl than its harvest gives, so rushing cannot multiply pearls.

Premium nursery prices remain **3–25 pearls**, and an Extraordinary Seed stays
at **10 pearls**. At level 10, the daily guarantee alone funds a 10-pearl seed
in four days; quests and fish can shorten that. There is no sand-dollar/pearl
exchange in this version: pearls come from playing and progressing.

The recurring prompt to buy a premium rare seed is disabled.

See [balance examples and assumptions](BALANCE.md) for early, middle and later
reefs, daily earnings, growth costs and the reproducible session model.

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

## Existing saves

On the first connection after updating, completed eligible story milestones
receive their newly added pearl bonuses once. Old quests are not replayed;
their XP and other rewards are not paid again. Future level-ups use the new
rewards; earlier level-ups are not paid again.

Running plant and fish timers shorten proportionally if the new timer is
shorter. Existing balances, identities, paid feeds and quest progress remain.
Old feeding costs are not refunded. Selecting Classic keeps already earned
rewards and shortened timers; restore a backup for an exact pre-update rollback.

This is an initial balance pass. The numbers have been modelled and tested,
but long-term pacing still needs feedback from different reefs and play schedules.
