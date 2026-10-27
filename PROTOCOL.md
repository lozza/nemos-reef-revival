# Nemo's Reef (Android 1.8.1, `com.disney.nemo`) — server protocol notes

Reverse-engineered from `lib/armeabi/libsdr.so` (Living Mobile "sdr" C++ engine, symbols intact).
All networking is native (bundled libcurl); the Java layer only hosts ads, billing and push.
Function names below are the client's own C++ symbols.

## Servers

Both plain HTTP; no TLS, no certificate pinning, responses are not signed.

| Purpose | Base URL in `libsdr.so` |
|---|---|
| Tapulous account API (`netcl::ta::Tap*`) | `http://nemo.apps.tapulous.com/nemo/api/` |
| Game API (`netcl::Action*`, game actions) | `http://nemo.pragueweb.tapulous.com/nemo/` |
| Push notifications (unused) | `http://apps.tapulous.com/misc/notifications/` |

The game client concatenates `"/api/..."` onto the game base, so paths arrive as `/nemo//api/...`.
`patcher/patch_apk.py` rewrites both base URLs in place (new URL ≤ original length).

## Boot sequence (`gi::GameInitiator::startBaseScheme`)

New install (no saved login):

1. `TapAuthorizeUser` → game `/api/authenticate`
2. `TapRecommendName` → account `recommend_name` (name dialog)
3. `TapRegisterUser` → account `register_user`
4. `TapAuthorizeUser` again
5. `ActionGetCurrentWorldVersion` → `/api/getCurrentWorldVersion`
6. resource cache sync → `/api/resources/` (only if needed)
7. `ActionGetDaWorld` ("getCurrentWorld") → `/api/getDaWorld` (skipped if the cached worldDef has the same version)
8. `ActionStartGame` ("startGame") → `/api/startgame`
9. `GA_Empty` game action → `/api`
10. `ActionGetLocalizedTextsBundle` → `/api/getLocalizedTextsBundle`

Existing installs skip steps 2–4. The friend-visit scheme adds `getTutorialWorld` / `getFriendWorld`.

## Tapulous account API (`netcl::ta::TapAction`)

POST form fields on every call: `format=json`, `api_key`, `timestamp` (unix s), `token_info` (device UDID),
`api-sig`, plus method fields.

- `api_key` candidate: `OI6TWPNH-IEJT-06C2-VG65-HP6HDJH8QHGZ`; API secret candidate `1VDE5CLX-U1RR-WHLQ-I2IF-HQRHTD609J14`
- `api-sig` = base64(HMAC-SHA1(API secret + "&", "POST&" + urlencode(url) + "&" + urlencode(sorted `k=v` joined by `&`))).
  OAuth-1 style; our server ignores it.

Response envelope (`TapAction::loadingFinished`): success `{"response": {...}, "attachedData": {...}}`;
error `{"status": {"code": int, "reason": str}}`. HTTP status must be 200. Leading junk before the first `{` is skipped.

| Class | method | request fields | `response` |
|---|---|---|---|
| TapAuthorizeUser | game `/api/authenticate` | `lang_code` | `user_id`, `username`; `attachedData.clientSignatureKey` |
| TapRegisterUser | `register_user` | `proposed_name` | `user{user_id, username}` |
| TapRecommendName | `recommend_name` | | `suggestedName` (required) |
| TapDeviceLogin | `login` | `username`, `email` | |
| TapDeviceLogout | `logout` | | |
| TapChangeName | `change_username` | `new_username` | |
| TapGetFriends | `get_friends` | | `friends[{user_id, username, has_app}]` |
| TapAddFriend | `add_friend` | `friend_name` | `friend{user_id, username}` |

`TapAuthorizeUser::isAuthorized` is true when `user_id` is non-empty.

## Game API (`netcl::ComManager::sendRequest`)

POST form fields:

- `json` = request body
- `uid` = user id
- `signature` = `sha1_hex(utf8(secret + json + uid))` (`DigestComposer`: values concatenated in key order).
  `secret` = `clientSignatureKey`, or the literal `bsF3Ey6XNn` before one is known. Our server ignores it.

### Batched game actions → `<base>/api`

```json
{"stamp": 12, "uid": "…", "worldVersion": "…", "serverId": "…", "lastStamp": 11,
 "actions": [{"action": "entityMove", "aid": 3, ...action fields...}]}
```

Response:

```json
{"resultCode": 200, "serverTime": <ms>, "serverId": "…", "stamp": 12,
 "reactions": [{"aid": 3, "resultCode": 200, ...}], "errorData": {...optional...}}
```

`resultCode` 200 (or absent) means success (`BasicGA::processResult`, `ComManager::getGameActionsResultCode`).
On errors the client resyncs entities from `errorData` (`EntityManager::processErrorOnEntity`).

