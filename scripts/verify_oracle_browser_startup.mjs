// Real, prompt-free browser verification. Only a fresh copied profile is used.
import { cp, lstat, readFile, readdir, writeFile, mkdtemp, rm, realpath } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { closePersonalizedBrowser } from '../bin/oracle_temporary_personalization_preflight.mjs';

const [packageArgument, profileArgument, receiptArgument, effortArgument = 'pro'] = process.argv.slice(2);
if (!packageArgument || !profileArgument || !receiptArgument) {
  throw new Error('Usage: node verify_oracle_browser_startup.mjs <validated-oracle-package> <login-profile> <receipt.json> [startup|pro|extra-high]');
}
if (!['startup', 'pro', 'extra-high'].includes(effortArgument)) throw new Error('Unsupported no-submit effort');
const packageRoot = await realpath(packageArgument);
const seed = await realpath(profileArgument);
const receiptPath = path.resolve(receiptArgument);
const metadata = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));
if (metadata.name !== '@steipete/oracle' || metadata.version !== '0.20.0') throw new Error('Unvalidated Oracle version');
const moduleAt = (relative) => import(pathToFileURL(path.join(packageRoot, relative)).href);
const temporary = await mkdtemp(path.join(os.tmpdir(), 'Codex-oracle-no-submit-'));
const logs = [];
const logger = (message) => { logs.push(String(message)); console.log(String(message)); };
let client;
let startup;
let preserveProfile = false;
let profileName;
let seedHash;
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const receipt = { schema: 'codex.oracle.no-submit-browser/v1', started_at: new Date().toISOString(),
  oracle_version: metadata.version, requested_effort: effortArgument, submission_action: 'none', ok: false };
