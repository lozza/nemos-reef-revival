"""Conservative recovery of collection progress from successful request history."""
import json

COLLECTION_CURRENCIES = {'CID_COLLECT_COUNT_ANY': 'virtualMoney',
                         'CID_HARVESTED_COUNT_ANY': 'energy'}


def collection_spec(task):
    """Story sources store [categories, amount]; native CountAny expects the reverse."""
    params = task.get('params', [])
    currency = COLLECTION_CURRENCIES.get(task.get('cid'))
    if not currency or len(params) != 2:
        return None
    try:
        amount = int(params[1])
    except (ValueError, TypeError):
        return None
    return (set(str(params[0]).split(',')), amount, currency) if amount > 0 else None


def native_parameters(task):
    params = list(task.get('params', []))
    if collection_spec(task):
        return [params[1], params[0]]
    return params


def _mentions(value, prototype, ident):
    if isinstance(value, dict):
        return ((value.get('prototypeId') == prototype and value.get('id') == ident)
                or any(_mentions(v, prototype, ident) for v in value.values()))
    return isinstance(value, list) and any(_mentions(v, prototype, ident) for v in value)


def confirmed_harvests(path, uid, world, protos, targets):
    """Return lower bounds, never infer earned currency from the current balance.

    Only count positive balance changes in successful, category-matching harvest
    batches after this exact quest instance first appeared. Skip purchases, mixed
    actions, level-ups, unknown/sold plants and incomplete log lines. This may
    undercount old play, but cannot turn gifts, exchanges or spending into harvests.
    Targets: (quest id, task id) -> (prototype id, categories, currency).
    """
    totals = {key: 0 for key in targets}
    if not targets:
        return totals
    entities = {e['id']: e for name in ('corals', 'livingPlants', 'patches', 'rarePlants', 'fishes')
                for e in world.get(name, [])}
    opened, previous = set(), None
    try:
        log = open(path, encoding='utf-8', errors='replace')
    except OSError:
        return totals
    with log:
        for line in log:
            try:
                row = json.loads(line)
                params = row.get('params', {})
                request = json.loads(params.get('json', '{}'))
                response = json.loads(row.get('response', ''))
                if str(request.get('uid') or params.get('uid', '')) != str(uid):
                    continue
                if row.get('status', 200) != 200 or response.get('resultCode', 200) != 200:
                    continue
                status = response.get('status')
                actions = request.get('actions', [])
                harvests = [a for a in actions if a.get('action') == 'entityCollectReward']
                replies = {a.get('aid'): a for a in response.get('reactions', [])}
                pure = harvests and all(a.get('action') in ('entityCollectReward', 'emptyAction')
                                        for a in actions)
                for key, (prototype, categories, currency) in targets.items():
                    if key in opened and pure and previous and status and previous.get('level') == status.get('level'):
                        eligible = True
                        for action in harvests:
                            entity = entities.get(action.get('data', {}).get('id'), {})
                            pid = entity.get('plantPrototypeId') or entity.get('prototypeId')
                            _, p = protos.get(pid)
                            if (replies.get(action.get('aid'), {}).get('resultCode') != 200
                                    or not p or p.get('shopCategory') not in categories):
                                eligible = False
                                break
                        if eligible and currency in status and currency in previous:
                            totals[key] += max(0, int(status[currency]) - int(previous[currency]))
                    if _mentions(response, prototype, key[0]):
                        opened.add(key)
                if isinstance(status, dict):
                    previous = status
            except (ValueError, TypeError, AttributeError, KeyError):
                # Logs can end in an interrupted/truncated write.
                continue
    return totals
