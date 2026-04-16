// Number formatting utilities used across all pages.

/** Format a number with comma separators: 91925.3 → "91,925.3" */
export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

/** Format bytes to human-readable: 1234567890 → "1.15 GB" */
export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i > 0 ? 2 : 0)} ${units[i]}`;
}

/** Format seconds as a compact duration: 3661 → "1h 1m", 86400 → "24h 0m" */
export function fmtDuration(seconds: number): string {
  if (seconds <= 0) return "0m";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h === 0) return `${m}m`;
  return `${h}h ${m}m`;
}

/** Format a ratio with 1 decimal: 91925.2345 → "91,925.2" */
export function fmtRatio(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const fixed = n.toFixed(1);
  const [whole, dec] = fixed.split(".");
  return `${parseInt(whole).toLocaleString("en-US")}.${dec}`;
}
