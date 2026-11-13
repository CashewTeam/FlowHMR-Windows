import { $, show, el } from './utils.js';
import { message as m, setText, t } from './i18n.js';

export function initMcpDialog() {
  const dialog = $('mcpDialog'), status = $('mcpStatus'), port = $('mcpPort');
  const toggle = $('mcpToggle'), test = $('mcpTest'), reset = $('mcpReset');
  const copy = $('mcpCopy'), token = $('mcpToken'), reveal = $('mcpReveal');
  const auth = $('mcpAuth');
  const autoStart = $('mcpAutoStart');
  let state = null, busy = false;
  let activityStamp = '', activityBusy = false;

  async function refreshActivity() {
    if (activityBusy) { activityPending = true; return; }
    activityBusy = true;
    try {
      const response = await fetch('/api/mcp/activity', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const stamp = JSON.stringify(data);
      if (stamp === activityStamp) return;
      activityStamp = stamp;
      const exportIds = new Set(data.exports.map(item => item.export_id));
      const allRows = [...data.operations.filter(row => !(row.tool === 'flowhmr_export_motion'
        && exportIds.has(row.details?.export_id))), ...data.exports.map(item => ({ ...item,
        tool: 'flowhmr_export_motion', state: item.status,
        created: Date.parse(item.created) / 1000, details: { path: item.path, job_id: item.job_id },
      }))].sort((a, b) => b.created - a.created);
      const rows = allRows.slice(0, 20);
      const label = row => {
        const state = row.state === 'done' && row.details?.status ? row.details.status : row.state;
        return `${row.tool} · ${t(`job.${state}`, {}, state)}`;
      };
      $('mcpActivityList').replaceChildren(...rows.map(row => {
        const item = el('div', 'mcp-activity-item');
        item.append(el('strong', '', label(row)));
        const details = row.error ? (typeof row.error === 'string' ? row.error : row.error.message)
          : [row.details?.job_id, row.details?.path, row.details?.export_id].filter(Boolean).join(' · ');
        if (details) item.append(el('div', '', details));
        return item;
      }));
      if (!rows.length) setText($('mcpActivityList'), m('mcp.no_activity'));
      const latest = allRows.find(row => !['flowhmr_get_status', 'flowhmr_get_parameter_schema',
        'flowhmr_list_jobs', 'flowhmr_get_job', 'flowhmr_get_export',
        'flowhmr_list_characters', 'flowhmr_get_character_pose'].includes(row.tool));
      setText($('mcpActivityStatus'), latest ? label(latest) : '');
      $('mcpActivityStatus').title = latest?.details?.path || latest?.details?.job_id || '';
    } catch (error) {
      if (dialog.open) setText($('mcpActivityList'), m('mcp.activity_failed', { detail: error.message }));
    } finally {
      activityBusy = false;
      if (activityPending) { activityPending = false; void refreshActivity(); }
    }
  }
  let activityPending = false;
  void refreshActivity();
  window.addEventListener('flowhmr:server-update', event => {
    if (['activity', 'exports', 'resync'].includes(event.detail.kind)) void refreshActivity();
    if (dialog.open && !busy && ['mcp_service', 'resync'].includes(event.detail.kind)) {
      request().then(update).catch(error => setText(status, error.message));
    }
  });
  window.addEventListener('flowhmr:sync-disconnected', () =>
    setText($('mcpActivityStatus'), m('connection.reconnecting')));

  async function request(body) {
    const response = await fetch('/api/mcp', body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    } : {});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  function render() {
    const running = !!state?.running;
    port.disabled = busy || running || !state;
    toggle.disabled = busy || !state;
    test.disabled = busy || !running;
    reset.disabled = busy || running || !state;
    auth.disabled = busy || running || !state;
    autoStart.disabled = busy || !state;
    show($('mcpTokenRow'), !!state?.auth_enabled);
    show($('mcpRevealRow'), !!state?.auth_enabled);
    show(reset, !!state?.auth_enabled);
    copy.disabled = busy || !state;
    setText(toggle, m(running ? 'mcp.stop' : 'mcp.start'));
  }

  function update(next) {
    state = next;
    port.value = state.port;
    $('mcpUrl').value = state.url;
    token.value = state.token;
    auth.checked = state.auth_enabled;
    autoStart.checked = state.auto_start;
    if (!state.auth_enabled) { token.type = 'password'; reveal.checked = false; }
    setText(status, state.startup_error || m(state.running ? 'mcp.running' : 'mcp.stopped'));
    render();
  }

  async function transact(action) {
    busy = true;
    render();
    setText(status, m('mcp.working'));
    try { await action(); }
    catch (error) { setText(status, error.message); }
    finally { busy = false; render(); }
  }

  $('btnMcp').addEventListener('click', () => {
    dialog.showModal();
    void refreshActivity();
    token.type = 'password';
    reveal.checked = false;
    transact(async () => update(await request()));
  });
  dialog.addEventListener('close', () => {
    token.type = 'password';
    reveal.checked = false;
  });
  reveal.addEventListener('change', () => { token.type = reveal.checked ? 'text' : 'password'; });
  auth.addEventListener('change', () => transact(async () => {
    try { update(await request({ action: 'configure_auth', auth_enabled: auth.checked })); }
    finally { auth.checked = !!state?.auth_enabled; }
  }));
  autoStart.addEventListener('change', () => transact(async () => {
    try { update(await request({ action: 'configure_auto_start', auto_start: autoStart.checked })); }
    finally { autoStart.checked = !!state?.auto_start; }
  }));
  toggle.addEventListener('click', () => transact(async () => {
    if (!state.running && !port.reportValidity()) throw new Error('Invalid port');
    update(await request(state.running ? { action: 'stop' } : { action: 'start', port: Number(port.value) }));
  }));
  reset.addEventListener('click', () => transact(async () => update(await request({ action: 'reset_token' }))));
  test.addEventListener('click', () => transact(async () => {
    const result = await request({ action: 'test' });
    setText(status, m('mcp.connected', { server: result.server, protocol: result.protocol_version }));
  }));
  copy.addEventListener('click', () => transact(async () => {
    const config = { url: state.url };
    if (state.auth_enabled) config.headers = { Authorization: `Bearer ${state.token}` };
    await navigator.clipboard.writeText(JSON.stringify(config, null, 2));
    setText(status, m('mcp.copied'));
  }));
  render();
}
