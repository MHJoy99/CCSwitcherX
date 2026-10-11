'use strict';
// Runs the real extension.js against a MOCK of the VS Code API and a sandbox of fake accounts.
// No window is opened. Usage: CA_CLAUDE_DIR=<sandbox>/claude CA_STORE=<sandbox>/store node test_extension.js
const Module = require('module');
const path = require('path');
const fs = require('fs');
const os = require('os');
const assert = require('assert');
const cp = require('child_process');
const http = require('http');

if (!process.env.CA_CLAUDE_DIR || !process.env.CA_STORE) {
  console.error('Refusing to run without CA_CLAUDE_DIR and CA_STORE: this test switches logins and providers, so it needs the sandbox from make_sandbox.py.');
  process.exit(2);
}
process.env.CA_NO_NETWORK = '1';  // the engine must not touch the network in tests
process.env.NO_PROXY = process.env.no_proxy = '127.0.0.1,localhost';  // the fake gateway below is local
const calls = { info: [], warn: [], error: [], statusMsg: [], progress: [], picks: [] };
const handlers = {};
let nextInput;
let nextWarnChoice;
let nextPick;
let focusHandler;
let appDirOverride = '';
let item;

// The extension runs the real engine; these only record what it asks for. Nothing is watched and no window is spawned.
const engineCalls = [];   // engine arguments after --json, e.g. ['provider', 'use', '<id>']
const spawned = [];
const watchers = [];      // {file, cb} for every fs.watchFile the extension sets up
const realExecFile = cp.execFile;
cp.execFile = function (file, args, ...rest) { engineCalls.push(args.slice(2)); return realExecFile.call(this, file, args, ...rest); };
cp.spawn = (cmd, args) => { spawned.push({ cmd, args }); return { unref() {} }; };
fs.watchFile = (file, _opts, cb) => { watchers.push({ file, cb }); };

const qp = {
  items: [], selectedItems: [], activeItems: [], on: {},
  show() { this.shown = true; }, hide() { if (this.on.hide) this.on.hide(); }, dispose() {},
  onDidAccept(fn) { this.on.accept = fn; return { dispose() {} }; },
  onDidTriggerItemButton(fn) { this.on.button = fn; return { dispose() {} }; },
  onDidHide(fn) { this.on.hide = fn; return { dispose() {} }; },
};

class ThemeColor { constructor(id) { this.id = id; } }
class ThemeIcon { constructor(id) { this.id = id; } }
class MarkdownString { constructor() { this.value = ''; } appendText(t) { this.value += t; return this; } }

const vscode = {
  ThemeColor, ThemeIcon, MarkdownString,
  StatusBarAlignment: { Left: 1, Right: 2 },
  QuickPickItemKind: { Separator: -1, Default: 0 },
  ProgressLocation: { Notification: 15 },
  window: {
    createOutputChannel: () => ({ appendLine() {}, show() {}, dispose() {} }),
    createStatusBarItem: () => (item = { text: '', show() {}, dispose() {} }),
    showInformationMessage: async (m) => { calls.info.push(m); },
    showWarningMessage: async (m) => { calls.warn.push(m); const c = nextWarnChoice; nextWarnChoice = undefined; return c; },
    showErrorMessage: async (m) => { calls.error.push(m); },
    setStatusBarMessage: (m) => { calls.statusMsg.push(m); },
    showInputBox: async () => { const v = nextInput; nextInput = undefined; return v; },
    onDidChangeWindowState: (fn) => { focusHandler = fn; return { dispose() {} }; },
    createQuickPick: () => { qp.items = []; qp.on = {}; return qp; },
    showQuickPick: async (items, opts) => { calls.picks.push({ items, opts }); const want = nextPick; nextPick = undefined; return want ? want(items) : undefined; },
    withProgress: async (o, fn) => { calls.progress.push(o.title); return fn({}, { onCancellationRequested() {} }); },
  },
  commands: { registerCommand: (id, fn) => { handlers[id] = fn; return { dispose() {} }; } },
  workspace: { getConfiguration: () => ({ get: (k) => (k === 'pythonPath' ? 'python' : k === 'appDir' ? (appDirOverride || path.join(path.resolve(__dirname, '..'), 'src')) : '') }) },
};

