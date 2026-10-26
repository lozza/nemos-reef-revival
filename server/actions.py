"""Game actions (the batched "actions" of the game API) applied to a stored world.

Formats follow the client's netcl::ga::GA_* classes; see ACTIONS.md for the full protocol. The
client applies costs and rewards itself from the prototypes and only takes ids and timers from the
server, so the server repeats the same arithmetic to keep "status" right for the next startgame.

Entities are stored in the world lists as the client reads them in startgame (id, prototypeId,
position, state, intervals ...). States are recomputed from the timers whenever the world is sent.
"""
import random
import time

import worlddef
import economy
# server ids must stay below the client's temporary ids (EntityIdGenerator starts at 1,000,000)
MAX_ENTITY_ID = 999999

CURRENCIES = {'hardMoney': 'hardMoney', 'virtualMoney': 'virtualMoney', 'energy': 'energy',
              'lowTool': 'lowTool', 'highTool': 'highTool'}

# client state codes read by restoreStateFromServer
BUILT, CONSTRUCTING = 1, 0
GROWING, WAITING_FEED, PRODUCING, READY = 3, 5, 6, 7
END_CONSTRUCT, END_FEED = 3, 4  # pushed message types
GROWABLE = ('ET_CORAL', 'ET_LIVING_PLANT', 'ET_ORN_PLANT', 'ET_RARE_PLANT')
HARVESTABLE = ('ET_CORAL', 'ET_LIVING_PLANT', 'ET_RARE_PLANT')