Action names (`GA_*::addToJson`): `emptyAction, resumeGame, entityConstruct, entityMove, entityRefund,
entitySeed, entityFeed, entityCollectReward, friendsEntityCollectReward, entitySpeedupFeed,
entityFreeSpeedupFeed, entityConstructionSpeedup, entityConstructionFreeSpeedup, entitySpeedupProduction,
entityFreeSpeedupProduction, seedEntitySeed, getFriendFish, getFriendVitality, dailyRewardCollect,
dailyTapReward, dailyTapRewardExtraTap, dailyTapRewardGetEverything, placeEntityInPatch,
placeEntitiesToInventory, getUserWorld, confirmMessages, placeItemForCleaning, cleanItemForCleaning,
sectorBuy, getCurrentWorld, ...` (≈49 classes; field layouts still to document). `resumeGame`'s reaction
carries `data` (user info, below).

### Single-purpose endpoints

| Endpoint | Request JSON | Response |
|---|---|---|
| `/api/getCurrentWorldVersion` | `uid`, `clientVersion` | root `worldVersion` (string) |
| `/api/getDaWorld` | `worldVersion` | worldDef JSON, **XOR-obfuscated** (below) |
| `/api/startgame` | `uid, name, clientVersion, startGameReason, deviceToken, timeZone, language, shop (GOOGLE/AMAZON/ITUNES), age` | user world (below) + root `stamp` (required) |
| `/api/getTutorialWorld` | `uid` | user world |
| `/api/getLocalizedTextsBundle` | `id`, `languageCode`, `uid` | `{"texts": {"STR_KEY": "text", ...}}` (`ActionGetLocalizedTextsBundle::loadingFinished` → `text::GameTextManager::load`) |
| `/api/reset` | `stamp`, `uid` | |
| `/api/resources/` | form `query` = JSON list of bundle names | `{"bundles": [...]}`, same format as `assets/bundle.json` |
| `/api/getFriendWorld`, `getFriendStatus`, `getFriendLastTime`, `getCoopQuestParticipants`, `startCoopQuest` | | TBD |

Every game response may carry `serverTime` (ms), `serverId`, `stamp`, `resultCode`.

### getDaWorld obfuscation

`cypher::XorCypher::xorFuncChar`: `plain[i] = body[i] ^ KEY[i & 0x7f]`, 128-byte key at `0xbf8f14`
(see `XOR_KEY` in `server/nemo_server.py`). The client caches the decoded JSON as `worldDef` (+`.metaData`)
in its documents directory (`World::saveWorldDefinitionToCache`), so a device that played online may still
hold the original economy data.

## worldDef (world definition)

`sdr::JSONObject::safeGet` **throws** on missing keys, and the virtual `get` (vtable +0x28) returns null, which
the callers usually dereference unchecked (e.g. `World::getTextBundleId`), so include every field a getter may read.

Top level (`World::loadWorldDefinition`, `processCurrentWorld`, `getDefinition`,
`PrototypeAttrib::loadPrototypePrimaryData/ShopData`):

`version`, `localizedTextsBundleId` (int), `resourcesBundleId` (int), `globalSettings`, `abTestSettings`, `coralPrototypeList`, `livingPlantPrototypeList`,
`housePrototypeList`, `decorationPrototypeList`, `ornamentalPlantPrototypeList`, `fishPrototypeList`,
`seedPrototypeList`, `rarePlantPrototypeList`, `ambientFishPrototypeList`, `patchList`, `ingameShopItems`,
`levelList`, `sectorPrototypeList`, `dailyRewardPrototypeList`, `chestsList`, `squirtMinigameList`,
`newPrototypeList`, `discounts`, `vitalityPatchList`, `conditionDefinitionList`, `questPrototypeList`,
`dialogPrototypeList`, `colorVariantList`, `patternList`, `inAppPricePoints`, `hintPrototypeList`,
`exchangePackagePrototypes`, `cooperativeQuests`, `pushNotificationPrototypeList`, `whatsNewList`.

`globalSettings` (`World::WorldSettings::load`): `DMOAnalyticLogLevel, GoogleAnalyticLogLevel,
facebookAndroidURL, facebookAmazonURL, facebookWallPostURL, gamePictureURL, localTapAuthorizing, timeoutTap,
timeoutGameAction, aliveMessageInterval, aliveMessageIntervalInactivity, inactivityTreshold,
minimalClientVersion, URLiTunes, URLAndroid, URLAmazon, burslyPregameInterstitalAndroid`.
`minimalClientVersion` must be ≤ `1.8.1` (`CU::canClientWorkWithWorldVersion`, `major.minor.patch`).

