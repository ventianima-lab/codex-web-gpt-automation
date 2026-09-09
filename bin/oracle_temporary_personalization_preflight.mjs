#!/usr/bin/env node
import path from 'node:path';
import childProcess from 'node:child_process';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const jsonAt = async (port, resource) => {
  const response = await fetch(`http://127.0.0.1:${port}/json/${resource}`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`Chrome endpoint returned ${response.status}`);
  return response.json();
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const isBlank = target => ['about:blank', 'chrome://newtab/', 'chrome://new-tab-page/']
  .includes(String(target.url ?? '').trim().toLowerCase());

export async function stopOwnedLauncher(launcher) {
  const child = launcher?.chromeProcess;
  let timer;
  const stopped = child && child.exitCode === null && child.signalCode === null
    ? new Promise(resolve => {
      child.once('close', resolve);
      timer = setTimeout(resolve, 10000);
    }) : Promise.resolve();
  try { await Promise.resolve().then(() => launcher?.kill()); await stopped; }
  finally { clearTimeout(timer); }
}

async function dependenciesFor(packageRoot, helperPath) {
  const moduleAt = relative => import(pathToFileURL(path.join(packageRoot, relative)).href);
  const requireFromOracle = createRequire(path.join(packageRoot, 'package.json'));
  return {
    lifecycle: await moduleAt('dist/src/browser/chromeLifecycle.js'),
    ...await moduleAt('dist/src/browser/actions/navigation.js'),
    ...await import(pathToFileURL(requireFromOracle.resolve('chrome-launcher')).href),
    ...await import(pathToFileURL(helperPath).href),
    jsonAt, pause,
  };
}

// The CLI and prompt-free canary share this startup path. Oracle later attaches
// with --browser-tab evidence.target_id instead of creating another tab.
export async function startPersonalizedBrowser(options, dependencies) {
  const { packageRoot, helperPath, profilePath, port = 0, url, profileName } = options;
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('invalid CDP port');
  const parsedUrl = new URL(url);
  if (parsedUrl.origin !== 'https://chatgpt.com' || parsedUrl.searchParams.get('temporary-chat') !== 'true') {
    throw new Error('expected a temporary ChatGPT startup URL');
  }
  const deps = dependencies ?? await dependenciesFor(packageRoot, helperPath);
  const logs = [];
  const logger = message => { logs.push(String(message).slice(0, 500)); options.logger?.(message); };
  logger.verbose = false;
  const flags = deps.lifecycle.buildChromeFlagsForTest(false, undefined, true);
  if (profileName) flags.push(`--profile-directory=${profileName}`);
  flags.push('--hide-crash-restore-bubble');
  const launchOptions = deps.lifecycle.resolveChromeLaunchOptionsForTest(flags, true);
  const launcher = new deps.Launcher({
    ...launchOptions, userDataDir: profilePath, port, startingUrl: url, handleSIGINT: false,
  }, {
    spawn(command, args, spawnOptions) {
      const child = childProcess.spawn(command, args, { ...spawnOptions, detached: true, windowsHide: true });
      child.unref();
      return child;
    },
  });
  let client;
  let deadlineTimer;
  const deadline = new Promise((_, reject) => {
    deadlineTimer = setTimeout(() => reject(new Error('owned browser startup deadline exceeded')),
      options.startupTimeoutMs ?? 85000);
  });
  const start = async () => {
    await launcher.launch();
    const actualPort = Number(launcher.port);
    if ((port && actualPort !== port) || !actualPort || !Number(launcher.pid)) {
      throw new Error('launched Chrome identity does not match the reserved endpoint');
    }
    let target;
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const targets = await deps.jsonAt(actualPort, 'list');
      target = targets.find(item => item.type === 'page' && item.url === url);
      if (target) break;
      await deps.pause(100);
    }
    if (!target) throw new Error('owned temporary-chat startup target is unavailable');
    const targetId = target.id ?? target.targetId;
    const connection = await deps.lifecycle.connectToRemoteChromeTarget('127.0.0.1', actualPort, logger, { targetId });
    client = connection.client;
    if (!client?.Runtime || connection.targetId !== targetId) throw new Error('startup tab identity changed');
    // Clean any startup blanks before UI checks can fail, only in this newly
    // launched browser. Never apply this cleanup to an existing user browser.
    await deps.lifecycle.closeBlankChromeTabs(actualPort, logger, '127.0.0.1', {
      excludeTargetIds: [targetId], preserveOneBlank: false,
    });
    const pages = (await deps.jsonAt(actualPort, 'list')).filter(item => item.type === 'page');
    if (pages.length !== 1 || (pages[0].id ?? pages[0].targetId) !== targetId || pages.some(isBlank)) {
      throw new Error('expected exactly the owned temporary-chat startup tab');
    }
    await Promise.all([client.Page.enable(), client.Runtime.enable()]);
    await client.Emulation?.setFocusEmulationEnabled({ enabled: true });
    await deps.ensurePromptReady(client.Runtime, 60000, logger);
    await deps.ensureChatMode(client.Runtime, client.Input, 10000, logger);
    await deps.ensurePromptReady(client.Runtime, 10000, logger);
    // A cold startup can expose the composer before React attaches the menu
    // handlers. Re-read the idempotent control, never retry a submitted prompt.
    for (let attempt = 0; ; attempt += 1) {
      try { await deps.ensureTemporaryChatPersonalization(client.Runtime, logger); break; }
      catch (error) {
        if (attempt >= 2) throw error;
        await deps.pause(250);
      }
    }
    const observed = await client.Runtime.evaluate({ expression: 'location.href', returnByValue: true });
    if (observed.result?.value !== url) throw new Error('temporary-chat startup URL changed');
    const version = await deps.jsonAt(actualPort, 'version');
    if (!version.webSocketDebuggerUrl?.startsWith(`ws://127.0.0.1:${actualPort}/devtools/browser/`)) {
      throw new Error('browser CDP identity is unavailable');
    }
    return { chrome: launcher, client, evidence: {
      ok: true, pid: Number(launcher.pid), port: actualPort, target_id: targetId,
      conversation_url: url, browser_ws: version.webSocketDebuggerUrl,
      startup_blank_tabs: 0, page_count: 1, personalization: 'enabled', logs,
    } };
  };
  try {
    return await Promise.race([start(), deadline]);
  } catch (error) {
    await Promise.resolve().then(() => client?.close()).catch(() => undefined);
    await stopOwnedLauncher(launcher).catch(() => undefined);
    throw error;
  } finally {
    clearTimeout(deadlineTimer);
  }
}

