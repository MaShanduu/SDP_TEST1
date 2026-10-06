import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Repo } from "../api/types";
import JobProgress from "../components/JobProgress";
import { EmptyState, ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { formatDelta, formatInt, formatIso, formatTs, shortSha, truncateMiddle } from "../lib/format";

const ACTIVE_STATUSES = new Set(["pending", "preparing", "ingesting"]);

export default function ReposPage() {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (silent = false) => {
    try {
      const list = await api.listRepos();
      setRepos(list);
      if (!silent) setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const active = useMemo(() => repos?.some((repo) => ACTIVE_STATUSES.has(repo.status)) ?? false, [repos]);

  // Poll while any ingestion job is still running so progress bars stay live.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(true), 1500);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  return (
    <div className="space-y-6">
      <AddRepoPanel onCreated={() => void refresh(true)} />

      <section>
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-600">
            Repositories{repos ? ` (${repos.length})` : ""}
          </h2>
          {active ? (
            <span className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner className="h-3 w-3" /> ingestion in progress…
            </span>
          ) : null}
        </div>

        {error ? (
          <div className="mb-3">
            <ErrorBanner message={error} onRetry={() => void refresh()} />
          </div>
        ) : null}

        {repos === null ? (
          <div className="grid place-items-center py-16">
            <Spinner />
          </div>
        ) : repos.length === 0 ? (
          <EmptyState title="No repositories yet">
            Add one above: paste a clone URL (https, ssh or git@host:path) or upload a zip archive
            of a repository that includes its .git directory. Ingestion runs in the background and
            the dashboard opens as soon as the metrics are ready.
          </EmptyState>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {repos.map((repo) => (
              <RepoCard key={repo.id} repo={repo} onChanged={() => void refresh(true)} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function AddRepoPanel({ onCreated }: { onCreated: () => void }) {
  const [mode, setMode] = useState<"url" | "zip">("url");
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    if (mode === "url" && !url.trim()) {
      setError("Enter a clone URL, e.g. https://github.com/redis/redis.git");
      return;
    }
    if (mode === "zip" && !file) {
      setError("Choose a zip archive of the repository (including its .git directory).");
      return;
    }
    setBusy(true);
    try {
      const created =
        mode === "url"
          ? await api.createRepoFromUrl(url.trim(), name.trim() || undefined)
          : await api.createRepoFromZip(file as File, name.trim() || undefined);
      setNotice(`Ingesting "${created.repo.name}" in the background (job #${created.job_id}).`);
      setUrl("");
      setName("");
      setFile(null);
      setFileKey((key) => key + 1);
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-950">Add a repository</h2>
        <div className="flex rounded-lg bg-sky-100 p-0.5">
          <button
            type="button"
            className={mode === "url" ? "tab tab-active" : "tab"}
            onClick={() => setMode("url")}
          >
            Clone URL
          </button>
          <button
            type="button"
            className={mode === "zip" ? "tab tab-active" : "tab"}
            onClick={() => setMode("zip")}
          >
            Zip upload
          </button>
        </div>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] md:items-end">
        {mode === "url" ? (
          <label className="grid gap-1 text-xs text-slate-500">
            Repository URL
            <input
              className="input w-full"
              placeholder="https://github.com/redis/redis.git"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              spellCheck={false}
            />
          </label>
        ) : (
          <label className="grid gap-1 text-xs text-slate-500">
            Zip archive (must contain the .git directory)
            <input
              key={fileKey}
              type="file"
              accept=".zip,application/zip"
              className="input w-full file:mr-3 file:rounded file:border-0 file:bg-sky-200 file:px-2 file:py-1 file:text-xs file:text-slate-800"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
        )}
        <label className="grid gap-1 text-xs text-slate-500">
          Display name (optional)
          <input
            className="input w-full"
            placeholder="Defaults to the repo name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button type="submit" className="btn-primary h-[38px]" disabled={busy}>
          {busy ? <Spinner className="h-3.5 w-3.5" /> : null}
          {busy ? "Starting…" : "Add repository"}
        </button>
      </div>

      <p className="mt-2 text-xs text-slate-600">
        Remote URLs are deep-cloned in full (a bare clone, no working tree); metrics follow the
        repository HEAD. Zip uploads are extracted safely and never execute repository code.
      </p>
      {error ? (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      ) : null}
      {notice ? <p className="mt-3 text-xs text-emerald-700">{notice}</p> : null}
    </form>
  );
}

function RepoCard({ repo, onChanged }: { repo: Repo; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(repo.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setName(repo.name), [repo.name]);

  const ready = repo.status === "ready";

  async function saveName() {
    const next = name.trim();
    if (!next || next === repo.name) {
      setEditing(false);
      setName(repo.name);
      return;
    }
    setBusy(true);
    try {
      await api.renameRepo(repo.id, next);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${repo.name}"? All ingested data and metrics will be removed.`)) return;
    setBusy(true);
    try {
      await api.deleteRepo(repo.id);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <article className="card flex flex-col gap-3 p-4">
      <header className="flex items-start gap-2">
        {editing ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void saveName();
            }}
          >
            <input
              autoFocus
              className="input min-w-0 flex-1"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={busy}>
              Save
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setEditing(false);
                setName(repo.name);
              }}
            >
              Cancel
            </button>
          </form>
        ) : (
          <>
            <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-950" title={repo.name}>
              {repo.name}
            </h3>
            <StatusBadge status={repo.status} />
            <button
              type="button"
              className="btn-ghost px-2 py-0.5 text-xs"
              onClick={() => setEditing(true)}
            >
              Rename
            </button>
          </>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className="mono rounded bg-sky-100 px-1.5 py-0.5 text-slate-600">{repo.source}</span>
        <span className="truncate" title={repo.source_ref}>
          {truncateMiddle(repo.source_ref, 48)}
        </span>
        {repo.branch ? <span>· {repo.branch}</span> : null}
        {repo.head_sha ? <span className="mono">· {shortSha(repo.head_sha)}</span> : null}
      </div>

      {ready ? (
        <>
          <dl className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
            <Stat label="Commits" value={formatInt(repo.n_commits)} />
            <Stat label="Files" value={formatInt(repo.n_files)} />
            <Stat label="Dirs" value={formatInt(repo.n_dirs)} />
            <Stat
              label="Authors"
              value={
                repo.n_authors === repo.n_identities
                  ? formatInt(repo.n_authors)
                  : `${formatInt(repo.n_authors)} / ${formatInt(repo.n_identities)} ids`
              }
              title="Effective authors (after merging) / pre-merge identities"
            />
            <Stat label="Added" value={formatDelta(repo.total_added)} tone="pos" />
            <Stat label="Removed" value={formatInt(repo.total_removed)} tone="neg" />
          </dl>
          <p className="text-xs text-slate-600">
            {formatTs(repo.first_ct)} → {formatTs(repo.last_ct)}
          </p>
        </>
      ) : (
        <div className="space-y-2">
          {repo.job ? (
            <JobProgress job={repo.job} />
          ) : (
            <p className="text-xs text-slate-500">Waiting to start…</p>
          )}
          {repo.error ? <p className="break-words text-xs text-rose-700">{repo.error}</p> : null}
        </div>
      )}

      {error ? <ErrorBanner message={error} /> : null}

      <footer className="mt-auto flex items-center gap-2 pt-1">
        {ready ? (
          <Link to={`/repos/${repo.id}`} className="btn-primary">
            Open dashboard
          </Link>
        ) : (
          <button type="button" className="btn-primary" disabled title="Available once ingestion completes">
            Open dashboard
          </button>
        )}
        <span className="ml-auto text-xs text-slate-600" title="Added to RAT">
          {formatIso(repo.ingested_at ?? repo.created_at)}
        </span>
        <button type="button" className="btn-danger" onClick={() => void remove()} disabled={busy}>
          Delete
        </button>
      </footer>
    </article>
  );
}

function Stat({
  label,
  value,
  tone,
  title,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg";
  title?: string;
}) {
  const cls = tone === "pos" ? "text-emerald-700" : tone === "neg" ? "text-rose-700" : "text-slate-800";
  return (
    <div title={title}>
      <dt className="text-slate-600">{label}</dt>
      <dd className={`tabular-nums ${cls}`}>{value}</dd>
    </div>
  );
}
