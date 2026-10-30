"""Builds a world definition ("worldDef") for the client from the prototypes bundled in the APK.

The original server sent the whole game economy (prices, timers, levels, quests, ...) in this
document. Only the prototype list (ids, types, graphics) can be recovered from the APK; every
number below is a placeholder chosen to make the game playable until better data turns up.

The client throws on missing keys (sdr::JSONObject::safeGet), so every prototype carries every
field any getter may read, with neutral defaults.
"""
import hashlib
import json
import zipfile

from ipro import read_attributes


# .ipro "type" -> worldDef list the client loads it from (proto::PrototypeAttrib::loadPrototypePrimaryData)
LIST_FOR_TYPE = {
    'ET_CORAL': 'coralPrototypeList',
    'ET_LIVING_PLANT': 'livingPlantPrototypeList',
    'ET_ORN_PLANT': 'ornamentalPlantPrototypeList',
    'ET_RARE_PLANT': 'rarePlantPrototypeList',
    'ET_NORMAL_DECORATION': 'decorationPrototypeList',
    'ET_SPEC_HOUSE': 'housePrototypeList',
    'ET_SEED': 'seedPrototypeList',
    'ET_PATCH': 'patchList',
    # level scenery (mushrooms, reef pieces): built like normal decorations, sent in the starter reef, not sold
    'ET_SCENE_DECORATION': 'decorationPrototypeList',
}

# .ipro "type" -> shop tab (ShopScreen::initShopCategories: CORAL, LIVINGPLANT, ORNAMENTALPLANT, RAREPLANT,
# SPECIAL). Decorations are sold under SPECIAL, as in the original shop.
SHOP_CATEGORY = {
    'ET_CORAL': 'CORAL',
    'ET_LIVING_PLANT': 'LIVINGPLANT',
    'ET_ORN_PLANT': 'ORNAMENTALPLANT',
    'ET_NORMAL_DECORATION': 'SPECIAL',
    'ET_RARE_PLANT': 'RAREPLANT',
    'ET_SEED': 'RAREPLANT',  # Mystery Seeds are sold in the Rare tab
    'ET_SPEC_HOUSE': 'SPECIAL',
    'ET_PATCH': 'SPECIAL',  # the empty Patch
}

PRODUCES = ('ET_CORAL', 'ET_LIVING_PLANT', 'ET_PATCH', 'ET_CHARACTER')
GROWS = ('ET_CORAL', 'ET_LIVING_PLANT', 'ET_ORN_PLANT', 'ET_RARE_PLANT', 'ET_SEED')

# Tutorial/server-only prototype ids referenced by the client code.
EXTRA_PATCHES = ['patch_tutorial_01', 'patch_tutorial_02']


def cost(coins=0, pearls=0, algae=0, low_tool=0, high_tool=0):
    # proto::Cost::Cost reads exactly these five keys
    return {'energy': algae, 'virtualMoney': coins, 'hardMoney': pearls,
            'lowTool': low_tool, 'highTool': high_tool}


def reward(xp=0, coins=0, pearls=0, algae=0, low_tool=0, high_tool=0):
    r = cost(coins, pearls, algae, low_tool, high_tool)
    r['xp'] = xp
    return r


def duration(seconds):
    # proto::ServerPrototype::getTime: {"duration": int64, "timeWarp": int}
    return {'duration': seconds * 1000, 'timeWarp': 0}


