import type { AnalyzeRequest, AnalyzeResponse, GeocodeResponse } from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
const REQUEST_TIMEOUT_MS = 90_000;

interface ApiErrorBody {
  error?: { code?: unknown; message?: unknown };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: "application/json", ...init?.headers },
    });
    const bodyText = await response.text();
    let body: unknown;
    try {
      body = bodyText ? JSON.parse(bodyText) : null;
    } catch {
      body = null;
    }
    if (!response.ok) {
      const error = body as ApiErrorBody | null;
      const message =
        typeof error?.error?.message === "string"
          ? error.error.message
          : bodyText || `Request failed with status ${response.status}`;
      throw new Error(message);
    }
    if (body === null) throw new Error("The server returned an empty or invalid JSON response.");
    return body as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("The request timed out. The server may still be starting; please try again.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function analyze(requestBody: AnalyzeRequest): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
  });
}

export function geocode(query: string): Promise<GeocodeResponse> {
  return request<GeocodeResponse>(`/api/geocode?q=${encodeURIComponent(query)}`);
}
