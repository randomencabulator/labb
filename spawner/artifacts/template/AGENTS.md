# This page

A web page made in a labb chat: Next.js 16 (App Router) built as a static site, React 19,
TypeScript, Tailwind CSS 4, components on Base UI, Phosphor icons, ten chart components and typed
diagrams. Everything it can use is installed already: there's no internet when it builds, so no new
packages.

## Files

```
app/page.tsx          the page: replace what's there with what was asked for
app/layout.tsx        the page's title and description (metadata), fonts
app/globals.css       the colours, light and dark: change the look here
app/<name>/page.tsx   another page, at <name>/ (link to it with next/link's <Link href="/<name>">)
components/ui/        buttons, cards, forms, tabs, dialogs, tables… (import from "@/components/ui")
components/charts/    the charts (import from "@/components/charts")
components/diagram/   diagrams (import from "@/components/diagram")
examples/             a dashboard, diagrams and a form to copy from; not part of the page
lib/cn.ts             cn(): joins class names
```

## Rules

- **Start every page and component file with `"use client";`** so hooks and state work.
- **It's a static site.** No API routes, server actions, `cookies()`, `headers()` or middleware.
  Data goes in the page or in a typed file (`data/sales.ts`). It may `fetch()` public APIs in the
  browser, in a `useEffect`.
- **Only what's installed:** `next`, `react`, `@base-ui/react`, `@phosphor-icons/react`,
  `recharts`, `@xyflow/react`, `elkjs`, `clsx`, `tailwind-merge`, `geist`. No `next/font/google`
  (no internet when it builds); Geist Sans and Mono are already set up (`font-sans`, `font-mono`).
- **Links and images:** use `<Link>` from `next/link` for pages, and plain `<img>` for pictures.
  Don't write the site's path into a link (`/artifacts/…`): it's added for you.
- **Colours come from the theme,** never literal ones (`#fff`, `text-blue-500`): `bg-background`,
  `text-foreground`, `bg-card`, `bg-muted`, `text-muted-foreground`, `border` (or `border-border`),
  `bg-primary text-primary-foreground`, `bg-accent text-accent-foreground`, `text-destructive`,
  `text-success`, `text-warning`, and `chart-1` … `chart-6`. Dark mode follows the system by itself
  (and `?theme=dark` or `?theme=light` in the address): never use `dark:` classes.
- **Make it work on a phone:** lay out with `flex`/`grid` and breakpoints (`sm:`, `lg:`), and keep
  wide things (tables, charts) in a container that fits.
- **The build checks the types.** If it fails, the error names the file, the line and what was
  expected: fix that, don't loosen the types (`any`, `as`, `@ts-ignore`).

## Components (`@/components/ui`)

```tsx
<Button variant="primary|outline|ghost|destructive|link" size="sm|md|lg|icon" onClick={…}>Save</Button>
<Card><CardHeader><CardTitle>…</CardTitle><CardDescription>…</CardDescription></CardHeader><CardContent>…</CardContent><CardFooter>…</CardFooter></Card>
<Badge variant="default|primary|outline|success|warning|destructive">New</Badge>
<Stat label="Revenue" value="€12.4k" change={8.2} hint="vs. last month" />
<Table rows={orders} columns={[{ key: "id", header: "Order" }, { key: "total", header: "Total", align: "right", render: (row) => `€${row.total}` }]} />
<Tabs defaultValue="a"><TabsList><Tab value="a">A</Tab><Tab value="b">B</Tab></TabsList><TabsPanel value="a">…</TabsPanel><TabsPanel value="b">…</TabsPanel></Tabs>
<Accordion items={[{ title: "Question", content: "Answer" }]} />
<Avatar name="Ada Lovelace" src={optionalUrl} />
<Progress label="Done" value={60} />
<Separator />

<Field label="Email" description="…" error={problem}><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
<Textarea />
<Select items={[{ value: "week", label: "This week" }, { value: "month", label: "This month" }]} value={range} onValueChange={setRange} />
<Checkbox label="I agree" checked={ok} onCheckedChange={setOk} />
<Switch label="Notifications" checked={on} onCheckedChange={setOn} />
<Slider value={n} onValueChange={(v) => setN(v as number)} min={0} max={100} />

<Dialog trigger={<Button>Open</Button>} title="Title" description="…">…</Dialog>
<Popover trigger={<Button variant="outline">Filters</Button>}>…</Popover>
<Tooltip content="Download"><Button size="icon" aria-label="Download"><DownloadIcon /></Button></Tooltip>
<Menu trigger={<Button variant="ghost" size="icon" aria-label="More"><DotsThreeIcon /></Button>} items={[{ label: "Edit", icon: <PencilSimpleIcon />, onClick }, "separator", { label: "Delete", danger: true }]} />
```

