'use strict';
// Claude Accounts: a thin VS Code front-end for ~/.claude-accounts/claude_accounts.py.
// All account logic (locks, encryption, rollback, crash recovery) lives in that tested Python engine;
// this file only shows the state and asks the engine to act. No secrets ever pass through here.

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const log = vscode.window.createOutputChannel('Claude Accounts');
const promptedUnsaved = new Set();
let item;
let state = null;      // last result of `state` (no secrets)
let problem = null;    // text when the engine can't be run
let inflight = null;
let lastRefresh = 0;
let busy = false;

// ---------------------------------------------------------------- engine
const cfg = () => vscode.workspace.getConfiguration('claudeAccounts');
const appDir = () => cfg().get('appDir') || path.join(os.homedir(), '.claude-accounts');
const pythonCmd = () => cfg().get('pythonPath') || 'python';

function run(args, { timeout = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const script = path.join(appDir(), 'claude_accounts.py');
    if (!fs.existsSync(script)) {
      reject(new Error(`Engine not found: ${script}`));
      return;
    }
    cp.execFile(pythonCmd(), [script, '--json', ...args],
      { windowsHide: true, timeout, maxBuffer: 4 * 1024 * 1024, env: process.env },
      (err, stdout, stderr) => {
        const lines = String(stdout).trim().split(/\r?\n/).filter(Boolean);
        try {
          resolve(JSON.parse(lines[lines.length - 1]));
        } catch (_) {
          const why = err && err.code === 'ENOENT'
            ? `Python was not found ("${pythonCmd()}"). Set claudeAccounts.pythonPath.`
            : (String(stderr).trim().split(/\r?\n/).pop() || (err && err.message) || 'Unreadable answer from the engine');
          reject(new Error(why));
        }
      });
  });
}

const messageOf = (e) => (e && e.message) || String(e);

// ---------------------------------------------------------------- providers
// A provider is another model gateway (custom URL + key + model map). While one is in use no saved account is in use,
// which is why an account counts as "in use" only when the engine says inUse (an older engine only knows `active`).
const inUse = (p) => (p.inUse === undefined ? !!p.active : !!p.inUse);
const activeProvider = () => {
  const route = (state && state.route) || {};
  const list = (state && state.providers) || [];
  return list.find((p) => p.id === route.id) || list.find((p) => p.active) || null;
};
const SETTLE_SECONDS = 2;  // Claude Code follows a settings change from its next message, about 1.5 s later

const TIERS = [['opus', 'Opus'], ['sonnet', 'Sonnet'], ['haiku', 'Haiku'], ['subagent', 'subagents']];
// Tiers that share a model are listed together: [{ model, labels: ['Opus', 'Sonnet'] }, { model, labels: ['Haiku', 'subagents'] }].
function modelGroups(models, tiers = TIERS) {
  const groups = [];
  for (const [key, label] of tiers) {
    const model = models && models[key];
    if (!model) continue;
    const group = groups.find((g) => g.model === model);
    if (group) group.labels.push(label); else groups.push({ model, labels: [label] });
  }
  return groups;
}
const modelsDetail = (m) => modelGroups(m).map((g) => `${g.labels.join(', ')} → ${g.model}`).join(' · ');
function modelsTip(m) {
  const parts = modelGroups(m, TIERS.slice(0, 3)).map((g) => `${g.labels.join('/')} → ${g.model}`);
  if (m && m.subagent && m.subagent !== m.haiku) parts.push(`subagents → ${m.subagent}`);
  return parts.join(', ');
}

