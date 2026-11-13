// One backend push connection shared by queue, MCP activity and character views.
export function initUIEvents() {
  const stream = new EventSource('/api/ui/events');
  stream.addEventListener('connected', () => {
    window.dispatchEvent(new CustomEvent('flowhmr:server-update', { detail: { kind: 'resync' } }));
  });
  stream.addEventListener('update', event => {
    window.dispatchEvent(new CustomEvent('flowhmr:server-update', { detail: JSON.parse(event.data) }));
  });
  stream.onerror = () => window.dispatchEvent(new Event('flowhmr:sync-disconnected'));
  window.addEventListener('pagehide', () => stream.close());
}
