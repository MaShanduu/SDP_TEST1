import { useEffect, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { CommitDetail, CommitsPage, Repo } from "../api/types";
import { ErrorBanner, Spinner } from "../components/ui";
import { formatDelta, formatInt, formatTs, shortSha } from "../lib/format";
import { useFilters } from "../state/filters";

const PAGE_SIZE = 50;

export default function CommitsTab({ repo }: { repo: Repo }) {
  const { path, commitSet, mode, shas, toggleSha } = useFilters();
  const [q, setQ] = useState("");
  const [order, setOrder] = useState("newest");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CommitsPage | null>(null);
  const [detail, setDetail] = useState<CommitDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setOffset(0);
  }, [q, order, path, commitSet]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(
      () => {
        api
          .commits(repo.id, {
            ...commitSet,
            path,
            q: q.trim() || undefined,
            order,
            limit: PAGE_SIZE,
            offset,
          })
          .then((next) => {
            if (cancelled) return;
            setPage(next);
            setError(null);
          })
          .catch((err) => {
            if (!cancelled) setError(errorMessage(err));
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      },
      q ? 220 : 0,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [repo.id, commitSet, path, q, order, offset]);

  async function openDetail(sha: string) {
    try {
      setDetail(await api.commitDetail(repo.id, sha));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const total = page?.total ?? 0;
  const selected = new Set(shas);

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner message={error} /> : null}

      <section className="card overflow-hidden">
        <header className="flex flex-wrap items-center gap-3 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-950">Commits in the commit set</h3>
          <span className="text-xs text-slate-500">
            {formatInt(total)} commit{total === 1 ? "" : "s"}
            {path ? ` touching ${path}` : ""}
            {loading ? " · loading…" : ""}
          </span>
          {mode === "list" ? (
            <span className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">
              {selected.size} toggleable into the picked set
            </span>
          ) : null}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <input
              className="input w-64"
              placeholder="Search subject or sha…"
              value={q}
              onChange={(event) => setQ(event.target.value)}
              spellCheck={false}
            />
            <select className="input py-1" value={order} onChange={(event) => setOrder(event.target.value)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </div>
        </header>

        {page === null ? (
          <div className="grid place-items-center py-10">
            <Spinner />
          </div>
        ) : page.rows.length === 0 ? (
          <p className="px-4 pb-6 text-xs text-slate-500">No commits match the current filters.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead className="border-y border-sky-200 bg-sky-50/80">
                  <tr>
                    {mode === "list" ? <th className="th w-8"> </th> : null}
                    <th className="th">Commit</th>
                    <th className="th">Date</th>
                    <th className="th">Author</th>
                    <th className="th text-right">Added</th>
                    <th className="th text-right">Removed</th>
                    <th className="th text-right">Churn</th>
                    <th className="th text-right">Files</th>
                  </tr>
                </thead>
                <tbody>
                  {page.rows.map((row) => (
                    <tr
                      key={row.sha}
                      className="cursor-pointer border-b border-sky-100 hover:bg-sky-50"
                      onClick={() => void openDetail(row.sha)}
                    >
                      {mode === "list" ? (
                        <td className="td" onClick={(event) => event.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selected.has(row.sha)}
                            onChange={() => toggleSha(row.sha)}
                            title="Add / remove this commit from the picked set"
                          />
                        </td>
                      ) : null}
                      <td className="td max-w-[34rem]">
                        <div className="flex items-center gap-2">
                          <span className="mono shrink-0 text-accent">{shortSha(row.sha)}</span>
                          <span className="truncate text-slate-800" title={row.subject}>
                            {row.subject || "(no subject)"}
                          </span>
                          {selected.has(row.sha) ? (
                            <span className="shrink-0 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] text-accent">
                              picked
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="td whitespace-nowrap text-slate-600">{formatTs(row.ct, true)}</td>
                      <td className="td text-slate-700">{row.author_name}</td>
                      <td
                        className="td text-right tabular-nums text-emerald-700"
                        title={`Whole commit: +${formatInt(row.total_added)} / -${formatInt(row.total_removed)} lines in ${formatInt(row.total_n_files)} files`}
                      >
                        {formatDelta(row.added)}
                      </td>
                      <td className="td text-right tabular-nums text-rose-700">{formatInt(row.removed)}</td>
                      <td className="td text-right tabular-nums">{formatInt(row.churn)}</td>
                      <td className="td text-right tabular-nums text-slate-600">{formatInt(row.n_files)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <footer className="flex items-center gap-2 border-t border-sky-200 px-4 py-2 text-xs text-slate-500">
              <span>
                {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {formatInt(total)}
              </span>
              {path ? (
                <span className="text-slate-600">line counts are scoped to {path}</span>
              ) : (
                <span className="text-slate-600">line counts cover the whole commit</span>
              )}
              <button
                type="button"
                className="btn-ghost ml-auto px-2 py-1 text-xs"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                ← Prev
              </button>
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-xs"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </footer>
          </>
        )}
      </section>

      {detail ? <DetailDrawer detail={detail} onClose={() => setDetail(null)} /> : null}
    </div>
  );
}

function DetailDrawer({ detail, onClose }: { detail: CommitDetail; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-xl flex-col border-l border-sky-200 bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="border-b border-sky-200 p-4">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="mono text-accent" title={detail.sha}>
                  {shortSha(detail.sha)}
                </span>
                <span className="text-xs text-slate-500">{formatTs(detail.ct, true)}</span>
              </div>
              <h3 className="mt-1 break-words text-sm text-slate-900">{detail.subject || "(no subject)"}</h3>
              <p className="mt-1 text-xs text-slate-500">
                {detail.author_name} &lt;{detail.author_email}&gt;
                {detail.identity_name && detail.identity_name !== detail.author_name ? (
                  <span className="text-slate-600">
                    {" "}
                    · committed as {detail.identity_name} &lt;{detail.identity_email}&gt;
                  </span>
                ) : null}
              </p>
              {detail.parent_sha ? (
                <p className="mono mt-1 text-xs text-slate-600">parent {shortSha(detail.parent_sha)}</p>
              ) : (
                <p className="mt-1 text-xs text-slate-600">initial commit</p>
              )}
            </div>
            <button type="button" className="btn-ghost" onClick={onClose}>
              Close
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-xs">
            <span className="text-emerald-700">+{formatInt(detail.added)}</span>
            <span className="text-rose-700">-{formatInt(detail.removed)}</span>
            <span className="text-slate-600">growth {formatDelta(detail.growth)}</span>
            <span className="text-slate-600">churn {formatInt(detail.churn)}</span>
            <span className="text-slate-600">
              {formatInt(detail.n_files)} file{detail.n_files === 1 ? "" : "s"}
            </span>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          {detail.files.length === 0 ? (
            <p className="p-4 text-xs text-slate-500">No measured changes (merge or empty commit).</p>
          ) : (
            <table className="w-full border-collapse">
              <thead className="border-b border-sky-200 bg-sky-50/80">
                <tr>
                  <th className="th">File</th>
                  <th className="th text-right">Added</th>
                  <th className="th text-right">Removed</th>
                </tr>
              </thead>
              <tbody>
                {detail.files.map((file) => (
                  <tr key={`${file.path}~${file.old_path ?? ""}`} className="border-b border-sky-100">
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <span className="mono truncate text-slate-800" title={file.path}>
                          {file.path}
                        </span>
                        {file.renamed && file.old_path ? (
                          <span className="shrink-0 text-[11px] text-amber-700" title={`Renamed from ${file.old_path}`}>
                            ← {file.old_path}
                          </span>
                        ) : null}
                        {file.is_binary ? (
                          <span className="shrink-0 rounded bg-sky-100 px-1.5 py-0.5 text-[11px] text-slate-500">
                            binary
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="td text-right tabular-nums text-emerald-700">
                      {file.is_binary ? "–" : formatDelta(file.added)}
                    </td>
                    <td className="td text-right tabular-nums text-rose-700">
                      {file.is_binary ? "–" : formatInt(file.removed)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </aside>
    </div>
  );
}
