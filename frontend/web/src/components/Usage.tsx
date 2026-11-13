import { useEffect, useMemo, useState } from "react";
import {
  type GatewayUsage,
  type Machine,
  type Usage,
  type UsageRange,
  type UserUsage,
  downloadUsageCsv,
  getGatewayUsage,
  getMachines,
  getServerUsage,
  getUsage,
  getUsersUsage,
} from "../api";
import { relativeTime, seconds } from "../format";

// --- Formatting ---------------------------------------------------------------

const whole = new Intl.NumberFormat();
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

/** 950, 12.4K, 3.1M: exact below 10,000 */
const count = (n: number) => (n < 10_000 ? whole.format(n) : compact.format(n));

/** $1,234.56, $0.42, $0.0031: cents from a dollar up, and small amounts to a few significant digits */
const usd = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });
const usdSmall = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumSignificantDigits: 2 });
const money = (n: number) => (n === 0 ? usd.format(0) : n < 0.01 ? (n < 0.0001 ? "<$0.0001" : usdSmall.format(n)) : usd.format(n));
/** A cost, or a dash for none (free, or no price set) */
const costText = (n: number | null | undefined) => (n == null ? "–" : money(n));

/** 45s, 12m, 3.5h */
function duration(ms: number) {
  if (ms < 60_000) return seconds(ms);
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

function bytes(n: number) {
  if (n < 1e6) return `${Math.round(n / 1e3)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}

const ms = (value: number | null) => (value == null ? "–" : seconds(value));

// --- The time range -----------------------------------------------------------

export type Period = "7" | "30" | "all";

const PERIODS: { id: Period; label: string }[] = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "all", label: "Everything" },
];

/** The range for a period: whole local days, ending today */
function rangeFor(period: Period): UsageRange {
  const tz = -new Date().getTimezoneOffset();
  if (period === "all") return { tz };
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (Number(period) - 1));
  return { since: start.toISOString(), tz };
}

function localDay(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function PeriodPicker({ value, onChange }: { value: Period; onChange: (period: Period) => void }) {
  return (
    <div className="segmented" role="radiogroup" aria-label="Time range">
      {PERIODS.map(p => (
        <button key={p.id} type="button" role="radio" aria-checked={value === p.id} onClick={() => onChange(p.id)}>
          {p.label}
        </button>
      ))}
    </div>
  );
}

/** Loads something for the range, again when it changes */
function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setError(null);
    load().then(
      result => current && setData(result),
      e => current && setError((e as Error).message),
    );
    return () => {
      current = false;
    };
  }, deps);
  return { data, error };
}

// --- Charts -------------------------------------------------------------------

// Categorical slots in a fixed order (the web chat always gets the first), and gray for "Other"
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const OTHER = "var(--series-other)";
const MAX_SERIES = SERIES.length;

type Series = { name: string; color: string };

/** Colors per source that stay put when the range changes: web first, machines by name */
function seriesFor(sources: string[]): { series: Series[]; nameOf: (source: string) => string } {
  const unique = [...new Set(sources)];
  const machines = unique.filter(s => s !== "web").sort();
  const ordered = [...(unique.includes("web") ? ["web"] : []), ...machines];
  const fits = ordered.length <= MAX_SERIES;
  const shown = fits ? ordered : ordered.slice(0, MAX_SERIES - 1);
  const series = shown.map((name, i) => ({ name, color: SERIES[i] }));
  if (!fits) series.push({ name: "Other", color: OTHER });
  return { series, nameOf: source => (shown.includes(source) ? source : "Other") };
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend">
      {series.map(s => (
        <span key={s.name}>
          <i style={{ background: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

type Column = { key: string; label: string; segments: { series: Series; value: number }[]; detail: string[] };

/** Columns from a baseline, stacked by series, with a tooltip on hover or focus */
function Columns({ columns, unit, ticks, format = n => `${count(n)} ${unit}` }: { columns: Column[]; unit: string; ticks: number[]; format?: (n: number) => string }) {
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...columns.map(c => c.segments.reduce((sum, s) => sum + s.value, 0)));
  const shown = active != null ? columns[active] : null;

  return (
    <div className="columns-chart">
      <div className="columns-max">
        {format(max)}
      </div>
      <div className="columns" onMouseLeave={() => setActive(null)}>
        {columns.map((column, i) => {
          const total = column.segments.reduce((sum, s) => sum + s.value, 0);
          const visible = column.segments.filter(s => s.value > 0);
          return (
            <button
              key={column.key}
              type="button"
              className={i === active ? "column active" : "column"}
              aria-label={`${column.label}: ${format(total)}`}
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            >
              <span className="stack" style={{ height: `${(total / max) * 100}%` }}>
                {visible.map(segment => (
                  <span key={segment.series.name} style={{ flexGrow: segment.value, background: segment.series.color }} />
                ))}
              </span>
            </button>
          );
        })}
      </div>
      <div className="columns-axis">
        {ticks.map(i => (
          <span key={i} style={{ left: `${((i + 0.5) / columns.length) * 100}%` }}>
            {columns[i]?.label}
          </span>
        ))}
      </div>
      {shown && (
        <div className={`chart-tooltip ${tooltipSide((active ?? 0) / columns.length)}`} style={{ left: `${(((active ?? 0) + 0.5) / columns.length) * 100}%` }} role="status">
          <b>{shown.label}</b>
          {shown.detail.map(line => (
            <div key={line}>{line}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Which way a tooltip opens, so it stays inside the chart near its edges */
const tooltipSide = (position: number) => (position < 0.25 ? "opens-right" : position > 0.75 ? "opens-left" : "");

const edgeTicks = (n: number) => (n <= 1 ? [0] : n < 6 ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1]);

type Metric = "tokens" | "cost";

/** Tokens (or dollars) per day (per month for long ranges), stacked by where they were used */
function DayChart({ usage, since, metric = "tokens" }: { usage: Usage; since?: string; metric?: Metric }) {
  const { columns, series } = useMemo(() => {
    const { series, nameOf } = seriesFor(usage.by_day.map(d => d.source));
    const first = since ? new Date(since) : usage.totals.first ? new Date(usage.totals.first) : new Date();
    const days: string[] = [];
    for (const day = new Date(first.getFullYear(), first.getMonth(), first.getDate()); day <= new Date(); day.setDate(day.getDate() + 1)) {
      days.push(localDay(day));
    }
    const monthly = days.length > 92;
    const keyOf = (day: string) => (monthly ? day.slice(0, 7) : day);
    const keys = [...new Set(days.map(keyOf))];
    const rows = new Map(keys.map(key => [key, new Map<string, { tokens: number; replies: number }>()]));
    for (const d of usage.by_day) {
      const row = rows.get(keyOf(d.day));
      if (!row) continue;
      const name = nameOf(d.source);
      const cell = row.get(name) ?? { tokens: 0, replies: 0 };
      cell.tokens += metric === "cost" ? (d.cost ?? 0) : d.input_tokens + d.output_tokens;
      cell.replies += d.replies;
      row.set(name, cell);
    }
    const label = (key: string) =>
      monthly
        ? new Date(`${key}-01T12:00:00`).toLocaleDateString(undefined, { month: "short", year: "numeric" })
        : new Date(`${key}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const columns: Column[] = keys.map(key => {
      const row = rows.get(key)!;
      const replies = [...row.values()].reduce((sum, c) => sum + c.replies, 0);
      return {
        key,
        label: label(key),
        segments: series.map(s => ({ series: s, value: row.get(s.name)?.tokens ?? 0 })),
        detail: [
          `${whole.format(replies)} ${replies === 1 ? "reply" : "replies"}`,
          ...series
            .filter(s => row.has(s.name))
            .map(s => `${s.name}: ${metric === "cost" ? money(row.get(s.name)!.tokens) : `${whole.format(row.get(s.name)!.tokens)} tokens`}`),
        ],
      };
    });
    return { columns, series };
  }, [usage, since, metric]);

  return (
    <figure className="chart">
      <figcaption>
        {metric === "cost" ? "Cost" : "Tokens"} per {columns.length && columns[0].key.length === 7 ? "month" : "day"}
      </figcaption>
      <Legend series={series} />
      <Columns columns={columns} unit="tokens" ticks={edgeTicks(columns.length)} format={metric === "cost" ? money : undefined} />
    </figure>
  );
}