// ---------------------------------------------------------------- status bar
function render() {
  item.backgroundColor = undefined;
  item.command = 'claudeAccounts.switch';
  const tip = new vscode.MarkdownString();
  tip.isTrusted = false;

  if (problem) {
    item.text = '$(error) Claude Accounts';
    item.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    item.command = 'claudeAccounts.showLog';
    tip.appendText(problem);
    item.tooltip = tip;
    return;
  }
  if (!state) {
    item.text = '$(sync~spin) Claude';
    item.tooltip = 'Claude Accounts is starting…';
    return;
  }
  const route = state.route || { kind: 'account' };
  if (route.kind === 'provider') {
    const prov = activeProvider();
    if (route.missing || !prov) {
      item.text = '$(warning) Provider missing';
      item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
      tip.appendText(`Claude Code is set to use a provider${route.baseUrl ? ` (${route.baseUrl})` : ''} that is no longer saved here. Click, then pick an account to switch back.`);
    } else {
      item.text = `$(plug) ${prov.name}`;
      tip.appendText(`Using provider ${prov.name} (${prov.host}); ${modelsTip(prov.models)}.`);
      tip.appendText('\n\nYour Claude subscription is not used until you switch back.');
      tip.appendText('\n\nClick to switch account or provider.');
    }
    item.tooltip = tip;
    return;
  }
  if (route.kind === 'custom') {
    item.text = '$(warning) Custom URL';
    tip.appendText(`Claude Code is pointed at ${route.baseUrl} outside this app, so it is not one of your saved providers. Your accounts are listed on click, but switching one does not undo that URL.`);
    item.tooltip = tip;
    return;
  }
  const active = state.profiles.find(inUse);
  if (active) {
    const soon = state.profiles.some((p) => !inUse(p) && (p.expired || (p.daysLeft !== null && p.daysLeft < 5)));
    const u = active.usage;
    const session = u && !u.stale && u.session && !u.session.over ? ` · ${u.session.pct}%` : '';
    item.text = `$(account) ${active.name}${session}${soon ? ' $(warning)' : ''}`;
    tip.appendText(`${active.name}\n${active.email} · ${active.detail}`);
    if (u) {
      tip.appendText(`\n\nUsage: ${u.text}`);
      if (u.resets) tip.appendText(`\n${u.resets}`);
      if (u.note) tip.appendText(`\n(${u.note})`);
    }
    tip.appendText('\n\nClick to switch account.');
    if (soon) tip.appendText('\n\nAnother saved account\'s sign-in is about to expire. Switch to it and use it once to renew.');
  } else if (state.unsaved) {
    item.text = '$(warning) Claude: save this login';
    item.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    item.command = 'claudeAccounts.saveCurrent';
    tip.appendText(`Signed in as ${state.unsaved.email} (${state.unsaved.detail}), but this login is not saved, so you could not switch back to it. Click to save.`);
  } else {
    item.text = '$(sign-in) Claude: not signed in';
    item.command = 'claudeAccounts.add';
    tip.appendText('Click to sign in.');
  }
  item.tooltip = tip;
}

async function refresh() {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await run(['state']);
      if (res.ok === false) throw new Error(res.error);
      state = res;
      if (!Array.isArray(state.providers)) state.providers = [];   // an engine from before providers
      if (!state.route) state.route = { kind: 'account' };
      problem = null;
      lastRefresh = Date.now();
    } catch (e) {
      problem = messageOf(e);
      log.appendLine(`[state] ${problem}`);
    } finally {
      inflight = null;
    }
    render();
    maybeOfferToSave();
  })();
  return inflight;
}

// ---------------------------------------------------------------- usage
// Asks the engine to update usage numbers where that is safe (it never refreshes a saved login to do so),
// then redraws. The engine rate-limits itself, so calling this often is harmless.
let lastUsage = 0;
async function refreshUsage(force) {
  if (!force && Date.now() - lastUsage < 120000) return;
  lastUsage = Date.now();
  try {
    await run(force ? ['usage', '--force'] : ['usage'], { timeout: 60000 });
  } catch (e) {
    log.appendLine(`[usage] ${messageOf(e)}`);
  }
  await refresh();
}

function usageLine(p) {
  const u = p.usage;
  if (!u) return null;
  return `$(graph) ${u.text}${u.resets ? `  —  ${u.resets}` : ''}${u.note ? `  (${u.note})` : ''}`;
}

// ---------------------------------------------------------------- actions
function suggestName() {
  const taken = new Set((state ? state.profiles : []).map((p) => p.name.toLowerCase()));
  const base = state && state.unsaved && state.unsaved.team ? 'Team' : 'Personal';
  let name = base;
  for (let i = 2; taken.has(name.toLowerCase()); i++) name = `${base} ${i}`;
  return name;
}

function maybeOfferToSave() {
  if (busy || !state || !state.unsaved) return;
  const key = `${state.unsaved.email}|${state.unsaved.detail}`;
  if (promptedUnsaved.has(key)) return;
  promptedUnsaved.add(key);
  vscode.window.showInformationMessage(
    `Your current Claude login (${state.unsaved.email} · ${state.unsaved.detail}) isn't saved yet, so you couldn't switch back to it.`,
    'Save it…').then((choice) => { if (choice) saveCurrent(); });
}

