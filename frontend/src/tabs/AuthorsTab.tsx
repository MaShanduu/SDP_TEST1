import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "../api/client";
import type { Author, Contributor, MergeSuggestion, Repo } from "../api/types";
import { EmptyState, ErrorBanner, Spinner } from "../components/ui";
import { formatInt, formatPercent } from "../lib/format";
import { useFilters } from "../state/filters";

/**
 * Author management (rubric "Author Merge"): merges detected by .mailmap show up
 * as aliases, heuristic suggestions can be merged in one click, and any set of
 * authors can be folded together manually.  All changes re-attribute commits
 * immediately, so every metric below reflects the merge.
 */
export default function AuthorsTab({
  repo,
  authors,
  reloadAuthors,
}: {
  repo: Repo;
  authors: Author[];
  reloadAuthors: () => Promise<void>;
}) {
  const { commitSet, path } = useFilters();
  const [contributors, setContributors] = useState<Contributor[] | null>(null);
  const [suggestions, setSuggestions] = useState<MergeSuggestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [target, setTarget] = useState<number | null>(null);
  const [renaming, setRenaming] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const [nextContributors, nextSuggestions] = await Promise.all([
        api.contributors(repo.id, { ...commitSet, path }),
        api.authorSuggestions(repo.id),
      ]);
      setContributors(nextContributors);
      setSuggestions(nextSuggestions);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [repo.id, commitSet, path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const contributorsById = useMemo(() => {
    const map = new Map<number, Contributor>();
    for (const contributor of contributors ?? []) map.set(contributor.author_id, contributor);
    return map;
  }, [contributors]);

  const authorsById = useMemo(() => {
    const map = new Map<number, Author>();
    for (const author of authors) map.set(author.id, author);
    return map;
  }, [authors]);

  const scopedChurn = (contributors ?? []).reduce((sum, row) => sum + row.churn, 0);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function mergeSelected() {
    if (!target || selected.size < 2) return;
    const sources = [...selected].filter((id) => id !== target);
    if (sources.length === 0) return;
    setBusy(true);
    try {
      await api.mergeAuthors(repo.id, target, sources);
      setSelected(new Set());
      setTarget(null);
      await Promise.all([reload(), reloadAuthors()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function mergeSuggestion(suggestion: MergeSuggestion) {
    const ids = [...new Set(suggestion.identities.map((identity) => identity.effective_author_id))];
    if (ids.length < 2) return;
    setBusy(true);
    try {
      await api.mergeAuthors(repo.id, ids[0], ids.slice(1));
      await Promise.all([reload(), reloadAuthors()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function detach(identityId: number) {
    setBusy(true);
    try {
      await api.detachAuthor(repo.id, identityId);
      await Promise.all([reload(), reloadAuthors()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveRename(authorId: number, name: string, email: string) {
    setBusy(true);
    try {
      await api.patchAuthor(repo.id, authorId, { name, email: email || undefined });
      setRenaming(null);
      await Promise.all([reload(), reloadAuthors()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner message={error} onRetry={() => void reload()} /> : null}

      {suggestions.length > 0 ? (
        <section className="card p-4">
          <h3 className="text-sm font-semibold text-slate-950">Suggested merges</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Identities that probably belong to the same person (shared email or near-identical name).
          </p>
          <div className="mt-2 divide-y divide-ink-800">
            {suggestions.map((suggestion, index) => (
              <div key={index} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2 text-xs">
                <span className="rounded bg-sky-100 px-1.5 py-0.5 text-slate-500">{suggestion.reason}</span>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  {suggestion.identities.map((identity) => (
                    <span key={identity.id} className="text-slate-700" title={`identity #${identity.id}`}>
                      {identity.name} &lt;{identity.email}&gt;{" "}
                      <span className="text-slate-600">({formatInt(identity.n_commits)} commits)</span>
                    </span>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn-primary ml-auto px-2 py-1 text-xs"
                  disabled={busy}
                  onClick={() => void mergeSuggestion(suggestion)}
                >
                  Merge
                </button>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {selected.size > 0 ? (
        <section className="card flex flex-wrap items-center gap-3 p-3 text-xs">
          <span className="text-slate-600">{selected.size} selected</span>
          <label className="flex items-center gap-2 text-slate-500">
            merge into
            <select
              className="input py-1"
              value={target ?? ""}
              onChange={(event) => setTarget(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="" disabled>
                choose target…
              </option>
              {[...selected].map((id) => {
                const author = authorsById.get(id);
                return (
                  <option key={id} value={id}>
                    {author ? `${author.name} <${author.email}>` : `#${id}`}
                  </option>
                );
              })}
            </select>
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !target || selected.size < 2}
            onClick={() => void mergeSelected()}
          >
            Merge selected
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setSelected(new Set());
              setTarget(null);
            }}
          >
            Clear selection
          </button>
          <span className="text-slate-600">
            Every commit of the source authors is re-attributed to the target; all metrics update immediately.
          </span>
        </section>
      ) : null}

      <section className="card overflow-hidden">
        <header className="flex flex-wrap items-center gap-3 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-950">Authors</h3>
          <span className="text-xs text-slate-500">
            {formatInt(authors.length)} effective author{authors.length === 1 ? "" : "s"} ·{" "}
            {formatInt(contributors?.length ?? 0)} with changes in this scope
          </span>
          <span className="ml-auto text-xs text-slate-600">
            select two or more rows to merge different identities of the same person
          </span>
        </header>

        {contributors === null && authors.length === 0 ? (
          <div className="grid place-items-center py-10">
            <Spinner />
          </div>
        ) : authors.length === 0 ? (
          <EmptyState title="No authors yet" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead className="border-y border-sky-200 bg-sky-50/80">
                <tr>
                  <th className="th w-8"> </th>
                  <th className="th">Author</th>
                  <th className="th text-right">Commits</th>
                  <th className="th text-right">Churn (scope)</th>
                  <th className="th text-right">Mods (scope)</th>
                  <th className="th">Ownership (scope)</th>
                  <th className="th">Identities</th>
                  <th className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {authors.map((author) => {
                  const stats = contributorsById.get(author.id);
                  const ownership = stats && scopedChurn > 0 ? stats.churn / scopedChurn : undefined;
                  return (
                    <tr key={author.id} className="border-b border-sky-100 align-top hover:bg-sky-100">
                      <td className="td">
                        <input
                          type="checkbox"
                          checked={selected.has(author.id)}
                          onChange={() => toggle(author.id)}
                          title="Select for manual merge"
                        />
                      </td>
                      <td className="td">
                        {renaming === author.id ? (
                          <RenameForm
                            name={author.name}
                            email={author.email}
                            busy={busy}
                            onCancel={() => setRenaming(null)}
                            onSave={(name, email) => void saveRename(author.id, name, email)}
                          />
                        ) : (
                          <div className="flex items-center gap-2">
                            <span className="text-slate-900">{author.name}</span>
                            {author.is_manual ? (
                              <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[11px] text-accent" title="Merged or renamed manually in the RAT">
                                manual
                              </span>
                            ) : null}
                          </div>
                        )}
                        <div className="text-xs text-slate-600">{author.email}</div>
                      </td>
                      <td className="td text-right tabular-nums">{formatInt(author.n_commits)}</td>
                      <td className="td text-right tabular-nums">{stats ? formatInt(stats.churn) : "–"}</td>
                      <td className="td text-right tabular-nums">{stats ? formatInt(stats.modifications) : "–"}</td>
                      <td className="td">
                        {ownership === undefined ? (
                          <span className="text-xs text-slate-600">–</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-sky-200">
                              <div
                                className="h-full rounded-full bg-accent"
                                style={{ width: `${Math.max(2, ownership * 100)}%` }}
                              />
                            </div>
                            <span className="tabular-nums text-xs text-slate-600">{formatPercent(ownership)}</span>
                          </div>
                        )}
                      </td>
                      <td className="td max-w-[26rem]">
                        <div className="flex flex-wrap gap-1">
                          {author.aliases.map((alias) => (
                            <span
                              key={alias.id}
                              className="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-100 px-2 py-0.5 text-[11px] text-slate-600"
                              title={`Merged identity (${formatInt(alias.n_commits)} commits)`}
                            >
                              {alias.name} &lt;{alias.email}&gt;
                              <button
                                type="button"
                                className="text-rose-700 hover:text-rose-200"
                                disabled={busy}
                                onClick={() => void detach(alias.id)}
                                title="Detach this identity back into its own author"
                              >
                                detach
                              </button>
                            </span>
                          ))}
                          {author.raw.map((raw) => (
                            <span
                              key={`${raw.name}~${raw.email}`}
                              className="inline-flex items-center gap-1 rounded-full border border-sky-200 px-2 py-0.5 text-[11px] text-slate-600"
                              title={`Pre-mailmap identity (${formatInt(raw.n_commits)} commits) — merged by .mailmap`}
                            >
                              {raw.name} &lt;{raw.email}&gt;
                            </span>
                          ))}
                          {author.aliases.length === 0 && author.raw.length <= 1 ? (
                            <span className="text-xs text-slate-700">single identity</span>
                          ) : null}
                        </div>
                      </td>
                      <td className="td text-right">
                        {renaming === author.id ? null : (
                          <button
                            type="button"
                            className="btn-ghost px-2 py-0.5 text-xs"
                            onClick={() => setRenaming(author.id)}
                          >
                            Rename
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function RenameForm({
  name,
  email,
  busy,
  onCancel,
  onSave,
}: {
  name: string;
  email: string;
  busy: boolean;
  onCancel: () => void;
  onSave: (name: string, email: string) => void;
}) {
  const [nextName, setNextName] = useState(name);
  const [nextEmail, setNextEmail] = useState(email);
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (nextName.trim()) onSave(nextName.trim(), nextEmail.trim());
      }}
    >
      <input
        autoFocus
        className="input py-1"
        value={nextName}
        onChange={(event) => setNextName(event.target.value)}
        placeholder="Display name"
      />
      <input
        className="input py-1"
        value={nextEmail}
        onChange={(event) => setNextEmail(event.target.value)}
        placeholder="Email"
      />
      <button type="submit" className="btn-primary px-2 py-1 text-xs" disabled={busy}>
        Save
      </button>
      <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}