Prototype (`proto::ServerPrototype` getters): `id`, `graphicalId` (= `.ipro` file name in
`assets/prototypes/`), `name`, `descriptionId`, `type`, `specificType`, `requiredLevel`, `visibleAtLevel`,
`visibleMaxLevel`, `availableTimeInterval{start,finish}`, `newUsersOnly`, `supportedShops`, `guiOrder`,
`shopCategory`, `maxInstancesCount`, `maxPlants`, `cost`, `constructionTime`, `speedupCost`,
`growingTime`, `growingCycles`, `growingCycleCost`, `producingTime`, `producingCost`, `producingSpeedupCost`,
`producedReward`, `rewardAfterBuilt`, `refundReward`, `friendReward`, `ownerReward`, `vitality`,
`colorVariantId`, `timeEvent`, `upgradePrototypeId`, `conditions[{parameterValueList[{index,value}]}]`.

- Cost (`proto::Cost`): `{energy, virtualMoney, hardMoney, lowTool, highTool}` (ints; algae, coins, pearls, tools)
- Times (`ServerPrototype::getTime`): `{duration: int64, timeWarp: int}`
- `ingameShopItems`: `[{prototypeId, cost, visibleAtLevel, visibleMaxLevel, availableTimeInterval{start,finish},
  supportedShops, newUsersOnly, maxInstancesCount, ...}]`; `getActiveShopData` picks the entry matching the
  player's level. `finish` 0 = no time limit. Owning `maxInstancesCount` of an item hides it from the shop.
- `shopCategory` (shop tab): `CORAL`, `LIVINGPLANT`, `ORNAMENTALPLANT`, `RAREPLANT`, `SPECIAL`
  (other category constants: `CURRENCY`, `AMBIENTFISH`, `GUESTFISH`)

Level (`data::Level::LevelDefinition::load`): `{level, requiredXP, reward{xp, virtualMoney, hardMoney, energy,
lowTool, highTool}, minChests, maxChests, squirtCost{...cost}, vitality{vitality, categories[{categoryId, vitality}]}}`.

Still to map: sectors, daily rewards, chests (`TreasureRule`), squirt minigame, quests, dialogs, discounts,
patterns, color variants, notifications (see `data::*::load`, `quest::QuestManager::load*`).

## User world (`/api/startgame` response)

`World::loadUserWorld` keeps the whole response; parsed by `World::processUserWorld` / `readEntities`:

- `status` (`data::UserStatus::loadUserStatus`): `hardMoney, virtualMoney, level, xp, energy, xpForNextLevel,
  lowTool, highTool, friendCollectCount, friendCollectMaxCount, friendCollectInterval{start,finish},
  lastSquirt, squirtStarted, tapRewardsCount, tapRewardsExtraTapAvail, tapRewardsGetAllAvail, activeDiscount`,
  and `vitality` as an **object** (`data::Vitality::load`): `{vitality, level (optional), categories[{categoryId, ...}]}`
- user info: `TheUser::updateUserInfo(root)` → `loadUserInfo`, which (if present) reads the **object**
  `resumeGame: {moneySpent (bool), tapjoyMoneySpent (bool), currentDailyRewardCountOffer (int),
  lastDailyRewardCountClaimed (int), dailyTapRewardReady (bool)}` at the root; the `resumeGame` reaction's
  `data` has the same shape
- `patterns` (optional, `data::PatternStatus::load`); `status.coopQuest` (optional)
- `user{name, uid}`, `coopQuest` (optional), `friendStatus` (friend visits)
- entity lists: `corals, houses, decorations, livingPlants, ornamentalPlants, fishes, quests, rarePlants,
  patches`; entity (`EntityManager::insertEntityOnTileFromServer`): `id, prototypeId, seedPrototypeId,
  patchPrototypeId, position{x, z}, transform, inventory`
- `sectors`: list of unlocked area names (`mapunlock::MapUnlockerBase::load`). If the key is missing the
  whole map stays locked (drawn white); when present, `base` is unlocked plus every listed area
  (`tiles`, `tiles_2` … `tiles_8`, from `assets/level.iscn`)
- `finishedTutorialQuests`

## Storage patch

`SdrRoot.init` (Java) keeps all data under `/sdcard/Android/data/<pkg>/files`, which this old app cannot create
on Android 11+, so logins (`tapLogin.bin`), the user secret and caches are never saved. `patch_apk.py`
nops the `move-result-object v3` after `ExternalStorage.getAppDataPath()` in `classes.dex` (offset `0x33a9c8`)
so the app's internal `dataDir` is used while the folder-creating code still runs, then fixes the dex
checksum/signature.

## Textures for non-PowerVR GPUs

