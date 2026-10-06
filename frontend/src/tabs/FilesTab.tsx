import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { FileRows, MetricsBundle, Repo, TreeResponse } from "../api/types";
import { ErrorBanner, OwnersBar, Spinner, StatCard } from "../components/ui";
import { formatDelta, formatInt, formatPercent } from "../lib/format";
import { useFilters } from "../state/filters";

const SORTS = [
  { key: "churn", label: "Churn" },
  { key: "added", label: "Added" },
  { key: "removed", label: "Removed" },
  { key: "mods", label: "Modifications" },
  { key: "commits", label: "Commits" },
  { key: "path", label: "Path" },
] as const;

const PAGE_SIZE = 50;

export default function FilesTab({ repo }: { repo: Repo }) {
  const { path, setPath, commitSet } = useFilters();
  const [scope, setScope] = useState<MetricsBundle | null>(null);
  const [tree, setTree] = useState<TreeResponse | null>(null);
  const [files, setFiles] = useState<FileRows | null>(null);
  const [sort, setSort] = useState<string>("churn");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setOffset(0);
  }, [path, commitSet, sort]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = { ...commitSet, path };
    Promise.all([
      api.metrics(repo.id, params),
      api.tree(repo.id, params),
      api.files(repo.id, { ...params, sort, limit: PAGE_SIZE, offset }),
    ])
      .then(([nextScope, nextTree, nextFiles]) => {
        if (cancelled) return;
        setScope(nextScope);
        setTree(nextTree);
        setFiles(nextFiles);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repo.id, commitSet, path, sort, offset]);

  const crumbs = useMemo(() => {
    const parts = path.split("/").filter(Boolean);
    return parts.map((part, index) => ({
      label: part,
      path: parts.slice(0, index + 1).join("/"),
    }));
  }, [path]);

  const scopeKind = scope?.scope.kind ?? (path ? "dir" : "repo");
  const total = files?.total ?? 0;
  const showTree = scopeKind !== "file" && (tree?.children.length ?? 0) > 0;

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner message={error} onRetry={() => setOffset((v) => v)} /> : null}

      <section className="card p-3">
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <button
            type="button"
            className="rounded px-1.5 py-0.5 text-slate-400 hover:bg-ink-800 hover:text-slate-200"
            onClick={() => setPath("")}
          >
            repo root
          </button>
          {crumbs.map((crumb, index) => (
            <span key={crumb.path} className="flex items-center gap-1">
              <span className="text-slate-700">/</span>
              <button
                type="button"
                className={
                  index === crumbs.length - 1
                    ? "rounded bg-ink-800 px-1.5 py-0.5 mono text-slate-200"
                    : "rounded px-1.5 py-0.5 mono text-slate-400 hover:bg-ink-800 hover:text-slate-200"
                }
                onClick={() => setPath(crumb.path)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
          <span className="ml-2 rounded bg-ink-850 px-1.5 py-0.5 text-slate-500">{scopeKind}</span>
          {loading ? <Spinner className="ml-1 h-3 w-3" /> : null}
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <StatCard label="Added" value={scope ? formatDelta(scope.added) : "…"} tone="pos" />
        <StatCard label="Removed" value={scope ? formatInt(scope.removed) : "…"} tone="neg" />
        <StatCard label="Growth" value={scope ? formatDelta(scope.growth) : "…"} />
        <StatCard label="Churn" value={scope ? formatInt(scope.churn) : "…"} />
        <StatCard label="Modifications" value={scope ? formatInt(scope.modifications) : "…"} />
        <StatCard
          label="Files under scope"
          value={scope ? formatInt(scope.n_files) : "…"}
          sub={`${formatInt(scope?.n_commits ?? 0)} commits in set`}
        />
      </section>

      {showTree && tree ? (
        <section className="card overflow-hidden">
          <header className="flex items-center gap-2 px-4 py-3">
            <h3 className="text-sm font-semibold text-white">Immediate children of {path || "the repository root"}</h3>
            <span className="text-xs text-slate-500">click a row to drill into it</span>
          </header>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead className="border-y border-ink-800 bg-ink-900/60">
                <tr>
                  <th className="th">Object</th>
                  <th className="th text-right">Files</th>
                  <th className="th text-right">Added</th>
                  <th className="th text-right">Removed</th>
                  <th className="th text-right">Growth</th>
                  <th className="th text-right">Churn</th>
                  <th className="th text-right">Commits</th>
                  <th className="th text-right">Mods</th>
                  <th className="th text-right">Churn rate</th>
                </tr>
              </thead>
              <tbody>
                {tree.children.map((child) => (
                  <tr
                    key={child.path}
                    className="cursor-pointer border-b border-ink-900 hover:bg-ink-850"
                    onClick={() => setPath(child.path)}
                    title={child.path}
                  >
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <span className="mono shrink-0 rounded bg-ink-850 px-1.5 py-0.5 text-[11px] text-slate-500">
                          {child.kind}
                        </span>
                        <span className={child.kind === "dir" ? "font-medium text-slate-200" : "text-slate-300"}>
                          {child.name}
                        </span>
                        {child.kind === "dir" ? (
                          <span className="text-xs text-slate-600">
                            {formatInt(child.n_files_head)} of {formatInt(child.n_files)} files at HEAD
                          </span>
                        ) : child.at_head ? null : (
                          <span className="text-xs text-slate-600">deleted</span>
                        )}
                      </div>
                    </td>
                    <td className="td text-right tabular-nums text-slate-400">
                      {child.kind === "dir" ? formatInt(child.n_files) : "–"}
                    </td>
                    <td className="td text-right tabular-nums text-emerald-300">{formatDelta(child.added)}</td>
                    <td className="td text-right tabular-nums text-rose-300">{formatInt(child.removed)}</td>
                    <td className="td text-right tabular-nums">{formatDelta(child.growth)}</td>
                    <td className="td text-right tabular-nums">{formatInt(child.churn)}</td>
                    <td className="td text-right tabular-nums">{formatInt(child.commits)}</td>
                    <td className="td text-right tabular-nums">{formatInt(child.modifications)}</td>
                    <td className="td text-right tabular-nums">{child.churn_rate.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="card overflow-hidden">
        <header className="flex flex-wrap items-center gap-3 px-4 py-3">
          <h3 className="text-sm font-semibold text-white">
            Files under {path || "the repository root"}
          </h3>
          <span className="text-xs text-slate-500">{formatInt(total)} files changed in this commit set</span>
          <label className="ml-auto flex items-center gap-2 text-xs text-slate-500">
            sort by
            <select
              className="input py-1"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              {SORTS.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </header>

        {files === null ? (
          <div className="grid place-items-center py-10">
            <Spinner />
          </div>
        ) : files.rows.length === 0 ? (
          <p className="px-4 pb-6 text-xs text-slate-500">
            No file changes in this commit set for this scope.
          </p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead className="border-y border-ink-800 bg-ink-900/60">
                  <tr>
                    <th className="th">Path</th>
                    <th className="th text-right">Added</th>
                    <th className="th text-right">Removed</th>
                    <th className="th text-right">Growth</th>
                    <th className="th text-right">Churn</th>
                    <th className="th text-right">Commits</th>
                    <th className="th text-right">Mods</th>
                    <th className="th text-right">Freq.</th>
                    <th className="th text-right">Rate</th>
                    <th className="th">Top owners</th>
                  </tr>
                </thead>
                <tbody>
                  {files.rows.map((row) => (
                    <tr
                      key={row.path}
                      className="cursor-pointer border-b border-ink-900 hover:bg-ink-850"
                      onClick={() => setPath(row.path)}
                      title={row.path}
                    >
                      <td className="td max-w-[28rem]">
                        <div className="flex items-center gap-2">
                          <span className="truncate mono text-slate-200">{row.path}</span>
                          {row.renamed ? (
                            <span className="shrink-0 rounded bg-ink-850 px-1.5 py-0.5 text-[11px] text-amber-300" title="Path recorded from a rename (change attributed to the new path)">
                              renamed
                            </span>
                          ) : null}
                          {row.is_binary ? (
                            <span className="shrink-0 rounded bg-ink-850 px-1.5 py-0.5 text-[11px] text-slate-500" title="Binary files are not measured (spec §2)">
                              binary
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="td text-right tabular-nums text-emerald-300">{formatDelta(row.added)}</td>
                      <td className="td text-right tabular-nums text-rose-300">{formatInt(row.removed)}</td>
                      <td className="td text-right tabular-nums">{formatDelta(row.growth)}</td>
                      <td className="td text-right tabular-nums">{formatInt(row.churn)}</td>
                      <td className="td text-right tabular-nums text-slate-400">{formatInt(row.commits)}</td>
                      <td className="td text-right tabular-nums">{formatInt(row.modifications)}</td>
                      <td className="td text-right tabular-nums text-slate-400">
                        {formatPercent(row.modification_frequency)}
                      </td>
                      <td className="td text-right tabular-nums text-slate-400">{row.churn_rate.toFixed(2)}</td>
                      <td className="td">
                        <OwnersBar owners={row.owners} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <footer className="flex items-center gap-2 border-t border-ink-800 px-4 py-2 text-xs text-slate-500">
              <span>
                {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {formatInt(total)}
              </span>
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
    </div>
  );
}
