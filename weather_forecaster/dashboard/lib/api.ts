// Base URL for the FastAPI backend.
// In local Docker: http://localhost:8000
// In production: set NEXT_PUBLIC_API_URL to the ALB endpoint.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetcher<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}
