"""Generate the exchange UI from the host's APK; no game assets are shipped."""
import copy
import hashlib
import struct

TEXTS = {
    'STR_EXCHANGE_CONFIRM': 'Spend sand dollars on this algae? Daily limit: three packs.',
    'STR_TRANSACTION_FAIL': "Exchange unavailable. Check your sand dollars and today's three-pack limit.",
    'STR_TRANSACTION_SUCCESS': 'Algae added to your reef!',
    'STR_EXCHANGE_PEARLS_HT': 'Sand Dollars to Algae',
    'STR_NOT_ENOUGH_HT_CHEST_WARNING': 'Algae exchange',
    'STR_NOT_ENOUGH_HT_CHEST': 'Exchange sand dollars for algae. Up to three packs each day.',
    'STR_NOT_ENOUGH_HT_CHEST_2':
        'Trade ${pearlsValue} sand dollars for ${rarespinesValue} algae?',
}


def field(value):
    encoded = value.encode('utf-8')
    return struct.pack('>H', len(encoded)) + encoded


def replace(data, old, new):
    source = field(old)
    if source not in data:
        raise ValueError('Exchange screen is missing ' + old)
    return data.replace(source, field(new))


def build(apk, revival=True):
    result = {}
    for name in ('currencyscreen_content_exchange.bin', 'dialog_question_exchange.bin'):
        path = 'assets/' + name
        if path not in apk.namelist():
            continue  # Synthetic runtime fixtures do not contain original UI assets.
        data = apk.read(path)
        if not revival:
            result[name] = data
            continue  # Serve original files to restore a previously cached trial UI.
        if name == 'currencyscreen_content_exchange.bin':
            data = replace(data, 'shop_pearl_2', 'shop_coin_3')
            # The final spine icon belongs to Game_UI; earlier icons inherit
            # collectibles.xic. Use sprite names from the appropriate atlas.
            marker = field('urchin_spine_extra_icon')
            chunks = data.split(marker)
            if len(chunks) < 3:
                raise ValueError('Unexpected exchange screen sprite layout')
            data = field('shop_algae_3').join(chunks[:-1]) + field('UI_currency_blob') + chunks[-1]
        else:
            data = replace(data, 'urchin_spine_extra_icon', 'UI_currency_blob')
        data = replace(data, 'UI_currency_pearl', 'UI_currency_coin')
        result[name] = data
    return result


def bundle(bundles, assets, base):
    result = copy.deepcopy(bundles)
    for bundle in result.get('bundles', []):
        for resource in bundle.get('resources', []):
            name = resource.get('name')
            if name in assets:
                payload = assets[name]
                digest = hashlib.sha1(payload).hexdigest()
                resource.update(remoteVersion=digest, size=len(payload),
                                url=f'{base}/nemo/revival-ui/{digest}/{name}')
    return result
