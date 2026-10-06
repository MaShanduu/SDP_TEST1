import type {
  Author,
  CommitsPage,
  CommitDetail,
  Contributor,
  FileRows,
  Job,
  MergeSuggestion,
  MetricsBundle,
  PathEntry,
  Repo,
  RepoCreated,
  SeriesResponse,
  TreeResponse,
} from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

type QueryValue = string | number | boolean | undefined | null | Array<string | number>;

function qs(params?: object): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as Array<[string, QueryValue]>) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      sp.set(key, value.join(","));
    } else {
      sp.set(key, String(value));
    }
  }
  const encoded = sp.toString();
  return encoded ? `?${encoded}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the RAT API. Is the backend running on :8000?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** The commit-set filter shared by all metric endpoints (spec section 2). */
export interface CommitSetQuery {
  ref?: string;
  since?: string | number;
  until?: string | number;
  commits?: string[];
  authors?: number[];
}

export interface MetricsParams extends CommitSetQuery {
  path?: string;
}

export const api = {
  // -- repositories ----------------------------------------------------
  listRepos: () => request<Repo[]>("/repos"),
  getRepo: (repoId: number) => request<Repo>(`/repos/${repoId}`),
  createRepoFromUrl: (url: string, name?: string) =>
    request<RepoCreated>("/repos/url", json("POST", { url, name: name || null })),
  createRepoFromZip: (file: File, name?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    return request<RepoCreated>("/repos/zip", { method: "POST", body: form });
  },
  renameRepo: (repoId: number, name: string) =>
    request<Repo>(`/repos/${repoId}`, json("PATCH", { name })),
  deleteRepo: (repoId: number) =>
    request<void>(`/repos/${repoId}`, { method: "DELETE" }),
  job: (jobId: number) => request<Job>(`/jobs/${jobId}`),

  // -- metrics ---------------------------------------------------------
  metrics: (repoId: number, params?: MetricsParams) =>
    request<MetricsBundle>(`/repos/${repoId}/metrics${qs(params)}`),
  series: (repoId: number, params?: MetricsParams & { bucket?: string }) =>
    request<SeriesResponse>(`/repos/${repoId}/series${qs(params)}`),
  contributors: (repoId: number, params?: MetricsParams) =>
    request<Contributor[]>(`/repos/${repoId}/contributors${qs(params)}`),
  files: (
    repoId: number,
    params?: MetricsParams & { sort?: string; limit?: number; offset?: number },
  ) => request<FileRows>(`/repos/${repoId}/files${qs(params)}`),
  tree: (repoId: number, params?: MetricsParams) =>
    request<TreeResponse>(`/repos/${repoId}/tree${qs(params)}`),
  paths: (repoId: number, q?: string, limit?: number) =>
    request<PathEntry[]>(`/repos/${repoId}/paths${qs({ q, limit })}`),
  commits: (
    repoId: number,
    params?: MetricsParams & { q?: string; order?: string; limit?: number; offset?: number },
  ) => request<CommitsPage>(`/repos/${repoId}/commits${qs(params)}`),
  commitDetail: (repoId: number, sha: string) =>
    request<CommitDetail>(`/repos/${repoId}/commits/${sha}`),

  // -- authors ---------------------------------------------------------
  authors: (repoId: number) => request<Author[]>(`/repos/${repoId}/authors`),
  authorSuggestions: (repoId: number) =>
    request<MergeSuggestion[]>(`/repos/${repoId}/authors/suggestions`),
  mergeAuthors: (repoId: number, targetId: number, sourceIds: number[]) =>
    request<{ merged: number; target_id: number }>(
      `/repos/${repoId}/authors/merge`,
      json("POST", { target_id: targetId, source_ids: sourceIds }),
    ),
  detachAuthor: (repoId: number, identityId: number) =>
    request<{ detached: number }>(`/repos/${repoId}/authors/${identityId}/detach`, {
      method: "POST",
    }),
  patchAuthor: (repoId: number, authorId: number, patch: { name?: string; email?: string }) =>
    request<{ updated: number }>(`/repos/${repoId}/authors/${authorId}`, json("PATCH", patch)),
};
