"""Revival economy: progression, production, pearls and a capped algae exchange.

No real-money purchases or pearl exchanges are provided. Classic mode keeps
the previous catalogue for hosts who want to opt out of the trial.
"""
import copy
import datetime
import math
import os
import random
import re
import uuid

VERSION = 'revival-1'
PROGRESSION_VERSION = 'revival-progression-1'
MAX_PACKS = 3
MIN_LEVEL = 6
MAX_LEVEL = 60
SEEDS = {
    'seed_coin': {'feed': 60, 'step': 900, 'rare': 0.25},
    'seed_coin_expensive': {'feed': 150, 'step': 1200, 'rare': 0.75},
    'seed_pearl': {'feed': 10, 'step': 300, 'rare': 1.0},
}
PACKAGE = re.compile(r'^revival-algae-v1-L(\d+)-([123])$')


def mode(value=None):
    value = value if value is not None else os.environ.get('NEMO_ECONOMY', 'revival')
    if value not in ('classic', 'revival'):
        raise ValueError('economy must be classic or revival')
    return value


def configure_story(data, profile):
    """Copy overrides; never modify the original story file or historical reef."""
    if profile != 'revival':
        return
    items = data.get('items', {})
    rare = sorted({pid for pid in items if pid.startswith('rare_plant_')}
                  | {pid for sid in SEEDS for pid in items.get(sid, {}).get('results', [])
                     if pid.startswith('rare_plant_')})
    common = sorted({pid for sid in SEEDS for pid in items.get(sid, {}).get('results', [])
                     if not pid.startswith('rare_plant_')})
    for sid, policy in SEEDS.items():
        if sid not in items:
            continue
        items[sid]['feed'] = policy['feed']
        items[sid]['step'] = policy['step']
        items[sid]['results'] = rare + (common if policy['rare'] < 1 else [])
    for it in items.values():
        pearls = int(it.get('produce', {}).get('hardMoney', 0))
        if 'fishFeed' in it and pearls:
            it['fishFeed'] = min(it['fishFeed'], 250 * pearls)
            it['every'] = min(it['every'], 86400)
    for q in data.get('quests', []):
        # Repeating minigames/homework and narrative-only dialogs must not
        # become a repeatable pearl faucet. Keep their original rewards.
        if q.get('type') != 'STORY' or q.get('repeat') or not q.get('reward'):
            continue
        level = max(1, int(q.get('level', 1)))
        if level < 6:
            continue  # preserve the guided opening
        r = q['reward']
        r['energy'] = max(int(r.get('energy', 0)), 10 * level)
        r['virtualMoney'] = max(int(r.get('virtualMoney', 0)), 20 * level)
        if q['id'].startswith(('story_quest_', 'kids_story_quest_', 'tankgang_')):
            r['hardMoney'] = max(int(r.get('hardMoney', 0)), 2 + level // 20)


def balance_prototype(p, item, profile):
    """Balance resolved prototypes, after inherited growth cycles are known.

    Larger, capped harvests use the native single-harvest display. No hidden
    banked rewards, fractional XP or new client collect semantics are needed.
    """
    if profile != 'revival':
        return
    pearls = int(p.get('producedReward', {}).get('hardMoney', 0))
    if 'fishFeed' in item and pearls:
        # A one-pearl speed-up must never manufacture four pearls repeatedly.
        p['producingSpeedupCost'] = dict(p.get('producingSpeedupCost') or {},
                                       hardMoney=pearls + 1)
    if not (p['id'].startswith(('plant_', 'coral_', 'orn_plant_')) and item.get('price')):
        return
    level = max(1, int(item.get('level', 1)))
    cycles = max(1, int(p.get('growingCycles', 1)))
    feed = p.get('growingCycleCost', {})
    feed['energy'] = min(int(feed.get('energy', 0)), 25 * level // cycles)
    p['growingCycleCost'] = feed
    growth = p.get('growingTime', {})
    growth['duration'] = min(int(growth.get('duration', 0)), 3600000 // cycles)
    p['growingTime'] = growth
    old_ms = int(p.get('producingTime', {}).get('duration', 0))
    r = p.get('producedReward', {})
    currency = 'energy' if r.get('energy') else 'virtualMoney' if r.get('virtualMoney') else None
    if currency is None or old_ms < 900000:
        return  # keep the quick starter harvests and their XP intact
    new_ms = min(old_ms, 14400000)  # four hours; a return visit has a substantial harvest
    hourly = (5 if currency == 'energy' else 10) * level
    r[currency] = max(int(r[currency]), math.ceil(hourly * new_ms / 3600000))
    # Never multiply XP/hour merely by shortening a timer. Integer rounding
    # goes down; XP remains unchanged when only currency output changes.
    r['xp'] = int(r.get('xp', 0)) * new_ms // old_ms
    # ServerPrototype::getTime reads timeWarp as well as duration. Replacing
    # the object with only duration throws during the client's harvest flow,
    # which can also interrupt its touch-release cleanup and cause false zoom.
    p['producingTime'] = dict(p['producingTime'], duration=new_ms)


def level_reward(level, original, profile):
    r = dict(original)
    if profile == 'revival' and level >= 2:
        r['hardMoney'] = max(int(r.get('hardMoney', 0)), 3 if level % 5 == 0 else 1)
        r['energy'] = max(int(r.get('energy', 0)), 10 * level)
    return r


def migrate_progression(w, protos, quest_definitions, now):
    """Preserve balances/identities/paid stages; shorten only longer timers once.

    Previously completed, non-repeating story milestones receive their newly
    added pearls once. Existing XP and past resource spending are untouched.
    """
    if w.get('progressionEconomyVersion') != PROGRESSION_VERSION:
        done = set(w.get('finishedTutorialQuests', []))
        bonus = sum(int(quest_definitions[q].get('reward', {}).get('hardMoney', 0))
                    for q in done if q in quest_definitions
                    and quest_definitions[q].get('type') == 'STORY'
                    and not quest_definitions[q].get('repeat'))
        w['status']['hardMoney'] = int(w['status'].get('hardMoney', 0)) + bonus
        w['progressionEconomyVersion'] = PROGRESSION_VERSION
    for name in ('corals', 'livingPlants', 'ornamentalPlants', 'rarePlants', 'fishes', 'patches'):
        for e in w.get(name, []):
            if e.get('progressionEconomyVersion') == PROGRESSION_VERSION:
                continue
            _, p = protos.get(e.get('plantPrototypeId') or e.get('prototypeId'))
            if not p:
                continue
            pairs = [('producingInterval', 'producingTime')]
            if not e.get('seedPrototypeId'):
                pairs.append(('growingInterval', 'growingTime'))
            for interval_key, duration_key in pairs:
                gi = e.get(interval_key)
                new = int(p.get(duration_key, {}).get('duration', 0))
                if not gi or not new:
                    continue
                old = int(gi['finish']) - int(gi['start'])
                if old > new and gi['finish'] > now:
                    remaining = min(old, gi['finish'] - now)
                    finish = now + math.ceil(new * remaining / old)
                    e[interval_key] = {'start': finish - new, 'finish': finish}
            e['progressionEconomyVersion'] = PROGRESSION_VERSION


def packages(level):
    """Native InAppShopManager::initExchangeContent fields, no billing items."""
    level = min(MAX_LEVEL, int(level))
    if level < MIN_LEVEL:
        return []
    blank = {k: 0 for k in ('energy', 'virtualMoney', 'hardMoney', 'lowTool', 'highTool')}
    return [{'prototypeId': f'revival-algae-v1-L{level}-{n}', 'guiOrder': n,
             'cost': dict(blank, virtualMoney=40 * level * n),
             'reward': dict(blank, xp=0, energy=20 * level * n)} for n in (1, 2, 3)]


def start_session(w):
    # The original client restarts its numeric request stamps when relaunched.
    w['exchangeSession'] = uuid.uuid4().hex


def exchange(w, data, stamp, aid, now, profile):
    def fail(code, message):
        return {'resultCode': code, 'data': {'errorMessage': message}}
    if profile != 'revival':
        return fail(403, 'The algae exchange is disabled on this server.')
    match = PACKAGE.fullmatch(str(data.get('exchangePackageId', '')))
    if not match:
        return fail(400, 'Unknown exchange offer.')
    offered_level, n = map(int, match.groups())
    level = int(w['status'].get('level', 1))
    if not MIN_LEVEL <= offered_level <= min(MAX_LEVEL, level):
        return fail(403, 'The algae exchange unlocks at level 6.')
    if not isinstance(stamp, int) or isinstance(stamp, bool) or stamp < 0 or aid is None:
        return fail(400, 'An exchange needs a request stamp and action ID.')
    if not w.get('exchangeSession'):
        return fail(409, 'Reconnect before using the algae exchange.')
    key = f"{w['exchangeSession']}:{stamp}:{aid}"
    receipts = w.get('exchangeReceipts', {})
    if key in receipts:
        previous = receipts[key]
        if previous['package'] != data['exchangePackageId']:
            return fail(409, 'The request ID was already used for a different offer.')
        return copy.deepcopy(previous['response'])
    day = datetime.datetime.fromtimestamp(now / 1000, datetime.timezone.utc).date().isoformat()
    saved = w.get('algaeExchange', {})
    used = int(saved.get('packs', 0)) if saved.get('day') == day else 0
    if used + n > MAX_PACKS:
        return fail(403, 'You can exchange up to three algae packs each day.')
    price, amount = 40 * offered_level * n, 20 * offered_level * n
    if int(w['status'].get('virtualMoney', 0)) < price:
        return fail(402, 'Not enough sand dollars.')
    # Game.game holds its world lock; this whole world (balances, usage and
    # receipt together) is committed by Store.save_world in one SQL transaction.
    w['status']['virtualMoney'] -= price
    w['status']['energy'] = int(w['status'].get('energy', 0)) + amount
    w['algaeExchange'] = {'day': day, 'packs': used + n}
    response = {'resultCode': 200, 'data': {'remainingPacks': MAX_PACKS - used - n}}
    receipts = dict(receipts)
    receipts[key] = {'package': data['exchangePackageId'], 'response': copy.deepcopy(response)}
    w['exchangeReceipts'] = dict(list(receipts.items())[-64:])
    return response


def choose_seed(w, sid, choices):
    """Roll the rarity group first, so adding species never changes the odds."""
    policy = SEEDS[sid]
    rare = [p for p in choices if p.startswith('rare_plant_')]
    common = [p for p in choices if not p.startswith('rare_plant_')]
    if not rare or (policy['rare'] < 1 and not common):
        raise ValueError('The seed needs valid rare and ornamental result pools.')
    misses = int(w.get('mysterySeedMisses', 0))
    rare_result = sid == 'seed_coin' and misses >= 4
    rare_result = rare_result or random.random() < policy['rare']
    plant = random.choice(rare if rare_result else common)
    if sid == 'seed_coin':
        w['mysterySeedMisses'] = 0 if rare_result else misses + 1
    return plant


def migrate_seed_timers(w, now):
    """One migration: retain the plant, feeds paid and fraction of a running stage."""
    for name in ('corals', 'livingPlants', 'ornamentalPlants', 'rarePlants'):
        for e in w.get(name, []):
            sid = e.get('seedPrototypeId')
            if sid not in SEEDS or e.get('seedEconomyVersion') == VERSION:
                continue
            gi = e.get('growingInterval')
            if e.get('state') == 3 and gi:
                old = int(gi.get('finish', 0)) - int(gi.get('start', 0))
                new = SEEDS[sid]['step'] * 1000
                if old > new and gi['finish'] > now:
                    remaining = min(old, max(0, gi['finish'] - now))
                    finish = now + math.ceil(new * remaining / old)
                    e['growingInterval'] = {'start': finish - new, 'finish': finish}
            e['seedEconomyVersion'] = VERSION
