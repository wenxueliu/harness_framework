export const HARNESS_API = (import.meta.env.VITE_HARNESS_API as string) || 'http://127.0.0.1:8080'

export function apiUrl(path: string): string {
  return `${HARNESS_API}${path}`
}

export class HarnessApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code: string,
    public readonly requestId?: string, public readonly details: Record<string, unknown> = {}) {
    super(message); this.name = 'HarnessApiError'
  }
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init)
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) {
    const error = payload.error
    if (error && typeof error === 'object') {
      const structured = error as Record<string, unknown>
      throw new HarnessApiError(String(structured.message || `Harness API 请求失败 (${response.status})`), response.status,
        String(structured.code || 'HTTP_ERROR'), String(payload.request_id || response.headers.get('X-Request-ID') || ''),
        (structured.details as Record<string, unknown>) || {})
    }
    throw new HarnessApiError(typeof error === 'string' ? error : `Harness API 请求失败 (${response.status})`,
      response.status, 'LEGACY_HTTP_ERROR', String(payload.request_id || response.headers.get('X-Request-ID') || ''))
  }
  return payload as T
}

export function jsonRequest(method: string, body?: unknown, headers?: HeadersInit): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body) }
}