async function saveCurrent() {
  await refresh();
  if (!state || !state.unsaved) {
    vscode.window.showInformationMessage('The current login is already saved.');
    return;
  }
  const name = await vscode.window.showInputBox({
    title: 'Save this Claude login',
    prompt: `Signed in as ${state.unsaved.email} (${state.unsaved.detail}). Name it so you can switch back to it:`,
    value: suggestName(),
    validateInput: (v) => (v.trim() ? undefined : 'Enter a name'),
  });
  if (!name) return;
  try {
    const res = await run(['save', name.trim()]);
    if (!res.ok) throw new Error(res.error);
    vscode.window.setStatusBarMessage(`$(check) Saved "${res.name}"`, 4000);
  } catch (e) {
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  }
  await refresh();
}

async function switchTo(p) {
  if (busy || inUse(p)) return;
  busy = true;
  item.text = `$(sync~spin) Switching to ${p.name}…`;
  let left = null;
  try {
    const res = await run(['switch', p.id], { timeout: 40000 });
    if (!res.ok) throw new Error(res.error);
    await refresh();  // the status bar now shows the new account; the swap itself took milliseconds
    vscode.window.setStatusBarMessage(`$(check) Claude is now using ${p.name}`, 5000);
    if (res.autosaved) {
      vscode.window.showInformationMessage(`Your previous login was saved as "${res.autosaved}".`);
    }
    if (res.changed) confirmInBackground(p);
    left = res.leftProvider || null;
  } catch (e) {
    log.appendLine(`[switch] ${messageOf(e)}`);
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
    await refresh();
  } finally {
    busy = false;
  }
  if (left) {  // switching to an account also leaves the provider that was in use
    await settleNotice(`Switching to ${p.name}…`, SETTLE_SECONDS);
    vscode.window.showInformationMessage(`Left provider "${left}". Using ${p.name} from your next message.`);
  }
}

// Claude Code follows a settings change from its next message, about two seconds later; say so while that happens.
function settleNotice(title, seconds) {
  const ms = Math.min(Math.max(Number(seconds === undefined ? SETTLE_SECONDS : seconds) || 0, 0), 10) * 1000;
  if (!ms) return Promise.resolve();
  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title },
    () => new Promise((resolve) => setTimeout(resolve, ms)));
}

async function switchToProvider(prov) {
  if (busy || prov.active) return;
  busy = true;
  item.text = `$(sync~spin) Switching to ${prov.name}…`;
  let res = null;
  try {
    res = await run(['provider', 'use', prov.id], { timeout: 40000 });
    if (!res.ok) throw new Error(res.error);
    await refresh();  // the status bar now shows the provider
  } catch (e) {
    res = null;
    log.appendLine(`[provider] ${messageOf(e)}`);
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
    await refresh();
  } finally {
    busy = false;
  }
  if (!res) return;
  const name = res.name || prov.name;
  await settleNotice(`Switching to ${name}…`, res.settleSeconds);
  vscode.window.showInformationMessage(`Using ${name} from your next message.`);
}

// Asks the engine to check every model of a provider with a real request (it takes a few seconds), then summarises.
let testing = false;
async function testProvider() {
  if (testing) {
    vscode.window.showInformationMessage('A provider test is already running.');
    return;
  }
  const list = (state && state.providers) || [];
  if (!list.length) {
    vscode.window.showInformationMessage('No providers saved yet. Use "Add provider…" first.');
    return;
  }
  const pick = await vscode.window.showQuickPick(list.map((p) => ({
    label: `${p.active ? '$(check)' : '$(plug)'} ${p.name}`,
    description: p.host,
    detail: modelsDetail(p.models),
    provider: p,
  })), { title: 'Test provider', placeHolder: 'Which provider should be tested?', matchOnDescription: true });
  if (!pick) return;
  const prov = pick.provider;
  testing = true;
  try {
    // exit code 1 still carries the per-model results on stdout, which run() hands back as a normal answer
    const res = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: `Testing ${prov.name}: asking each model to use a tool…`,
    }, () => run(['provider', 'test', prov.id], { timeout: 10 * 60 * 1000 }));
    if (!Array.isArray(res.results) || !res.results.length) throw new Error(res.error || 'The test returned no results.');
    const rows = res.results.map((r) => {
      const tiers = (r.tiers || []).map((t) => (TIERS.find((x) => x[0] === t) || [t, t])[1]).join(', ');
      const who = `${r.model}${tiers ? ` (${tiers})` : ''}`;
      return r.ok ? `✓ ${who} ${Number(r.seconds).toFixed(1)}s` : `✗ ${who}: ${r.detail}`;
    });
    if (res.results.every((r) => r.ok)) {
      vscode.window.showInformationMessage(`Provider "${prov.name}" works. ${rows.join(' · ')}`);
    } else {
      vscode.window.showWarningMessage(`Provider "${prov.name}" has a problem. ${rows.join(' · ')}`);
    }
  } catch (e) {
    log.appendLine(`[provider test] ${messageOf(e)}`);
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  } finally {
    testing = false;
  }
}

