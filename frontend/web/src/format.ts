import type { Stats } from "./api";

const number = new Intl.NumberFormat();

export function seconds(ms: number) {
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

/** "gemma4:e4b · 12.4s · first token 1.8s · 3,688 in / 412 out · 39 tok/s · 2 tool calls" */
export function formatStats(stats: Stats) {
  const parts = [stats.model, seconds(stats.duration_ms)];
  if (stats.engine) parts.splice(1, 0, stats.engine.split("@")[0]);
  if (stats.first_token_ms != null) parts.push(`first token ${seconds(stats.first_token_ms)}`);
  // A stopped model call reports no usage: its input is unknown and its output is estimated
  const output = `${stats.stopped ? "~" : ""}${number.format(stats.output_tokens)} out`;
  if (stats.input_tokens) parts.push(`${number.format(stats.input_tokens)} in / ${output}`);
  else if (stats.output_tokens) parts.push(output);
  if (stats.generation_ms) parts.push(`${Math.round(stats.output_tokens / (stats.generation_ms / 1000))} tok/s`);
  if (stats.tool_calls) parts.push(`${stats.tool_calls} tool call${stats.tool_calls === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/** "now", "5m", "3h", "2d", then the date */
export function relativeTime(iso: string) {
  const date = new Date(iso);
  const minutes = (Date.now() - date.getTime()) / 60_000;
  if (minutes < 1) return "now";
  if (minutes < 60) return `${Math.floor(minutes)}m`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h`;
  if (minutes < 60 * 24 * 7) return `${Math.floor(minutes / 60 / 24)}d`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A moment near now, in local time: "today 08:00", "tomorrow 08:00", "Mon 26 Oct, 08:00" */
export function moment(iso: string) {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((day.getTime() - today.getTime()) / 86_400_000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  if (days === -1) return `yesterday ${time}`;
  return `${date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
}
