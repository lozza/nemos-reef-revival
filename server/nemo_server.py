#!/usr/bin/env python3
"""Private server for Nemo's Reef (Android 1.8.1). Standard library only.

    python nemo_server.py --apk nemos-reef-1-8-1-en-android.apk [--port 8080]

Answers both the Tapulous account API and the game API (see PROTOCOL.md and ACTIONS.md). Every request and
response is appended to <data>/requests.log as JSON lines so unknown calls can be studied.
"""
import argparse
import copy
import email.parser
import email.policy
import hashlib
import json
import os
import random
import sqlite3
import string
import threading
import time
import traceback
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import actions
import iscn
import quests
import texts
import worlddef
import economy
import economy_ui

# 128-byte table from cypher::XorCypher::xorFuncChar; getDaWorld bodies are XORed with key[i & 0x7f].
XOR_KEY = bytes.fromhex(
    '06429d212c941939646215fa85a0450e8c554d4de90c3bb1378c14553e516e6f'
    'd612949349ca962ed836db1dda33eb4b0062ff1509de594593f7ba70ab310b15'
    'f6d28021eada52bc4ee5b46cf7c8038ecefb16a6c7c8640290237b4744de384e'
    '283a884cb8844c54df27b9152fc5b7ef0642705954041d2b05161d804590134a')

ENTITY_LISTS = ('corals', 'houses', 'decorations', 'livingPlants', 'ornamentalPlants',
                'fishes', 'quests', 'rarePlants', 'patches')


def xor(data):
    return bytes(b ^ XOR_KEY[i & 0x7f] for i, b in enumerate(data))


def now_ms():
    return int(time.time() * 1000)