def prototype(pid, graphical_id, name, etype, specific_type, order, level=1, price=None):
    price = price or cost(coins=100)
    return {
        'id': pid,
        'graphicalId': graphical_id,
        'name': name,
        'nameId': name,
        'descriptionId': '',
        'type': etype,
        'specificType': specific_type or '',
        'requiredLevel': level,
        'visibleAtLevel': level,
        'visibleMaxLevel': 999,
        'newUsersOnly': False,
        'guiOrder': order,
        'shopCategory': '',
        # ShopManager::isPrototypeVisibeInShop hides an item once you own maxInstancesCount of it
        'maxInstancesCount': 999,
        'maxPlants': 0,
        'cost': price,
        'constructionTime': duration(30),
        'speedupCost': cost(pearls=1),
        'growingTime': duration(60),
        'growingCycles': 3,
        'growingCycleCost': cost(coins=10),
        'producingTime': duration(300),
        'producingCost': cost(algae=1),
        'producingSpeedupCost': cost(pearls=1),
        'producedReward': reward(xp=5, coins=20),
        'rewardAfterBuilt': reward(xp=10),
        'refundReward': reward(coins=price.get('virtualMoney', 0) // 2),
        # helping on a friend's reef: about +15 XP / +15 sand dollars a tap in the Let's Play
        'friendReward': reward(xp=15, coins=15),
        'ownerReward': reward(xp=15, coins=15),
        'vitality': 0,
        'supportedShops': ['GOOGLE', 'AMAZON', 'ITUNES'],
        'availableTimeInterval': {'start': 0, 'finish': 0},  # finish 0 = no time limit
    }


GET_IT_EARLY_MULTIPLIER = 2
GET_IT_EARLY_ALGAE = 20
DAILY_TAPS = 3


def shop_item(p):
    """ingameShopItems entry: ServerPrototype::getActiveShopData picks one by level (CriteriumLevel),
    then the shop reads price, time window and limits from it."""
    keys = ('cost', 'supportedShops', 'visibleAtLevel', 'visibleMaxLevel', 'availableTimeInterval',
            'newUsersOnly', 'maxInstancesCount', 'shopCategory', 'guiOrder', 'requiredLevel')
    item = {k: p[k] for k in keys}
    item['prototypeId'] = p['id']
    return item


# Treasure chests (data::TreasureRule): the client spawns them itself. Each kind has a weight ("chance") and
# rules by level; a rule's open cost is rolled between minCost and maxCost (Treasure::computeCostFromRule) and
# times are seconds. The fan wiki: pick lock free and ~10 % successful, common spines raise the open
# percentage step by step, rare spines (highTool) always open, 0-12 of them.
CHEST_KINDS = {  # id: (weight, rare spines to open (min, max))
    'chest_coins_low_cheap': (20, (1, 2)), 'chest_coins_high_cheap': (12, (2, 4)), 'chest_coins_high_exp': (5, (6, 10)),
    'chest_algae_low_cheap': (20, (1, 2)), 'chest_algae_high_cheap': (12, (2, 4)), 'chest_algae_high_exp': (5, (6, 10)),
    'chest_pearls_low_cheap': (12, (2, 3)), 'chest_pearls_high_cheap': (6, (3, 6)), 'chest_pearls_high_exp': (3, (8, 12)),
}


def chest_rule(spines, minimum_level=1):
    return {
        'chance': 100, 'minLevel': minimum_level, 'maxLevel': 999,
        'minCost': cost(high_tool=spines[0]), 'maxCost': cost(high_tool=spines[1]),
        'minAppearanceTime': 600, 'maxAppearanceTime': 1800,  # the next chest comes 10-30 minutes later
        'screenTime': 3600,  # and stays an hour
        'picklockAvailableProgress': 0, 'lowToolSuccessChance': 60, 'lowToolAverageStepsCount': 3,
    }


def chests():
    out = [{'id': cid, 'chance': weight, 'costs': [chest_rule(spines)]} for cid, (weight, spines) in CHEST_KINDS.items()]
    # the tutorial chest (Treasure::addTutorialChest) opens with no spines at all
    out.append({'id': 'chest_tutorial', 'chance': 0, 'costs': [chest_rule((0, 0))]})
    return out


# Hide and Seek with Squirt (QuestGameSquirt). The game screen only runs for the minigame id
# "questGame_Squirt" (QuestMinigame::open); the quest's CID_MINIGAME task names it.
SQUIRT_GAME = 'questGame_Squirt'
SQUIRT_INTERVAL = 3 * 3600
# hot-to-cold hint colours around the hidden fish, as colorVariantList ids (ColorPalette)
SQUIRT_ZONE_COLORS = {'squirt_zone_1': (235, 60, 50), 'squirt_zone_2': (245, 150, 40), 'squirt_zone_3': (240, 220, 60),
                      'squirt_zone_4': (120, 200, 90), 'squirt_zone_5': (80, 140, 230)}


def squirt_minigame():
    """data::MinigameSettings::MinigameDefinition. Times are {duration ms, timeWarp}; without "moves" the moves
    are unlimited. Each fish found may drop spines (lowTool / highTool)."""
    d = {
        'id': SQUIRT_GAME, 'mainCharacterId': 'fish_special_squirt',
        'duration': duration(90), 'extraDuration': duration(30),
        'lowToolDropMin': 1, 'lowToolDropMax': 2, 'lowToolDropChance': 40, 'lowToolDropValue': 1,
        'highToolDropMin': 0, 'highToolDropMax': 1, 'highToolDropChance': 10, 'highToolDropValue': 1,
        'zone1Size': 2, 'zone2Size': 4, 'zone3Size': 6, 'zone4Size': 9,
        'extraCost': cost(pearls=1),  # 30 more seconds after time runs out
    }
    for i, name in enumerate(SQUIRT_ZONE_COLORS, 1):
        d['zone%dColor' % i] = name
    return d


def levels(count=60):
    out, xp = [], 0
    for lv in range(1, count + 1):
        out.append({
            'level': lv,
            'requiredXP': xp,
            'reward': reward(coins=100 * lv, pearls=1 if lv % 5 == 0 else 0, algae=5),
            'minChests': 1,
            'maxChests': 3,
            'squirtCost': cost(pearls=1),  # Hide and Seek before Squirt is ready again (buySquirt)
            'vitality': {'vitality': 100, 'categories': []},
        })
        xp += 50 * lv
    return out


def read_prototypes(apk_path):
    """Returns [(file stem, attributes)] for every assets/prototypes/*.ipro in the APK."""
    with zipfile.ZipFile(apk_path) as z:
        names = sorted(n for n in z.namelist() if n.startswith('assets/prototypes/') and n.endswith('.ipro'))
        return [(n.rsplit('/', 1)[1][:-5], read_attributes(z.read(n))) for n in names]


def build(apk_path, story=None):
    wd = {
        'version': '',  # filled in by finish()
        # Read with JSONObject::get (null when missing → crash) by World::getTextBundleId/getResourceBundleId.
        'localizedTextsBundleId': 1,
        'resourcesBundleId': 1,
        'globalSettings': {
            'minimalClientVersion': '1.0.0',
            'timeoutTap': 30,
            'timeoutGameAction': 30,
            'aliveMessageInterval': 10,  # keep-alives also deliver pushed messages (growth/construction ends)
            'aliveMessageIntervalInactivity': 300,
            'inactivityTreshold': 300,
            'localTapAuthorizing': False,
            'DMOAnalyticLogLevel': 0,
            'GoogleAnalyticLogLevel': 0,
            'facebookAndroidURL': '', 'facebookAmazonURL': '', 'facebookWallPostURL': '',
            'gamePictureURL': '', 'URLiTunes': '', 'URLAndroid': '', 'URLAmazon': '',
            'burslyPregameInterstitalAndroid': False,
            # World::WorldSettings::load also reads these (0 when missing):
            'getItEarlyCostMultiplier': GET_IT_EARLY_MULTIPLIER,  # GET IT EARLY = price x this
            'getItEarlyBonusAlgaeAmount': GET_IT_EARLY_ALGAE,  # "... plus N FREE algae"
            'dailyTapRewardsCount': DAILY_TAPS,  # daily reward: hidden plants the player may tap per day
            'dailyTapRewardsGetEverythingCostPearls': 5,
            # DailyRewardManager uses TWO delays in minutes: before/after all picks are spent.
            # Withdraw is not a repeat count. Its old value of 1 caused a one-minute nag after
            # claiming, because the client's login-ready flag stays true until its next resume.
            # DisplayCount is the actual presentation cap (per client process, not per day).
            # Keep unused picks available on reconnect, without repeatedly interrupting play.
            'dailyRewardDisplayDelay': 240,
            'dailyRewardDisplayDelayWithdraw': 240,
            'dailyRewardDisplayCount': 1,
            'squirtIntervalTime': SQUIRT_INTERVAL,  # seconds between two games of Hide and Seek
            # GameScreen::popUpBuyRareSeed ("It has been a while since we have attracted our last Rare
            # Fish..."): at most once a level, from lowVitality_minLevel, once the level is more than
            # xpPercentage % done, while the reef's Vitality % is under _overall or its Rare Plants % under
            # _rarePlants. Missing, they are 0 and it shows from level 1.
            'lowVitality_minLevel': 12,
            'lowVitality_xpPercentage': 50,
            'lowVitality_overall': 40,
            'lowVitality_rarePlants': 20,
        },
        # World::loadWorldDefinition reads test/variant (strings), control (int), active (bool)
        'abTestSettings': {'test': '', 'variant': '', 'control': 0, 'active': False},
        'ambientFishPrototypeList': [],
        'fishPrototypeList': [],
        'ingameShopItems': [],
        'levelList': levels(),
        # Not reconstructed yet; empty lists keep the parsers happy.
        'sectorPrototypeList': [], 'dailyRewardPrototypeList': [], 'chestsList': chests(),
        'squirtMinigameList': [squirt_minigame()], 'newPrototypeList': [], 'discounts': [],
        'vitalityPatchList': [], 'conditionDefinitionList': [], 'questPrototypeList': [],
        'dialogPrototypeList': [], 'colorVariantList': [], 'patternList': [],
        'inAppPricePoints': [], 'hintPrototypeList': [], 'exchangePackagePrototypes': [],
        'cooperativeQuests': [], 'pushNotificationPrototypeList': [], 'whatsNewList': [],
    }
    for lst in LIST_FOR_TYPE.values():
        wd[lst] = []

    order = 0
    for stem, a in read_prototypes(apk_path):
        etype, st = a.get('type'), a.get('specificType', '')
        if etype == 'ET_CHARACTER':
            lst = 'ambientFishPrototypeList' if st == 'EST_FISH_AMBIENT' else 'fishPrototypeList'
        else:
            lst = LIST_FOR_TYPE.get(etype)
        if not lst:
            continue
        order += 1
        p = prototype(stem, stem, a.get('name') or stem, etype, st, order)
        p['shopCategory'] = SHOP_CATEGORY.get(etype, '')
        # ShopItem::setTimeLabels shows the growth / production rows whenever those times are non-zero,
        # so only items that really grow or produce keep them (keys stay: the getters use safeGet).
        if etype not in PRODUCES:
            p['producingTime'] = duration(0)
            p['producedReward'] = reward()
        if etype not in GROWS:
            p['growingTime'] = duration(0)
        if etype != 'ET_SPEC_HOUSE':
            # only houses have construction graphics (constr_0); a decoration under construction has no
            # drawable group to show and crashes the client (Entity::calculateDrawablesHeight)
            p['constructionTime'] = duration(0)
        if etype == 'ET_PATCH':
            p['maxPlants'] = 4  # EntityPatch::getPlantMax: four plants of a kind make the upgraded plant
        wd[lst].append(p)
        if etype != 'ET_SCENE_DECORATION':
            wd['ingameShopItems'].append(shop_item(p))

    patch = next((p for p in wd['patchList'] if p['graphicalId'] == 'spec_house_patch'), None)
    if patch:
        for pid in EXTRA_PATCHES:
            wd['patchList'].append(dict(patch, id=pid))
    if story:
        story.extend_worlddef(wd)
    return finish(wd)


def finish(wd):
    """Stamps a numeric version derived from the content, so clients refresh their cached copy
    whenever the definition changes. CU::canClientWorkWithWorldVersion parses major.minor.patch."""
    wd['version'] = ''
    digest = hashlib.sha1(json.dumps(wd, sort_keys=True).encode()).hexdigest()
    wd['version'] = '1.8.1.%d' % (int(digest[:7], 16))
    return wd


if __name__ == '__main__':
    import sys
    json.dump(build(sys.argv[1]), sys.stdout, indent=1)