180 textures (`assets/-device_320|640/*.ddspvr`) exist only as PVRTC 4bpp (DDS header, FourCC `PVR4` RGBA /
`PVR3` RGB). Devices without PVRTC (the client reports `Supported compress:png`; this includes Mali/Adreno and
the emulator) ask for `<name>.png` instead, which the original server delivered through resource bundles; a
missing one makes `GameScreen::updateLoad` stage 2 throw and retry forever. `patch_apk.py` decodes them
(`patcher/pvrtc.py`, pure Python; output matches `texture2ddecoder`) and adds the PNGs next to the originals.

`sdr::Texture::load` first reads `<name>.meta` (`Texture::Meta::load`, big-endian): a u16 count, then
`count` pairs of (u8 key, u8 value): 0/1 = wrap S/T flags, 2/3 = min/mag filter (through a table),
4 = generate-mipmaps flag, 5 = int, 6 = `IRenderer::PixelFormat` passed to `Bitmap(path, format)`, which
converts the decoded PNG. Formats seen: 2 = RGBA8, 7 = RGBA4 (the names `getFormatName` prints in the
texture list); `Bitmap::convertBitmap` handles 1, 2, 7, 8, 9, 10. Most metas are `00 01 06 07` (RGBA4).

Most reef scenery and shop items are sprites (`.sspr`, pixel rectangles) on two atlases that exist only as
PVRTC, `fr_items_dynamic_02` and `_04` (2048x2048 for `-device_640`, 1024x1024 for `-device_320`). On the
emulator these still draw white. The textures are decoded correctly, and other converted textures (e.g.
`bubble2D_01`) work. With `--debug-log` the patcher also hooks `Sprite::load` and `Texture::load` to log
every name loaded (stubs in the zero padding at `0xc0e940`, first LOAD segment grown to cover them).
That log shows neither atlas is ever requested on an empty reef, so the white areas there are something else.

The white areas on the reef are the sand-path tiles of the iso tile map (`tileset_new.itil` ->
`tiles_new.png`; 188 tile graphics of 92x46 px, 246 tiles) and sprites on the item atlases. The game's log on
LDPlayer 9 (GL renderer "Adreno (TM) 750" through the ANDROID_EMU translator, max texture size 16384) lists
every texture with its format: the ones that draw white are the large non-RGBA8 ones (`tiles_new` 1024x1024
RGB8, `fr_items_dynamic_02/_04` 2048x2048 RGBA4), while `items_static` (1024x1024 RGBA8) and the RGBA4 fonts
(up to 512x1024) draw fine. `--emulator` therefore rewrites every `-device_*/*.meta` to ask for RGBA8.

Two-texture fish materials (a white cut-out `_d` on unit 0, the colours in `_lm` multiplied in on unit 1)
drew black on the emulator; `--emulator` bakes them into one texture.

## Music

The APK has `assets/music/mp3index.def` but no `music0..3.ogg`; `SdrMediaPlayer` opens them with `openFd`, so
they must be stored uncompressed. `patch_apk.py --music DIR` adds your own files.

## Locating crashes in the emulator

logcat backtraces only show `libndk_translation.so`. The full tombstone (`adb bugreport`, `FS/data/tombstones/`)
dumps memory near `ecx`/`edx`, which holds the translator's guest state: values inside the `libsdr.so` mapping
(see the maps section for its base) are guest PC/LR; subtract the base to get a file offset.

## Debug logging

`sdr::Debug::log` is compiled to `bx lr` in release builds. `patch_apk.py --debug-log` branches the
`log(String)` / `log(int, String, Severity)` overloads to the working `Debug::print` overloads, so the
engine's own trace shows up in `adb logcat`. It also branches the empty `Debug::exceptionThrown(String)`
(called from every `sdr::BaseException` constructor) to `Debug::print`, so exception messages such as
`Key 'x' not found in dictionary` are logged too.

## Tooling (`tools/`)

- `pic.py` + `strmap.py` → every call site with resolved string arguments (`calls.txt`)
- `emu_init.py`: Unicorn-emulates all static initialisers to name the global `sdr::String` keys
- `fstr.sh <regex>`: string arguments used per function
- `disx.py <addr> <len>`: Thumb disassembly annotated with JSON key names, strings and callees
- `jsontypes.py <addr> <len>`: how a parser uses each key it reads (toInt/toBool/get → object, count → list, ...);
  JSON vtable: +0xc toInt, +0x10 toLong, +0x14 toFloat, +0x18 toBool, +0x1c toString, +0x20 at(i),
  +0x24 count, +0x28 get(key), +0x2c hasKey, +0x30 key(i)
- `DumpDecomp.java`: Ghidra headless decompiler dump (fixes ARM veneers and false noreturn flags)
- `resolve.py` / `clean.py`: annotate PIC string references in Ghidra output and strip noise
- Ghidra loads the library at base `0x10000` (file offset = Ghidra address − 0x10000)
