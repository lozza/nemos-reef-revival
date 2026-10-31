"""Record the audited gameplay inputs for each desktop build, without game assets."""
import hashlib
import json
from pathlib import Path

BACKEND_COMMIT = '9f7c247d49699e268058bc81ea99158f0246cdbc'
SERVER_FILES = ('actions.py','economy.py','economy_ui.py','ipro.py','iscn.py',
                'nemo_server.py','quests.py','quest_history.py','story.json','texts.py','worlddef.py')
DESKTOP_FILES = ('camera_zoom.py','desktop_input.py','run_client.py','gameplay_compatibility.py',
                 'revival_exchange.py','gameplay_contract.py')


def write_build_manifest(bundle, root):
    root, bundle = Path(root), Path(bundle)
    paths = [root/'server'/name for name in SERVER_FILES]
    paths += [root/'desktop'/name for name in DESKTOP_FILES]
    result = {'backend_baseline_commit':BACKEND_COMMIT,
              'desktop_safety_fixes':['NUL-terminated worldDef','account status envelope','exhausted daily tap guard'],
              'features':['quest progress recovery','rare growth','Revival economy','daily reward safety',
                          'algae exchange','visible ambient fish','mouse-wheel zoom','empty Patch shop labels'],
              'source_sha256':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    (bundle/'gameplay-build-info.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