const origLoad = Module._load;
Module._load = function (request) { return request === 'vscode' ? vscode : origLoad.apply(this, arguments); };
const REPO = path.resolve(__dirname, '..');
const ext = require(path.join(REPO, 'extension', 'extension.js'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(cond, what, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (cond()) return; await sleep(100); }
  throw new Error(`timed out waiting for: ${what} (status bar says "${item.text}")`);
}
const sandboxToken = () => JSON.parse(fs.readFileSync(path.join(process.env.CA_CLAUDE_DIR, '.credentials.json'), 'utf8')).claudeAiOauth.refreshToken;
const labels = () => qp.items.map((i) => i.label);

// Runs the engine directly, the way a terminal would (used to set up providers and to change things "from outside").
const ENGINE = path.join(REPO, 'src', 'claude_accounts.py');
function engine(args, env = {}) {
  const out = cp.execFileSync('python', ['-I', ENGINE, '--json', ...args], { env: { ...process.env, ...env }, encoding: 'utf8' });
  const res = JSON.parse(out.trim().split(/\r?\n/).pop());
  assert.ok(res.ok !== false, `engine ${args.join(' ')}: ${res.error}`);
  return res;
}

// A fake model gateway on localhost: "gem-y" always fails; any other model answers with a tool call and then a text step.
function startGateway() {
  const gw = { hits: [] };
  gw.server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      let j = {};
      try { j = JSON.parse(body); } catch (_) { /* leave empty */ }
      gw.hits.push({ url: req.url, model: j.model, messages: (j.messages || []).length, key: req.headers['x-api-key'] });
      const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
      if (j.model === 'gem-y') return send(500, { error: { message: 'upstream exploded' } });
      if ((j.messages || []).length === 1) return send(200, { content: [{ type: 'tool_use', id: 'toolu_1', name: 'get_weather', input: { city: 'Paris' } }] });
      return send(200, { content: [{ type: 'text', text: 'It is 18C and sunny in Paris.' }] });
    });
  });
  return new Promise((resolve) => gw.server.listen(0, '127.0.0.1', () => { gw.port = gw.server.address().port; resolve(gw); }));
}

// Fires a file-watcher callback until the status bar shows what we expect (the extension ignores changes within 2.5 s of a refresh).
async function nudge(cb, cond, what) {
  for (let i = 0; i < 4; i++) {
    await sleep(2700);
    cb();
    const end = Date.now() + 3000;
    while (Date.now() < end) { if (cond()) return; await sleep(100); }
  }
  throw new Error(`timed out waiting for: ${what} (status bar says "${item.text}")`);
}
let passed = 0;
const ok = (name) => { passed++; console.log(`  ok  ${name}`); };

// Claude Code keeps usage numbers for the active account in .claude.json; plant the same thing in the sandbox.
{
  const file = path.join(process.env.CA_CLAUDE_DIR, '.claude.json');
  const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
  const iso = (h) => new Date(Date.now() + h * 3600000).toISOString();
  cfg.cachedUsageUtilization = {
    fetchedAtMs: Date.now() - 5000, accountUuid: cfg.oauthAccount.accountUuid,
    utilization: { five_hour: { utilization: 64, resets_at: iso(3) }, seven_day: { utilization: 34, resets_at: iso(70) } },
  };
  fs.writeFileSync(file, JSON.stringify(cfg));
}