/** The day chart, in tokens or (with prices set) dollars */
function DayCharts({ usage, since, costs }: { usage: Usage; since?: string; costs: boolean }) {
  const [metric, setMetric] = useState<Metric>("tokens");
  const shown = costs ? metric : "tokens";
  return (
    <div className="day-charts">
      {costs && (
        <div className="segmented small" role="radiogroup" aria-label="Show">
          {(["tokens", "cost"] as const).map(m => (
            <button key={m} type="button" role="radio" aria-checked={shown === m} onClick={() => setMetric(m)}>
              {m === "tokens" ? "Tokens" : "Cost"}
            </button>
          ))}
        </div>
      )}
      <DayChart usage={usage} since={since} metric={shown} />
    </div>
  );
}

/** Replies by the hour of the day, with the typical wait for the first token */
function HourChart({ usage, gateway }: { usage: Usage; gateway?: GatewayUsage }) {
  const series: Series = { name: "Replies", color: SERIES[0] };
  const columns: Column[] = Array.from({ length: 24 }, (_, hour) => {
    const row = usage.by_hour.find(h => h.hour === hour);
    const calls = gateway?.by_hour.find(h => h.hour === hour);
    return {
      key: String(hour),
      label: `${String(hour).padStart(2, "0")}:00`,
      segments: [{ series, value: row?.replies ?? 0 }],
      detail: [
        `${whole.format(row?.replies ?? 0)} replies`,
        ...(row?.avg_first_token_ms != null ? [`first token ${seconds(row.avg_first_token_ms)} on average`] : []),
        ...(calls ? [`${whole.format(calls.requests)} model calls through the gateway${calls.failures ? `, ${calls.failures} failed` : ""}`] : []),
      ],
    };
  });
  return (
    <figure className="chart">
      <figcaption>Replies by hour of the day</figcaption>
      <Columns columns={columns} unit="replies" ticks={[0, 6, 12, 18, 23]} />
    </figure>
  );
}