A component that isn't here can be built on Base UI the same way (`components/ui/*.tsx` show how);
its documentation is in `node_modules/@base-ui/react/docs/react/components/<name>.md`.

## Icons (`@phosphor-icons/react`)

Every name ends in `Icon`: `HouseIcon`, `MagnifyingGlassIcon`, `GearIcon`, `UserIcon`,
`ChartLineIcon`, `CalendarIcon`, `CheckIcon`, `XIcon`, `PlusIcon`, `ArrowRightIcon`, `TrashIcon`,
`DownloadIcon`, `EnvelopeIcon`, `StarIcon`, `HeartIcon`, `WarningIcon`, `InfoIcon`. Size them with
classes (`className="size-5"`); `weight="bold|fill|duotone|light|thin"` changes the style.

## Charts (`@/components/charts`)

Each takes `data` (an array of rows) and the fields to show, by name, checked against the rows: a
field that isn't there, or isn't a number where one's needed, fails the build. All take `height`
(300), `format` (how numbers show: `(v) => \`€${v}\``), `legend` and `className`. A series is a field
name or `{ key, label, color }`.

| Chart | For | Props |
| --- | --- | --- |
| `BarChart` | comparing amounts | `x`, `series`, `stacked`, `horizontal` |
| `LineChart` | change over time | `x`, `series`, `curved`, `dots` |
| `AreaChart` | totals over time, parts adding up | `x`, `series`, `stacked` |
| `ComboChart` | bars with lines over them | `x`, `bars`, `lines` |
| `PieChart` | parts of a whole (a few) | `name`, `value`, `donut` |
| `ScatterChart` | two numbers' relation, bubbles | `x`, `y`, `size`, `group`, `xLabel`, `yLabel` |
| `RadarChart` | things on the same measures | `axis`, `series` |
| `RadialChart` | progress towards goals | `name`, `value`, `max` |
| `FunnelChart` | how many get through each step | `name`, `value` |
| `Treemap` | a whole split into many parts | `name`, `value` |

```tsx
const sales = [{ month: "Jan", online: 120, shop: 80 }, { month: "Feb", online: 140, shop: 75 }];
<BarChart data={sales} x="month" series={["online", "shop"]} format={(v) => `€${v}`} />
```

Put a chart in a `Card`; it fills the card's width.

## Diagrams (`@/components/diagram`)

Boxes and arrows, laid out by themselves. Nodes are an object keyed by id, and every edge must use
those ids (a typo fails the build). `direction` is `"right"` (the default) or `"down"`.

```tsx
const flow = defineDiagram({
  direction: "down",
  nodes: {
    start: { label: "Order placed", kind: "start" },
    paid: { label: "Paid?", kind: "decision" },
    ship: { label: "Ship it", detail: "within 2 days" },
    remind: { label: "Send a reminder" },
    done: { label: "Done", kind: "end" },
  },
  edges: [
    { from: "start", to: "paid" },
    { from: "paid", to: "ship", label: "yes" },
    { from: "paid", to: "remind", label: "no", dashed: true },
    { from: "ship", to: "done" },
  ],
});
<Diagram diagram={flow} height={420} />
```

Kinds: `default`, `start`, `end`, `decision`, `person`, `service`, `data`, `note`. An edge can be
`dashed` or `animated`. Define a diagram outside the component (as above), not inside it.
