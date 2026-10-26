"""Revival economy v1: seeded plants and a capped sand-dollar/algae exchange.

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
