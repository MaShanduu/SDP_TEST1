import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { CommitRow } from "../api/types";
import { formatTs, shortSha } from "../lib/format";
import { useFilters } from "../state/filters";
import { Spinner } from "./ui";

/**
 * Modal for the "manually selected list of commits" commit-set mode.
 * Selection is stored in the URL (filter state) and posted as `commits=<csv>`.
 */
export default function CommitPicker({ repoId, onClose }: { repoId: number; onClose: () => void }) {
  const { shas, toggleSha, setShas } = useFilters();
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<CommitRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const selected = useMemo(() => new Set(shas), [shas]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setRows(null);
      api
        .commits(repoId, { q: q.trim() || undefined, limit: 100, order: "newest" })
        .then((page) => {
          if (cancelled) return;
          setRows(page.rows);
          setTotal(page.total);
          setError(null);
        })
        .catch((err) => {
          if (cancelled) return;
          setRows([]);
          setError(errorMessage(err));
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [q, repoId]);

  const shown = rows ?? [];
  const allShownSelected = shown.length > 0 && shown.every((row) => selected.has(row.sha));

  function toggleShown() {
    if (allShownSelected) {
      const shownSet = new Set(shown.map((row) => row.sha));
      setShas(shas.filter((sha) => !shownSet.has(sha)));
    } else {
      setShas([...new Set([...shas, ...shown.map((row) => row.sha)])]);
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="card flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-sky-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-950">Pick commits</h3>
          <span className="text-xs text-slate-500">{selected.size} selected</span>
          <button type="button" className="btn-primary ml-auto" onClick={onClose}>
            Done
          </button>
        </header>
        <div className="flex flex-wrap items-center gap-2 border-b border-sky-200 px-4 py-2">
          <input
            autoFocus
            className="input min-w-40 flex-1"
            placeholder="Search subject or sha…"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            spellCheck={false}
          />
          <button type="button" className="btn-ghost" disabled={shown.length === 0} onClick={toggleShown}>
            {allShownSelected ? "Unselect shown" : "Select shown"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={shas.length === 0}
            onClick={() => setShas([])}
          >
            Clear all
          </button>
        </div>
        {error ? <p className="px-4 py-2 text-xs text-rose-700">{error}</p> : null}
        <div className="min-h-0 flex-1 overflow-auto">
          {rows === null ? (
            <div className="grid place-items-center py-10">
              <Spinner />
            </div>
          ) : shown.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-slate-500">No commits match.</p>
          ) : (
            shown.map((row) => (
              <label
                key={row.sha}
                className="flex cursor-pointer items-center gap-3 border-b border-sky-100 px-4 py-2 text-xs hover:bg-sky-50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(row.sha)}
                  onChange={() => toggleSha(row.sha)}
                />
                <span className="mono shrink-0 text-slate-500">{shortSha(row.sha)}</span>
                <span className="shrink-0 text-slate-500">{formatTs(row.ct)}</span>
                <span className="hidden shrink-0 text-slate-600 sm:inline">{row.author_name}</span>
                <span className="min-w-0 flex-1 truncate text-slate-700" title={row.subject}>
                  {row.subject}
                </span>
              </label>
            ))
          )}
        </div>
        <footer className="border-t border-sky-200 px-4 py-2 text-xs text-slate-600">
          Showing {shown.length} of {total} commits (newest first).
          {shas.length === 0
            ? " Nothing selected yet — the full history is used until you pick commits."
            : ` ${shas.length} commit${shas.length === 1 ? "" : "s"} in the commit set.`}
        </footer>
      </div>
    </div>
  );
}