class Store:
    """SQLite-backed users and worlds."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY, udid TEXT UNIQUE, name TEXT, secret TEXT, created INTEGER);
            CREATE TABLE IF NOT EXISTS worlds (uid TEXT PRIMARY KEY, world TEXT);
            CREATE TABLE IF NOT EXISTS friends (uid TEXT, friend TEXT, PRIMARY KEY (uid, friend));
        ''')

    def user_by_udid(self, udid):
        with self.lock:
            r = self.db.execute('SELECT uid, name, secret FROM users WHERE udid=?', (udid,)).fetchone()
        return r and {'uid': r[0], 'name': r[1], 'secret': r[2]}

    def user(self, uid):
        with self.lock:
            r = self.db.execute('SELECT uid, name, secret FROM users WHERE uid=?', (uid,)).fetchone()
        return r and {'uid': r[0], 'name': r[1], 'secret': r[2]}

    def create_user(self, udid, name):
        uid = str(random.randint(10 ** 9, 10 ** 10 - 1))
        secret = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
        with self.lock, self.db:
            self.db.execute('INSERT INTO users VALUES (?,?,?,?,?)', (uid, udid, name, secret, int(time.time())))
        return {'uid': uid, 'name': name, 'secret': secret}

    def name_taken(self, name, uid=None):
        with self.lock:
            r = self.db.execute('SELECT uid FROM users WHERE lower(name)=lower(?) AND uid IS NOT ?', (name, uid)).fetchone()
        return bool(r)

    def unique_name(self, name, uid=None):
        """Player names are unique (friends find each other by name): a taken one gets a number."""
        name = (name or '').strip()[:20] or 'Diver'
        if not self.name_taken(name, uid):
            return name
        n = 2
        while self.name_taken('%s%d' % (name, n), uid):
            n += 1
        return '%s%d' % (name, n)

    def user_by_name(self, name):
        with self.lock:
            r = self.db.execute('SELECT uid, name, secret FROM users WHERE lower(name)=lower(?)',
                                ((name or '').strip(),)).fetchone()
        return r and {'uid': r[0], 'name': r[1], 'secret': r[2]}

    def add_friend(self, uid, friend):
        """Friendship goes both ways: each sees the other in their list."""
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO friends VALUES (?,?)', (uid, friend))
            self.db.execute('INSERT OR IGNORE INTO friends VALUES (?,?)', (friend, uid))

    def friends(self, uid):
        with self.lock:
            return [{'uid': r[0], 'name': r[1]} for r in self.db.execute(
                'SELECT users.uid, users.name FROM friends JOIN users ON users.uid = friends.friend '
                'WHERE friends.uid=? ORDER BY lower(users.name)', (uid,))]

    def rename(self, uid, name):
        with self.lock, self.db:
            self.db.execute('UPDATE users SET name=? WHERE uid=?', (name, uid))

    def world(self, uid):
        with self.lock:
            r = self.db.execute('SELECT world FROM worlds WHERE uid=?', (uid,)).fetchone()
        return json.loads(r[0]) if r else None

    def save_world(self, uid, world):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO worlds VALUES (?,?)', (uid, json.dumps(world)))

    def delete_world(self, uid):
        with self.lock, self.db:
            self.db.execute('DELETE FROM worlds WHERE uid=?', (uid,))


def new_world(user):
    """A fresh reef. Keys follow World::processUserWorld / readEntities / data::UserStatus."""
    w = {name: [] for name in ENTITY_LISTS}
    w['user'] = {'uid': user['uid'], 'name': user['name']}
    w['finishedTutorialQuests'] = []
    # mapunlock::MapUnlockerBase::load runs only if "sectors" exists: it unlocks "base" plus every
    # area named in the list (scene areas: tiles, tiles_2 ... tiles_8). Without it the map stays locked.
    # It recomputes the unlocked tiles only when the list is non-empty, so "base" is always listed.
    w['sectors'] = ['base']
    w['status'] = {
        'hardMoney': 10, 'virtualMoney': 100, 'energy': 100, 'level': 1, 'xp': 0,
        'xpForNextLevel': 50, 'lowTool': 0, 'highTool': 0,
        # data::Vitality::load: {vitality, level (optional), categories[{categoryId, ...}]}
        'vitality': {'vitality': 100, 'level': 1, 'categories': []},
        'friendCollectCount': 0, 'friendCollectMaxCount': FRIEND_ACTIONS,
        'friendCollectInterval': {'start': 0, 'finish': 0},
        'lastSquirt': 0, 'squirtStarted': 0,
        'tapRewardsCount': 0, 'tapRewardsExtraTapAvail': False, 'tapRewardsGetAllAvail': False,
    }
    w['data'] = user_info()
    return w


SECTOR_ADDED = 9  # message type
# reef areas (level.iscn tile groups) in order outward from "base", and the level each opens at
EXPANSIONS = [(4, 'tiles'), (8, 'tiles_2'), (12, 'tiles_3'), (16, 'tiles_4'), (21, 'tiles_5'), (27, 'tiles_6'),
              (33, 'tiles_7'), (40, 'tiles_8')]
FRIEND_UID = 'friend_nemo'
FRIEND_ACTIONS = 5  # "Daily Actions: 5/5" on a friend's reef


def user_info(w=None):
    # data::UserStatus::loadUserInfo(obj): if obj has "resumeGame", reads these fields inside it.
    # dailyTapRewardReady opens the daily reward (DailyRewardManager::checkDailyRewards).
    ready = bool(w) and actions.Reef.daily_taps_left(w) > 0
    return {'resumeGame': {'moneySpent': False, 'tapjoyMoneySpent': False,
                           'currentDailyRewardCountOffer': 0, 'lastDailyRewardCountClaimed': 0,
                           'dailyTapRewardReady': ready}}


class Game:
    def __init__(self, args):
        os.makedirs(args.data, exist_ok=True)
        self.store = Store(os.path.join(args.data, 'nemo.db'))
        self.log_path = os.path.join(args.data, 'requests.log')
        self.log_lock = threading.Lock()
        self.world_lock = threading.Lock()
        self.story = (quests.NoStory() if args.no_story else
                      quests.Story(economy_mode=getattr(args, 'economy', None)))
        if args.worlddef:
            with open(args.worlddef, 'rb') as f:
                self.worlddef = json.load(f)
            if not self.worlddef.get('version'):
                worlddef.finish(self.worlddef)
        else:
            self.worlddef = worlddef.build(args.apk, self.story)
        self.reef = actions.Reef(actions.Prototypes(self.worlddef))
        self.dummy_friend = None
        self.last_seen = {}  # uid -> ms of the player's last request (getFriendLastTime)
        # The client keeps one downloaded text bundle and only asks again when this id changes
        # (GameTextManager::existTextBundle), so the id covers every text the server sends.
        h = hashlib.sha1(str(self.worlddef.get('localizedTextsBundleId')).encode())
        h.update(json.dumps(texts.build(args.apk, 'en'), sort_keys=True).encode('utf-8'))
        if self.story.economy_mode == 'revival':
            h.update(json.dumps(economy_ui.TEXTS, sort_keys=True).encode('utf-8'))
        bundle_id = 2 + int(h.hexdigest()[:7], 16)
        # The Kids edition (age under the game's limit) gets its own worldDef; getCurrentWorldVersion tells each
        # player which one, and the client downloads it by version.
        self.args, self.bundle_id = args, bundle_id
        self.world_versions, self.worlddef_blobs = {}, {}
        self.publish_worlddefs()
        with zipfile.ZipFile(args.apk) as z:
            self.bundles = json.loads(z.read('assets/bundle.json'))
            self.economy_assets = economy_ui.build(z, revival=self.story.economy_mode == 'revival')
            # The designed starting layout; nemo_2/_3 are later upgrade stages of the same anemone and
            # Peach's house belongs to a later area.
            self.starter = [e for e in iscn.entities(z.read('assets/level.iscn'))
                            if e['ipro'][:-5] not in ('spec_house_nemo_2', 'spec_house_nemo_3', 'spec_house_peach')]
        if args.reset_all:
            with self.store.lock, self.store.db:
                self.store.db.execute('DELETE FROM worlds')
                self.store.db.execute('DELETE FROM users')
                self.store.db.execute('DELETE FROM friends')
            print('everything reset: every device is a new player again (asked for a name)')
        if args.reset_worlds:
            with self.store.lock, self.store.db:
                self.store.db.execute('DELETE FROM worlds')
            print('all reefs reset: every player starts again with the starter reef')
        self.apk = args.apk
        self.texts = {}
        self.server_id = '1'

    def log(self, entry):
        with self.log_lock, open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    # ---------------------------------------------------------------- Tapulous account API
    def tap_user(self, p, create_name=None):
        """The account of this device (token_info is the device id). Accounts are only made by register_user,
        after the player has chosen a name in the PROFILE NAME dialog."""
        udid = p.get('token_info') or p.get('udid') or 'unknown'
        user = self.store.user_by_udid(udid)
        if not user and create_name:
            user = self.store.create_user(udid, self.store.unique_name(create_name))
        return user

    def tap(self, method, p):
        if method == 'recommend_name':
            return {'response': {'suggestedName': self.store.unique_name('Diver%04d' % random.randint(0, 9999))}}
        if method == 'authenticate':
            user = self.tap_user(p)
            if not user:  # a new device: an empty user_id makes the client ask for a name and register
                return {'response': {'user_id': '', 'username': ''}}
            return {'response': {'user_id': user['uid'], 'username': user['name']},
                    'attachedData': {'clientSignatureKey': user['secret']}}
        if method == 'register_user':
            user = self.tap_user(p, create_name=p.get('proposed_name'))
            return {'response': {'user': {'user_id': user['uid'], 'username': user['name']}}}
        user = self.tap_user(p, create_name='Diver%04d' % random.randint(0, 9999))
        if method == 'change_username':
            name = self.store.unique_name(p.get('new_username') or user['name'], user['uid'])
            self.store.rename(user['uid'], name)
            return {'response': {'user_id': user['uid'], 'username': name}}
        if method in ('login', 'logout'):
            return {'response': {'user_id': user['uid'], 'username': user['name']}}
        if method == 'get_friends':
            return {'response': {'friends': [{'user_id': f['uid'], 'username': f['name'], 'has_app': True}
                                             for f in self.store.friends(user['uid'])]}}
        if method == 'add_friend':  # by profile name; both players then see each other
            friend = self.store.user_by_name(p.get('friend_name'))
            if not friend or friend['uid'] == user['uid']:
                return {'status': {'code': 404, 'reason': 'No player with that name'}}
            self.store.add_friend(user['uid'], friend['uid'])
            return {'response': {'friend': {'user_id': friend['uid'], 'username': friend['name']}}}
        return None

    # ---------------------------------------------------------------- game API
    def envelope(self, body, req):
        body.setdefault('resultCode', 200)
        body.setdefault('serverTime', now_ms())
        body.setdefault('serverId', self.server_id)
        body.setdefault('stamp', req.get('stamp', 0))
        return body

    def user_world(self, uid):
        user = self.store.user(uid)
        if not user:
            return None
        w = self.store.world(uid)
        if w is None:
            w = new_world(user)
            self.starter_reef(w)
            self.story.new_player(w)
            self.store.save_world(uid, w)
        elif (not isinstance(w.get('status', {}).get('vitality'), dict)
              or not isinstance(w.get('data', {}).get('resumeGame'), dict)
              or not w.get('sectors')):  # worlds saved by older versions
            w['status']['vitality'] = new_world(user)['status']['vitality']
            w['data'] = user_info()
            w['sectors'] = w.get('sectors') or ['base']
            self.store.save_world(uid, w)
        if self.story.migrate(w):
            self.store.save_world(uid, w)
        return w

    def publish_worlddefs(self, rebuild=False):
        """The normal and the Kids worldDef, stamped and ready to send. Time events' windows are in the quest
        prototypes, so when one ends the definitions are built again with the next ones (a new version, which
        the clients download)."""
        if rebuild:
            self.worlddef = worlddef.build(self.args.apk, self.story)
        self.worlddefs = {False: self.worlddef}
        self.player_world_versions = {}
        self.worlddef_blobs = {}
        if not self.args.worlddef and not self.args.no_story:
            self.worlddefs[True] = worlddef.build(self.args.apk, quests.Story(
                kids=True, economy_mode=self.story.economy_mode))
        for kids, wd in self.worlddefs.items():
            wd['localizedTextsBundleId'] = self.bundle_id
            worlddef.finish(wd)  # new version stamp, so clients fetch the definition with the new id
            self.world_versions[kids] = str(wd['version'])
            self.worlddef_blobs[str(wd['version'])] = xor(json.dumps(wd, separators=(',', ':')).encode('utf-8'))
        self.world_version = self.world_versions[False]

    def player_world_version(self, w, exchange_client=False):
        """A level-sized exchange catalogue; immutable versions keep prices honest."""
        kids = bool(w and w.get('kids'))
        if self.story.economy_mode != 'revival' or self.args.worlddef:
            return self.world_versions.get(kids, self.world_version)
        level = max(1, min(economy.MAX_LEVEL,
                    int((w or {}).get('status', {}).get('level', 1))))
        key = (kids, level, bool(exchange_client))
        with self.world_lock:
            if key not in self.player_world_versions:
                wd = copy.deepcopy(self.worlddefs.get(kids, self.worlddef))
                wd['exchangePackagePrototypes'] = economy.packages(level) if exchange_client else []
                worlddef.finish(wd)
                version = str(wd['version'])
                self.worlddef_blobs[version] = xor(json.dumps(wd, separators=(',', ':')).encode('utf-8'))
                self.player_world_versions[key] = version
            return self.player_world_versions[key]

    def game(self, endpoint, p):
        until = getattr(self.story, 'events_until', 0)
        if until and now_ms() >= until:
            with self.world_lock:
                if now_ms() >= getattr(self.story, 'events_until', 0):
                    self.publish_worlddefs(rebuild=True)
        """Returns (bytes, content_type)."""
        req = {}
        if p.get('json'):
            try:
                req = json.loads(p['json'])
            except ValueError:
                pass
        uid = p.get('uid') or req.get('uid') or ''
        if uid:
            self.last_seen[uid] = now_ms()

        if endpoint == 'getdaworld':
            version = p.get('worldVersion') or req.get('worldVersion') or ''
            if version not in self.worlddef_blobs:
                version = self.player_world_version(self.store.world(uid) if uid else None,
                                                    p.get('_exchange_client', False))
            return self.worlddef_blobs[version], 'application/octet-stream'
        if endpoint == 'getcurrentworldversion':
            w = self.store.world(uid) if uid else None
            body = {'worldVersion': self.player_world_version(w, p.get('_exchange_client', False))}
        elif endpoint in ('gettutorialworld', 'getfriendworld'):
            body = self.friend_world(uid, req.get('friendUid') if endpoint == 'getfriendworld' else None)
        elif endpoint == 'startgame':
            w = self.user_world(uid)
            if w:
                with self.world_lock:
                    self.reef.events = []
                    if self.story.economy_mode == 'revival':
                        economy.migrate_seed_timers(w, now_ms())
                        economy.migrate_progression(w, self.reef.protos, self.story.quests, now_ms())
                        economy.start_session(w)
                    self.reef.refresh(w, now_ms())
                    # "age" (ActionStartGame): 1 = under the age limit the player entered, 2 = at or over it
                    if req.get('age') in (1, 2):
                        w['kids'] = req['age'] == 1
                    self.finish_visit(w)
                    self.level_gifts(w)
                    self.pattern_fish(w)
                    self.reef.new_day(w)
                    self.reef.vitality(w)
                    self.expand(w, push=False)
                    self.story.catch_up(w, push=False)  # in the startgame quests list
                    self.homework(w)
                    self.story.run_events(w, now_ms(), push=False)
                    self.story.recover_seed_tasks(w, self.reef.protos,
                                                 lambda q, r: self.quest_done(w, q, r))
                    # quest messages are covered by the quests list; entity events still have to be pushed
                    w['messages'] = [m for m in w.get('messages', []) if m['type'] != quests.QUEST_OPEN]
                    self.push_events(w)
                    self.unstick_quests(w)
                    self.store.save_world(uid, w)
            body = dict(w or new_world({'uid': uid, 'name': 'Diver'}))
            for k in ('nextEntityId', 'nextQuestId', 'nextMessageId', 'messages', 'patterns',
                      'exchangeSession', 'exchangeReceipts', 'algaeExchange', 'mysterySeedMisses',
                      'progressionEconomyVersion'):
                body.pop(k, None)
            # World::processUserWorld passes the root to data::TheUser::updateUserInfo
            body['resumeGame'] = user_info(w)['resumeGame']
            for k in ('moneySpent', 'tapjoyMoneySpent', 'currentDailyRewardCountOffer',
                      'lastDailyRewardCountClaimed', 'dailyTapRewardReady'):
                body.pop(k, None)  # flat copies sent by older versions
        elif endpoint == 'reset':
            self.store.delete_world(uid)
            body = {}
        elif endpoint == 'resources':
            body = economy_ui.bundle(self.bundles, self.economy_assets, p.get('_asset_base', ''))
        elif endpoint == 'getfriendstatus':
            # data::FriendList::update reads "participants" (a missing key crashes): level and co-op state per friend
            body = {'participants': []}
            for fid in req.get('friends') or []:
                fw = self.store.world(str(fid))
                if fw:
                    body['participants'].append({'uid': str(fid), 'level': int(fw['status'].get('level', 1)),
                                                 'coopQuestStatus': 0, 'clientVersion': '1.8.1'})
        elif endpoint == 'getfriendlasttime':
            # {"friends": {uid: last seen, epoch ms}} (the co-op tab shows "offline" for 0 or very old times)
            body = {'friends': {str(fid): self.last_seen.get(str(fid), 0) for fid in req.get('friends') or []},
                    'participants': []}
        elif endpoint == 'getlocalizedtextsbundle':
            # ActionGetLocalizedTextsBundle::loadingFinished hands response["texts"] to GameTextManager::load.
            lang = p.get('languageCode') or req.get('languageCode') or 'en'
            if lang not in self.texts:
                self.texts[lang] = texts.build(self.apk, lang)
                self.texts[lang].update(self.story.texts())
                if self.story.economy_mode == 'revival':
                    self.texts[lang].update(economy_ui.TEXTS)
            body = {'texts': self.texts[lang]}
        else:  # '/api': queued game actions
            with self.world_lock:
                w = self.user_world(uid)
                self.reef.events = []
                body = {'reactions': [self.reaction(w, a, req.get('stamp'),
                                     p.get('_exchange_client', False)) for a in req.get('actions', [])]}
                if w:
                    self.reef.refresh(w, now_ms())
                    self.push_events(w)
                    self.story.catch_up(w)  # quests that open with a level-up
                    self.story.run_events(w, now_ms())
                    self.expand(w)
                    self.reef.vitality(w)
                    # The client never changes currencies itself: the HUD only updates from a "status"
                    # object next to the reactions (ComManager::loadingFinished → updateUserStatus).
                    body['status'] = w['status']
                    if w.get('messages'):
                        body['messages'] = list(w['messages'])
                    self.store.save_world(uid, w)
        return json.dumps(self.envelope(body, req)).encode('utf-8'), 'application/json'

    def reaction(self, w, action, stamp=None, exchange_client=False):
        if action.get('action') == 'resumeGame':
            return {'aid': action.get('aid'), 'resultCode': 200, 'data': user_info(w)}
        if w is None:
            return {'aid': action.get('aid'), 'resultCode': 200}
        try:
            name, d = action.get('action'), action.get('data') or {}
            ok = {'aid': action.get('aid'), 'resultCode': 200}
            if name == 'exchangeMoney':
                if not exchange_client:
                    return {'aid': action.get('aid'), 'resultCode': 403,
                            'data': {'errorMessage': 'Update the APK patch for algae exchanges.'}}
                return dict(economy.exchange(w, d, stamp, action.get('aid'), now_ms(),
                                             self.story.economy_mode), aid=action.get('aid'))
            if name == 'questTaskSetState':
                return dict(ok, data=self.story.task_state(w, d, lambda q, r: self.quest_done(w, q, r)))
            if name == 'questStart':
                e = self.story.find(w, d.get('id'))
                if e and self.story.is_squirt(e['prototypeId']):
                    w['status']['squirtStarted'] = now_ms()  # a game of Hide and Seek begins
                self.story.start(w, d)
                return ok
            if name == 'questBuyHint':
                self.story.buy_hint(w, d, self.reef.pay)
                return ok
            if name == 'confirmMessages':
                self.story.confirm(w, d)
                return ok
            if name == 'savePattern':
                if d.get('patternId') and d['patternId'] not in w.setdefault('patterns', []):
                    w['patterns'].append(d['patternId'])
                    self.pattern_fish(w)
                return ok
            if name == 'buyPatternHint':
                return ok
            if name == 'chestCollectReward':
                r = self.reef.apply(w, action, now_ms())
                self.treasure_fish(w)
                return r
            if name == 'friendsEntityCollectReward':
                return dict(ok, data=self.friend_collect(w, d))
            if name == 'getFriendFish':  # the friend's sticker album: data::FriendFishList::load reads "fishes"
                fw = self.store.world(str(d.get('friendUid'))) or {}
                return dict(ok, data={'fishes': fw.get('fishes', [])})
            if name == 'getFriendVitality':  # data::Vitality::load reads "vitality"
                fw = self.store.world(str(d.get('friendUid'))) or {}
                return dict(ok, data={'vitality': fw.get('status', {}).get('vitality') or
                                      {'vitality': 0, 'level': 1, 'categories': []}})
            return self.reef.apply(w, action, now_ms())
        except Exception:
            traceback.print_exc()
            return {'aid': action.get('aid'), 'resultCode': 200, 'data': {}}


    def push_events(self, w):
        events, self.reef.events = self.reef.events, []
        for kind, data in events:
            w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': kind, 'data': data})
            if kind == actions.END_FEED:
                self.attract_rare_fish(w, data['id'])

    def attract_rare_fish(self, w, plant_id):
        """A rare plant has grown: the rare fish that loves it comes to the reef (or, for plants without a
        partner, a rare fish the reef does not have yet). A reef welcomes one fish of each kind."""
        e = next((x for x in w.get('rarePlants', []) if x['id'] == plant_id), None)
        rare = self.story.data.get('rareFish') or {}
        if not e or e.get('attracted') or not rare.get('all'):
            return
        e['attracted'] = True
        owned = {f.get('prototypeId') for f in w.get('fishes', [])}
        fish = rare.get('pairs', {}).get(e['prototypeId'])
        if not fish or fish in owned:
            fish = next((f for f in rare['all'] if f not in owned), None)
        if not fish:
            return
        pos = e.get('position', {})
        f = self.reef.add_entity(w, fish, pos.get('x', 80), pos.get('z', 80))
        if f:  # message type 1: EntityManager::processFishAddedMessage swims the fish in
            w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': 1, 'data': dict(f)})

    def level_gifts(self, w):
        """Once: the level-up gifts of the levels a player reached before level-ups gave any."""
        if w.get('levelGifts'):
            return
        w['levelGifts'] = 1
        lvl = int(w['status'].get('level', 1))
        given = set((self.story.data.get('levels') or {}).get('rewards', {}))
        for l in self.reef.protos.levels:
            if 1 < l['level'] <= lvl and str(l['level']) not in given:
                self.reef.pay(w, l.get('reward'), +1)

    def pattern_fish(self, w):
        """The fish a plant combination attracted moves in for good. The client's fish is temporary
        (PatternSystem::attracktFish) and is not reliably rebuilt when the reef loads, so every combination's
        fish is kept as a real entity here, one of each kind (it shows from the next start; the one swimming
        now stays until then)."""
        defs = {pt['id']: pt for pt in self.story.data.get('patterns', [])
                if w.get('kids') or not pt.get('kidsOnly')}
        # Combinations standing in the reef count too, even if the game never reported them (savePattern):
        # the near items within the 6x6 area around the central item (PatternArea, centred on it)
        placed = [e for lst in ('livingPlants', 'ornamentalPlants', 'corals', 'decorations', 'rarePlants')
                  for e in w.get(lst, []) if not e.get('inventory') and e.get('position')]
        for pt in defs.values():
            if pt['id'] in w.get('patterns', []) or not pt['nearPrototypes']:
                continue  # (one-item combinations: the starter reef has some; the game reports those itself)
            for c in (e for e in placed if e['prototypeId'] == pt['centralPrototype']):
                cx, cz = c['position']['x'], c['position']['z']
                r = int(pt.get('areaSize', 6)) // 2
                if all(any(e['prototypeId'] == n and e is not c and cx - r <= e['position']['x'] <= cx + r + 1
                           and cz - r <= e['position']['z'] <= cz + r + 1 for e in placed) for n in pt['nearPrototypes']):
                    w.setdefault('patterns', []).append(pt['id'])
                    break
        for pid in w.get('patterns', []):
            pt = defs.get(pid)
            if not pt or any(f.get('prototypeId') == pt['producedFishId'] for f in w.get('fishes', [])):
                continue
            central = next((e for lst in ('livingPlants', 'ornamentalPlants', 'corals', 'decorations')
                            for e in w.get(lst, []) if e.get('prototypeId') == pt['centralPrototype']), None)
            pos = (central or {}).get('position', {})
            self.reef.add_entity(w, pt['producedFishId'], pos.get('x', 80), pos.get('z', 80))
        w.pop('patternFish', None)

    def treasure_fish(self, w):
        """A chest opened during a treasure fish's month may hold that fish (surely by the TREASURE_CHESTS-th).
        Message type 1 with an EST_FISH_TREASURE fish plays ScriptTreasureFishAdded (the "new treasure fish" scene)."""
        now = now_ms()
        owned = {f.get('prototypeId') for f in w.get('fishes', [])}
        for fid, (start, end) in getattr(self.story, 'treasure', {}).items():
            if not start <= now <= end or fid in owned:
                continue
            key = '%s@%d' % (fid, start)
            count = w.setdefault('treasureChests', {}).get(key, 0) + 1
            w['treasureChests'][key] = count
            if count >= quests.TREASURE_CHESTS or random.random() < 0.2:
                e = self.reef.add_entity(w, fid, 80, 80)
                if e:
                    w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': 1, 'data': dict(e)})
            return

    def unstick_quests(self, w):
        """A running grow/build task whose item is already grown/built (finished while the game was closed,
        or before the server sent completion messages) never ticks, so resend the completion message."""
        queued = {(m['type'], m['data'].get('id')) for m in w.get('messages', []) if isinstance(m.get('data'), dict)}
        for q in w.get('quests', []):
            proto = self.story.quests.get(q['prototypeId'], {})
            for i, task in enumerate(proto.get('tasks', [])):
                st = next((t for t in q['tasks'] if t['tid'] == i + 1), {})
                if st.get('finished') or task['cid'] not in ('CID_GROWN', 'CID_BUILT') or len(task.get('params', [])) < 2:
                    continue
                for lst in ('corals', 'livingPlants', 'ornamentalPlants', 'rarePlants', 'houses', 'decorations', 'patches'):
                    for e in w.get(lst, []):
                        if e.get('prototypeId') != task['params'][1]:
                            continue
                        if task['cid'] == 'CID_GROWN' and e.get('state') in (actions.BUILT, actions.PRODUCING, actions.READY):
                            msg = (actions.END_FEED, {'id': e['id'], 'state': e['state']})
                        elif task['cid'] == 'CID_BUILT' and e.get('state') == actions.BUILT:
                            msg = (actions.END_CONSTRUCT, {'id': e['id']})
                        else:
                            continue
                        if (msg[0], e['id']) not in queued:
                            queued.add((msg[0], e['id']))
                            w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': msg[0], 'data': msg[1]})

    def homework(self, w):
        """Mr. Ray's homework for today: grow something the player can buy, collect from one of their living
        plants and harvest from one of their corals, a few times each."""
        level = int(w['status'].get('level', 1))
        rnd = random.Random('%s %s' % (w.get('user', {}).get('uid'), time.strftime('%Y-%m-%d')))
        sold = [s['prototypeId'] for s in self.worlddef['ingameShopItems'] if s.get('requiredLevel', 1) <= level
                and (self.reef.protos.get(s['prototypeId'])[1] or {}).get('type') in ('ET_CORAL', 'ET_LIVING_PLANT', 'ET_ORN_PLANT')]
        living = sorted({e['prototypeId'] for e in w.get('livingPlants', [])}) or ['plant_table_yellow']
        corals = sorted({e['prototypeId'] for e in w.get('corals', [])}) or ['coral_fan']
        targets = [(rnd.choice(sold or ['coral_fan']), rnd.randint(2, 3)),
                   (rnd.choice(living), rnd.randint(3, 6)), (rnd.choice(corals), rnd.randint(3, 6))]
        weekday = int(time.strftime('%w'))
        self.story.open_homework(w, time.strftime('%Y-%m-%d'), weekday, targets)

    def friend_world(self, uid, friend_uid=None):
        """The reef shown when visiting a friend. "Random friend" (CU::visitRandomPlayer) asks for the tutorial
        world; there are no other players, so every visit shows Nemo's starter reef. World::processUserWorld
        reads "friendStatus" (data::FriendStatus::load: hardMoney, virtualMoney, level, xp, energy,
        xpForNextLevel) and "user" {name, uid} for the visited friend; "status" is still the visitor's own."""
        if self.dummy_friend is None:
            f = new_world({'uid': FRIEND_UID, 'name': 'Tad'})
            self.starter_reef(f)
            # A lived-in reef like the one visited in the Let's Play: corals and plants ready to help with,
            # Dory, a lionfish and a seahorse.
            for pid, x, z in (('coral_sunset_cup', 88, 80), ('coral_fruit_loop', 80, 73), ('plant_table_yellow', 72, 81),
                              ('plant_table_yellow', 84, 76), ('plant_barrel_sponge', 82, 83), ('deco_rock', 70, 83),
                              ('fish_special_dory', 81, 79), ('fish_rare_lion', 83, 81), ('fish_guest_seahorse', 78, 84)):
                self.reef.add_entity(f, pid, x, z)
            f['quests'] = []
            for k in ('nextEntityId', 'nextQuestId', 'nextMessageId', 'messages', 'patterns'):
                f.pop(k, None)
            self.dummy_friend = f
        fw = self.store.world(str(friend_uid)) if friend_uid else None
        if fw:  # a real friend's reef, as it is now (getFriendWorld {friendUid})
            with self.world_lock:
                self.reef.refresh(fw, now_ms())
            body = json.loads(json.dumps(fw))
            friend = self.store.user(str(friend_uid)) or {}
            body['user'] = {'name': friend.get('name', 'Friend'), 'uid': str(friend_uid)}
            body['quests'] = []
            for k in ('nextEntityId', 'nextQuestId', 'nextMessageId', 'messages', 'patterns'):
                body.pop(k, None)
        else:
            body = json.loads(json.dumps(self.dummy_friend))
        body['friendStatus'] = {k: body['status'].get(k, 0) for k in
                                ('hardMoney', 'virtualMoney', 'level', 'xp', 'energy', 'xpForNextLevel')}
        if not fw:
            body['friendStatus'].update(level=7, xp=1300, xpForNextLevel=1300)
        body['resumeGame'] = user_info()['resumeGame']
        with self.world_lock:
            w = self.user_world(uid)
            if w:
                today = time.strftime('%Y-%m-%d')
                if w.get('friendActionsDay') != today:
                    w['friendActionsDay'] = today
                    w['status']['friendCollectCount'] = 0
                w['status']['friendCollectMaxCount'] = FRIEND_ACTIONS
                body['status'] = w['status']
                w['visitedFriend'] = True
                self.store.save_world(uid, w)
        return body

    def friend_collect(self, w, d):
        """friendsEntityCollectReward {id, friendUid}: a tap on a friend's ready item. The visitor gets the item's
        friendReward; the item's Harvestable::processResponseCollect reads its next "producingInterval"."""
        now = now_ms()
        fw = self.store.world(str(d.get('friendUid'))) if d.get('friendUid') else None
        lst, e = self.reef.find(fw or self.dummy_friend or {}, d.get('id'))
        _, p = self.reef.protos.get(e['prototypeId']) if e else (None, None)
        p = p or {}
        st = w['status']
        if st.get('friendCollectCount', 0) < st.get('friendCollectMaxCount', FRIEND_ACTIONS):
            st['friendCollectCount'] = st.get('friendCollectCount', 0) + 1
            self.reef.earn(w, p.get('friendReward'))
        return {'producingInterval': actions.interval(now, self.reef.ms(p, 'producingTime'))}

    def finish_visit(self, w):
        """Back home after a visit: finishes a "Visit a Friend's Reef" task the client did not report itself."""
        if not w.pop('visitedFriend', False):
            return
        for q in list(w.get('quests', [])):
            proto = self.story.quests.get(q['prototypeId'], {})
            for i, task in enumerate(proto.get('tasks', []), 1):
                if task['cid'] == 'CID_FRIEND_VISIT':
                    self.story.task_state(w, {'id': q['id'], 'tid': i, 'finished': True},
                                          lambda qq, r: self.quest_done(w, qq, r))

    def starter_reef(self, w):
        now = now_ms()
        for e in self.starter:
            self.reef.add_entity(w, e['ipro'][:-5], e['x'], e['z'], e['mirror'], now)
        for fish, x, z in (('fish_special_nemo', 77, 79), ('fish_special_marlin', 79, 78)):
            self.reef.add_entity(w, fish, x, z)

    def quest_done(self, w, q, reward):
        """A quest's last task finished: reward, fish that move in, newly opened areas."""
        self.reef.earn(w, reward)
        if self.story.is_squirt(q['id']):  # won: Squirt rests before the next game
            w['status']['lastSquirt'] = now_ms()
        fish_list = list(q.get('grantFish', []))
        if q.get('challengeFish'):  # a weekend challenge: the next of the 2014 challenge fish not in the reef yet
            pools = self.story.data.get('challengeFish', {})
            owned = {e.get('prototypeId') for e in w.get('fishes', [])}
            order = pools.get(q['challengeFish'], []) + [f for k, v in sorted(pools.items()) for f in v]
            fish_list += [f for f in order if f not in owned][:1]
        for fish in fish_list:
            if not any(e.get('prototypeId') == fish for e in w.get('fishes', [])):
                e = self.reef.add_entity(w, fish, 82, 84)
                if e:  # message type 1: EntityManager::processFishAddedMessage swims the fish in now
                    w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': 1, 'data': dict(e)})
        for sector in q.get('unlockSectors', []):
            self.unlock_sector(w, sector)

    def unlock_sector(self, w, sector, push=True):
        """Opens a reef area. Message type 9 {"sector": name} makes IsoMap::processSectorAdded unlock it with the
        "Your reef has expanded!" scene (ScriptMapUnlocked); at startgame the "sectors" list is enough."""
        if sector in w['sectors']:
            return
        w['sectors'].append(sector)
        if push:
            w.setdefault('messages', []).append({'id': self.story.message_id(w), 'type': SECTOR_ADDED,
                                                 'data': {'sector': sector}})

    def expand(self, w, push=True):
        """Reef expansions. The original opened them as friends joined; with no other players they come with
        levels instead, the first at level 4 as in the Let's Play."""
        level = int(w['status'].get('level', 1))
        for need, sector in EXPANSIONS:
            if level >= need:
                self.unlock_sector(w, sector, push)


