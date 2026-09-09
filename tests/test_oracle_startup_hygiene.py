from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import chatgpt_oracle_compat as compat


def test_install_includes_all_current_and_migration_patches() -> None:
    import codexpro_lifecycle as lifecycle
    shipped = set(lifecycle.manifest_files(ROOT))
    patch_root = ROOT / 'bin/oracle-compat/0.18.0'
    for contract in compat.PATCHES.values():
        references = [contract.get('patch'), contract.get('legacy_patch'), *contract.get('legacy_patches', {}).values()]
        for reference in references:
            if reference:
                relative = (patch_root / reference).resolve().relative_to(ROOT).as_posix()
                assert relative in shipped, relative


@pytest.mark.parametrize('synchronous_kill', [False, True])
def test_no_submit_receipt_collision_still_cleans_profile(tmp_path: Path, synchronous_kill: bool) -> None:
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    package = tmp_path / 'package'
    browser = package / 'dist/src/browser'
    (browser / 'actions').mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({
        'name': '@steipete/oracle', 'version': '0.18.0', 'type': 'module',
    }), encoding='utf-8')
    (browser / 'chromeLifecycle.js').write_text('export {};', encoding='utf-8')
    (browser / 'actions/thinkingTime.js').write_text('export async function ensureThinkingTime() {}', encoding='utf-8')
    (browser / 'profileCopy.js').write_text(
        "import {writeFile} from 'node:fs/promises'; import path from 'node:path'; "
        "export async function copyChromeProfile(seed, target) { "
        "await writeFile(path.join(target, 'Cookies'), 'private fixture'); throw Error('copy failed'); }",
        encoding='utf-8',
    )
    if synchronous_kill:
        (tmp_path / 'Default').mkdir()
        (tmp_path / 'Default/Preferences').write_text('{}', encoding='utf-8')
        (browser / 'profileCopy.js').write_text(
            "import {mkdir, writeFile} from 'node:fs/promises'; import path from 'node:path'; "
            "export async function copyChromeProfile(seed, target) { "
            "await mkdir(path.join(target, 'Default')); "
            "await writeFile(path.join(target, 'Default', 'Preferences'), '{}'); return 'Default'; }",
            encoding='utf-8',
        )
        (browser / 'chromeLifecycle.js').write_text(
            "import {writeFile} from 'node:fs/promises'; import path from 'node:path'; "
            "export async function prepareCopiedProfileStartup(config, target) { "
            "await writeFile(path.join(target, 'Default', 'Preferences'), JSON.stringify({"
            "profile:{exit_type:'Normal',exited_cleanly:true},session:{restore_on_startup:5,startup_urls:[]}})); } "
            "export async function launchChrome() { return {pid: 123, port: 999, kill() {}}; } "
            "export async function connectWithNewTab() { throw Error('connection failed'); }",
            encoding='utf-8',
        )
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('preserve original', encoding='utf-8')
    environment = {**os.environ, 'TEMP': str(temporary), 'TMP': str(temporary), 'TMPDIR': str(temporary)}
    result = subprocess.run([node, str(ROOT / 'scripts/verify_oracle_browser_startup.mjs'),
                             str(package), str(tmp_path), str(receipt)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'EEXIST' in result.stderr
    assert receipt.read_text(encoding='utf-8') == 'preserve original'
    assert list(temporary.iterdir()) == []


def test_owned_browser_startup_preserves_seed_and_unrelated_tabs(tmp_path: Path) -> None:
    source = Path(os.environ.get('ORACLE_018_PACKAGE_ROOT', '__unset__'))
    if not source.is_dir():
        if os.environ.get('CI'):
            pytest.fail('Exact Oracle 0.18.0 package required')
        pytest.skip('Exact Oracle package unavailable')
    package = tmp_path / 'package'
    shutil.copytree(source, package)
    compat.ensure_oracle_compatibility('oracle 0.18.0', package_root=package, backup_root=tmp_path / 'backup')
    text = (package / 'dist/src/browser/chromeLifecycle.js').read_text(encoding='utf-8')
    replacements = {
        'import CDP from "chrome-remote-interface";': 'const CDP = globalThis.testCDP;',
        'import { launch, Launcher } from "chrome-launcher";': 'const launch=async()=>({port:12345,pid:123}); class Launcher {static defaultFlags(){return [];}}',
        'import { cleanupStaleProfileState } from "./profileState.js";': 'const cleanupStaleProfileState=async()=>{};',
        'import { delay } from "./utils.js";': 'const delay=async()=>{};',
        'import { isWsl, resolveWslChromeLaunchRoute } from "./wslHost.js";': 'const isWsl=()=>false; const resolveWslChromeLaunchRoute=()=>({});',
    }
    for before, after in replacements.items():
        assert before in text
        text = text.replace(before, after)
    module = tmp_path / 'lifecycle.mjs'
    module.write_text(text, encoding='utf-8')
    seed, copied = tmp_path / 'seed', tmp_path / 'copy'
    original = {'profile': {'exit_type': 'Crashed', 'exited_cleanly': False, 'unrelated': 7},
                'session': {'restore_on_startup': 1, 'startup_urls': ['https://example.test']},
                'unrelated': {'keep': True}}
    for directory in (seed, copied):
        (directory / 'Default').mkdir(parents=True)
        (directory / 'Default/Preferences').write_text(json.dumps(original), encoding='utf-8')
        (directory / 'Default/Cookies').write_bytes(b'opaque-cookie-fixture')
    script = """
const closed=[];
let targets=[{id:'startup',type:'page',url:'about:blank'},
 {id:'existing-chat',type:'page',url:'https://chatgpt.com/c/keep'},
 {id:'changed',type:'page',url:'about:blank'}];
globalThis.testCDP=Object.assign(async()=>({close:async()=>{}}),{
 List:async()=>targets,
 New:async()=>{targets.push({id:'active',type:'page',url:'about:blank'});return {id:'active'};},
 Close:async({id})=>{closed.push(id);targets=targets.filter(t=>t.id!==id);}
});
const m=await import(MODULE);
await m.launchChrome({copyProfileSource:SEED},COPY,()=>{});
targets.find(t=>t.id==='changed').url='https://example.test/navigated';
targets.push({id:'later-blank',type:'page',url:'about:blank'});
await m.connectWithNewTab(12345,()=>{},'about:blank');
if(JSON.stringify(closed)!=='["startup"]') throw Error('Wrong owned startup tab cleanup '+JSON.stringify(closed));
await m.connectWithNewTab(9999,()=>{},'about:blank');
if(closed.length!==1) throw Error('Reused browser tabs closed');
let sourceRejected=false;
try {await m.prepareCopiedProfileStartup({copyProfileSource:SEED},SEED);} catch {sourceRejected=true;}
if(!sourceRejected) throw Error('Source profile accepted');
await m.prepareCopiedProfileStartup({},SEED);
console.log('PASS');
""".replace('MODULE', json.dumps(module.as_uri())).replace('SEED', json.dumps(str(seed))).replace('COPY', json.dumps(str(copied)))
    node = shutil.which('node')
    assert node
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads((seed / 'Default/Preferences').read_text(encoding='utf-8')) == original
    changed = json.loads((copied / 'Default/Preferences').read_text(encoding='utf-8'))
    assert changed['profile'] == {'exit_type': 'Normal', 'exited_cleanly': True, 'unrelated': 7}
    assert changed['session'] == {'restore_on_startup': 5, 'startup_urls': []}
    assert changed['unrelated'] == original['unrelated']
    assert (copied / 'Default/Cookies').read_bytes() == b'opaque-cookie-fixture'