class ActionRejected(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def interval(start, length):
    return {'start': start, 'finish': start + length}


class Prototypes:
    """Lookup of worldDef prototypes by id, with the list each entity type is stored in."""

    def __init__(self, wd):
        self.economy_mode = wd.get('revivalEconomy', 'classic')
        self.by_id = {}
        world_list = {'coralPrototypeList': 'corals', 'livingPlantPrototypeList': 'livingPlants',
                      'ornamentalPlantPrototypeList': 'ornamentalPlants', 'rarePlantPrototypeList': 'rarePlants',
                      'decorationPrototypeList': 'decorations', 'housePrototypeList': 'houses',
                      'seedPrototypeList': None, 'patchList': 'patches',
                      'fishPrototypeList': 'fishes', 'ambientFishPrototypeList': 'fishes'}
        for key, lst in world_list.items():
            for p in wd.get(key, []):
                self.by_id[p['id']] = (lst, p)
        # an item can have several shop entries, one per level range (ServerPrototype::getActiveShopData)
        self.shop = {}
        for s in wd.get('ingameShopItems', []):
            self.shop.setdefault(s['prototypeId'], []).append(s)
        self.levels = sorted(wd.get('levelList', []), key=lambda l: l['level'])
        self.vitality_patches = wd.get('vitalityPatchList') or []

    def get(self, pid):
        return self.by_id.get(pid, (None, None))

    def shop_cost(self, pid, level):
        """The price the shop shows at this level: the early (pearl) price before the item unlocks."""
        for s in self.shop.get(pid, []):
            if s.get('visibleAtLevel', 1) <= level <= s.get('visibleMaxLevel', 999):
                return s['cost']
        return None


class Reef:
    def __init__(self, protos):
        self.protos = protos
        self.events = []  # (message type, data) produced by advance(); taken by the server per request

    # ------------------------------------------------------------ helpers
    @staticmethod
    def find(w, eid):
        for lst in ('corals', 'houses', 'decorations', 'livingPlants', 'ornamentalPlants',
                    'fishes', 'rarePlants', 'patches'):
            for e in w.get(lst, []):
                if e.get('id') == eid:
                    return lst, e
        return None, None

    @staticmethod
    def new_id(w):
        used = {e.get('id') for lst in ('corals', 'houses', 'decorations', 'livingPlants', 'ornamentalPlants',
                                        'fishes', 'rarePlants', 'patches') for e in w.get(lst, [])}
        n = w.get('nextEntityId', 1)
        while n in used:
            n += 1
        if n > MAX_ENTITY_ID:
            n = next(i for i in range(1, MAX_ENTITY_ID + 1) if i not in used)
        w['nextEntityId'] = n + 1
        return n

    def pay(self, w, cost, sign=-1):
        st = w['status']
        for k in CURRENCIES:
            st[k] = max(0, st.get(k, 0) + sign * int((cost or {}).get(k, 0)))

    @staticmethod
    def level(w):
        return int(w.get('status', {}).get('level', 1))

    def earn(self, w, reward):
        self.pay(w, reward, +1)
        xp = int((reward or {}).get('xp', 0))
        if xp:
            st = w['status']
            st['xp'] = st.get('xp', 0) + xp
            self.update_level(w)

    def update_level(self, w):
        st = w['status']
        levels = self.protos.levels
        lvl = st.get('level', 1)
        while True:
            nxt = next((l for l in levels if l['level'] == lvl + 1), None)
            if not nxt or st['xp'] < nxt['requiredXP']:
                break
            lvl += 1
            self.pay(w, nxt.get('reward'), +1)
        st['level'] = lvl
        nxt = next((l for l in levels if l['level'] == lvl + 1), None)
        st['xpForNextLevel'] = nxt['requiredXP'] if nxt else st['xp']

    @staticmethod
    def ms(p, key):
        return int((p.get(key) or {}).get('duration', 0))

    # ------------------------------------------------------------ state shown to the client
    def refresh(self, w, now):
        """Advances every entity whose timer ran out. Returns the messages the client needs for what
        finished: growth and construction completion only reach the quest system through pushed messages
        (EntityManager::processEntityEndFeedMessage / processEntityEndConstructMessage)."""
        for lst in ('corals', 'houses', 'decorations', 'livingPlants', 'ornamentalPlants', 'rarePlants',
                    'patches', 'fishes'):
            for e in w.get(lst, []):
                self.advance(e, now)

    def advance(self, e, now):
        """Moves one entity on by its timers; finished growth/building is recorded in self.events as
        (message type, data) for the caller to push."""
        before = e.get('state')
        self._advance(e, now)
        after = e.get('state')
        if before == CONSTRUCTING and after == BUILT:
            self.events.append((END_CONSTRUCT, {'id': e['id']}))
        elif before == GROWING and after in (BUILT, PRODUCING, READY):
            self.events.append((END_FEED, {'id': e['id'], 'state': after}))

    def _advance(self, e, now):
        _, p = self.protos.get(e.get('prototypeId'))
        etype = (p or {}).get('type')
        st = e.get('state')
        if st == CONSTRUCTING and (etype != 'ET_SPEC_HOUSE' or e.get('constructionInterval', {}).get('finish', 0) <= now):
            e['state'] = BUILT
            e.pop('constructionInterval', None)
        elif st == GROWING and e.get('growingInterval', {}).get('finish', 0) <= now:
            if e.get('growingCycle', 0) < int(self.growth_proto(e).get('growingCycles', 1)):
                e['state'] = WAITING_FEED
            elif etype in HARVESTABLE:
                fin = e['growingInterval']['finish']
                e['state'] = PRODUCING
                e['producingInterval'] = interval(fin, self.ms(p, 'producingTime'))
            else:
                e['state'] = BUILT
            if e['state'] != GROWING:
                e.pop('growingInterval', None)
        if e.get('state') == PRODUCING and e.get('producingInterval', {}).get('finish', 0) <= now:
            e['state'] = READY

    def add_entity(self, w, pid, x, z, mirror=False, now=0):
        """Puts a fully grown/built entity into the world (starter reef, quest rewards)."""
        lst, p = self.protos.get(pid)
        if not lst:
            return None
        e = {'id': self.new_id(w), 'prototypeId': pid, 'position': {'x': x, 'z': z, 'transform': int(bool(mirror))}}
        etype = p.get('type')
        if etype in HARVESTABLE:
            e.update(state=READY, seeded=now, growingCycle=int(p.get('growingCycles', 1)))
        elif etype in GROWABLE:
            e.update(state=BUILT, seeded=now, growingCycle=int(p.get('growingCycles', 1)))
        elif etype == 'ET_PATCH':
            e.update(state=BUILT, plantCount=0)
        elif lst != 'fishes':
            e['state'] = BUILT
        w.setdefault(lst, []).append(e)
        return e

    # ------------------------------------------------------------ actions
    def apply(self, w, action, now):
        """Returns the reaction for one action (always with resultCode)."""
        name = action.get('action')
        d = action.get('data') or {}
        r = {'aid': action.get('aid'), 'resultCode': 200}
        handler = getattr(self, 'a_' + str(name), None)
        if handler:
            try:
                data = handler(w, d, now)
            except ActionRejected as exc:
                return {'aid': action.get('aid'), 'resultCode': exc.code,
                        'data': {'errorMessage': exc.message}}
            if data is not None:
                r['data'] = data
        return r

    def place(self, w, d, now):
        pid = d.get('prototypeId')
        lst, p = self.protos.get(pid)
        if not lst:
            return None, None, None
        e = {'id': self.new_id(w), 'prototypeId': pid,
             'position': {'x': int(d.get('x', 0)), 'z': int(d.get('z', 0)), 'transform': int(d.get('transform', 0)) & 1}}
        price = self.protos.shop_cost(pid, self.level(w)) or p.get('cost')
        if p.get('type') == 'ET_PATCH':  # the Nth patch costs what vitalityPatchList says for it
            n = len(w.get('patches', []))
            row = next((r for r in self.protos.vitality_patches if r.get('order') == n + 1), None)
            if row:
                price = row['cost']
        if d.get('early'):  # GET IT EARLY: the price times the multiplier, plus free algae
            price = {k: v * worlddef.GET_IT_EARLY_MULTIPLIER for k, v in (price or {}).items()}
            self.earn(w, {'energy': worlddef.GET_IT_EARLY_ALGAE})
        self.pay(w, price)
        w.setdefault(lst, []).append(e)
        return lst, p, e

    def a_entityConstruct(self, w, d, now):
        lst, p, e = self.place(w, d, now)
        if not e:
            return {'id': self.new_id(w), 'constructionInterval': interval(now, 0)}
        if p['type'] == 'ET_PATCH':
            e.update(state=BUILT, plantCount=0)
            return {'id': e['id']}
        e['constructionInterval'] = interval(now, self.ms(p, 'constructionTime'))
        e['state'] = CONSTRUCTING
        out = {'id': e['id'], 'constructionInterval': dict(e['constructionInterval'])}
        if p['type'] != 'ET_SPEC_HOUSE':
            e['state'] = BUILT  # decorations are placed finished (they have no construction graphics)
            e.pop('constructionInterval', None)
        self.earn(w, p.get('rewardAfterBuilt'))
        return out

    def a_entitySeed(self, w, d, now):
        lst, p, e = self.place(w, d, now)
        if not e:
            return {'id': self.new_id(w), 'seeded': now}
        e.update(state=WAITING_FEED, seeded=now, growingCycle=0)
        return {'id': e['id'], 'seeded': now}

    def a_placeItemForCleaning(self, w, d, now):
        lst, p, e = self.place(w, d, now)
        return {'id': e['id'] if e else self.new_id(w)}

    def a_seedEntitySeed(self, w, d, now):
        # A mystery seed grows into a random plant from its "seedResults" (story.json items "results").
        _, seed = self.protos.get(d.get('prototypeId'))
        price = self.protos.shop_cost(d.get('prototypeId'), self.level(w)) or (seed or {}).get('cost')
        choices = [pid for pid in (seed or {}).get('seedResults', []) if self.protos.get(pid)[0]]
        if self.protos.economy_mode == 'revival' and d.get('prototypeId') in economy.SEEDS:
            if any(int(w['status'].get(k, 0)) < int(v) for k, v in (price or {}).items()):
                raise ActionRejected(402, 'Not enough currency for this seed')
            plant = economy.choose_seed(w, d['prototypeId'], choices)
        else:
            plant = random.choice(choices) if choices else next(
                (pid for pid, (lst, p) in self.protos.by_id.items() if lst == 'livingPlants'), None)
        self.pay(w, price)
        lst, p = self.protos.get(plant)
        e = {'id': self.new_id(w), 'prototypeId': plant, 'seedPrototypeId': d.get('prototypeId'),
             'position': {'x': int(d.get('x', 0)), 'z': int(d.get('z', 0)), 'transform': int(d.get('transform', 0)) & 1},
             'state': WAITING_FEED, 'seeded': now, 'growingCycle': 0}
        w.setdefault(lst or 'livingPlants', []).append(e)
        if self.protos.economy_mode == 'revival':
            e['seedEconomyVersion'] = economy.VERSION
        return {'id': e['id'], 'seeded': now, 'prototypeId': plant}

    def a_entityMove(self, w, d, now):
        _, e = self.find(w, d.get('id'))
        if e:
            e['position'] = {'x': int(d.get('x', 0)), 'z': int(d.get('z', 0)), 'transform': int(d.get('transform', 0)) & 1}
            e.pop('inventory', None)
        return None

    def a_entityRefund(self, w, d, now):
        lst, e = self.find(w, d.get('id'))
        if e:
            _, p = self.protos.get(e['prototypeId'])
            self.earn(w, (p or {}).get('refundReward'))
            w[lst].remove(e)
        return None

    def growth_proto(self, e):
        """A plant grown from a mystery seed grows (and costs algae) like the seed until it is mature: the
        client shows the seed's feeding cost and timer."""
        _, p = self.protos.get(e.get('seedPrototypeId') or e.get('prototypeId'))
        if not p:
            _, p = self.protos.get(e.get('prototypeId'))
        return p or {}

    def grow(self, w, e, now, pay=True):
        p = self.growth_proto(e)
        if pay:
            if (self.protos.economy_mode == 'revival'
                    and e.get('seedPrototypeId') in economy.SEEDS
                    and any(int(w['status'].get(k, 0)) < int(v)
                            for k, v in p.get('growingCycleCost', {}).items())):
                raise ActionRejected(402, 'Not enough algae to feed this seed')
            self.pay(w, p.get('growingCycleCost'))
        e['growingCycle'] = min(e.get('growingCycle', 0) + 1, int(p.get('growingCycles', 1)))
        e['growingInterval'] = interval(now, self.ms(p, 'growingTime'))
        e['state'] = GROWING
        out = {'growingCycle': e['growingCycle'], 'growingInterval': e['growingInterval']}
        _, ep = self.protos.get(e.get('prototypeId'))
        if e['growingCycle'] >= int(p.get('growingCycles', 1)) and (ep or {}).get('type') in HARVESTABLE:
            # The last feeding: Growable::processResponseGrow takes the first production's "producingInterval"
            # from this reply (without it the plant never counts down to its first harvest). _advance starts
            # production at the same moment.
            out['producingInterval'] = interval(e['growingInterval']['finish'], self.ms(ep, 'producingTime'))
        return out

    def a_entityFeed(self, w, d, now):
        _, e = self.find(w, d.get('id'))
        if not e:
            return {'growingCycle': 1, 'growingInterval': interval(now, 0)}
        self.advance(e, now)
        return self.grow(w, e, now)

    def speedup_grow(self, w, d, now, paid):
        _, e = self.find(w, d.get('id'))
        if not e:
            return {'growingCycle': 1, 'growingInterval': interval(now, 0)}
        _, p = self.protos.get(e['prototypeId'])
        if paid:
            self.pay(w, (p or {}).get('speedupCost'))
        gi = e.get('growingInterval') or interval(now, 0)
        e['growingInterval'] = {'start': gi['start'], 'finish': now}
        out = {'growingCycle': e.get('growingCycle', 1), 'growingInterval': e['growingInterval']}
        self.advance(e, now)
        if e.get('state') == PRODUCING:
            out['producingInterval'] = e['producingInterval']
        return out

    def a_entitySpeedupFeed(self, w, d, now):
        return self.speedup_grow(w, d, now, True)

    def a_entityFreeSpeedupFeed(self, w, d, now):
        return self.speedup_grow(w, d, now, False)

    def a_entityCollectReward(self, w, d, now):
        _, e = self.find(w, d.get('id'))
        if not e:
            return {'producingInterval': interval(now, 0)}
        _, p = self.protos.get(e['prototypeId'])
        p = p or {}
        if p.get('type') == 'ET_PATCH':  # a full patch yields what its upgraded plant produces
            p = dict(self.patch_plant(e), type='ET_PATCH')
        self.earn(w, p.get('producedReward'))
        if p.get('type') in HARVESTABLE or p.get('type') == 'ET_PATCH':
            start = int(d.get('performed') or now)
            e['state'] = PRODUCING
            e['producingInterval'] = interval(start, self.ms(p, 'producingTime'))
            return {'producingInterval': e['producingInterval']}
        e['state'] = 1  # fish: idle again
        e.pop('producingInterval', None)
        return {}

    def speedup_production(self, w, d, now, paid):
        _, e = self.find(w, d.get('id'))
        if not e:
            return {'producingInterval': interval(now, 0)}
        _, p = self.protos.get(e['prototypeId'])
        if paid:
            self.pay(w, (p or {}).get('producingSpeedupCost'))
        pi = e.get('producingInterval') or interval(now, 0)
        e['producingInterval'] = {'start': pi['start'], 'finish': now}
        e['state'] = READY
        return {'producingInterval': e['producingInterval']}

    def a_entitySpeedupProduction(self, w, d, now):
        return self.speedup_production(w, d, now, True)

    def a_entityFreeSpeedupProduction(self, w, d, now):
        return self.speedup_production(w, d, now, False)

    def speedup_construction(self, w, d, now, paid):
        _, e = self.find(w, d.get('id'))
        if not e:
            return {'constructionInterval': interval(now, 0)}
        _, p = self.protos.get(e['prototypeId'])
        if paid:
            self.pay(w, (p or {}).get('speedupCost'))
        ci = e.get('constructionInterval') or interval(now, 0)
        out = {'constructionInterval': {'start': ci['start'], 'finish': now}}
        e['state'] = CONSTRUCTING
        e['constructionInterval'] = dict(out['constructionInterval'])
        self.advance(e, now)  # -> built, and the construction-ended message for CID_BUILT
        return out

    def a_entityConstructionSpeedup(self, w, d, now):
        return self.speedup_construction(w, d, now, True)

    def a_entityConstructionFreeSpeedup(self, w, d, now):
        return self.speedup_construction(w, d, now, False)

    def a_placeEntityInPatch(self, w, d, now):
        plst, plant = self.find(w, d.get('id'))
        _, patch = self.find(w, d.get('patchId'))
        if not patch:
            return {'full': False, 'plantCount': 0}
        _, pp = self.protos.get(patch['prototypeId'])
        cap = max(1, int((pp or {}).get('maxPlants') or 3))
        patch['plantCount'] = min(cap, patch.get('plantCount', 0) + 1)
        if plant:
            patch['plantPrototypeId'] = plant['prototypeId']
            w[plst].remove(plant)
        out = {'full': patch['plantCount'] >= cap, 'plantCount': patch['plantCount']}
        if out['full']:  # the plants become the upgraded plant, which produces on its own timer
            patch['state'] = PRODUCING
            patch['producingInterval'] = interval(now, self.ms(self.patch_plant(patch), 'producingTime'))
            out['producingInterval'] = patch['producingInterval']
        return out

    def patch_plant(self, patch):
        """The prototype a full patch produces as: its plant's upgradePrototypeId (or the plant itself)."""
        _, plant = self.protos.get(patch.get('plantPrototypeId'))
        _, upgraded = self.protos.get((plant or {}).get('upgradePrototypeId'))
        return upgraded or plant or {}

    def a_placeEntitiesToInventory(self, w, d, now):
        for eid in d.get('ids') or []:
            _, e = self.find(w, eid)
            if e:
                e['inventory'] = True
        return {}

    def a_fishFeed(self, w, d, now):
        _, e = self.find(w, d.get('id'))
        _, p = self.protos.get((e or {}).get('prototypeId'))
        p = p or {}
        self.pay(w, p.get('producingCost'))
        pi = interval(now, self.ms(p, 'producingTime'))
        if e:
            e['state'] = PRODUCING
            e['producingInterval'] = pi
        return {'producingInterval': pi}

    def a_collectAllFish(self, w, d, now):
        ids = []
        for e in w.get('fishes', []):
            self.advance(e, now)
            if e.get('state') == READY:
                _, p = self.protos.get(e['prototypeId'])
                self.earn(w, (p or {}).get('producedReward'))
                e['state'] = 1
                e.pop('producingInterval', None)
                ids.append(e['id'])
        return {'fishes': ids, 'reward': {'hardMoney': 0}}

    # ------------------------------------------------------------ daily reward (tap the hidden plants)
    # ------------------------------------------------------------ vitality
    def vitality(self, w):
        """data::Vitality: the hearts of the grown ornamental plants (category 1), rare plants (2), rare fish (3)
        and guest fish (4), each capped by the level's category cap; the total (category 0) is their sum."""
        raw = {1: 0, 2: 0, 3: 0, 4: 0}
        for lst, cat in (('ornamentalPlants', 1), ('rarePlants', 2)):
            for e in w.get(lst, []):
                if e.get('state') not in (GROWING, WAITING_FEED, CONSTRUCTING) and not e.get('inventory'):
                    raw[cat] += int((self.protos.get(e['prototypeId'])[1] or {}).get('vitality', 0) or 0)
        for e in w.get('fishes', []):
            pid = e.get('prototypeId', '')
            hearts = int((self.protos.get(pid)[1] or {}).get('vitality', 0) or 0)
            if pid.startswith('fish_rare_'):
                raw[3] += hearts
            elif pid.startswith('fish_guest_'):
                raw[4] += hearts or 100
        level = next((l for l in self.protos.levels if l['level'] == self.level(w)), {})
        caps = {c['categoryId']: c['vitality'] for c in (level.get('vitality') or {}).get('categories', [])}
        got = {c: min(v, caps.get(c, v)) for c, v in raw.items()}
        total = min(sum(got.values()), (level.get('vitality') or {}).get('vitality') or sum(got.values()))
        w['status']['vitality'] = {'vitality': total, 'level': self.level(w), 'categories':
                                   [{'categoryId': 0, 'vitality': total}] +
                                   [{'categoryId': c, 'vitality': v} for c, v in sorted(got.items())]}
        return total

    @staticmethod
    def daily_taps_left(w):
        st = w.get('status', {})
        if w.get('dailyTapDay') != time.strftime('%Y-%m-%d'):
            return worlddef.DAILY_TAPS
        return max(0, worlddef.DAILY_TAPS - int(st.get('tapRewardsCount', 0)))

    def new_day(self, w):
        """A new day: the daily reward opens again with all its taps. The ad-funded extra tap and the pearl
        "get everything" button stay hidden."""
        today = time.strftime('%Y-%m-%d')
        st = w['status']
        if w.get('dailyTapDay') != today:
            w['dailyTapDay'] = today
            st['tapRewardsCount'] = 0
        st['tapRewardsExtraTapAvail'] = False
        st['tapRewardsGetAllAvail'] = False

    def a_dailyTapReward(self, w, d, now):
        """One tap: DialogDailyReward::processResponse shows data.reward on the tapped plant."""
        self.new_day(w)
        st = w['status']
        reward = {}
        if st['tapRewardsCount'] < worlddef.DAILY_TAPS:
            st['tapRewardsCount'] += 1
            level = self.level(w)
            # Pearls can no longer be bought (Google Play), so the last tap of the day always holds some
            if st['tapRewardsCount'] == worlddef.DAILY_TAPS:
                kind = 'hardMoney'
            else:
                kind = random.choices(['virtualMoney', 'energy', 'xp', 'hardMoney'], [40, 30, 15, 15])[0]
            amount = {'virtualMoney': 30 + 15 * level, 'energy': 10 + 5 * level, 'xp': 10 + 5 * level,
                      'hardMoney': 2 + level // 10}[kind]
            reward = {kind: amount}
            self.earn(w, reward)
        full = {k: int(reward.get(k, 0)) for k in ('xp', 'virtualMoney', 'energy', 'hardMoney', 'lowTool', 'highTool')}
        return {'reward': full}

    # ------------------------------------------------------------ treasure chests
    def chest_reward(self, w, chest):
        """What a chest holds: its kind's currency (more in the expensive ones), sometimes spines."""
        level = self.level(w)
        r = {}
        if chest == 'chest_tutorial':
            r = {'virtualMoney': 100, 'lowTool': 3, 'highTool': 2}
        else:
            kind = chest.split('_')[1] if chest.count('_') >= 2 else 'coins'
            tier = 3 if chest.endswith('high_exp') else 2 if chest.endswith('high_cheap') else 1
            if kind == 'pearls':
                r['hardMoney'] = random.randint(1, 2) * tier
            elif kind == 'algae':
                r['energy'] = random.randint(20, 40) * tier * (5 + level) // 5
            else:
                r['virtualMoney'] = random.randint(40, 80) * tier * (5 + level) // 5
            if random.random() < 0.5:
                r['lowTool'] = random.randint(1, 3)
            if random.random() < 0.2:
                r['highTool'] = 1
        self.earn(w, r)
        return {k: int(r.get(k, 0)) for k in ('xp', 'virtualMoney', 'energy', 'hardMoney', 'lowTool', 'highTool')}

    def a_chestPicklock(self, w, d, now):
        """The free pick lock: about one try in ten opens the chest (DialogFlowTreasure reads data.reward)."""
        if d.get('prototypeId') == 'chest_tutorial' or random.random() < 0.1:
            return {'reward': self.chest_reward(w, d.get('prototypeId', ''))}
        return {}

    def a_chestSpentTool(self, w, d, now):
        """Common spines spent without opening the chest (the client rolled the attempt)."""
        self.pay(w, d.get('cost'))
        return {}

    def a_chestCollectReward(self, w, d, now):
        """The chest opened with spines: pay them, hand out what is inside."""
        self.pay(w, d.get('cost'))
        return {'reward': self.chest_reward(w, d.get('prototypeId', ''))}

    # ------------------------------------------------------------ Hide and Seek with Squirt
    def a_buySquirt(self, w, d, now):
        """Play now although Squirt is not ready yet: the level's squirtCost."""
        level = next((l for l in self.protos.levels if l['level'] == self.level(w)), {})
        self.pay(w, level.get('squirtCost'))
        w['status']['lastSquirt'] = 0
        w['status']['squirtStarted'] = now
        return None

    def a_squirtFailed(self, w, d, now):
        """Not all fish found: Squirt rests until squirtIntervalTime has passed (TheUser::remainingTimeToNextMinigame)."""
        w['status']['lastSquirt'] = now
        return None

    def a_minigamePayToContinue(self, w, d, now):
        self.pay(w, d.get('cost'))
        return None

    def a_collectSquirtTapReward(self, w, d, now):
        self.earn(w, d.get('reward'))
        return None

    def a_questTaskSetState(self, w, d, now):
        if d.get('reward'):
            self.earn(w, d['reward'])
        return {'questFinished': bool(d.get('finished'))}

