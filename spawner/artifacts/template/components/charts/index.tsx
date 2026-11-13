"use client";

// Ten ready-made charts on Recharts, typed by the rows they get: `x` must be a field of the row and
// every series a field holding numbers, so a typo or a text field is an error when the page builds.
//
//   const sales = [{ month: "Jan", online: 120, shop: 80 }, …];
//   <BarChart data={sales} x="month" series={["online", "shop"]} />
//
// Colours come from the theme (--chart-1 … --chart-6 in app/globals.css) unless a series gives one.

import type { ReactNode } from "react";
import {
  Area,
  AreaChart as RAreaChart,
  Bar,
  BarChart as RBarChart,
  CartesianGrid,
  ComposedChart as RComposedChart,
  Funnel,
  FunnelChart as RFunnelChart,
  LabelList,
  Legend,
  Line,
  LineChart as RLineChart,
  Pie,
  PieChart as RPieChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart as RRadarChart,
  RadialBar,
  RadialBarChart as RRadialBarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart as RScatterChart,
  Tooltip,
  Treemap as RTreemap,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { cn } from "@/lib/cn";

/** A field of the row */
export type Key<T> = Extract<keyof T, string>;
/** A field of the row that holds numbers */
export type NumberKey<T> = { [K in keyof T]-?: T[K] extends number | null | undefined ? K : never }[keyof T] & string;
/** A series: a number field, or one with its label and colour */
export type Series<T> = NumberKey<T> | { key: NumberKey<T>; label?: string; color?: string };

export const palette = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];

const colorAt = (i: number) => palette[i % palette.length]!;

function seriesOf<T>(series: readonly Series<T>[]) {
  return series.map((s, i) =>
    typeof s === "string" ? { key: s, label: s, color: colorAt(i) } : { key: s.key, label: s.label ?? s.key, color: s.color ?? colorAt(i) },
  );
}

/** What every chart takes besides its data */
export type ChartBase = {
  /** Height in pixels (300); the width fills its container */
  height?: number;
  /** How a number is shown on the axis and in the tooltip, e.g. (v) => `€${v}` */
  format?: (value: number) => string;
  /** Show the legend (on by default with more than one series) */
  legend?: boolean;
  className?: string;
};

function Frame({ height = 300, className, children }: { height?: number; className?: string; children: ReactNode }) {
  return (
    <div className={cn("w-full text-xs", className)} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

const axis = { stroke: "var(--muted-foreground)", fontSize: 12, tickLine: false, axisLine: false } as const;

function tooltip(format?: (value: number) => string) {
  return (
    <Tooltip
      cursor={{ fill: "var(--muted)", opacity: 0.6 }}
      contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--foreground)" }}
      formatter={(value) => (typeof value === "number" && format ? format(value) : String(value))}
    />
  );
}

const grid = <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />;
const legendOf = (shown: boolean) => (shown ? <Legend iconType="circle" iconSize={8} /> : null);
const rows = <T,>(data: readonly T[]) => data as T[];

export type BarChartProps<T> = ChartBase & {
  data: readonly T[];
  /** The field along the axis: a month, a name, a category */
  x: Key<T>;
  series: readonly Series<T>[];
  /** Bars on top of each other instead of side by side */
  stacked?: boolean;
  /** Bars going right instead of up */
  horizontal?: boolean;
};

/** 1. Bars: compare amounts across categories */
export function BarChart<T>({ data, x, series, stacked, horizontal, height, format, legend, className }: BarChartProps<T>) {
  const all = seriesOf(series);
  return (
    <Frame height={height} className={className}>
      <RBarChart data={rows(data)} layout={horizontal ? "vertical" : "horizontal"}>
        {grid}
        {horizontal ? (
          <>
            <XAxis type="number" {...axis} tickFormatter={format} />
            <YAxis type="category" dataKey={x} {...axis} width={90} />
          </>
        ) : (
          <>
            <XAxis dataKey={x} {...axis} />
            <YAxis {...axis} tickFormatter={format} width={48} />
          </>
        )}
        {tooltip(format)}
        {legendOf(legend ?? all.length > 1)}
        {all.map((s) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} stackId={stacked ? "stack" : undefined} radius={stacked ? 0 : 4} maxBarSize={48} />
        ))}
      </RBarChart>
    </Frame>
  );
}

export type LineChartProps<T> = ChartBase & {
  data: readonly T[];
  x: Key<T>;
  series: readonly Series<T>[];
  /** Smooth curves instead of straight segments */
  curved?: boolean;
  /** A dot on every point */
  dots?: boolean;
};

