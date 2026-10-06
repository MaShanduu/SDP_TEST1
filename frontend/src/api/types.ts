export type RepoStatus = "pending" | "preparing" | "ingesting" | "ready" | "error";
export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  id: number;
  repo_id: number | null;
  kind: string;
  status: JobStatus;
  phase: string | null;
  progress: number;
  message: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Repo {
  id: number;
  name: string;
  source: "url" | "zip";
  source_ref: string;
  status: RepoStatus;
  error: string | null;
  branch: string | null;
  head_sha: string | null;
  created_at: string;
  ingested_at: string | null;
  n_commits: number;
  n_files: number;
  n_dirs: number;
  n_identities: number;
  n_authors: number;
  total_added: number;
  total_removed: number;
  first_ct: number | null;
  last_ct: number | null;
  job: Job | null;
}

export interface RepoCreated {
  repo: Repo;
  job_id: number;
}

export interface Scope {
  path: string;
  kind: "repo" | "dir" | "file";
  name: string;
}

export interface MetricsBundle {
  scope: Scope;
  n_commits: number;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  n_files: number;
  modifications: number;
  modification_frequency: number;
  churn_rate: number;
}

export interface Contributor {
  author_id: number;
  name: string;
  email: string;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  n_commits: number;
  modifications: number;
  modification_frequency: number;
  churn_rate: number;
  ownership: number;
}

export interface FileOwner {
  author_id: number;
  name: string;
  churn: number;
  share: number;
}

export interface FileRow {
  path: string;
  name: string;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  commits: number;
  modifications: number;
  modification_frequency: number;
  churn_rate: number;
  is_binary: boolean;
  renamed: boolean;
  owners: FileOwner[];
}

export interface FileRows {
  total: number;
  n_commits: number;
  rows: FileRow[];
}

export type TreeNodeKind = "file" | "dir";

export interface TreeNode {
  path: string;
  name: string;
  kind: TreeNodeKind;
  binary: boolean;
  at_head: boolean;
  n_files: number;
  n_files_head: number;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  commits: number;
  modifications: number;
  modification_frequency: number;
  churn_rate: number;
}

export interface TreeResponse {
  path: string;
  kind: string;
  n_commits: number;
  children: TreeNode[];
}

export interface SeriesPoint {
  t: number;
  label: string;
  sha?: string;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  modifications: number;
  commits: number;
}

export interface SeriesResponse {
  bucket: "day" | "week" | "month" | "commit";
  points: SeriesPoint[];
}

export interface CommitRow {
  sha: string;
  ct: number;
  subject: string;
  parent_sha: string | null;
  author_id: number;
  author_name: string;
  added: number;
  removed: number;
  churn: number;
  n_files: number;
  total_added: number;
  total_removed: number;
  total_n_files: number;
}

export interface CommitsPage {
  total: number;
  rows: CommitRow[];
}

export interface CommitFile {
  path: string;
  old_path: string | null;
  added: number;
  removed: number;
  is_binary: boolean;
  renamed: boolean;
}

export interface CommitDetail {
  sha: string;
  ct: number;
  subject: string;
  parent_sha: string | null;
  author_id: number;
  author_name: string;
  author_email: string;
  identity_name: string | null;
  identity_email: string | null;
  added: number;
  removed: number;
  growth: number;
  churn: number;
  n_files: number;
  files: CommitFile[];
}

export interface RawIdentity {
  name: string;
  email: string;
  n_commits: number;
}

export interface AuthorAlias {
  id: number;
  name: string;
  email: string;
  n_commits: number;
  raw: RawIdentity[];
}

export interface Author {
  id: number;
  name: string;
  email: string;
  is_manual: boolean;
  n_commits: number;
  raw: RawIdentity[];
  aliases: AuthorAlias[];
}

export interface MergeSuggestion {
  reason: string;
  identities: {
    id: number;
    name: string;
    email: string;
    n_commits: number;
    effective_author_id: number;
  }[];
}

export interface PathEntry {
  path: string;
  kind: TreeNodeKind;
}
