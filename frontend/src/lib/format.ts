export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return value.toLocaleString("en-US");
}

export function formatDelta(value: number): string {
  return value > 0 ? `+${value.toLocaleString("en-US")}` : value.toLocaleString("en-US");
}

export function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatTs(ts: number | null | undefined, withTime = false): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function formatIso(iso: string | null | undefined): string {
  if (!iso) return "–";
  // SQLite stores UTC as "YYYY-MM-DD HH:MM:SS"; normalise to ISO with a Z suffix.
  let raw = iso.trim().replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(raw)) raw += "Z";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "–";
}

export function toUnix(datetimeLocal: string): number | undefined {
  if (!datetimeLocal) return undefined;
  const ms = Date.parse(`${datetimeLocal}:00Z`.replace(/:00:00Z$/, ":00Z"));
  const parsed = Number.isNaN(ms) ? Date.parse(`${datetimeLocal}Z`) : ms;
  return Number.isNaN(parsed) ? undefined : Math.floor(parsed / 1000);
}

export function truncateMiddle(text: string, max = 46): string {
  if (text.length <= max) return text;
  const half = Math.floor((max - 1) / 2);
  return `${text.slice(0, half)}…${text.slice(text.length - half)}`;
}
