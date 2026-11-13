// Keep job polling available when a remote page cannot use the local event stream.
export function initUIEvents() {
  const disconnected = () => window.dispatchEvent(new Event('flowhmr:sync-disconnected'));
  if (typeof EventSource === 'undefined' || !['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)) {
    disconnected();
    return;
  }
  const stream = new EventSource('/api/ui/events');
  let connected = false;
  stream.addEventListener('connected', () => {
    connected = true;
    window.dispatchEvent(new CustomEvent('flowhmr:server-update', { detail: { kind: 'resync' } }));
  });
  stream.addEventListener('update', event => {
    window.dispatchEvent(new CustomEvent('flowhmr:server-update', { detail: JSON.parse(event.data) }));
  });
  stream.onerror = () => { connected = false; disconnected(); };
  const timeout = setTimeout(() => { if (!connected) disconnected(); }, 3000);
  window.addEventListener('pagehide', () => { clearTimeout(timeout); stream.close(); });
}
