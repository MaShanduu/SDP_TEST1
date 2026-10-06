import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import type { CommitSetQuery } from "../api/client";

export type CommitMode = "all" | "range" | "list" | "ref";

interface FiltersValue {
  path: string;
  authorIds: number[];
  mode: CommitMode;
  ref: string;
  since: string;
  until: string;
  shas: string[];
  activeCount: number;
  commitSet: CommitSetQuery;
  setPath(path: string): void;
  toggleAuthor(id: number): void;
  clearAuthors(): void;
  setMode(mode: CommitMode): void;
  setRef(ref: string): void;
  setSince(value: string): void;
  setUntil(value: string): void;
  toggleSha(sha: string): void;
  setShas(shas: string[]): void;
  reset(): void;
}

const FiltersContext = createContext<FiltersValue | null>(null);

const MODES: CommitMode[] = ["all", "range", "list", "ref"];

function parseIds(raw: string | null): number[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((part) => Number.parseInt(part.trim(), 10))
    .filter((n) => Number.isFinite(n));
}

function parseShas(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Filter state lives in the URL query string so every view is shareable and
 * survives reloads.  Server-side filtering: the derived `commitSet` is sent to
 * every metric endpoint (repository / author / path / commit-set filters).
 */
export function FilterProvider({ children }: { children: ReactNode }) {
  const [params, setParams] = useSearchParams();

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const rawMode = params.get("mode") as CommitMode | null;
  const mode: CommitMode = rawMode && MODES.includes(rawMode) ? rawMode : "all";
  const path = params.get("path") ?? "";
  const authorIds = useMemo(() => parseIds(params.get("authors")), [params]);
  const ref = params.get("ref") ?? "";
  const since = params.get("since") ?? "";
  const until = params.get("until") ?? "";
  const shas = useMemo(() => parseShas(params.get("shas")), [params]);

  const commitSet = useMemo<CommitSetQuery>(() => {
    const cs: CommitSetQuery = {};
    if (mode === "list" && shas.length > 0) cs.commits = shas;
    if (mode === "range") {
      if (since) cs.since = since;
      if (until) cs.until = until;
    }
    if (mode === "ref" && ref.trim()) cs.ref = ref.trim();
    if (authorIds.length > 0) cs.authors = authorIds;
    return cs;
  }, [mode, shas, since, until, ref, authorIds]);

  const value = useMemo<FiltersValue>(() => {
    const activeCount =
      (path ? 1 : 0) +
      (authorIds.length > 0 ? 1 : 0) +
      (mode !== "all" && (mode === "list" ? shas.length > 0 : true) ? 1 : 0);

    return {
      path,
      authorIds,
      mode,
      ref,
      since,
      until,
      shas,
      activeCount,
      commitSet,
      setPath: (next) => update({ path: next }),
      toggleAuthor: (id) => {
        const set = new Set(authorIds);
        if (set.has(id)) set.delete(id);
        else set.add(id);
        update({ authors: [...set].sort((a, b) => a - b).join(",") || null });
      },
      clearAuthors: () => update({ authors: null }),
      setMode: (next) => update({ mode: next === "all" ? null : next }),
      setRef: (next) => update({ ref: next }),
      setSince: (next) => update({ since: next }),
      setUntil: (next) => update({ until: next }),
      toggleSha: (sha) => {
        const set = new Set(shas);
        if (set.has(sha)) set.delete(sha);
        else set.add(sha);
        update({ shas: [...set].join(",") || null });
      },
      setShas: (next) => update({ shas: next.join(",") || null }),
      reset: () =>
        update({ path: null, authors: null, mode: null, ref: null, since: null, until: null, shas: null }),
    };
  }, [path, authorIds, mode, ref, since, until, shas, commitSet, update]);

  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>;
}

export function useFilters(): FiltersValue {
  const ctx = useContext(FiltersContext);
  if (!ctx) throw new Error("useFilters must be used inside <FilterProvider>");
  return ctx;
}
