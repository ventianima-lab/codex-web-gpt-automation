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


@pytest.mark.parametrize('has_preferences', [False, True])
def test_no_submit_receipt_collision_still_cleans_profile(tmp_path: Path, has_preferences: bool) -> None:
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    package = tmp_path / 'package'
    browser = package / 'dist/src/browser'
    (browser / 'actions').mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({
        'name': '@steipete/oracle', 'version': '0.20.0', 'type': 'module',
    }), encoding='utf-8')
    seed = tmp_path / 'seed'
    seed.mkdir()
    (seed / 'Cookies').write_bytes(b'private fixture')
    if has_preferences:
        (seed / 'Default').mkdir()
        (seed / 'Default/Preferences').write_text('{}', encoding='utf-8')
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('preserve original', encoding='utf-8')
    environment = {**os.environ, 'TEMP': str(temporary), 'TMP': str(temporary), 'TMPDIR': str(temporary)}
    result = subprocess.run([node, str(ROOT / 'scripts/verify_oracle_browser_startup.mjs'),
                             str(package), str(seed), str(receipt)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'EEXIST' in result.stderr
    assert receipt.read_text(encoding='utf-8') == 'preserve original'
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize('failure', ['none', 'ready', 'personalization', 'deadline'])
@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_single_startup_tab_cleanup_precedes_failing_checks(failure: str, platform: str) -> None:
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    module = (ROOT / 'bin/oracle_temporary_personalization_preflight.mjs').as_uri()
    script = r"""
import assert from 'node:assert/strict';
const {startPersonalizedBrowser} = await import(MODULE);
const url='https://chatgpt.com/?temporary-chat=true';
let pages=[{id:'owned',type:'page',url},{id:'startup-blank',type:'page',url:'about:blank'}];
let kills=0, closes=0, opts, promptChecks=0, personalized=false, hidden=0;
const client={Page:{enable:async()=>{}},Runtime:{enable:async()=>{},evaluate:async()=>({result:{value:url}})},
 close:async()=>{closes++},Emulation:{setFocusEmulationEnabled:async()=>{}}};
class Launcher {
 constructor(options){opts=options;this.port=12345;this.pid=321;}
 async launch(){}
 kill(){kills++;}
}
const deps={Launcher,pause:async()=>{},
 jsonAt:async(port,resource)=>resource==='list'?pages:{webSocketDebuggerUrl:'ws://127.0.0.1:12345/devtools/browser/exact'},
 lifecycle:{buildChromeFlagsForTest:()=>[],resolveChromeLaunchOptionsForTest:flags=>({chromeFlags:flags,ignoreDefaultFlags:true}),
 positionChromeWindowOffscreen:async()=>{hidden++;},
 connectToRemoteChromeTarget:async(host,port,log,options)=>{assert.equal(options.targetId,'owned');return {client,targetId:'owned'};},
 closeBlankChromeTabs:async(port,log,host,options)=>{
  assert.equal(options.preserveOneBlank,false);assert.deepEqual(options.excludeTargetIds,['owned']);pages=pages.filter(t=>t.id!=='startup-blank');
 }},
 ensurePromptReady:async()=>{assert.equal(pages.length,1);promptChecks++;if(FAILURE==='deadline')await new Promise(()=>{});if(FAILURE==='ready')throw Error('ready failed');},
 ensureChatMode:async()=>{},
 ensureTemporaryChatPersonalization:async()=>{assert.equal(pages.length,1);if(FAILURE==='personalization')throw Error('personalization failed');personalized=true;}
};
try {
 const session=await startPersonalizedBrowser({port:12345,url,profilePath:'owned-copy',startupTimeoutMs:100,platform:PLATFORM},deps);
 assert.equal(FAILURE,'none');assert.equal(session.evidence.target_id,'owned');
 assert.equal(session.evidence.page_count,1);assert.equal(session.evidence.startup_blank_tabs,0);
 assert.equal(personalized,true);assert.equal(kills,0);
} catch(error){assert.notEqual(FAILURE,'none',error.stack);assert.match(error.message,/failed|deadline/);assert.equal(kills,1);assert.equal(closes,1);}
assert.equal(opts.startingUrl,url);assert.ok(opts.chromeFlags.includes('--hide-crash-restore-bubble'));
assert.equal(pages.length,1);assert.ok(promptChecks>0);
assert.equal(hidden,PLATFORM==='darwin'?1:0);
""".replace('MODULE', json.dumps(module)).replace('FAILURE', json.dumps(failure)).replace('PLATFORM', json.dumps(platform))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mismatch', ['none', 'browser', 'target', 'url', 'extra-page'])
def test_browser_close_checks_exact_identity_and_tabs(mismatch: str) -> None:
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    module = (ROOT / 'bin/oracle_temporary_personalization_preflight.mjs').as_uri()
    script = r"""
import assert from 'node:assert/strict';
const {closePersonalizedBrowser} = await import(MODULE);
let sent=0;
const ws='ws://127.0.0.1:12345/devtools/browser/exact',url='https://chatgpt.com/?temporary-chat=true';
const page={targetId:MISMATCH==='target'?'foreign':'owned',type:'page',url:MISMATCH==='url'?'https://example.test':url};
globalThis.fetch=async resource=>({ok:true,json:async()=>String(resource).endsWith('/version')?
 {webSocketDebuggerUrl:MISMATCH==='browser'?'ws://foreign':ws}:
 (MISMATCH==='extra-page'?[page,{id:'extra',type:'page',url:'about:blank'}]:[page])});
globalThis.WebSocket=class extends EventTarget {
 constructor(){super();queueMicrotask(()=>this.dispatchEvent(new Event('open')));}
 send(value){const request=JSON.parse(value);let result={};if(request.id===1){assert.equal(request.method,'Target.getTargets');result={targetInfos:MISMATCH==='extra-page'?[page,{targetId:'extra',type:'page',url:'about:blank'}]:[page]};}else{assert.equal(request.method,'Browser.close');sent++;}queueMicrotask(()=>this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({id:request.id,result})})));}
 close(){}
};
if(MISMATCH==='none'){assert.equal((await closePersonalizedBrowser(12345,ws,'owned',url)).closed,true);assert.equal(sent,1);}
else {await assert.rejects(closePersonalizedBrowser(12345,ws,'owned',url),/refusing/);assert.equal(sent,0);}
""".replace('MODULE', json.dumps(module)).replace('MISMATCH', json.dumps(mismatch))
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


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