// Providers are set up in the Claude Accounts window (address, key and model map need a proper form).
function addProvider() {
  vscode.window.setStatusBarMessage('Opening the Claude Accounts window: add the provider there.', 6000);
  openManager();
}

// The switch is already in effect. This only double-checks it stuck and that the claude tool agrees.
function confirmInBackground(p) {
  run(['settle', p.id], { timeout: 90000 }).then((r) => {
    if (r.ok === false) {
      vscode.window.showWarningMessage('A running Claude Code session keeps putting the old account back. Close its chats (or reload the window), then switch again.');
    } else if (r.verified === false) {
      vscode.window.showWarningMessage(`Switched to "${p.name}", but the claude command-line tool did not confirm it. Try switching again.`);
    }
    return refresh();
  }).catch((e) => log.appendLine(`[settle] ${messageOf(e)}`));
}

async function addAccount() {
  if (busy) return;
  busy = true;
  const cancelFile = path.join(os.tmpdir(), `claude-accounts-cancel-${process.pid}-${Date.now()}`);
  try {
    const res = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: 'Claude Accounts: finish signing in in your browser. Pick the account to add (use "Switch account" on the page if it shows the wrong one), then press Authorize.',
      cancellable: true,
    }, (_progress, token) => {
      token.onCancellationRequested(() => { try { fs.writeFileSync(cancelFile, ''); } catch (_) { /* ignore */ } });
      return run(['add', '--cancel-file', cancelFile], { timeout: 11 * 60 * 1000 });
    });
    await afterAdd(res);
  } catch (e) {
    log.appendLine(`[add] ${messageOf(e)}`);
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  } finally {
    try { fs.unlinkSync(cancelFile); } catch (_) { /* already gone */ }
    busy = false;
    await refresh();
  }
}

async function afterAdd(res) {
  switch (res.status) {
    case 'cancelled':
      vscode.window.showInformationMessage('Sign-in cancelled. Your previous account is still active.');
      break;
    case 'failed':
    case 'timeout':
      vscode.window.showWarningMessage(`Sign-in did not finish${res.detail ? ` (${res.detail})` : ''}. Your previous account is still active.`);
      break;
    case 'same':
      vscode.window.showWarningMessage(`That was the same account as "${res.name}". To add a different one, choose "Switch account" on the browser's sign-in page.`);
      break;
    case 'known':
      vscode.window.showInformationMessage(`Signed in as "${res.name}" (already saved, sign-in refreshed).`);
      break;
    case 'new': {
      // Already saved under a suggested name so nothing can be lost; offer a better one.
      const name = await vscode.window.showInputBox({
        title: 'Name this account',
        prompt: 'Signed in and saved. Name this account (for example Personal or Team):',
        value: res.name,
        validateInput: (v) => (v.trim() ? undefined : 'Enter a name'),
      });
      let finalName = res.name;
      if (name && name.trim() !== res.name) {
        try {
          const r = await run(['rename', res.id, name.trim()]);
          if (!r.ok) throw new Error(r.error);
          finalName = r.name;
        } catch (e) {
          vscode.window.showWarningMessage(`${messageOf(e)} Kept the name "${res.name}".`);
        }
      }
      vscode.window.showInformationMessage(`Added "${finalName}" and signed in with it. Open chats switch on their next message; if one doesn't, start a new chat.`);
      break;
    }
    default:
      vscode.window.showWarningMessage(`Unexpected sign-in result: ${res.status}`);
  }
}

