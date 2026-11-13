"use client";

// An example to copy from, not part of the page: a dashboard with every chart. The page builds it
// too (its types are checked), so it always works with the components as they are.

import { useState } from "react";
import { ChartBarIcon, TableIcon } from "@phosphor-icons/react";
import {
  AreaChart,
  BarChart,
  ComboChart,
  FunnelChart,
  LineChart,
  PieChart,
  RadarChart,
  RadialChart,
  ScatterChart,
  Treemap,
} from "@/components/charts";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Select, Stat, Tab, Table, Tabs, TabsList, TabsPanel } from "@/components/ui";

const months = [
  { month: "Jan", online: 120, shop: 80, returns: 6 },
  { month: "Feb", online: 140, shop: 75, returns: 9 },
  { month: "Mar", online: 180, shop: 90, returns: 7 },
  { month: "Apr", online: 170, shop: 110, returns: 12 },
  { month: "May", online: 210, shop: 120, returns: 10 },
  { month: "Jun", online: 260, shop: 115, returns: 14 },
];

const channels = [
  { channel: "Search", visits: 4200 },
  { channel: "Social", visits: 2600 },
  { channel: "Email", visits: 1500 },
  { channel: "Direct", visits: 1100 },
];

const products = [
  { name: "Lamp", price: 40, sold: 320, rating: 4.4, line: "Home" },
  { name: "Chair", price: 120, sold: 140, rating: 4.1, line: "Home" },
  { name: "Mug", price: 12, sold: 900, rating: 4.7, line: "Kitchen" },
  { name: "Kettle", price: 55, sold: 260, rating: 3.9, line: "Kitchen" },
];

const skills = [
  { skill: "Speed", ours: 80, theirs: 65 },
  { skill: "Price", ours: 60, theirs: 85 },
  { skill: "Support", ours: 90, theirs: 55 },
  { skill: "Range", ours: 70, theirs: 75 },
  { skill: "Design", ours: 85, theirs: 60 },
];

const goals = [
  { team: "North", done: 82 },
  { team: "South", done: 64 },
  { team: "West", done: 45 },
];

const signup = [
  { step: "Visited", people: 10000 },
  { step: "Signed up", people: 3200 },
  { step: "Activated", people: 1400 },
  { step: "Paid", people: 380 },
];

const euros = (value: number) => `€${value}`;

export default function Dashboard() {
  const [range, setRange] = useState<"half" | "quarter">("half");
  const shown = range === "half" ? months : months.slice(3);

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sales</h1>
          <p className="text-sm text-muted-foreground">First half of the year</p>
        </div>
        <Select
          aria-label="Range"
          items={[
            { value: "half", label: "Six months" },
            { value: "quarter", label: "Last quarter" },
          ]}
          value={range}
          onValueChange={setRange}
        />
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Revenue" value="€48.2k" change={12.5} hint="vs. last half" />
        <Stat label="Orders" value="1,920" change={4.1} />
        <Stat label="Returns" value="58" change={-2.3} />
        <Stat label="Rating" value="4.4" hint="from 812 reviews" />
      </div>

      <Tabs defaultValue="charts">
        <TabsList>
          <Tab value="charts"><ChartBarIcon /> Charts</Tab>
          <Tab value="table"><TableIcon /> Table</Tab>
        </TabsList>
        <TabsPanel value="charts" className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle>Sales by month</CardTitle><CardDescription>Bars, side by side</CardDescription></CardHeader>
            <CardContent><BarChart data={shown} x="month" series={["online", "shop"]} format={euros} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Trend</CardTitle></CardHeader>
            <CardContent><LineChart data={shown} x="month" series={[{ key: "online", label: "Online" }, { key: "shop", label: "In shop" }]} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Total, stacked</CardTitle></CardHeader>
            <CardContent><AreaChart data={shown} x="month" series={["online", "shop"]} stacked /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Sales and returns</CardTitle></CardHeader>
            <CardContent><ComboChart data={shown} x="month" bars={["online"]} lines={[{ key: "returns", color: "var(--chart-4)" }]} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Visits by channel</CardTitle></CardHeader>
            <CardContent><PieChart data={channels} name="channel" value="visits" donut /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Price and sales</CardTitle></CardHeader>
            <CardContent><ScatterChart data={products} x="price" y="sold" size="rating" group="line" xLabel="Price" yLabel="Sold" /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Us and them</CardTitle></CardHeader>
            <CardContent><RadarChart data={skills} axis="skill" series={["ours", "theirs"]} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Goals reached</CardTitle></CardHeader>
            <CardContent><RadialChart data={goals} name="team" value="done" max={100} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Sign-up funnel</CardTitle></CardHeader>
            <CardContent><FunnelChart data={signup} name="step" value="people" /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Units sold</CardTitle></CardHeader>
            <CardContent><Treemap data={products} name="name" value="sold" /></CardContent>
          </Card>
        </TabsPanel>
        <TabsPanel value="table">
          <Table
            rows={products}
            rowKey={(row) => row.name}
            columns={[
              { key: "name", header: "Product" },
              { key: "line", header: "Line", render: (row) => <Badge>{row.line}</Badge> },
              { key: "price", header: "Price", align: "right", render: (row) => euros(row.price) },
              { key: "sold", header: "Sold", align: "right" },
            ]}
          />
        </TabsPanel>
      </Tabs>
    </main>
  );
}
