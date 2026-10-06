import { useEffect, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, errorMessage } from "../api/client";
import type { Contributor, MetricsBundle, Repo, SeriesResponse } from "../api/types";
import { ErrorBanner, Spinner, StatCard } from "../components/ui";
import { formatDelta, formatInt, formatPercent } from "../lib/format";
import { useFilters } from "../state/filters";

const TOOLTIP_STYLE = {
  background: "#0f151d",
  border: "1px solid #26313f",
  borderRadius: 8,
  fontSize: 12,
} as const;

const BUCKETS = ["auto", "day", "week", "month"] as const;

export default function OverviewTab({ repo }: { repo: Repo }) {
  const { commitSet, path } = useFilters();
  const [metrics, setMetrics] = useState<MetricsBundle | null>(null);
  const [contributors, setContributors] = useState<Contributor[]>([]);
  const [series, setSeries] = useState<SeriesResponse | null>(null);
  const [bucket, setBucket] = useState<(typeof BUCKETS)[number]>("auto");
  const [error, setError] = useState<string | null>(null);
  const [loadingKpi, setLoadingKpi] = useState(true);
  const [loadingSeries, setLoadingSeries] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoadingKpi(true);
    const params = { ...commitSet, path };
    Promise.all([api.metrics(repo.id, params), api.contributors(repo.id, params)])
      .then(([nextMetrics, nextContributors]) => {
        if (cancelled) return;
        setMetrics(nextMetrics);
        setContributors(nextContributors);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingKpi(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repo.id, commitSet, path]);

  useEffect(() => {
    let cancelled = false;
    setLoadingSeries(true);
    api
      .series(repo.id, { ...commitSet, path, bucket })
      .then((next) => {
        if (!cancelled) setSeries(next);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingSeries(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repo.id, commitSet, path, bucket]);

  const chartData = (series?.points ?? []).map((point) => ({
    label: point.label,
    added: point.added,
    removed: -point.removed,
    churn: point.churn,
    commits: point.commits,
  }));

  const topContributors = [...contributors].sort((a, b) => b.churn - a.churn).slice(0, 12);

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner message={error} /> : null}

      <section className="card p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-slate-600">
            scope: {metrics ? metrics.scope.kind : path ? "…" : "repo"}
          </span>
          <span className="mono truncate text-slate-700" title={metrics?.scope.path || ""}>
            {metrics?.scope.path || "repository root"}
          </span>
          <span className="ml-auto">
            {metrics ? `${formatInt(metrics.scope.path ? metrics.n_files : repo.n_files)} files` : ""}
          </span>
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <StatCard label="Commits in set" value={metrics ? formatInt(metrics.n_commits) : "…"} sub="/ H (non-merge)" />
        <StatCard label="Added lines" value={metrics ? formatDelta(metrics.added) : "…"} tone="pos" />
        <StatCard label="Removed lines" value={metrics ? formatInt(metrics.removed) : "…"} tone="neg" />
        <StatCard
          label="Growth"
          value={metrics ? formatDelta(metrics.growth) : "…"}
          tone={metrics && metrics.growth < 0 ? "neg" : "pos"}
          sub="added − removed"
        />
        <StatCard label="Churn" value={metrics ? formatInt(metrics.churn) : "…"} sub="added + removed" />
        <StatCard label="Modifications" value={metrics ? formatInt(metrics.modifications) : "…"} sub="commits with changes" />
        <StatCard
          label="Mod. frequency"
          value={metrics ? formatPercent(metrics.modification_frequency) : "…"}
          sub="modifications / commits"
        />
        <StatCard
          label="Churn rate"
          value={metrics ? metrics.churn_rate.toFixed(2) : "…"}
          sub="changed lines / commit"
        />
      </section>

      <section className="card p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold text-slate-950">Line activity over the commit set</h3>
          <div className="ml-auto flex rounded-lg bg-sky-100 p-0.5">
            {BUCKETS.map((key) => (
              <button
                key={key}
                type="button"
                className={bucket === key ? "tab tab-active" : "tab"}
                onClick={() => setBucket(key)}
              >
                {key}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-0.5 text-xs text-slate-500">
          Added lines above the axis, removed lines below; churn and commit counts overlaid.
          {series ? ` Bucketed by ${series.bucket}.` : ""}
        </p>
        {loadingSeries && !series ? (
          <div className="grid place-items-center py-16">
            <Spinner />
          </div>
        ) : chartData.length === 0 ? (
          <p className="py-10 text-center text-xs text-slate-500">No commits in this commit set.</p>
        ) : (
          <div className="mt-3">
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="#1a2330" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#26313f" }}
                  minTickGap={28}
                />
                <YAxis
                  yAxisId="left"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => formatInt(Math.abs(Number(value)))}
                  width={58}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  allowDecimals={false}
                  width={42}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "#94a3b8" }}
                  formatter={(value, name) => {
                    const n = Number(value);
                    const label = String(name);
                    if (label === "removed") return [formatInt(Math.abs(n)), "removed"];
                    if (label === "churn") return [formatInt(n), "churn"];
                    if (label === "commits") return [formatInt(n), "commits"];
                    return [formatInt(n), label];
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar yAxisId="left" dataKey="added" name="added" stackId="lines" fill="#34d399" />
                <Bar yAxisId="left" dataKey="removed" name="removed" stackId="lines" fill="#fb7185" />
                <Line yAxisId="left" dataKey="churn" name="churn" type="monotone" stroke="#38bdf8" strokeWidth={2} dot={false} />
                <Line yAxisId="right" dataKey="commits" name="commits" type="monotone" stroke="#fbbf24" strokeWidth={1.5} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <header className="px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-950">Author impact on this scope (spec §2.5)</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            The author filter is ignored here so ownership shares always sum to 100% for the current
            scope and commit set.
          </p>
        </header>
        {loadingKpi && contributors.length === 0 ? (
          <div className="grid place-items-center py-10">
            <Spinner />
          </div>
        ) : topContributors.length === 0 ? (
          <p className="px-4 pb-4 text-xs text-slate-500">No authors with changes in this commit set.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead className="border-y border-sky-200 bg-sky-50/80">
                <tr>
                  <th className="th">Author</th>
                  <th className="th text-right">Commits</th>
                  <th className="th text-right">Added</th>
                  <th className="th text-right">Removed</th>
                  <th className="th text-right">Churn</th>
                  <th className="th text-right">Modifications</th>
                  <th className="th">Ownership (churn share)</th>
                </tr>
              </thead>
              <tbody>
                {topContributors.map((author) => (
                  <tr key={author.author_id} className="border-b border-sky-100 hover:bg-sky-100">
                    <td className="td">
                      <div className="text-slate-800">{author.name}</div>
                      <div className="text-xs text-slate-600">{author.email}</div>
                    </td>
                    <td className="td text-right tabular-nums">{formatInt(author.n_commits)}</td>
                    <td className="td text-right tabular-nums text-emerald-700">{formatDelta(author.added)}</td>
                    <td className="td text-right tabular-nums text-rose-700">{formatInt(author.removed)}</td>
                    <td className="td text-right tabular-nums">{formatInt(author.churn)}</td>
                    <td className="td text-right tabular-nums">{formatInt(author.modifications)}</td>
                    <td className="td">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-sky-200">
                          <div
                            className="h-full rounded-full bg-accent"
                            style={{ width: `${Math.max(2, author.ownership * 100)}%` }}
                          />
                        </div>
                        <span className="tabular-nums text-xs text-slate-600">
                          {formatPercent(author.ownership)}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
