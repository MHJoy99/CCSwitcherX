'use strict';
// Runs the real extension.js against a MOCK of the VS Code API and a sandbox of fake accounts.
// No window is opened. Usage: CA_CLAUDE_DIR=<sandbox>/claude CA_STORE=<sandbox>/store node test_extension.js
const Module = require('module');
const path = require('path');
const fs = require('fs');
const os = require('os');
const assert = require('assert');

process.env.CA_NO_NETWORK = '1';  // the engine must not touch the network in tests
const calls = { info: [], warn: [], error: [], statusMsg: [] };
const handlers = {};
let nextInput;
let nextWarnChoice;
let item;

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
    onDidChangeWindowState: () => ({ dispose() {} }),
    createQuickPick: () => { qp.items = []; qp.on = {}; return qp; },
    withProgress: async (_o, fn) => fn({}, { onCancellationRequested() {} }),
  },
  commands: { registerCommand: (id, fn) => { handlers[id] = fn; return { dispose() {} }; } },
  workspace: { getConfiguration: () => ({ get: (k) => (k === 'pythonPath' ? 'python' : k === 'appDir' ? path.join(path.resolve(__dirname, '..'), 'src') : '') }) },
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

  for (const d of context.subscriptions) d.dispose && d.dispose();
  console.log(`\n${passed} checks passed`);
  process.exit(0);
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
