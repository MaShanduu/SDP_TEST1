import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Author, PathEntry, Repo } from "../api/types";
import type { CommitSetQuery } from "../api/client";
import { useFilters, type CommitMode } from "../state/filters";
import CommitPicker from "./CommitPicker";
import { Spinner } from "./ui";
import { formatInt } from "../lib/format";

/**
 * Global filter bar (rubric "Filtering"): repository scope (path), author
 * selection and the commit set (all history / date range / picked commits /
 * reference commit).  Every metric endpoint receives these as query params.
 */
export default function FilterBar({ repo, authors }: { repo: Repo; authors: Author[] }) {
  const { activeCount, reset, mode, shas, commitSet } = useFilters();
  const [picking, setPicking] = useState(false);

  return (
    <div className="card flex flex-wrap items-center gap-2 p-3">
      <PathPicker repoId={repo.id} />
      <AuthorFilter authors={authors} />
      <ModeTabs />
      {mode === "range" ? <RangeControls /> : null}
      {mode === "ref" ? <RefControl /> : null}
      {mode === "list" ? (
        <button type="button" className="btn-ghost" onClick={() => setPicking(true)}>
          {shas.length > 0
            ? `${formatInt(shas.length)} commit${shas.length === 1 ? "" : "s"} selected`
            : "Pick commits…"}
        </button>
      ) : null}
      <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
        <span title="Current commit set sent to every metric endpoint">{describeCommitSet(commitSet)}</span>
        {activeCount > 0 ? (
          <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={reset}>
            Reset filters
          </button>
        ) : null}
      </div>
      {picking ? <CommitPicker repoId={repo.id} onClose={() => setPicking(false)} /> : null}
    </div>
  );
}

function describeCommitSet(cs: CommitSetQuery): string {
  const parts: string[] = [];
  if (cs.commits && cs.commits.length > 0) parts.push(`${cs.commits.length} picked`);
  else if (cs.since || cs.until) parts.push("date range");
  else if (cs.ref) parts.push(`ref ${cs.ref}`);
  else parts.push("all history");
  if (cs.authors && cs.authors.length > 0) {
    parts.push(`${cs.authors.length} author${cs.authors.length === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function PathPicker({ repoId }: { repoId: number }) {
  const { path, setPath } = useFilters();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<PathEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const blurTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .paths(repoId, query.trim(), 12)
        .then(setOptions)
        .catch(() => setOptions([]))
        .finally(() => setLoading(false));
    }, 160);
    return () => window.clearTimeout(timer);
  }, [open, query, repoId]);

  function pick(next: string) {
    setPath(next);
    setOpen(false);
  }

  return (
    <div className="relative">
      <input
        className="input w-72"
        placeholder="Repository root — filter by file or directory"
        value={open ? query : path}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onChange={(event) => setQuery(event.target.value)}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => setOpen(false), 150);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter" && options[0]) pick(options[0].path);
        }}
        spellCheck={false}
        title={path || "All files (repository scope)"}
      />
      {path ? (
        <button
          type="button"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-1 text-sm text-slate-500 hover:text-slate-900"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setPath("")}
          title="Clear scope"
        >
          ×
        </button>
      ) : null}
      {open ? (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-80 w-[28rem] max-w-[80vw] overflow-auto rounded-lg border border-sky-200 bg-white shadow-xl">
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 hover:bg-sky-100"
            onMouseDown={(event) => {
              event.preventDefault();
              window.clearTimeout(blurTimer.current);
              pick("");
            }}
          >
            <span className="mono shrink-0 text-slate-600">repo</span>
            <span>Repository root (all files)</span>
          </button>
          {loading && options.length === 0 ? (
            <div className="flex items-center gap-2 px-3 py-2 text-xs text-slate-500">
              <Spinner className="h-3 w-3" /> searching…
            </div>
          ) : null}
          {options.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-sky-100"
              onMouseDown={(event) => {
                event.preventDefault();
                window.clearTimeout(blurTimer.current);
                pick(entry.path);
              }}
              title={entry.path}
            >
              <span className="mono shrink-0 text-slate-600">{entry.kind}</span>
              <span className="truncate text-slate-700">{entry.path}</span>
            </button>
          ))}
          {!loading && options.length === 0 ? (
            <div className="px-3 py-2 text-xs text-slate-600">No path matches.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function AuthorFilter({ authors }: { authors: Author[] }) {
  const { authorIds, toggleAuthor, clearAuthors } = useFilters();
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => new Set(authorIds), [authorIds]);
  const label =
    authorIds.length === 0 ? "All authors" : `${authorIds.length} author${authorIds.length === 1 ? "" : "s"}`;

  return (
    <div className="relative">
      <button type="button" className={authorIds.length > 0 ? "btn-primary" : "btn-ghost"} onClick={() => setOpen((v) => !v)}>
        {label}
        <span className="text-xs opacity-70">▾</span>
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-30 mt-1 max-h-80 w-72 overflow-auto rounded-lg border border-sky-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-sky-200 px-3 py-2 text-xs text-slate-500">
              <span>Filter by author</span>
              <button type="button" className="hover:text-slate-900" onClick={clearAuthors}>
                Clear
              </button>
            </div>
            {authors.length === 0 ? (
              <div className="px-3 py-2 text-xs text-slate-600">No authors loaded.</div>
            ) : null}
            {authors.map((author) => (
              <label
                key={author.id}
                className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-sky-100"
                title={author.email}
              >
                <input
                  type="checkbox"
                  checked={selected.has(author.id)}
                  onChange={() => toggleAuthor(author.id)}
                />
                <span className="min-w-0 flex-1 truncate text-slate-700">{author.name}</span>
                <span className="tabular-nums text-slate-600">{formatInt(author.n_commits)}</span>
              </label>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

const MODE_LABELS: Array<{ key: CommitMode; label: string }> = [
  { key: "all", label: "All history" },
  { key: "range", label: "Date range" },
  { key: "list", label: "Pick commits" },
  { key: "ref", label: "Reference" },
];

function ModeTabs() {
  const { mode, setMode } = useFilters();
  return (
    <div className="flex rounded-lg bg-sky-100 p-0.5">
      {MODE_LABELS.map(({ key, label }) => (
        <button
          key={key}
          type="button"
          className={mode === key ? "tab tab-active" : "tab"}
          onClick={() => setMode(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function RangeControls() {
  const { since, until, setSince, setUntil } = useFilters();
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <label className="flex items-center gap-1">
        from
        <input
          type="datetime-local"
          className="input"
          value={since}
          onChange={(event) => setSince(event.target.value)}
          onBlur={(event) => setSince(event.target.value)}
        />
      </label>
      <label className="flex items-center gap-1">
        until
        <input
          type="datetime-local"
          className="input"
          value={until}
          onChange={(event) => setUntil(event.target.value)}
          onBlur={(event) => setUntil(event.target.value)}
        />
      </label>
      <span className="text-slate-600" title="Committer date, inclusive start and exclusive end (interpreted as UTC)">
        [from, until)
      </span>
    </div>
  );
}

function RefControl() {
  const { ref, setRef } = useFilters();
  return (
    <label className="flex items-center gap-1 text-xs text-slate-500">
      ref
      <input
        className="input w-52"
        placeholder="branch, tag or sha (default HEAD)"
        value={ref}
        onChange={(event) => setRef(event.target.value)}
        spellCheck={false}
      />
    </label>
  );
}