class Handler(BaseHTTPRequestHandler):
    game = None
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def params(self, body):
        url = urllib.parse.urlsplit(self.path)
        p = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query, keep_blank_values=True).items()}
        ctype = self.headers.get('Content-Type', '')
        if ctype.startswith('multipart/'):
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b'Content-Type: ' + ctype.encode() + b'\r\n\r\n' + body)
            for part in msg.iter_parts():
                name = part.get_param('name', header='content-disposition')
                if name:
                    p[name] = part.get_payload(decode=True).decode('utf-8', 'replace')
        elif body:
            text = body.decode('utf-8', 'replace')
            if text.lstrip().startswith('{'):
                p.setdefault('json', text)
            else:
                p.update({k: v[-1] for k, v in urllib.parse.parse_qs(text, keep_blank_values=True).items()})
        return url.path, p

    def handle_any(self):
        # Docker probes must not create the fallback account or append request logs.
        if self.command == 'GET' and urllib.parse.urlsplit(self.path).path == '/healthz':
            with self.game.store.lock:
                self.game.store.db.execute('SELECT 1').fetchone()
            out = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        path, p = self.params(body)
        p['_asset_base'] = 'http://' + self.headers.get('Host', '127.0.0.1:8080')
        segs = [s for s in path.split('/') if s]
        p['_exchange_client'] = len(segs) > 1 and segs[:2] == ['nemo', 'e1']
        if len(segs) == 4 and segs[:2] == ['nemo', 'revival-ui']:
            payload = self.game.economy_assets.get(segs[3])
            if payload is None or hashlib.sha1(payload).hexdigest() != segs[2]:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            print('served revival UI:', segs[3])
            return
        endpoint = (segs[-1] if segs else '').lower()
        if 'resources' in [s.lower() for s in segs]:
            endpoint = 'resources'
        try:
            tap = self.game.tap(endpoint, p)
            if tap is not None:
                out, ctype = json.dumps(tap).encode('utf-8'), 'application/json'
            else:
                out, ctype = self.game.game(endpoint, p)
            status = 200
        except Exception:
            traceback.print_exc()
            out, ctype, status = b'{"resultCode":500}', 'application/json', 500
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        shown = '<%d bytes xor>' % len(out) if endpoint == 'getdaworld' else out.decode('utf-8', 'replace')[:4000]
        self.game.log({'t': time.strftime('%H:%M:%S'), 'method': self.command, 'path': self.path,
                       'params': p, 'status': status, 'response': shown})
        print('%s %s %s -> %d (%d bytes)' % (time.strftime('%H:%M:%S'), self.command, path, status, len(out)))

    do_GET = do_POST = handle_any


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apk', required=True, help='original nemos-reef 1.8.1 APK (prototypes, bundle.json)')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--data', default='data', help='directory for the database and request log')
    ap.add_argument('--worlddef', help='use this worldDef JSON instead of generating one')
    ap.add_argument('--reset-worlds', action='store_true',
                    help='delete every saved reef (accounts are kept); players get the starter reef again')
    ap.add_argument('--reset-all', action='store_true',
                    help='delete every account and reef; every device starts as a new player')
    ap.add_argument('--no-story', action='store_true', help='turn off the story quests (story.json)')
    ap.add_argument('--economy', choices=('classic', 'revival'), default=economy.mode(),
                    help='economy profile (default: NEMO_ECONOMY or revival)')
    ap.add_argument('--event', action='append', default=[], metavar='QUEST_ID',
                    help='run this time event now, e.g. time_event_bruce (testing; may be repeated)')
    args = ap.parse_args()
    for qid in args.event:
        quests.FORCED_EVENTS[qid] = now_ms() - 60000
    Handler.game = Game(args)
    print('Nemo\'s Reef server on http://%s:%d  (world version %s, log: %s)'
          % (args.host, args.port, Handler.game.world_version, Handler.game.log_path))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()