async function renameProfile(p) {
  const name = await vscode.window.showInputBox({
    title: 'Rename account', value: p.name, validateInput: (v) => (v.trim() ? undefined : 'Enter a name'),
  });
  if (!name || name.trim() === p.name) return;
  try {
    const res = await run(['rename', p.id, name.trim()]);
    if (!res.ok) throw new Error(res.error);
  } catch (e) {
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  }
  await refresh();
}

async function removeProfile(p) {
  const choice = await vscode.window.showWarningMessage(
    `Remove "${p.name}" from this list?`,
    { modal: true, detail: p.active ? 'You stay signed in with it right now; only the saved copy is removed.' : 'You can add it back later by signing in again.' },
    'Remove');
  if (choice !== 'Remove') return;
  try {
    const res = await run(['remove', p.id]);
    if (!res.ok) throw new Error(res.error);
    vscode.window.setStatusBarMessage(`Removed "${p.name}"`, 4000);
  } catch (e) {
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  }
  await refresh();
}

async function runDoctor(quiet) {
  try {
    const res = await run(['doctor'], { timeout: 120000 });
    for (const c of res.checks) log.appendLine(`${c.ok ? 'ok  ' : 'FAIL'} ${c.name}: ${c.detail}`);
    if (!res.ok) {
      const bad = res.checks.filter((c) => !c.ok).map((c) => c.name).join(', ');
      const pick = await vscode.window.showWarningMessage(`Claude Accounts self-check found a problem: ${bad}. Claude Code may have changed how it stores logins.`, 'Show details');
      if (pick) log.show(true);
    } else if (!quiet) {
      vscode.window.showInformationMessage('Claude Accounts self-check: everything is fine.');
    }
  } catch (e) {
    log.appendLine(`[doctor] ${messageOf(e)}`);
    if (!quiet) vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  }
}

function openManager() {
  const py = pythonCmd();
  const pyw = py.replace(/python(\.exe)?$/i, 'pythonw$1');
  try {
    cp.spawn(pyw, [path.join(appDir(), 'claude_accounts.py')], { detached: true, stdio: 'ignore' }).unref();
  } catch (e) {
    vscode.window.showErrorMessage(`Claude Accounts: ${messageOf(e)}`);
  }
}