(async () => {
  const context = { subscriptions: [] };
  const t0 = Date.now();
  ext.activate(context);
  await until(() => item.text.includes('Personal · 64%'), 'status bar shows the active account and its session usage');
  ok(`status bar shows "${item.text}" after ${Date.now() - t0} ms`);
  assert.ok(item.tooltip.value.includes('alex@example.invalid'));
  ok('tooltip names the account email');
  assert.ok(item.tooltip.value.includes('Usage: Session 64% · Week 34%') && item.tooltip.value.includes('Session resets in'));
  ok('tooltip shows usage and when it resets');
  for (const id of ['switch', 'add', 'saveCurrent', 'openManager', 'doctor', 'showLog']) assert.ok(handlers[`claudeAccounts.${id}`], id);
  ok('all 6 commands registered');

  // picker lists the accounts, marks the active one, offers actions
  await handlers['claudeAccounts.switch']();
  assert.ok(labels().some((l) => l.includes('$(check) Personal')));
  assert.ok(labels().some((l) => l.includes('Team')));
  assert.ok(labels().some((l) => l.includes('Add account')));
  ok(`picker lists accounts: ${labels().filter(Boolean).join(' | ')}`);
  const personalRow = qp.items.find((i) => i.profile && i.profile.name === 'Personal');
  assert.ok(personalRow.detail.includes('Session 64%') && personalRow.detail.includes('Week 34%'), personalRow.detail);
  assert.ok(qp.items.some((i) => i.action === 'usage'));
  ok(`picker row shows usage: ${personalRow.detail}`);
  const teamRow = qp.items.find((i) => i.profile && i.profile.name === 'Team');
  assert.strictEqual(teamRow.detail, undefined);
  ok('an account with no usage data shows nothing made up');

  // switch to Team
  qp.selectedItems = [qp.items.find((i) => i.profile && i.profile.name === 'Team')];
  const t1 = Date.now();
  qp.on.accept();
  await until(() => item.text.includes('Team') && !item.text.includes('Switching'), 'status bar shows Team');
  ok(`switched to Team, status bar updated in ${Date.now() - t1} ms`);
  assert.strictEqual(sandboxToken(), 'fake-rt-t');
  ok('credentials file now holds the Team login');
  await sleep(6000);  // background confirmation (settle + claude auth status)
  assert.deepStrictEqual(calls.warn, [], `unexpected warnings: ${calls.warn}`);
  assert.deepStrictEqual(calls.error, []);
  ok('background confirmation raised no warnings or errors');

  await handlers['claudeAccounts.switch']();
  const idlePersonal = qp.items.find((i) => i.profile && i.profile.name === 'Personal');
  assert.ok(idlePersonal.detail && idlePersonal.detail.includes('Session 64%'), `idle detail: ${idlePersonal.detail}`);
  ok('an idle account keeps its last known usage');
  qp.selectedItems = [qp.items.find((i) => i.action === 'usage')];
  qp.on.accept();
  await sleep(2500);
  assert.deepStrictEqual(calls.error, []);
  ok('Refresh usage runs without errors');

  // and back
  await handlers['claudeAccounts.switch']();
  qp.selectedItems = [qp.items.find((i) => i.profile && i.profile.name === 'Personal')];
  qp.on.accept();
  await until(() => item.text.includes('Personal') && !item.text.includes('Switching'), 'status bar shows Personal again');
  assert.strictEqual(sandboxToken(), 'fake-rt-p');
  ok('switched back to Personal');

  // rename through the row button
  await sleep(3000);
  await handlers['claudeAccounts.switch']();
  const teamItem = qp.items.find((i) => i.profile && i.profile.name === 'Team');
  nextInput = 'Work';
  qp.on.button({ button: { tooltip: 'Rename' }, item: teamItem });
  await until(() => calls.error.length === 0 && true, 'rename settled', 500);
  await sleep(2500);
  await handlers['claudeAccounts.switch']();
  assert.ok(labels().some((l) => l.includes('Work')) && !labels().some((l) => l.includes('Team')));
  ok('rename works');

  // remove through the row button (confirmation answered "Remove")
  const workItem = qp.items.find((i) => i.profile && i.profile.name === 'Work');
  nextWarnChoice = 'Remove';
  qp.on.button({ button: { tooltip: 'Remove from list' }, item: workItem });
  await sleep(2500);
  await handlers['claudeAccounts.switch']();
  assert.ok(!labels().some((l) => l.includes('Work')));
  ok('remove works');

  // self-check command
  await handlers['claudeAccounts.doctor']();
  assert.ok(calls.info.some((m) => m.includes('everything is fine')), `doctor said: ${calls.info}`);
  ok('self-check passes');

  // ================================================================ providers
  // Two providers go into the sandbox store through the engine; "BDX Muse" talks to a fake gateway on localhost.
  const KEY = 'sk-test-SECRET-extension-1234567890';
  const gw = await startGateway();
  const GW_URL = `http://127.0.0.1:${gw.port}`;
  const GW_HOST = `127.0.0.1:${gw.port}`;
  const settingsFile = path.join(process.env.CA_CLAUDE_DIR, 'settings.json');
  const readSettings = () => (fs.existsSync(settingsFile) ? JSON.parse(fs.readFileSync(settingsFile, 'utf8')) : {});
  const reset = () => { for (const k of Object.keys(calls)) calls[k].length = 0; engineCalls.length = 0; };
  const order = () => qp.items.map((i) => (i.kind === vscode.QuickPickItemKind.Separator ? `--${i.label}--` : i.label));
  const providerRow = (name) => qp.items.find((i) => i.provider && i.provider.name === name);
  const accountRow = (name) => qp.items.find((i) => i.profile && i.profile.name === name);
  const actionRow = (action) => qp.items.find((i) => i.action === action);
  const accept = (row) => { qp.selectedItems = [row]; qp.on.accept(); };
  const openPicker = () => handlers['claudeAccounts.switch']();
  const poke = async (cond, what) => {   // window focus makes the extension re-read the state
    for (let i = 0; i < 5; i++) {
      focusHandler({ focused: true });
      const end = Date.now() + 2500;
      while (Date.now() < end) { if (cond()) return; await sleep(100); }
    }
    throw new Error(`timed out waiting for: ${what} (status bar says "${item.text}")`);
  };
  engine(['provider', 'add', 'BDX Muse', '--url', GW_URL, '--key-env', 'CA_TEST_KEY', '--opus', 'muse-x', '--sonnet', 'muse-x', '--haiku', 'gem-y'], { CA_TEST_KEY: KEY });
  engine(['provider', 'add', 'Spare', '--url', GW_URL, '--key-env', 'CA_TEST_KEY', '--opus', 'a-1', '--sonnet', 'b-2', '--haiku', 'b-2'], { CA_TEST_KEY: KEY });

  // picker: accounts first, then a "Providers" group, then the actions
  reset();
  await openPicker();
  const o = order();
  const iProviders = o.indexOf('--Providers--');
  const iPersonal = o.findIndex((l) => l.includes('Personal'));
  const iMuse = o.findIndex((l) => l.includes('BDX Muse'));
  const iSpare = o.findIndex((l) => l.includes('Spare'));
  const iAddAccount = o.findIndex((l) => l.includes('Add account'));
  const iAddProvider = o.findIndex((l) => l.includes('Add provider'));
  const iTestProvider = o.findIndex((l) => l.includes('Test provider'));
  assert.ok(iPersonal >= 0 && iPersonal < iProviders && iProviders < iMuse && iMuse < iSpare && iSpare < iAddAccount, o.join(' | '));
  assert.ok(iAddAccount < iAddProvider && iAddProvider < iTestProvider && o[iAddAccount - 1] === '----', o.join(' | '));
  assert.strictEqual(qp.items[iProviders].kind, vscode.QuickPickItemKind.Separator);
  ok(`picker order is accounts, "Providers" group, then actions: ${o.map((l) => l || '(line)').join(' | ')}`);
  const museRow = providerRow('BDX Muse');
  assert.strictEqual(museRow.label, '$(plug) BDX Muse');
  assert.strictEqual(museRow.description, GW_HOST);
  assert.strictEqual(museRow.detail, 'Opus, Sonnet → muse-x · Haiku, subagents → gem-y');
  assert.strictEqual(providerRow('Spare').detail, 'Opus → a-1 · Sonnet, Haiku, subagents → b-2');
  ok(`provider rows show host and the model map: "${museRow.detail}"`);
  assert.strictEqual(labels().filter((l) => l.includes('$(check)')).length, 1);
  assert.ok(item.text.startsWith('$(account) Personal') && !item.text.includes('plug'), item.text);
  ok('with no provider in use the status bar still shows the account and only the account has a check mark');
  const muse = museRow.provider;

  // select a provider
  reset();
  const t2 = Date.now();
  accept(museRow);
  await until(() => item.text === '$(plug) BDX Muse', 'status bar shows the provider');
  ok(`status bar shows "${item.text}" after ${Date.now() - t2} ms`);
  assert.ok(engineCalls.some((a) => a.join(' ') === `provider use ${muse.id}`), JSON.stringify(engineCalls));
  ok('selecting a provider runs "provider use <id>"');
  const tip = item.tooltip.value;
  assert.ok(tip.includes(`Using provider BDX Muse (${GW_HOST})`) && tip.includes('Opus/Sonnet → muse-x, Haiku → gem-y')
    && tip.includes('Your Claude subscription is not used until you switch back'), tip);
  assert.ok(!item.text.includes('%') && item.command === 'claudeAccounts.switch' && item.backgroundColor === undefined);
  ok('provider tooltip names the host and models and says the subscription is not used; no usage percentage; click still opens the picker');
  await until(() => calls.info.includes('Using BDX Muse from your next message.'), 'the "from your next message" notice');
  assert.ok(Date.now() - t2 >= 1800, `notice came after only ${Date.now() - t2} ms`);
  assert.ok(calls.progress.includes('Switching to BDX Muse…'), JSON.stringify(calls.progress));
  ok(`settle notification "${calls.progress[0]}" ran, then the notice came after ${Date.now() - t2} ms`);
  const env = readSettings().env || {};
  assert.strictEqual(env.ANTHROPIC_BASE_URL, GW_URL);
  assert.ok(!fs.readFileSync(settingsFile, 'utf8').includes(KEY));
  assert.deepStrictEqual(calls.error, []);
  ok('the engine pointed Claude Code settings at the gateway (the key is not in settings.json)');

  // while a provider is in use, no account is in use even though its login is the live one
  await openPicker();
  const idlePersonalRow = accountRow('Personal');
  assert.ok(idlePersonalRow.profile.active === true && idlePersonalRow.profile.inUse === false);
  assert.ok(idlePersonalRow.label.startsWith('$(account)') && !idlePersonalRow.description.includes('in use'), idlePersonalRow.label);
  assert.ok(providerRow('BDX Muse').label.startsWith('$(check)') && providerRow('BDX Muse').description.includes('(in use)'));
  assert.strictEqual(qp.activeItems[0].provider.name, 'BDX Muse');
  assert.strictEqual(labels().filter((l) => l.includes('$(check)')).length, 1);
  ok('account with inUse=false (but active=true) is not marked in use; the provider has the check mark and is preselected');
  reset();
  accept(providerRow('BDX Muse'));
  await sleep(500);
  assert.ok(!engineCalls.some((a) => a[0] === 'provider') && calls.progress.length === 0 && calls.info.length === 0);
  ok('picking the provider that is already in use does nothing');

  // provider to provider
  reset();
  accept(providerRow('Spare'));
  await until(() => item.text === '$(plug) Spare', 'status bar shows the second provider');
  assert.ok(item.tooltip.value.includes('Opus → a-1, Sonnet/Haiku → b-2'), item.tooltip.value);
  await until(() => calls.info.includes('Using Spare from your next message.'), 'notice for the second provider');
  ok('switching from one provider to another works and the tooltip groups models by tier');

  // an account that is the live login but not in use can still be picked; that leaves the provider
  await openPicker();
  reset();
  const t5 = Date.now();
  const personalId = accountRow('Personal').profile.id;
  accept(accountRow('Personal'));
  await until(() => item.text.startsWith('$(account) Personal'), 'status bar shows the account again');
  assert.ok(engineCalls.some((a) => a.join(' ') === `switch ${personalId}`), JSON.stringify(engineCalls));
  ok('selecting an account while a provider is in use runs "switch <id>" (not ignored because the login was already live)');
  await until(() => calls.info.includes('Left provider "Spare". Using Personal from your next message.'), 'notice after leaving the provider');
  assert.ok(calls.progress.includes('Switching to Personal…') && Date.now() - t5 >= 1800);
  assert.ok(!('ANTHROPIC_BASE_URL' in (readSettings().env || {})));
  ok('leaving a provider shows the settle notification and the notice; settings.json no longer points at the gateway');
  await sleep(6000);  // background confirmation (settle + claude auth status)
  assert.deepStrictEqual(calls.warn, [], `unexpected warnings: ${calls.warn}`);
  assert.deepStrictEqual(calls.error, []);

  // Test provider...: one row ok, one failing (the engine exits 1 but still answers with the rows)
  await openPicker();
  reset();
  gw.hits.length = 0;
  nextPick = (items) => items.find((i) => i.provider.name === 'BDX Muse');
  accept(actionRow('testProvider'));
  await until(() => calls.warn.length === 1, 'test result for a provider with a failing model');
  assert.deepStrictEqual(calls.picks[0].items.map((i) => i.provider.name), ['BDX Muse', 'Spare']);
  assert.strictEqual(calls.picks[0].items[0].detail, 'Opus, Sonnet → muse-x · Haiku, subagents → gem-y');
  ok('"Test provider…" opens a second quick pick that lists the providers');
  assert.ok(engineCalls.some((a) => a.join(' ') === `provider test ${muse.id}`), JSON.stringify(engineCalls));
  assert.ok(calls.progress.some((t) => t.startsWith('Testing BDX Muse')), JSON.stringify(calls.progress));
  const bad = calls.warn[0];
  assert.ok(bad.startsWith('Provider "BDX Muse" has a problem.') && bad.includes('✓ muse-x (Opus, Sonnet)')
    && bad.includes('✗ gem-y (Haiku, subagents): HTTP 500: upstream exploded'), bad);
  assert.deepStrictEqual(calls.info, []);
  assert.deepStrictEqual(calls.error, []);
  ok(`failing model is reported as a warning: ${bad}`);
  assert.deepStrictEqual(gw.hits.map((h) => `${h.model}:${h.messages}`), ['muse-x:1', 'muse-x:3', 'gem-y:1']);
  assert.ok(gw.hits.every((h) => h.key === KEY && h.url === '/v1/messages'));
  ok('the test really went through the gateway: tool call, follow-up step, and the saved key');

  await openPicker();
  reset();
  nextPick = (items) => items.find((i) => i.provider.name === 'Spare');
  accept(actionRow('testProvider'));
  await until(() => calls.info.length === 1, 'test result for a provider that works');
  const good = calls.info[0];
  assert.ok(good.startsWith('Provider "Spare" works.') && good.includes('✓ a-1 (Opus)') && good.includes('✓ b-2 (Sonnet, Haiku, subagents)') && !good.includes('✗'), good);
  assert.deepStrictEqual(calls.warn, []);
  ok(`all models passing is an information message: ${good}`);

  await openPicker();
  reset();
  accept(actionRow('testProvider'));   // the second quick pick is dismissed
  await sleep(500);
  assert.ok(!engineCalls.some((a) => a[1] === 'test') && !calls.info.length && !calls.warn.length && !calls.error.length);
  ok('dismissing the provider list tests nothing');

  await openPicker();
  reset();
  nextPick = () => ({ provider: { id: 'ghost', name: 'Ghost' } });
  accept(actionRow('testProvider'));
  await until(() => calls.error.length === 1, 'error for a provider that is gone');
  assert.strictEqual(calls.error[0], 'Claude Accounts: No saved provider named "ghost". Try: provider list');
  assert.deepStrictEqual(calls.info.concat(calls.warn), []);
  ok(`engine error while testing shows in the error display: ${calls.error[0]}`);

  // error display when "provider use" fails: another credential in settings.json would override the provider's key
  fs.writeFileSync(settingsFile, JSON.stringify({ env: { ANTHROPIC_AUTH_TOKEN: 'x' } }));
  await openPicker();
  reset();
  accept(providerRow('BDX Muse'));
  await until(() => calls.error.length === 1, 'error from provider use');
  assert.ok(calls.error[0].startsWith('Claude Accounts: settings.json already sets ANTHROPIC_AUTH_TOKEN'), calls.error[0]);
  await until(() => item.text.startsWith('$(account) Personal'), 'status bar settles back on the account after the error');
  assert.deepStrictEqual(calls.info, []);
  assert.ok(!calls.progress.length);
  ok(`a failed provider switch shows the engine's message, no success notice, and the status bar recovers`);
  fs.rmSync(settingsFile, { force: true });

  // route shown for settings that this app did not write (via window focus, which re-reads the state)
  fs.writeFileSync(settingsFile, JSON.stringify({ env: { ANTHROPIC_BASE_URL: 'https://proxy.example.invalid' } }));
  await poke(() => item.text === '$(warning) Custom URL', 'custom URL in the status bar');
  assert.ok(item.tooltip.value.includes('https://proxy.example.invalid') && item.tooltip.value.includes('outside this app'), item.tooltip.value);
  assert.ok(item.command === 'claudeAccounts.switch' && item.backgroundColor === undefined);
  ok('a base URL set outside this app shows "Custom URL" with a tooltip naming it');
  fs.writeFileSync(settingsFile, JSON.stringify({
    env: { ANTHROPIC_BASE_URL: 'https://gone.example.invalid' },
    apiKeyHelper: '"python" -I "C:\\gone\\claude_accounts.py" key aaaaaaaaaaaa',
  }));
  await poke(() => item.text === '$(warning) Provider missing', 'missing provider in the status bar');
  assert.ok(item.backgroundColor instanceof ThemeColor && item.backgroundColor.id === 'statusBarItem.warningBackground');
  ok('settings pointing at a provider that is no longer saved show a warning');
  fs.rmSync(settingsFile, { force: true });
  await poke(() => item.text.startsWith('$(account) Personal'), 'account again after settings are clean');

  // changes made outside (terminal or window) are noticed: the watchers cover settings.json and route.json
  const home = os.homedir();
  const watched = watchers.map((w) => w.file);
  const settingsPath = path.join(home, '.claude', 'settings.json');
  const routePath = path.join(home, '.claude-accounts', 'route.json');
  for (const f of [path.join(home, '.claude.json'), path.join(home, '.claude', '.credentials.json'), settingsPath, routePath]) {
    assert.ok(watched.includes(f), `not watched: ${f}`);
  }
  ok('file watchers cover .claude.json, .credentials.json, settings.json and route.json');
  engine(['provider', 'use', muse.id]);
  await nudge(watchers.find((w) => w.file === settingsPath).cb, () => item.text === '$(plug) BDX Muse', 'status bar follows "provider use" run from outside');
  ok('an outside "provider use" shows up in the status bar through the settings.json watcher');
  engine(['provider', 'off']);
  await nudge(watchers.find((w) => w.file === routePath).cb, () => item.text.startsWith('$(account) Personal'), 'status bar follows "provider off" run from outside');
  ok('an outside "provider off" shows up in the status bar through the route.json watcher');

  // "Add provider…" opens the Claude Accounts window (here the spawn is only recorded)
  await openPicker();
  reset();
  spawned.length = 0;
  accept(actionRow('addProvider'));
  assert.strictEqual(spawned.length, 1);
  assert.ok(spawned[0].args[0].endsWith('claude_accounts.py') && spawned[0].args.length === 1, JSON.stringify(spawned[0].args));
  assert.ok(calls.statusMsg.some((m) => m.includes('add the provider there')));
  ok('"Add provider…" opens the Claude Accounts window');

  // an engine from before providers (no inUse, route or providers in the state) still works
  const stubDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ca-old-engine-'));
  fs.writeFileSync(path.join(stubDir, 'claude_accounts.py'), [
    'import json, sys',
    'if "state" in sys.argv:',
    '    print(json.dumps({"signedIn": True, "active": None, "unsaved": None, "profiles": [{"id": "x1", "name": "Old", "email": "old@example.invalid", "plan": "max", "detail": "Max", "team": False, "active": True, "expired": False, "daysLeft": 20, "usage": None}]}))',
    'else:',
    '    print(json.dumps({"ok": False, "error": "stub"}))',
  ].join('\n'));
  appDirOverride = stubDir;
  await poke(() => item.text === '$(account) Old', 'status bar with an old engine');
  await openPicker();
  assert.ok(labels().includes('$(check) Old') && !order().includes('--Providers--') && !labels().some((l) => l.includes('Test provider')), order().join(' | '));
  assert.strictEqual(qp.placeholder, 'Choose the account Claude Code should use');
  appDirOverride = '';
  await poke(() => item.text.startsWith('$(account) Personal'), 'status bar back on the real engine');
  fs.rmSync(stubDir, { recursive: true, force: true });
  ok('an engine without providers or inUse still shows the active account and a plain account list');

  // no secret ever reached the extension; the version matches the engine
  assert.ok(!JSON.stringify([calls, engineCalls, item.tooltip.value, qp.items]).includes(KEY));
  ok('the API key never appears in messages, tooltips, picker rows or engine arguments');
  const pkg = JSON.parse(fs.readFileSync(path.join(REPO, 'extension', 'package.json'), 'utf8'));
  const engineVersion = /__version__ = "([^"]+)"/.exec(fs.readFileSync(path.join(REPO, 'src', 'claude_accounts_core.py'), 'utf8'))[1];
  assert.strictEqual(pkg.version, engineVersion);
  ok(`extension version ${pkg.version} matches the engine`);
  gw.server.close();

  for (const d of context.subscriptions) d.dispose && d.dispose();
  console.log(`\n${passed} checks passed`);
  process.exit(0);
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
