# Revival economy: measured catalogue and session model

This report is generated from the current code and a host-supplied Android 1.8.1 APK. “Classic” means this server’s previous reconstructed catalogue, not independently verified historical settings.

## Returning twice daily

Model: one mature plant, two 15-minute visits twelve hours apart; check harvests once per minute. Simulate fourteen days and report only the last day. Each plant holds one harvest. There are no quests, upgrades, exchanges or purchases in this table. These are predictions, not device measurements.

| Plant | Classic harvest / wait | Revival harvest / wait | Classic/day | Revival/day |
|---|---:|---:|---:|---:|
| Blue Mushroom Coral | 10 / 1 h | 20 / 1 h | 20 | 40 |
| Green Mushroom Coral | 15 / 5 h | 120 / 4 h | 30 | 240 |
| Bamboo Coral | 50 / 24 h | 240 / 4 h | 50 | 480 |
| Tentacle Coral | 40 / 8 h | 560 / 4 h | 80 | 1120 |
| Barrel Sponges | 250 / 24 h | 800 / 4 h | 250 | 1600 |
| Blue Tube Sponges | 50 / 4 h | 1520 / 4 h | 100 | 3040 |

## Example daily budgets

Illustrative reefs, not a recommended mandatory layout: five mature corals and two mature coin plants. Use the same two-visit schedule. The spending column budgets three nursery plants at the level’s maximum growth cost, one Mystery Seed’s complete feeding (180 algae) and one one-pearl fish feeding (250 algae). Plant purchase prices and the seed’s 150-sand-dollar price are additional. Fish ownership and completed growth are assumptions. No daily reward or quest income is counted.

| Level | Coral ×5; coin plant ×2 | Algae/day | Sand dollars/day | Budgeted algae | Algae left |
|---|---|---:|---:|---:|---:|
| 6 | coral_circles_green; plant_sponge_beds_yellow | 1200 | 800 | 880 | 320 |
| 20 | coral_bamboo; plant_barrel_sponge | 2400 | 3200 | 1930 | 470 |
| 38 | coral_tentacle; plant_tube_sponge_blue | 5600 | 6080 | 3280 | 2320 |

## Growth costs

| Plant | Classic total algae | Revival total algae | Classic growth | Revival growth |
|---|---:|---:|---:|---:|
| Bamboo Coral | 450 | 300 | 210 min | 60 min |
| Barrel Sponges | 4500 | 498 | 15 min | 15 min |
| Yellow Mangrove | 15000 | 948 | 3 min | 3 min |

## Pearls earned through play

| Level | Daily guaranteed pearls after three taps | Expected pearls from all three taps | Days to 10 / 25 pearls using guarantee only |
|---|---:|---:|---:|
| 6 | 2 | 2.6 | 5 / 13 |
| 10 | 3 | 3.9 | 4 / 9 |
| 20 | 4 | 5.2 | 3 / 7 |
| 38 | 5 | 6.5 | 2 / 5 |
| 51 | 7 | 9.1 | 2 / 4 |

Daily pearl rewards above already existed. New sources: one pearl at ordinary level-ups, three at multiples of five (keep larger pre-existing rewards), plus two to four per main-story/Tank Gang milestone from level six. Side quests, narrative dialogs and repeatable quests do not receive pearl bonuses.

The catalogue contains 62 pearl-awarding one-time milestones, totalling 188 pearls across their requirements. Kids-edition alternatives are included in that catalogue total; it is not a promise that one account can claim every entry.

Rare fish retain their pearl yield but cost at most 250 algae per pearl and take at most 24 hours. For example, the four-pearl Blue Bicolor Angelfish costs 1,000 algae instead of 450,000. Its paid speed-up costs five pearls, preventing a profitable pearl rush/collect loop. Rare plants/fish still have to be obtained through gameplay. Native rewards stay whole numbers.

Premium nursery prices remain 3–25 pearls, and the guaranteed-rare seed remains 10. There is no sand-dollar/pearl exchange in this iteration: fish, quests and daily rewards supply pearls. The algae exchange remains a capped supplement, not required in the example budgets.

## Limits and trade-offs

- Quick starter producers retain their rewards and timers. Constant collecting still earns more; later plants now offer worthwhile return visits.
- Nursery growth costs never exceed 25 × unlock level algae in total. Equal integer feeding stages can make the total up to two algae cheaper than the ceiling.
- Nursery growth takes at most one hour across all stages, assuming prompt feeding. Already shorter times stay shorter.
- Slow producer currency yield is never reduced; waits cap at four hours. XP per shortened cycle rounds down so maximum harvest XP/hour never rises. Casual players may still gain more daily XP by making more successful visits.
- The server stores one ready harvest, not an unlimited offline bank. Long absences do not earn multiple days of production.
- No backwards currency exchange exists. Refunds never exceed the purchase’s sand-dollar price and do not refund pearls. Pearl-producing fish cost more pearls to rush than they produce.
- Saved balances, plants, feeding stages and quest progress remain. One-time migration grants the new pearl bonuses for already completed eligible story milestones; it does not refund old feeding costs or pay old quests again.
- These figures are an initial balance, not evidence from weeks of player testing. Actual reef layouts, available space, quest requirements and visit frequency affect progression.
