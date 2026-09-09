// Real, prompt-free browser verification. Only a fresh copied profile is used.
import { readFile, writeFile, mkdtemp, rm, realpath } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const [packageArgument, profileArgument, receiptArgument] = process.argv.slice(2);
if (!packageArgument || !profileArgument || !receiptArgument) {
  throw new Error('Usage: node verify_oracle_browser_startup.mjs <validated-oracle-package> <login-profile> <receipt.json>');
}
const packageRoot = await realpath(packageArgument);
const seed = await realpath(profileArgument);
const receiptPath = path.resolve(receiptArgument);
const metadata = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));
if (metadata.name !== '@steipete/oracle' || metadata.version !== '0.18.0') throw new Error('Unvalidated Oracle version');
const moduleAt = (relative) => import(pathToFileURL(path.join(packageRoot, relative)).href);
const lifecycle = await moduleAt('dist/src/browser/chromeLifecycle.js');
const { copyChromeProfile } = await moduleAt('dist/src/browser/profileCopy.js');
const { ensureThinkingTime } = await moduleAt('dist/src/browser/actions/thinkingTime.js');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'Codex-oracle-no-submit-'));
const logs = [];
const logger = (message) => { logs.push(String(message)); console.log(String(message)); };
let chrome;
let client;
let profileName;
let seedHash;
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const receipt = { schema: 'codex.oracle.no-submit-browser/v1', started_at: new Date().toISOString(),
  oracle_version: metadata.version, submission_action: 'none', ok: false };
try {
  profileName = await copyChromeProfile(seed, temporary);
  seedHash = hash(await readFile(path.join(seed, profileName, 'Preferences')));
  // Reproduce crash state only in the disposable copy, never in the login seed.
  const prefsPath = path.join(temporary, profileName, 'Preferences');
  const prefs = JSON.parse(await readFile(prefsPath, 'utf8'));
  prefs.profile = { ...prefs.profile, exit_type: 'Crashed', exited_cleanly: false };
  await writeFile(prefsPath, JSON.stringify(prefs));
  await lifecycle.prepareCopiedProfileStartup({ copyProfileSource: seed, chromeProfile: profileName }, temporary);
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
  chrome = await lifecycle.launchChrome({ copyProfileSource: seed, chromeProfile: profileName, headless: false, debugPort: 0 }, temporary, logger);
  receipt.chrome_pid = chrome.pid;
  const connection = await lifecycle.connectWithNewTab(chrome.port, logger, 'about:blank', chrome.host, { fallbackToDefault: false });
  client = connection.client;
  await client.Page.enable();
  await client.Runtime.enable();
  await client.Page.navigate({ url: 'https://chatgpt.com/' });
  const deadline = Date.now() + 60000;
  let ready = false;
  while (Date.now() < deadline) {
    const result = await client.Runtime.evaluate({ expression: "Boolean(document.querySelector('#prompt-textarea'))", returnByValue: true });
    if (result.result?.value === true) { ready = true; break; }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (!ready) throw new Error('Signed-in composer unavailable; no prompt was submitted');
  const results = [];
  for (const level of ['pro', 'extra-high']) {
    const before = logs.length;
    await ensureThinkingTime(client.Runtime, level, logger, null);
    const evidence = logs.slice(before).filter(line => line.startsWith('[browser] Thinking time: Latest /'));
    if (!evidence.some(line => line.includes('Latest explicitly selected'))) throw new Error('Latest selection proof missing');
    results.push({ level, evidence });
  }
  const { targetInfos } = await client.Target.getTargets();
  const pages = targetInfos.filter(target => target.type === 'page');
  if (pages.length !== 1 || pages[0].targetId !== connection.targetId || !pages[0].url.startsWith('https://chatgpt.com/')) {
    throw new Error('Unexpected owned startup tabs');
  }
  const composer = await client.Runtime.evaluate({ expression: "document.querySelector('#prompt-textarea')?.innerText ?? ''", returnByValue: true });
  if (String(composer.result?.value ?? '').trim()) throw new Error('Composer unexpectedly nonempty');
  receipt.levels = results;
  receipt.page_count = pages.length;
  receipt.seed_preferences_unchanged = seedHash === hash(await readFile(path.join(seed, profileName, 'Preferences')));
  if (!receipt.seed_preferences_unchanged) throw new Error('Login seed changed during verification');
  receipt.ok = true;
} catch (error) {
  receipt.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  try {
    if (client) {
      await Promise.resolve().then(() => client.Browser.close()).catch(() => {});
      await Promise.resolve().then(() => client.close()).catch(() => {});
    }
    if (chrome) await Promise.resolve().then(() => chrome.kill()).catch(() => {});
    receipt.finished_at = new Date().toISOString();
    await writeFile(receiptPath, JSON.stringify(receipt, null, 2), { flag: 'wx' });
  } finally {
    // This exact directory was freshly allocated above for this invocation only.
    await rm(temporary, { recursive: true, force: true });
  }
  console.log(JSON.stringify(receipt));
}