try {
  const excluded = new Set([
    'Cache', 'Code Cache', 'GPUCache', 'Crashpad', 'ShaderCache', 'Sessions',
    'SingletonCookie', 'SingletonLock', 'SingletonSocket', 'DevToolsActivePort',
    'chrome.pid', 'oracle-automation.lock',
  ]);
  for (const entry of await readdir(seed, { withFileTypes: true })) {
    if (excluded.has(entry.name) || entry.isSymbolicLink()) continue;
    await cp(path.join(seed, entry.name), path.join(temporary, entry.name), {
      recursive: true,
      force: false,
      errorOnExist: true,
      filter: async (source) => !excluded.has(path.basename(source)) && !(await lstat(source)).isSymbolicLink(),
    });
  }
  const profiles = [];
  for (const entry of await readdir(temporary, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (await readFile(path.join(temporary, entry.name, 'Preferences')).catch(() => null)) {
      profiles.push(entry.name);
    }
  }
  profileName = profiles.includes('Default') ? 'Default' : profiles[0];
  if (!profileName) throw new Error('Copied profile has no Preferences file');
  seedHash = hash(await readFile(path.join(seed, profileName, 'Preferences')));
  // Reproduce crash state only in the disposable copy, never in the login seed.
  const prefsPath = path.join(temporary, profileName, 'Preferences');
  const prefs = JSON.parse(await readFile(prefsPath, 'utf8'));
  prefs.profile = { ...prefs.profile, exit_type: 'Crashed', exited_cleanly: false };
  await writeFile(prefsPath, JSON.stringify(prefs));
  // The local wrapper normalizes only its disposable run copy before launch.
  prefs.profile = { ...prefs.profile, exit_type: 'Normal', exited_cleanly: true };
  prefs.session = { ...prefs.session, restore_on_startup: 5, startup_urls: [] };
  await writeFile(prefsPath, JSON.stringify(prefs));
  const prepared = JSON.parse(await readFile(prefsPath, 'utf8'));
  receipt.startup_preferences = {
    exit_type: prepared.profile?.exit_type,
    exited_cleanly: prepared.profile?.exited_cleanly,
    restore_on_startup: prepared.session?.restore_on_startup,
    startup_url_count: prepared.session?.startup_urls?.length,
  };
  if (receipt.startup_preferences.exit_type !== 'Normal' ||
      receipt.startup_preferences.exited_cleanly !== true ||
      receipt.startup_preferences.restore_on_startup !== 5 ||
      receipt.startup_preferences.startup_url_count !== 0) throw new Error('Copied crash-startup preferences not normalized');
  const bin = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../bin');
  const preflight = await promisify(execFile)(process.execPath, [
    path.join(bin, 'oracle_temporary_personalization_preflight.mjs'), packageRoot,
    path.join(bin, 'oracle_temporary_personalization.mjs'), temporary, '0',
    'https://chatgpt.com/?temporary-chat=true',
  ], { encoding: 'utf8', windowsHide: true, timeout: 120000 });
  startup = JSON.parse(preflight.stdout);
  receipt.startup = startup;
  receipt.chrome_pid = startup.pid;
  // The production helper has exited; verify Chrome survived that process and
  // Oracle's documented existing-tab route still reuses the only page.
  const { connectToExistingChatGptTab } = await moduleAt('dist/src/browser/liveTabs.js');
  const attached = await connectToExistingChatGptTab({ host: '127.0.0.1', port: startup.port, ref: startup.target_id });
  client = attached.client;
  await Promise.all([client.Runtime.enable(), client.Page.enable()]);
  await client.Emulation?.setFocusEmulationEnabled({ enabled: true });
  const { targetInfos } = await client.Target.getTargets();
  const pages = targetInfos.filter(target => target.type === 'page');
  if (pages.length !== 1 || pages[0].targetId !== startup.target_id || !pages[0].url.startsWith('https://chatgpt.com/')) {
    throw new Error('Unexpected owned startup tabs');
  }
  const composer = await client.Runtime.evaluate({ expression: "document.querySelector('#prompt-textarea')?.innerText ?? ''", returnByValue: true });
  if (String(composer.result?.value ?? '').trim()) throw new Error('Composer unexpectedly nonempty');
  receipt.personalization = 'enabled';
  receipt.page_count = pages.length;
  receipt.existing_tab_reused = true;
  if (effortArgument !== 'startup') {
    const { ensurePromptReady } = await moduleAt('dist/src/browser/actions/navigation.js');
    const { ensureModelSelection } = await moduleAt('dist/src/browser/actions/modelSelection.js');
    const model = await ensureModelSelection(client.Runtime, 'Latest', logger, 'select');
    receipt.model = model;
    if (model.verified !== true || !['Latest', '최신', '最新'].includes(model.resolvedLabel)) {
      throw new Error('Native Latest selection was not verified');
    }
    await ensurePromptReady(client.Runtime, 10000, logger);
    const { ensureThinkingTime } = await moduleAt('dist/src/browser/actions/thinkingTime.js');
    const effort = await ensureThinkingTime(client.Runtime, effortArgument, logger, 'Latest');
    receipt.effort = effort;
    if (effort.verified !== true) throw new Error(`Native Latest ${effortArgument} effort was not verified`);
  }
  receipt.seed_preferences_unchanged = seedHash === hash(await readFile(path.join(seed, profileName, 'Preferences')));
  if (!receipt.seed_preferences_unchanged) throw new Error('Login seed changed during verification');
  receipt.ok = true;
} catch (error) {
  receipt.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  try {
    if (client) await Promise.resolve().then(() => client.close()).catch(() => {});
    if (startup) {
      receipt.browser_cleanup = await closePersonalizedBrowser(startup.port, startup.browser_ws,
        startup.target_id, startup.conversation_url).catch(error => ({ ok: false, error: error.message }));
      if (!receipt.browser_cleanup.ok) {
        receipt.ok = false; process.exitCode = 1; preserveProfile = true;
        receipt.temporary_profile_retained = temporary;
      }
    }
    receipt.finished_at = new Date().toISOString();
    await writeFile(receiptPath, JSON.stringify(receipt, null, 2), { flag: 'wx' });
  } finally {
    // This exact directory was freshly allocated above for this invocation only.
    if (!preserveProfile) await rm(temporary, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
  console.log(JSON.stringify(receipt));
}