/** 2. Lines: how values change over time */
export function LineChart<T>({ data, x, series, curved = true, dots, height, format, legend, className }: LineChartProps<T>) {
  const all = seriesOf(series);
  return (
    <Frame height={height} className={className}>
      <RLineChart data={rows(data)}>
        {grid}
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} tickFormatter={format} width={48} />
        {tooltip(format)}
        {legendOf(legend ?? all.length > 1)}
        {all.map((s) => (
          <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} type={curved ? "monotone" : "linear"} dot={dots ?? false} />
        ))}
      </RLineChart>
    </Frame>
  );
}

export type AreaChartProps<T> = ChartBase & {
  data: readonly T[];
  x: Key<T>;
  series: readonly Series<T>[];
  /** Areas on top of each other, adding up */
  stacked?: boolean;
};

/** 3. Areas: totals over time, or parts adding up to them (stacked) */
export function AreaChart<T>({ data, x, series, stacked, height, format, legend, className }: AreaChartProps<T>) {
  const all = seriesOf(series);
  return (
    <Frame height={height} className={className}>
      <RAreaChart data={rows(data)}>
        {grid}
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} tickFormatter={format} width={48} />
        {tooltip(format)}
        {legendOf(legend ?? all.length > 1)}
        {all.map((s) => (
          <Area key={s.key} dataKey={s.key} name={s.label} stroke={s.color} fill={s.color} fillOpacity={0.2} strokeWidth={2} type="monotone" stackId={stacked ? "stack" : undefined} />
        ))}
      </RAreaChart>
    </Frame>
  );
}

export type ComboChartProps<T> = ChartBase & {
  data: readonly T[];
  x: Key<T>;
  /** Series drawn as bars */
  bars: readonly Series<T>[];
  /** Series drawn as lines over them */
  lines: readonly Series<T>[];
};

/** 4. Bars and lines together: amounts, and a rate or target over them */
export function ComboChart<T>({ data, x, bars, lines, height, format, legend, className }: ComboChartProps<T>) {
  const all = seriesOf([...bars, ...lines]);
  return (
    <Frame height={height} className={className}>
      <RComposedChart data={rows(data)}>
        {grid}
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} tickFormatter={format} width={48} />
        {tooltip(format)}
        {legendOf(legend ?? true)}
        {all.slice(0, bars.length).map((s) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} radius={4} maxBarSize={48} />
        ))}
        {all.slice(bars.length).map((s) => (
          <Line key={s.key} dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} type="monotone" dot={false} />
        ))}
      </RComposedChart>
    </Frame>
  );
}

export type PieChartProps<T> = ChartBase & {
  data: readonly T[];
  /** The field naming each slice */
  name: Key<T>;
  /** The number field sizing each slice */
  value: NumberKey<T>;
  /** A ring instead of a full pie */
  donut?: boolean;
};

/** 5. Pie or donut: parts of one whole (keep it to a few slices) */
export function PieChart<T>({ data, name, value, donut, height, format, legend, className }: PieChartProps<T>) {
  const slices = data.map((row, i) => ({ ...row, fill: colorAt(i) }));
  return (
    <Frame height={height} className={className}>
      <RPieChart>
        {tooltip(format)}
        {legendOf(legend ?? true)}
        <Pie data={slices} dataKey={value} nameKey={name} innerRadius={donut ? "55%" : 0} outerRadius="80%" paddingAngle={donut ? 2 : 0} stroke="var(--card)" />
      </RPieChart>
    </Frame>
  );
}

export type ScatterChartProps<T> = ChartBase & {
  data: readonly T[];
  x: NumberKey<T>;
  y: NumberKey<T>;
  /** A number field sizing each dot (a bubble chart) */
  size?: NumberKey<T>;
  /** A field splitting the dots into coloured groups */
  group?: Key<T>;
  xLabel?: string;
  yLabel?: string;
};

/** 6. Scatter or bubbles: how two numbers relate */
export function ScatterChart<T>({ data, x, y, size, group, xLabel, yLabel, height, format, legend, className }: ScatterChartProps<T>) {
  const groups = group ? [...new Set(data.map((row) => String(row[group])))] : [""];
  return (
    <Frame height={height} className={className}>
      <RScatterChart>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis type="number" dataKey={x} name={xLabel ?? x} {...axis} tickFormatter={format} />
        <YAxis type="number" dataKey={y} name={yLabel ?? y} {...axis} tickFormatter={format} width={48} />
        {size && <ZAxis type="number" dataKey={size} range={[40, 400]} />}
        {tooltip(format)}
        {legendOf(legend ?? groups.length > 1)}
        {groups.map((name, i) => (
          <Scatter key={name} name={name || undefined} data={rows(group ? data.filter((row) => String(row[group]) === name) : data)} fill={colorAt(i)} fillOpacity={0.75} />
        ))}
      </RScatterChart>
    </Frame>
  );
}

