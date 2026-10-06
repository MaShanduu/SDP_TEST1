import type { Job } from "../api/types";

/** Ingestion progress bar driven by the job row (polled by the parent). */
export default function JobProgress({ job, className = "" }: { job: Job; className?: string }) {
  const failed = job.status === "error";
  const done = job.status === "done";
  const pct = done ? 100 : Math.max(2, Math.min(100, Math.round((job.progress ?? 0) * 100)));
  const label = failed
    ? "Ingestion failed"
    : done
      ? "Ready"
      : (job.message ?? job.phase ?? job.status);

  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
        <span className="truncate">{label}</span>
        {!failed ? <span className="tabular-nums">{pct}%</span> : null}
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800">
        <div
          className={`h-full rounded-full transition-all ${
            failed ? "bg-rose-500" : done ? "bg-emerald-500" : "bg-accent"
          }`}
          style={{ width: `${failed ? 100 : pct}%` }}
        />
      </div>
      {failed && job.error ? (
        <p className="mt-1 break-words text-xs text-rose-300">{job.error}</p>
      ) : null}
    </div>
  );
}
