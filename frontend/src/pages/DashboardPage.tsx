import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Author, Repo } from "../api/types";
import FilterBar from "../components/FilterBar";
import JobProgress from "../components/JobProgress";
import { ErrorBanner, Spinner, StatusBadge } from "../components/ui";
import { formatDelta, formatInt, shortSha, truncateMiddle } from "../lib/format";
import { FilterProvider } from "../state/filters";
import AuthorsTab from "../tabs/AuthorsTab";
import CommitsTab from "../tabs/CommitsTab";
import FilesTab from "../tabs/FilesTab";
import OverviewTab from "../tabs/OverviewTab";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "files", label: "Files & directories" },
  { key: "authors", label: "Authors" },
  { key: "commits", label: "Commits" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function normaliseTab(raw: string | null): TabKey {
  const found = TABS.find((tab) => tab.key === raw);
  return found ? found.key : "overview";
}

export default function DashboardPage() {
  const { repoId } = useParams();
  const id = Number(repoId);
  const [repo, setRepo] = useState<Repo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.getRepo(id);
      setRepo(next);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const ingesting = repo !== null && repo.status !== "ready" && repo.status !== "error";

  // Keep polling while the repository is still being ingested.
  useEffect(() => {
    if (!ingesting) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [ingesting, load]);

  if (!Number.isFinite(id)) {
    return <ErrorBanner message="Invalid repository id in the URL." />;
  }
  if (!repo && error) {
    return (
      <div className="space-y-3">
        <ErrorBanner message={error} onRetry={() => void load()} />
        <Link to="/" className="btn-ghost">
          Back to repositories
        </Link>
      </div>
    );
  }
  if (!repo) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <RepoHeader repo={repo} />
      {repo.status === "ready" ? (
        <FilterProvider>
          <DashboardBody repo={repo} />
        </FilterProvider>
      ) : repo.status === "error" ? (
        <ErrorBanner message={repo.error ?? "Ingestion failed."} onRetry={() => void load()} />
      ) : (
        <div className="card p-4">
          <div className="mb-2 text-sm text-slate-300">Ingesting repository…</div>
          {repo.job ? <JobProgress job={repo.job} /> : <Spinner />}
          <p className="mt-2 text-xs text-slate-500">
            The dashboard becomes available as soon as ingestion finishes; this page updates itself.
          </p>
        </div>
      )}
    </div>
  );
}

function RepoHeader({ repo }: { repo: Repo }) {
  return (
    <div className="card flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
      <Link to="/" className="btn-ghost shrink-0" title="All repositories">
        ← Repositories
      </Link>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-base font-semibold text-white" title={repo.name}>
            {repo.name}
          </h1>
          <StatusBadge status={repo.status} />
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500" title={repo.source_ref}>
          {repo.source} · {truncateMiddle(repo.source_ref, 60)}
          {repo.branch ? ` · ${repo.branch}` : ""}
          {repo.head_sha ? ` · ${shortSha(repo.head_sha)}` : ""}
        </p>
      </div>
      {repo.status === "ready" ? (
        <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-400">
          <span>
            <span className="text-slate-600">commits </span>
            {formatInt(repo.n_commits)}
          </span>
          <span>
            <span className="text-slate-600">files </span>
            {formatInt(repo.n_files)}
          </span>
          <span>
            <span className="text-slate-600">dirs </span>
            {formatInt(repo.n_dirs)}
          </span>
          <span>
            <span className="text-slate-600">authors </span>
            {formatInt(repo.n_authors)}
          </span>
          <span className="text-emerald-300" title="Added lines over the full history">
            {formatDelta(repo.total_added)}
          </span>
          <span className="text-rose-300" title="Removed lines over the full history">
            -{formatInt(repo.total_removed)}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function DashboardBody({ repo }: { repo: Repo }) {
  const [params, setParams] = useSearchParams();
  const tab = normaliseTab(params.get("tab"));

  const [authors, setAuthors] = useState<Author[]>([]);
  const [authorsError, setAuthorsError] = useState<string | null>(null);

  const reloadAuthors = useCallback(async () => {
    try {
      setAuthors(await api.authors(repo.id));
      setAuthorsError(null);
    } catch (err) {
      setAuthorsError(errorMessage(err));
    }
  }, [repo.id]);

  useEffect(() => {
    void reloadAuthors();
  }, [reloadAuthors]);

  function changeTab(key: TabKey) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (key === "overview") next.delete("tab");
        else next.set("tab", key);
        return next;
      },
      { replace: true },
    );
  }

  return (
    <>
      <FilterBar repo={repo} authors={authors} />
      {authorsError ? <ErrorBanner message={`Authors failed to load: ${authorsError}`} onRetry={() => void reloadAuthors()} /> : null}
      <div className="flex flex-wrap items-center gap-1 border-b border-ink-800 pb-2">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={tab === key ? "tab tab-active" : "tab"}
            onClick={() => changeTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "overview" ? <OverviewTab repo={repo} /> : null}
      {tab === "files" ? <FilesTab repo={repo} /> : null}
      {tab === "authors" ? (
        <AuthorsTab repo={repo} authors={authors} reloadAuthors={reloadAuthors} />
      ) : null}
      {tab === "commits" ? <CommitsTab repo={repo} /> : null}
    </>
  );
}