/** Names with a bar for their share of the largest, and the value at the end */
function BarList({ title, rows }: { title: string; rows: { name: string; value: number; label: string; color?: string }[] }) {
  if (!rows.length) return null;
  const max = Math.max(1, ...rows.map(r => r.value));
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <ul className="bar-list">
        {rows.map(row => (
          <li key={row.name} title={`${row.name}: ${row.label}`}>
            <span className="bar-name">{row.name}</span>
            <span className="bar-track">
              <span className="bar" style={{ width: `${Math.max((row.value / max) * 100, 1)}%`, background: row.color }} />
            </span>
            <span className="bar-value">{row.label}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

function Tiles({ tiles }: { tiles: { label: string; value: string; note?: string }[] }) {
  return (
    <div className="tiles">
      {tiles.map(tile => (
        <div key={tile.label} className="tile">
          <div className="tile-value">{tile.value}</div>
          <div className="tile-label">{tile.label}</div>
          {tile.note && <div className="tile-note">{tile.note}</div>}
        </div>
      ))}
    </div>
  );
}

function totalTiles(usage: Usage) {
  const t = usage.totals;
  const unpriced = t.replies - t.priced_replies;
  return [
    { label: "Replies", value: count(t.replies), note: `in ${count(t.conversations)} conversation${t.conversations === 1 ? "" : "s"}` },
    ...(t.cost != null
      ? [{ label: "Cost", value: money(t.cost), note: unpriced ? `${count(unpriced)} free or without a price` : undefined }]
      : []),
    { label: "Tokens in", value: count(t.input_tokens), note: t.cache_read_tokens ? `${count(t.cache_read_tokens)} from the cache` : undefined },
    { label: "Tokens out", value: count(t.output_tokens) },
    { label: "Waiting for replies", value: duration(t.duration_ms) },
    { label: "Tool calls", value: count(t.tool_calls) },
    { label: "Stopped or failed", value: count(t.stopped + t.errors), note: t.errors ? `${t.errors} failed` : undefined },
  ];
}

type ToolCounts = { tool_calls: number; tool_repaired: number; tool_constrained: number; tool_failed: number };

/** The share of a model's tool calls that were right as the model wrote them */
const toolsOk = (m: ToolCounts) => {
  if (!m.tool_calls) return "–";
  const ok = m.tool_calls - m.tool_repaired - m.tool_constrained - m.tool_failed;
  return `${Math.max(0, Math.round((ok / m.tool_calls) * 100))}%`;
};

const toolNote = (m: ToolCounts) =>
  m.tool_calls
    ? `${whole.format(m.tool_calls)} calls: ${whole.format(m.tool_repaired)} fixed by labb, ` +
      `${whole.format(m.tool_constrained)} rewritten with constrained decoding, ${whole.format(m.tool_failed)} still broken`
    : "No tool calls";

function ModelsTable({ usage }: { usage: Usage }) {
  if (!usage.by_model.length) return null;
  const costs = usage.totals.cost != null;
  return (
    <table className="settings-table">
      <thead>
        <tr>
          <th>Model</th>
          <th className="num">Replies</th>
          <th className="num">Tokens</th>
          {costs && <th className="num">Cost</th>}
          <th className="num">First token</th>
          <th className="num">Slowest</th>
          <th className="num">Speed</th>
          <th className="num" title="Tool calls the model got right as written; the rest were fixed or failed">Tool calls OK</th>
        </tr>
      </thead>
      <tbody>
        {usage.by_model.map(m => (
          <tr key={m.model}>
            <td>{m.model}</td>
            <td className="num">{whole.format(m.replies)}</td>
            <td className="num" title={m.cache_read_tokens ? `${whole.format(m.cache_read_tokens)} input tokens from the cache` : undefined}>
              {count(m.input_tokens + m.output_tokens)}
            </td>
            {costs && (
              <td className="num" title={m.cost == null ? "Free, or no price set (Settings → Models → Prices)" : m.priced_replies < m.replies ? `${whole.format(m.replies - m.priced_replies)} replies from before it had a price` : undefined}>
                {costText(m.cost)}
              </td>
            )}
            <td className="num">{ms(m.avg_first_token_ms)}</td>
            <td className="num">{ms(m.max_first_token_ms)}</td>
            <td className="num">{m.tokens_per_second == null ? "–" : `${m.tokens_per_second} tok/s`}</td>
            <td className="num" title={toolNote(m)}>{toolsOk(m)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Csv({ onClick }: { onClick: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button type="button" className="csv" onClick={() => onClick().catch(e => setError((e as Error).message))}>
        Download CSV
      </button>
      {error && <span className="problem">{error}</span>}
    </>
  );
}

/** One person's numbers: tiles, charts and tables (the chats' titles only for their owner) */
function UsageView({ usage, range, onOpenChat }: { usage: Usage; range: UsageRange; onOpenChat?: (threadId: string) => void }) {
  if (!usage.totals.replies) return <p className="hint">No replies in this time.</p>;
  // The same color per source as in the chart
  const { series, nameOf } = seriesFor(usage.by_day.map(d => d.source));
  const colorOf = (source: string) => series.find(s => s.name === nameOf(source))?.color ?? OTHER;
  const costs = usage.totals.cost != null;
  return (
    <>
      <Tiles tiles={totalTiles(usage)} />
      <DayCharts usage={usage} since={range.since} costs={costs} />
      <div className="chart-pair">
        <BarList
          title="Where"
          rows={usage.by_source.map(s => ({
            name: s.source,
            value: s.input_tokens + s.output_tokens,
            label: `${count(s.input_tokens + s.output_tokens)} tokens${costs && s.cost != null ? ` · ${money(s.cost)}` : ""}`,
            color: colorOf(s.source),
          }))}
        />
        <BarList title="Tools used most" rows={usage.tools.map(t => ({ name: t.name, value: t.calls, label: whole.format(t.calls) }))} />
      </div>
      <h3>Models</h3>
      <ModelsTable usage={usage} />
      {usage.by_engine.some(h => h.engine) && (
        <>
          <h3>Engines</h3>
          <table className="settings-table">
            <thead><tr><th>Engine</th><th>Replies</th><th>Tokens</th><th>Average time</th><th>Model calls</th><th>Tool calls</th><th>Errors</th></tr></thead>
            <tbody>
              {usage.by_engine.map(h => (
                <tr key={h.engine ?? "default"}>
                  <td>{h.engine ?? "labb default"}</td>
                  <td>{whole.format(h.replies)}</td>
                  <td>{count(h.input_tokens + h.output_tokens)}</td>
                  <td>{(h.avg_duration_ms / 1000).toFixed(1)}s</td>
                  <td>{whole.format(h.llm_calls)}</td>
                  <td>{whole.format(h.tool_calls)}</td>
                  <td>{whole.format(h.errors)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {usage.top_conversations && usage.top_conversations.length > 0 && (
        <>
          <h3>Used the most tokens</h3>
          <table className="settings-table">
            <thead>
              <tr>
                <th>Conversation</th>
                <th>Where</th>
                <th className="num">Replies</th>
                <th className="num">Tokens</th>
                {costs && <th className="num">Cost</th>}
              </tr>
            </thead>
            <tbody>
              {usage.top_conversations.map(c => (
                <tr key={c.thread_id}>
                  <td>
                    {c.exists && onOpenChat ? (
                      <button type="button" className="link" onClick={() => onOpenChat(c.thread_id)}>
                        {c.title}
                      </button>
                    ) : (
                      <span className="hint">{c.title}</span>
                    )}
                  </td>
                  <td>{c.source}</td>
                  <td className="num">{whole.format(c.replies)}</td>
                  <td className="num">{count(c.tokens)}</td>
                  {costs && <td className="num">{costText(c.cost)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

// --- Your usage -----------------------------------------------------------------

export function UsageTab({ onOpenChat }: { onOpenChat: (threadId: string) => void }) {
  const [period, setPeriod] = useState<Period>("30");
  const range = useMemo(() => rangeFor(period), [period]);
  const { data, error } = useLoad(() => getUsage(range), [range]);

  return (
    <div className="usage">
      <div className="usage-bar">
        <PeriodPicker value={period} onChange={setPeriod} />
        <Csv onClick={() => downloadUsageCsv("/usage", range, "usage")} />
      </div>
      <p className="hint">Your replies in the web chat and from labb on your PCs (sessions they've sent to this server).</p>
      {error && <div className="problem">{error}</div>}
      {data && <UsageView usage={data} range={range} onOpenChat={onOpenChat} />}
    </div>
  );
}

// --- Server stats (admins) ------------------------------------------------------------

type Section = "overview" | "users" | "models" | "machines";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "users", label: "Users" },
  { id: "models", label: "Models" },
  { id: "machines", label: "Machines" },
];

function Overview({ range }: { range: UsageRange }) {
  const { data, error } = useLoad(() => getServerUsage(range), [range]);
  if (error) return <div className="problem">{error}</div>;
  if (!data) return null;
  const active = data.active_users;
  const storage = data.storage;
  return (
    <>
      <div className="usage-bar">
        <span />
        <Csv onClick={() => downloadUsageCsv("/admin/usage", range, "usage")} />
      </div>
      {active && (
        <Tiles
          tiles={[
            { label: "Active today", value: count(active.day) },
            { label: "Active this week", value: count(active.week) },
            { label: "Active this month", value: count(active.month) },
          ]}
        />
      )}
      <UsageView usage={data} range={range} />
      {storage && (
        <>
          <h3>Storage</h3>
          <Tiles
            tiles={[
              { label: "Database", value: bytes(storage.bytes) },
              { label: "Web chats", value: count(storage.chats) },
              { label: "Synced sessions", value: count(storage.synced_sessions) },
              { label: "Undo snapshots", value: count(storage.snapshots) },
            ]}
          />
        </>
      )}
    </>
  );
}

type SortKey = "username" | "replies" | "tokens" | "cost" | "last_active";

function UsersSection({ range, onPick }: { range: UsageRange; onPick: (user: UserUsage) => void }) {
  const { data, error } = useLoad(() => getUsersUsage(range), [range]);
  const [sort, setSort] = useState<{ key: SortKey; down: boolean }>({ key: "tokens", down: true });
  if (error) return <div className="problem">{error}</div>;
  if (!data) return null;

  const costs = data.some(u => u.cost != null);
  const value = (u: UserUsage) =>
    sort.key === "tokens" ? u.input_tokens + u.output_tokens : sort.key === "replies" ? u.replies : sort.key === "cost" ? (u.cost ?? 0) : (u[sort.key] ?? "");
  const rows = [...data].sort((a, b) => {
    const [x, y] = [value(a), value(b)];
    const order = x < y ? -1 : x > y ? 1 : 0;
    return sort.down ? -order : order;
  });
  const header = (key: SortKey, label: string, num = false) => (
    <th className={num ? "num" : undefined} aria-sort={sort.key === key ? (sort.down ? "descending" : "ascending") : undefined}>
      <button type="button" className="sort" onClick={() => setSort({ key, down: sort.key === key ? !sort.down : key !== "username" })}>
        {label}
        {sort.key === key ? (sort.down ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );

  return (
    <>
      <div className="usage-bar">
        <span className="hint">Click a user to see their usage.</span>
        <Csv onClick={() => downloadUsageCsv("/admin/usage/users", range, "users")} />
      </div>
      <table className="settings-table">
        <thead>
          <tr>
            {header("username", "User")}
            {header("replies", "Replies", true)}
            {header("tokens", "Tokens", true)}
            {costs && header("cost", "Cost", true)}
            {header("last_active", "Last active")}
            <th>Where</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(u => (
            <tr key={u.id} className={u.disabled ? "revoked" : undefined}>
              <td>
                <button type="button" className="link" onClick={() => onPick(u)}>
                  {u.username}
                </button>
              </td>
              <td className="num">{whole.format(u.replies)}</td>
              <td className="num">{count(u.input_tokens + u.output_tokens)}</td>
              {costs && <td className="num">{costText(u.cost)}</td>}
              <td>{u.last_active ? relativeTime(u.last_active) : "never"}</td>
              <td>{u.sources.join(", ") || "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function UserDetail({ user, range, onBack }: { user: UserUsage; range: UsageRange; onBack: () => void }) {
  const { data, error } = useLoad(() => getServerUsage(range, user.id), [range, user.id]);
  return (
    <>
      <div className="usage-bar">
        <button type="button" onClick={onBack}>
          ← All users
        </button>
        <Csv onClick={() => downloadUsageCsv("/admin/usage", range, `usage-${user.username}`, { user_id: user.id })} />
      </div>
      <h3>{user.username}</h3>
      <p className="hint">Models: {user.models.join(", ") || "none"}. Admins see numbers, not what anyone asked.</p>
      {error && <div className="problem">{error}</div>}
      {data && <UsageView usage={data} range={range} />}
    </>
  );
}

function ModelsSection({ range }: { range: UsageRange }) {
  const usage = useLoad(() => getServerUsage(range), [range]);
  const gateway = useLoad(() => getGatewayUsage(range), [range]);
  const error = usage.error ?? gateway.error;
  if (error) return <div className="problem">{error}</div>;
  if (!usage.data || !gateway.data) return null;
  const g = gateway.data;
  return (
    <>
      <div className="usage-bar">
        <span className="hint">Replies saved in chats and synced sessions, and every model call through the gateway as it happens.</span>
        <Csv onClick={() => downloadUsageCsv("/admin/usage/gateway", range, "gateway")} />
      </div>
      <ModelsTable usage={usage.data} />
      <HourChart usage={usage.data} gateway={g} />
      <p className="hint">
        Ollama answers one request at a time unless <code>OLLAMA_NUM_PARALLEL</code> is set: long first tokens at busy hours mean replies are
        waiting for each other.
      </p>
      <h3>Through the gateway</h3>
      <Tiles
        tiles={[
          { label: "Model calls", value: count(g.totals.requests) },
          { label: "Failed", value: count(g.totals.failures) },
          { label: "Tokens", value: count(g.totals.input_tokens + g.totals.output_tokens) },
          { label: "Pages read", value: count(g.totals.pages) },
        ]}
      />
      {g.by_model.length > 0 && (
        <table className="settings-table">
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">Calls</th>
              <th className="num">Failed</th>
              <th className="num">Tokens</th>
              <th className="num">Speed</th>
            </tr>
          </thead>
          <tbody>
            {g.by_model.map(m => (
              <tr key={m.model}>
                <td>{m.model}</td>
                <td className="num">{whole.format(m.requests)}</td>
                <td className="num">{whole.format(m.failures)}</td>
                <td className="num">{count(m.input_tokens + m.output_tokens)}</td>
                <td className="num">{m.tokens_per_second == null ? "–" : `${m.tokens_per_second} tok/s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function MachinesSection({ range }: { range: UsageRange }) {
  const { data, error } = useLoad(() => getMachines(range), [range]);
  if (error) return <div className="problem">{error}</div>;
  if (!data) return null;
  const outdated = data.machines.filter(m => m.outdated).length;
  return (
    <>
      <div className="usage-bar">
        <span className="hint">
          The server has labb {data.server_version}.{" "}
          {outdated ? `${outdated} key${outdated === 1 ? "" : "s"} last ran an older version: run labb --update there.` : ""}
        </span>
        <Csv onClick={() => downloadUsageCsv("/admin/usage/machines", range, "machines")} />
      </div>
      {data.machines.length === 0 ? (
        <p className="hint">No keys yet.</p>
      ) : (
        <table className="settings-table">
          <thead>
            <tr>
              <th>Machine</th>
              <th>User</th>
              <th>Version</th>
              <th>Last seen</th>
              <th className="num">Model calls</th>
              <th className="num">Pages</th>
            </tr>
          </thead>
          <tbody>
            {data.machines.map((m: Machine) => (
              <tr key={m.key_id}>
                <td>
                  {m.machine ?? m.name}
                  {m.machine && m.machine !== m.name && <div className="hint">key “{m.name}”</div>}
                </td>
                <td>{m.username}</td>
                <td>{m.version ? <span className={m.outdated ? "badge old" : "badge"}>{m.version}{m.outdated ? " · update" : ""}</span> : "–"}</td>
                <td>{m.last_seen ? relativeTime(m.last_seen) : m.last_used_at ? relativeTime(m.last_used_at) : "never"}</td>
                <td className="num">{whole.format(m.requests)}</td>
                <td className="num">{whole.format(m.pages)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export function StatsTab({ section: shown, onSection: setSection }: { section?: string; onSection: (section: Section) => void }) {
  const [period, setPeriod] = useState<Period>("30");
  const section = SECTIONS.find(s => s.id === shown)?.id ?? "overview";
  const [picked, setPicked] = useState<UserUsage | null>(null);
  const range = useMemo(() => rangeFor(period), [period]);

  return (
    <div className="usage">
      <div className="usage-bar">
        <div className="segmented" role="tablist" aria-label="Section">
          {SECTIONS.map(s => (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={section === s.id}
              onClick={() => {
                setSection(s.id);
                setPicked(null);
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
        <PeriodPicker value={period} onChange={setPeriod} />
      </div>
      {section === "overview" && <Overview range={range} />}
      {section === "users" && (picked ? <UserDetail user={picked} range={range} onBack={() => setPicked(null)} /> : <UsersSection range={range} onPick={setPicked} />)}
      {section === "models" && <ModelsSection range={range} />}
      {section === "machines" && <MachinesSection range={range} />}
    </div>
  );
}