export async function closePersonalizedBrowser(port, expectedWs, targetId, expectedUrl) {
  const version = await jsonAt(port, 'version');
  if (!expectedWs || version.webSocketDebuggerUrl !== expectedWs) {
    throw new Error('refusing to close a browser whose CDP identity changed');
  }
  const socket = new WebSocket(expectedWs);
  await new Promise((resolve, reject) => {
    let finished = false;
    const finish = error => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      socket.close();
      error ? reject(error) : resolve();
    };
    const timeout = setTimeout(() => finish(new Error('browser close timeout')), 5000);
    socket.addEventListener('open', () => socket.send(JSON.stringify({ id: 1, method: 'Target.getTargets' })));
    socket.addEventListener('message', event => {
      const value = JSON.parse(String(event.data));
      if (value.id === 1) {
        const pages = value.result?.targetInfos?.filter(item => item.type === 'page');
        if (value.error || !pages || pages.length !== 1 || pages[0].targetId !== targetId || pages[0].url !== expectedUrl) {
          finish(new Error('refusing to close a browser whose owned tab changed or has additional tabs'));
          return;
        }
        // Read and close through the same verified browser connection.
        socket.send(JSON.stringify({ id: 2, method: 'Browser.close' }));
      }
      if (value.id === 2) finish(value.error ? new Error('Browser.close rejected') : undefined);
    });
    socket.addEventListener('error', () => finish(new Error('browser close failed')));
    socket.addEventListener('close', () => finish(new Error('browser close was not acknowledged')));
  });
  return { ok: true, closed: true };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    if (process.argv[2] === '--close') {
      const [port, ws, target, url] = process.argv.slice(3);
      process.stdout.write(JSON.stringify(await closePersonalizedBrowser(Number(port), ws, target, url)));
    } else {
      const [packageRoot, helperPath, profilePath, port, url] = process.argv.slice(2);
      if (!packageRoot || !helperPath || !profilePath || !port || !url) throw new Error('missing preflight arguments');
      const session = await startPersonalizedBrowser({
        packageRoot: path.resolve(packageRoot), helperPath: path.resolve(helperPath),
        profilePath: path.resolve(profilePath), port: Number(port), url,
      });
      await session.client.close();
      process.stdout.write(JSON.stringify(session.evidence));
    }
  } catch (error) {
    process.stderr.write(JSON.stringify({ ok: false, error: String(error?.message ?? error) }));
    process.exitCode = 2;
  }
}
