/**
 * Typed API client.
 *
 * The access token is held in memory only. Putting it in localStorage would
 * make it reachable from any script on the page; the refresh token lives in an
 * httpOnly cookie the browser sends automatically, and a 401 triggers a silent
 * refresh followed by one retry.
 */

import type {
  DocumentSummary,
  EgressReport,
  LoginResponse,
  ModelsResponse,
  PageBlocks,
  Principal,
  ReadinessReport,
  SearchResponse,
} from "@/lib/types";

const BASE = "/api/v1";

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const problem = await response.json();
    return new ApiError(
      problem.detail ?? `Request failed (${response.status})`,
      response.status,
      problem.code,
    );
  } catch {
    return new ApiError(`Request failed (${response.status})`, response.status);
  }
}

/**
 * Refresh the access token, coalescing concurrent attempts.
 *
 * Several requests can 401 at once when a token expires; without this they
 * would each rotate the refresh token, and rotation is single-use — the second
 * one would look like a replayed token and revoke the whole session.
 */
async function refresh(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) return false;
      const data: LoginResponse = await response.json();
      accessToken = data.access_token;
      return true;
    } catch {
      return false;
    } finally {
      // Cleared on the next tick so callers awaiting this promise all observe
      // the same result before a fresh attempt becomes possible.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  });

  if (response.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refresh()) return request<T>(path, init, false);
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return response.json();
}

// --- auth -------------------------------------------------------------------

export const api = {
  async login(username: string, password: string): Promise<LoginResponse> {
    const data = await request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    accessToken = data.access_token;
    return data;
  },

  async logout(): Promise<void> {
    try {
      await request("/auth/logout", { method: "POST" });
    } finally {
      accessToken = null;
    }
  },

  me: () => request<Principal>("/auth/me"),

  /** Restore a session from the refresh cookie on a page load. */
  async restore(): Promise<Principal | null> {
    if (!(await refresh())) return null;
    try {
      return await api.me();
    } catch {
      return null;
    }
  },

  // --- documents ------------------------------------------------------------

  documents: (params: { doc_type?: string; search?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.doc_type) query.set("doc_type", params.doc_type);
    if (params.search) query.set("search", params.search);
    query.set("limit", String(params.limit ?? 100));
    return request<DocumentSummary[]>(`/documents?${query}`);
  },

  document: (id: string) => request<DocumentSummary>(`/documents/${id}`),

  pageBlocks: (id: string, page: number) =>
    request<PageBlocks>(`/documents/${id}/pages/${page}/blocks`),

  pageImageUrl: (id: string, page: number) => `${BASE}/documents/${id}/pages/${page}/image`,

  documentChunks: (id: string) =>
    request<
      Array<{
        chunk_id: string;
        parent_id: string | null;
        ordinal: number;
        text: string;
        token_count: number;
        page_from: number;
        page_to: number;
        section_path: string[];
        mean_confidence: number;
        indexed: boolean;
      }>
    >(`/documents/${id}/chunks`),

  // --- search ---------------------------------------------------------------

  search: (query: string, k = 8) =>
    request<SearchResponse>("/search", {
      method: "POST",
      body: JSON.stringify({ query, k }),
    }),

  // --- system ---------------------------------------------------------------

  models: () => request<ModelsResponse>("/models"),
  readiness: () => request<ReadinessReport>("/health/ready"),
  egress: () => request<EgressReport>("/health/egress"),
  routingStats: () =>
    request<{
      decisions: number;
      by_lane: Record<string, number>;
      by_stage: Record<string, number>;
      stage0_hit_rate: number;
      decide_p50_ms: number;
      decide_p95_ms: number;
    }>("/models/routing-stats"),
};

/**
 * The page image is served by an authenticated endpoint, so an <img src> cannot
 * fetch it — the browser will not attach the bearer token. It is fetched here
 * and handed back as an object URL.
 */
export async function fetchPageImage(id: string, page: number): Promise<string> {
  const response = await fetch(api.pageImageUrl(id, page), {
    credentials: "include",
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  if (!response.ok) throw await parseError(response);
  return URL.createObjectURL(await response.blob());
}
