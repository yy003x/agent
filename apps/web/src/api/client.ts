const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = data.detail ?? data.error ?? `HTTP ${response.status}`;
    throw new Error(Array.isArray(detail) ? JSON.stringify(detail) : String(detail));
  }
  return data as T;
}
