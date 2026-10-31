"""Asset-free checks that packaged desktop backends include the Revival fixes."""
import time


def verify():
    import actions
    import economy
    import quests
    import quest_history
    import worlddef
    from gameplay_compatibility import FISH_VISIBILITY
    import camera_zoom

    story = quests.Story(economy_mode='revival')
    for fid in ('fish_rare_vampire', 'fish_rare_vampire_green',
                'fish_rare_vampire_red', 'fish_rare_vampire_orange'):
        crab = story.data['items'][fid]
        assert crab['fishFeed'] == 100 and crab['every'] == 86400
        assert crab['produce'] == {'hardMoney': 1} and crab['hearts'] == 500
    for qid, amount in (('side_quest_03', 300), ('side_quest_us_06', 50)):
        task = story.quest_prototype(story.quests[qid])['endConditionList'][0]
        assert task['parameterValueList'][0] == {'index':1,'value':str(amount)}
    assert callable(story.recover_collection_tasks)
    assert callable(story.recover_seed_tasks)
    assert 'ET_RARE_PLANT' not in actions.HARVESTABLE
    assert 'ET_PATCH' not in worlddef.PRODUCES
    p = {'id':'coral_check','growingCycles':3,'growingCycleCost':{'energy':30},
         'growingTime':{'duration':1000,'timeWarp':0},'producedReward':{'energy':10,'xp':6},
         'producingTime':{'duration':28800000,'timeWarp':7}}
    economy.balance_prototype(p,{'price':{'virtualMoney':100},'level':10},'revival')
    assert p['producingTime'] == {'duration':14400000,'timeWarp':7}
    reef = actions.Reef(actions.Prototypes({'revivalEconomy':'revival'}))
    w = {'dailyTapDay':time.strftime('%Y-%m-%d'),'status':{'level':10,'tapRewardsCount':2}}
    assert reef.a_dailyTapReward(w,{},0)['reward']['hardMoney'] == 3
    try:
        reef.a_dailyTapReward(w,{},0)
    except actions.ActionRejected:
        pass
    else:
        raise AssertionError('Exhausted daily rewards must not return an empty success')
    assert FISH_VISIBILITY == (0x2a5384,bytes.fromhex('a354'),bytes.fromhex('c046'))
    assert camera_zoom.zoom(None,0) is False
    return {'quest_counters':'passed','historical_collection_recovery':'included',
            'rare_growth':'passed','production_time_metadata':'passed','daily_rewards':'passed',
            'fish_visibility':'included','mouse_wheel_zoom':'included','empty_patch_shop_labels':'passed',
            'crab_pearl_rewards':'passed'}