export type RadarChartProps<T> = ChartBase & {
  data: readonly T[];
  /** The field naming each spoke: a skill, a feature */
  axis: Key<T>;
  series: readonly Series<T>[];
};

/** 7. Radar: several things compared on the same few measures */
export function RadarChart<T>({ data, axis: spoke, series, height, format, legend, className }: RadarChartProps<T>) {
  const all = seriesOf(series);
  return (
    <Frame height={height} className={className}>
      <RRadarChart data={rows(data)} outerRadius="75%">
        <PolarGrid stroke="var(--border)" />
        <PolarAngleAxis dataKey={spoke} stroke="var(--muted-foreground)" fontSize={12} />
        {tooltip(format)}
        {legendOf(legend ?? all.length > 1)}
        {all.map((s) => (
          <Radar key={s.key} dataKey={s.key} name={s.label} stroke={s.color} fill={s.color} fillOpacity={0.2} strokeWidth={2} />
        ))}
      </RRadarChart>
    </Frame>
  );
}

export type RadialChartProps<T> = ChartBase & {
  data: readonly T[];
  name: Key<T>;
  value: NumberKey<T>;
  /** The value a full ring stands for (the largest value by default) */
  max?: number;
};

/** 8. Radial bars: progress of a few things towards a goal */
export function RadialChart<T>({ data, name, value, max, height, format, legend, className }: RadialChartProps<T>) {
  const rings = data.map((row, i) => ({ ...row, name: String(row[name]), fill: colorAt(i) }));
  const top = max ?? Math.max(...data.map((row) => Number(row[value]) || 0));
  return (
    <Frame height={height} className={className}>
      <RRadialBarChart data={rings} innerRadius="25%" outerRadius="95%" startAngle={90} endAngle={-270} barSize={14}>
        <PolarAngleAxis type="number" domain={[0, top]} tick={false} />
        {tooltip(format)}
        {legendOf(legend ?? true)}
        <RadialBar dataKey={value} background={{ fill: "var(--muted)" }} cornerRadius={7} />
      </RRadialBarChart>
    </Frame>
  );
}

export type FunnelChartProps<T> = ChartBase & {
  /** The steps, from the first (largest) to the last */
  data: readonly T[];
  name: Key<T>;
  value: NumberKey<T>;
};

/** 9. Funnel: how many get through each step */
export function FunnelChart<T>({ data, name, value, height, format, className }: FunnelChartProps<T>) {
  const steps = data.map((row, i) => ({ ...row, fill: colorAt(i) }));
  return (
    <Frame height={height} className={className}>
      <RFunnelChart>
        {tooltip(format)}
        <Funnel data={steps} dataKey={value} nameKey={name} isAnimationActive stroke="var(--card)">
          <LabelList position="right" dataKey={name} fill="var(--foreground)" stroke="none" fontSize={12} />
        </Funnel>
      </RFunnelChart>
    </Frame>
  );
}

export type TreemapProps<T> = ChartBase & {
  data: readonly T[];
  name: Key<T>;
  value: NumberKey<T>;
};

type Tile = { x?: number; y?: number; width?: number; height?: number; index?: number; name?: string };

/** 10. Treemap: how a whole splits into many parts, by area */
export function Treemap<T>({ data, name, value, height, className }: TreemapProps<T>) {
  const tiles = data.map((row) => ({ name: String(row[name]), size: Number(row[value]) || 0 }));
  return (
    <Frame height={height} className={className}>
      <RTreemap
        data={tiles}
        dataKey="size"
        nameKey="name"
        isAnimationActive={false}
        content={({ x = 0, y = 0, width = 0, height: h = 0, index = 0, name: label }: Tile) => (
          <g>
            <rect x={x} y={y} width={width} height={h} rx={4} fill={colorAt(index)} stroke="var(--card)" strokeWidth={2} />
            {width > 50 && h > 24 && (
              <text x={x + 8} y={y + 18} fill="white" fontSize={12} fontWeight={500}>
                {label}
              </text>
            )}
          </g>
        )}
      />
    </Frame>
  );
}