// ---------------------------------------------------------------- picker
async function showPicker() {
  refreshUsage(false);  // in the background; the numbers update the next time the list is opened
  await refresh();
  if (problem) {
    vscode.window.showErrorMessage(`Claude Accounts: ${problem}`);
    return;
  }
  const qp = vscode.window.createQuickPick();
  qp.title = 'Claude Accounts';
  qp.placeholder = state.providers.length ? 'Choose the account or provider Claude Code should use' : 'Choose the account Claude Code should use';
  qp.matchOnDescription = true;

  const rowButtons = [
    { iconPath: new vscode.ThemeIcon('edit'), tooltip: 'Rename' },
    { iconPath: new vscode.ThemeIcon('trash'), tooltip: 'Remove from list' },
  ];
  const items = [];
  if (state.unsaved) {
    items.push({ label: '$(save) Save current login…', description: `${state.unsaved.email} · ${state.unsaved.detail}`, action: 'save' });
    items.push({ label: '', kind: vscode.QuickPickItemKind.Separator });
  }
  for (const p of state.profiles) {
    let detail = usageLine(p) || undefined;
    if (p.expired || p.signInRejected) detail = '$(error) Sign-in expired. Use "Add account…" to sign in again.';
    else if (!inUse(p) && p.daysLeft !== null && p.daysLeft < 7) {
      const warn = `$(warning) Saved sign-in expires in ${Math.max(0, Math.floor(p.daysLeft))} day(s). Use it before then.`;
      detail = p.usage ? `${warn}   ${p.usage.text}` : warn;
    }
    items.push({
      label: `${inUse(p) ? '$(check)' : '$(account)'} ${p.name}`,
      description: `${p.email} · ${p.detail}${inUse(p) ? '  (in use)' : ''}`,
      detail,
      profile: p,
      buttons: rowButtons,
    });
  }
  if (state.providers.length) {
    items.push({ label: 'Providers', kind: vscode.QuickPickItemKind.Separator });
    for (const prov of state.providers) {
      const models = modelsDetail(prov.models);
      items.push({
        label: `${prov.active ? '$(check)' : '$(plug)'} ${prov.name}`,
        description: `${prov.host}${prov.active ? '  (in use)' : ''}`,
        detail: prov.keyOk === false ? `$(warning) No API key saved. ${models}` : models,
        provider: prov,
      });
    }
  }
  if (items.length) items.push({ label: '', kind: vscode.QuickPickItemKind.Separator });
  items.push({ label: '$(add) Add account…', action: 'add' });
  items.push({ label: '$(add) Add provider…', description: 'set up in the Claude Accounts window', action: 'addProvider' });
  if (state.providers.length) items.push({ label: '$(beaker) Test provider…', action: 'testProvider' });
  items.push({ label: '$(refresh) Refresh usage', action: 'usage' });
  items.push({ label: '$(window) Open manager window', action: 'manager' });
  items.push({ label: '$(pulse) Run self-check', action: 'doctor' });
  qp.items = items;
  const current = items.find((i) => (i.profile && inUse(i.profile)) || (i.provider && i.provider.active));
  if (current) qp.activeItems = [current];

  qp.onDidAccept(() => {
    const sel = qp.selectedItems[0];
    qp.hide();
    if (!sel) return;
    if (sel.profile) switchTo(sel.profile);
    else if (sel.provider) switchToProvider(sel.provider);
    else if (sel.action === 'add') addAccount();
    else if (sel.action === 'addProvider') addProvider();
    else if (sel.action === 'testProvider') testProvider();
    else if (sel.action === 'save') saveCurrent();
    else if (sel.action === 'usage') {
      vscode.window.setStatusBarMessage('$(sync~spin) Refreshing usage…', 4000);
      refreshUsage(true);
    } else if (sel.action === 'manager') openManager();
    else if (sel.action === 'doctor') runDoctor(false);
  });
  qp.onDidTriggerItemButton((e) => {
    qp.hide();
    if (e.button.tooltip === 'Rename') renameProfile(e.item.profile);
    else removeProfile(e.item.profile);
  });
  qp.onDidHide(() => qp.dispose());
  qp.show();
}

// ---------------------------------------------------------------- lifecycle
function activate(context) {
  item = vscode.window.createStatusBarItem('claudeAccounts.status', vscode.StatusBarAlignment.Right, 1000);
  item.name = 'Claude Accounts';
  item.show();
  render();

  context.subscriptions.push(
    item, log,
    vscode.commands.registerCommand('claudeAccounts.switch', showPicker),
    vscode.commands.registerCommand('claudeAccounts.add', addAccount),
    vscode.commands.registerCommand('claudeAccounts.saveCurrent', saveCurrent),
    vscode.commands.registerCommand('claudeAccounts.openManager', openManager),
    vscode.commands.registerCommand('claudeAccounts.doctor', () => runDoctor(false)),
    vscode.commands.registerCommand('claudeAccounts.showLog', () => log.show(true)),
    vscode.window.onDidChangeWindowState((w) => { if (w.focused) refresh(); }),
  );

  // Notice logins that change outside this window (another VS Code window, a terminal /login, the manager window).
  // A provider switch (from a terminal or the manager window) rewrites Claude Code's settings.json and the engine's
  // route.json, so those are watched too (editing the provider in use re-applies it through the same two files;
  // the list of other providers is read whenever the picker opens).
  const watched = [
    path.join(os.homedir(), '.claude.json'),
    path.join(os.homedir(), '.claude', '.credentials.json'),
    path.join(os.homedir(), '.claude', 'settings.json'),
    path.join(os.homedir(), '.claude-accounts', 'route.json'),
  ];
  for (const file of watched) {
    fs.watchFile(file, { interval: 2000, persistent: false }, () => {
      if (!busy && Date.now() - lastRefresh > 2500) refresh();
    });
  }
  const poll = setInterval(() => { if (!busy) refresh(); }, 60000);
  const usagePoll = setInterval(() => { if (!busy) refreshUsage(false); }, 5 * 60000);
  context.subscriptions.push({
    dispose() {
      clearInterval(poll);
      clearInterval(usagePoll);
      for (const file of watched) fs.unwatchFile(file);
    },
  });

  refresh().then(() => { refreshUsage(false); setTimeout(() => runDoctor(true), 4000); });
}

function deactivate() {}

module.exports = { activate, deactivate };
