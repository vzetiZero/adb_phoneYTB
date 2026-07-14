const API_BASE = ""

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export function createLogSocket(onMessage: (msg: string) => void): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const host = window.location.host
  const ws = new WebSocket(`${proto}//${host}/ws/logs`)
  ws.onmessage = (e) => onMessage(e.data)
  ws.onerror = () => {}
  ws.onclose = () => {}
  return ws
}
