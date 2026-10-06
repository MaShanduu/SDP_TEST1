import type { ReactNode } from "react";
import type { FileOwner, RepoStatus } from "../api/types";
import { formatInt, formatPercent } from "../lib/format";

type Tone = "slate" | "green" | "red" | "amber" | "blue";

const TONES: Record<Tone, string> = {
  slate: "border-sky-200 bg-sky-50 text-slate-700",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  red: "border-rose-200 bg-rose-50 text-rose-700",
  amber: "border-amber-200 bg-amber-50 text-amber-700",
  blue: "border-blue-200 bg-blue-50 text-blue-700",
};

export function Badge({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

const STATUS_TONES: Record<RepoStatus, Tone> = {
  pending: "slate",
  preparing: "amber",
  ingesting: "blue",
  ready: "green",
  error: "red",
};

export function StatusBadge({ status }: { status: RepoStatus }) {
  return <Badge tone={STATUS_TONES[status]}>{status}</Badge>;
}

export function StatCard({
  label,
  value,
  sub,
  tone,
  title,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "pos" | "neg";
  title?: string;
}) {
  const valueClass = tone === "pos" ? "text-emerald-700" : tone === "neg" ? "text-rose-700" : "text-slate-950";
  return (
    <div className="card px-4 py-3" title={title}>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${valueClass}`}>{value}</div>
      {sub ? <div className="mt-0.5 text-xs text-slate-500">{sub}</div> : null}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-sky-200 border-t-accent ${className}`}
      aria-label="loading"
    />
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
      <span className="flex-1">{message}</span>
      {onRetry ? (
        <button type="button" className="btn-ghost" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card grid place-items-center gap-2 px-6 py-12 text-center">
      <div className="text-sm font-medium text-slate-800">{title}</div>
      {children ? <div className="max-w-md text-xs text-slate-500">{children}</div> : null}
    </div>
  );
}

const OWNER_COLORS = [
  "#38bdf8",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#a78bfa",
  "#f97316",
  "#2dd4bf",
  "#e879f9",
];

/** Stacked mini-bar of per-author churn shares (spec §2.5 ownership). */
export function OwnersBar({ owners }: { owners: FileOwner[] }) {
  if (owners.length === 0) return <span className="text-xs text-slate-600">–</span>;
  const title = owners
    .map((owner) => `${owner.name}: ${formatInt(owner.churn)} churn (${formatPercent(owner.share)})`)
    .join("\n");
  return (
    <div className="flex h-1.5 w-28 overflow-hidden rounded-full bg-sky-200" title={title}>
      {owners.map((owner, index) => (
        <div
          key={owner.author_id}
          style={{
            width: `${Math.max(3, owner.share * 100)}%`,
            background: OWNER_COLORS[index % OWNER_COLORS.length],
          }}
        />
      ))}
    </div>
  );
}
