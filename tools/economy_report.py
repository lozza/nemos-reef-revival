"""Reproduce the economy comparison from a host's own original APK.

No saved reef is read or changed. Report assumptions are deliberately explicit.
Run: python tools/economy_report.py --apk nemo.apk --output BALANCE.md
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions
import quests
import worlddef


def daily(p, visits=(0, 720), minutes=15):
    """Mature single plant, check each minute; discard 13 warm-up days."""
    period = p['producingTime']['duration'] // 1000
    last, totals = -period, {}
    for day in range(14):
        for opening in visits:
            for minute in range(minutes + 1):
                now = (day * 1440 + opening + minute) * 60
                if now - last >= period:
                    last = now
                    if day == 13:
                        for currency, amount in p['producedReward'].items():
                            totals[currency] = totals.get(currency, 0) + amount
    return totals


def report(apk):
    stories = {m: quests.Story(economy_mode=m) for m in ('classic', 'revival')}
    protos = {m: actions.Prototypes(worlddef.build(apk, s)) for m, s in stories.items()}
    cases = [('Blue Mushroom Coral', 'coral_bell_blue', 'energy'),
             ('Green Mushroom Coral', 'coral_circles_green', 'energy'),
             ('Bamboo Coral', 'coral_bamboo', 'energy'),
             ('Tentacle Coral', 'coral_tentacle', 'energy'),
             ('Barrel Sponges', 'plant_barrel_sponge', 'virtualMoney'),
             ('Blue Tube Sponges', 'plant_tube_sponge_blue', 'virtualMoney')]
    lines = ['# Revival economy: measured catalogue and session model', '',
        'This report is generated from the current code and a host-supplied Android 1.8.1 APK. '
        '“Classic” means this server’s previous reconstructed catalogue, not independently verified historical settings.', '',
        '## Returning twice daily', '',
        'Model: one mature plant, two 15-minute visits twelve hours apart; check harvests once per minute. '
        'Simulate fourteen days and report only the last day. Each plant holds one harvest. '
        'There are no quests, upgrades, exchanges or purchases in this table. These are predictions, not device measurements.', '',
        '| Plant | Classic harvest / wait | Revival harvest / wait | Classic/day | Revival/day |',
        '|---|---:|---:|---:|---:|']
    for name, pid, currency in cases:
        old, new = (protos[m].get(pid)[1] for m in ('classic', 'revival'))
        def harvest(p):
            return f"{p['producedReward'][currency]} / {p['producingTime']['duration']/3600000:g} h"
        lines.append(f'| {name} | {harvest(old)} | {harvest(new)} | '
                     f'{daily(old).get(currency, 0)} | {daily(new).get(currency, 0)} |')
    lines += ['', '## Example daily budgets', '',
        'Illustrative reefs, not a recommended mandatory layout: five mature corals and two mature coin plants. '
        'Use the same two-visit schedule. The spending column budgets three nursery plants at the level’s '
        'maximum growth cost, one Mystery Seed’s complete feeding (180 algae) and one one-pearl fish feeding (250 algae). '
        'Plant purchase prices and the seed’s 150-sand-dollar price are additional. '
        'Fish ownership and completed growth are assumptions. No daily reward or quest income is counted.', '',
        '| Level | Coral ×5; coin plant ×2 | Algae/day | Sand dollars/day | Budgeted algae | Algae left |',
        '|---|---|---:|---:|---:|---:|']
    for level, coral, plant in [(6, 'coral_circles_green', 'plant_sponge_beds_yellow'),
                                (20, 'coral_bamboo', 'plant_barrel_sponge'),
                                (38, 'coral_tentacle', 'plant_tube_sponge_blue')]:
        algae = 5 * daily(protos['revival'].get(coral)[1]).get('energy', 0)
        coins = 2 * daily(protos['revival'].get(plant)[1]).get('virtualMoney', 0)
        spending = 3 * (25 * level) + 180 + 250
        lines.append(f'| {level} | {coral}; {plant} | {algae} | {coins} | {spending} | {algae-spending} |')
    lines += ['', '## Growth costs', '',
        '| Plant | Classic total algae | Revival total algae | Classic growth | Revival growth |',
        '|---|---:|---:|---:|---:|']
    for name, pid in [('Bamboo Coral', 'coral_bamboo'), ('Barrel Sponges', 'plant_barrel_sponge'),
                      ('Yellow Mangrove', 'orn_plant_mangrove_yellow')]:
        if not protos['revival'].get(pid)[1]:
            continue
        old, new = (protos[m].get(pid)[1] for m in ('classic', 'revival'))
        def total(p, key, subkey):
            return p[key][subkey] * p['growingCycles']
        lines.append(f"| {name} | {total(old, 'growingCycleCost', 'energy')} | "
                     f"{total(new, 'growingCycleCost', 'energy')} | "
                     f"{total(old, 'growingTime', 'duration')/60000:g} min | "
                     f"{total(new, 'growingTime', 'duration')/60000:g} min |")
    lines += ['', '## Pearls earned through play', '',
        '| Level | Daily guaranteed pearls after three taps | Expected pearls from all three taps | Days to 10 / 25 pearls using guarantee only |',
        '|---|---:|---:|---:|']
    import math
    for level in (6, 10, 20, 38, 51):
        guaranteed = 2 + level // 10
        lines.append(f'| {level} | {guaranteed} | {1.3*guaranteed:g} | '
                     f'{math.ceil(10/guaranteed)} / {math.ceil(25/guaranteed)} |')
    milestones = [q for q in stories['revival'].quests.values() if q.get('reward', {}).get('hardMoney')]
    lines += ['', 'Daily pearl rewards above already existed. New sources: one pearl at ordinary level-ups, '
        'three at multiples of five (keep larger pre-existing rewards), plus two to four per main-story/Tank Gang '
        'milestone from level six. Side quests, narrative dialogs and repeatable quests do not receive pearl bonuses.', '',
        f'The catalogue contains {len(milestones)} pearl-awarding one-time milestones, totalling '
        f"{sum(q['reward']['hardMoney'] for q in milestones)} pearls across their requirements. "
        'Kids-edition alternatives are included in that catalogue total; it is not a promise that one account can claim every entry.', '',
        'Rare fish retain their pearl yield but cost at most 250 algae per pearl and take at most 24 hours. '
        'For example, the four-pearl Blue Bicolor Angelfish costs 1,000 algae instead of 450,000. '
        'Its paid speed-up costs five pearls, preventing a profitable pearl rush/collect loop. '
        'Rare plants/fish still have to be obtained through gameplay. Native rewards stay whole numbers.', '',
        'Premium nursery prices remain 3–25 pearls, and the guaranteed-rare seed remains 10. '
        'There is no sand-dollar/pearl exchange in this iteration: fish, quests and daily rewards supply pearls. '
        'The algae exchange remains a capped supplement, not required in the example budgets.', '',
        '## Limits and trade-offs', '',
        '- Quick starter producers retain their rewards and timers. Constant collecting still earns more; later plants now offer worthwhile return visits.',
        '- Nursery growth costs never exceed 25 × unlock level algae in total. Equal integer feeding stages can make the total up to two algae cheaper than the ceiling.',
        '- Nursery growth takes at most one hour across all stages, assuming prompt feeding. Already shorter times stay shorter.',
        '- Slow producer currency yield is never reduced; waits cap at four hours. XP per shortened cycle rounds down so maximum harvest XP/hour never rises. Casual players may still gain more daily XP by making more successful visits.',
        '- The server stores one ready harvest, not an unlimited offline bank. Long absences do not earn multiple days of production.',
        '- No backwards currency exchange exists. Refunds never exceed the purchase’s sand-dollar price and do not refund pearls. Pearl-producing fish cost more pearls to rush than they produce.',
        '- Saved balances, plants, feeding stages and quest progress remain. One-time migration grants the new pearl bonuses for already completed eligible story milestones; it does not refund old feeding costs or pay old quests again.',
        '- These figures are an initial balance, not evidence from weeks of player testing. Actual reef layouts, available space, quest requirements and visit frequency affect progression.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = report(args.apk)
    if args.output:
        Path(args.output).write_text(result, encoding='utf-8')
    else:
        print(result)
