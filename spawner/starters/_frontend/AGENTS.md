# The app's page

This file is for coding agents changing the page. It's a React app, built with Vite, with
[shadcn/ui](https://ui.shadcn.com) components on Tailwind CSS v4, in plain JavaScript (JSX, not
TypeScript). There's no dev server and nothing to deploy: a change to these files is built (`npm
install`, `npm run build`) and served by itself a moment later, and a build that fails leaves the last
good page up (`look_at_app` says why it failed). It's served apart from the app's server, which only
gets the page's calls to `/api/…`.

## Files

| | |
|---|---|
| `index.html` | the page's shell: the app's name in `<title>`, its look on `<html>`, dark mode |
| `src/main.jsx` | starts React, with `TooltipProvider` and `Toaster` around the app (keep both) |
| `src/App.jsx` | the page itself: start here |
| `src/components/ui/` | the shadcn/ui components: the app's own files, to use and change |
| `src/index.css` | Tailwind, shadcn's base, the neutral colours and the theme's variables |
| `src/themes.css` | every look the app can have: colours, spacing and shadows, fonts |
| `src/shadcn.css` | shadcn's Tailwind variants and animations: leave it as it is |
| `src/lib/utils.js` | `cn()`, to join class names (later ones win) |
| `components.json` | shadcn's settings, for its CLI |
| `package.json` | packages, with exact versions |

`@/` is `src/`: `import { Button } from "@/components/ui/button"`. Put new components of the app's own
in `src/components/`, and keep `App.jsx` to the page's layout once it grows.

## The look

What was picked when the app was made is on `<html>` in `index.html`; `src/themes.css` has all of
them, so changing an attribute changes the whole app:

| Attribute | Values | What it changes |
|---|---|---|
| `data-color` | `neutral`, `blue`, `emerald`, `violet`, `amber` | primary colour, accents, focus rings, charts |
| `data-style` | `compact`, `balanced`, `roomy` | spacing (Tailwind's `--spacing`), corners (`--radius`), shadows |
| `data-font` | `geist`, `inter`, `jakarta`, `lora`, `mono` | the font of everything (headings use `font-heading`) |

Dark mode is the `dark` class on `<html>`, which `index.html` sets from the system. Every colour has a
light and a dark value, so a page that only uses the theme's colours works in both.

**Rules:**
- **Only the theme's colours**, as Tailwind classes: `bg-background`, `text-foreground`, `bg-card`,
  `text-card-foreground`, `bg-primary`, `text-primary-foreground`, `bg-secondary`, `bg-muted`,
  `text-muted-foreground`, `bg-accent`, `text-accent-foreground`, `text-destructive`, `border` (it's
  `border-border`), `ring-ring`, `bg-chart-1` to `bg-chart-5`. With opacity: `bg-primary/10`. Never
  Tailwind's palette (`bg-blue-500`), hex colours or `style={{ color }}`: they ignore the picked colours
  and break in dark mode.
- **Spacing and corners from Tailwind's scale** (`p-4`, `gap-2`, `rounded-lg`), never pixels
  (`p-[13px]`): they follow `data-style`.
- **Shadows**: `shadow-xs` to `shadow-2xl`, which follow `data-style`; compact is nearly flat.
- **Components first**: a button is `<Button>`, a text field `<Input>`, a box of content a `<Card>`.
- **Icons** from `lucide-react`: `import { Plus } from "lucide-react"`, then `<Plus />` (buttons size them).
- **Phones**: the page is often narrow. Use `flex-wrap`, `grid` with `sm:`/`md:` columns, and
  `w-full max-w-…` rather than fixed widths.

## Components

All in `src/components/ui/`, each imported from its file (`@/components/ui/<file>`):

| File | Exports | Notes |
|---|---|---|
| `button` | `Button`, `buttonVariants` | `variant`: `default`, `outline`, `secondary`, `ghost`, `destructive`, `link`; `size`: `default`, `xs`, `sm`, `lg`, `icon`, `icon-xs`, `icon-sm`, `icon-lg`; `asChild` to make a link look like one |
| `card` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardAction`, `CardContent`, `CardFooter` | `size="sm"` for a tighter one; `CardAction` goes in the header, on the right |
| `input` | `Input` | |
| `textarea` | `Textarea` | |
| `label` | `Label` | |
| `field` | `Field`, `FieldLabel`, `FieldDescription`, `FieldError`, `FieldGroup`, `FieldSet`, `FieldLegend`, `FieldContent`, `FieldTitle`, `FieldSeparator` | how forms are laid out (below) |
| `select` | `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem`, `SelectGroup`, `SelectLabel`, `SelectSeparator` | |
| `checkbox` | `Checkbox` | `checked`, `onCheckedChange` |
| `switch` | `Switch` | `checked`, `onCheckedChange` |
| `dialog` | `Dialog`, `DialogTrigger`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription`, `DialogFooter`, `DialogClose` | `open`/`onOpenChange` to control it |
| `sheet` | `Sheet`, `SheetTrigger`, `SheetContent`, `SheetHeader`, `SheetTitle`, `SheetDescription`, `SheetFooter`, `SheetClose` | a panel from an edge (`side`: `right`, `left`, `top`, `bottom`) |
| `dropdown-menu` | `DropdownMenu`, `DropdownMenuTrigger`, `DropdownMenuContent`, `DropdownMenuItem`, `DropdownMenuLabel`, `DropdownMenuSeparator`, `DropdownMenuGroup`, `DropdownMenuCheckboxItem`, `DropdownMenuRadioGroup`, `DropdownMenuRadioItem`, `DropdownMenuShortcut`, `DropdownMenuSub`, `DropdownMenuSubTrigger`, `DropdownMenuSubContent` | `variant="destructive"` on an item |
| `tabs` | `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` | `defaultValue` or `value`/`onValueChange`; `variant="line"` on `TabsList` |
| `table` | `Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableRow`, `TableHead`, `TableCell`, `TableCaption` | |
| `badge` | `Badge` | `variant`: `default`, `secondary`, `outline`, `destructive`, `ghost` |
| `alert` | `Alert`, `AlertTitle`, `AlertDescription`, `AlertAction` | `variant="destructive"` for errors |
| `avatar` | `Avatar`, `AvatarImage`, `AvatarFallback`, `AvatarGroup`, `AvatarGroupCount`, `AvatarBadge` | |
| `tooltip` | `Tooltip`, `TooltipTrigger`, `TooltipContent` | `TooltipTrigger asChild` around the button |
| `skeleton` | `Skeleton` | a grey block while something loads: `<Skeleton className="h-5 w-40" />` |
| `spinner` | `Spinner` | in a button while it works |
| `empty` | `Empty`, `EmptyHeader`, `EmptyMedia`, `EmptyTitle`, `EmptyDescription`, `EmptyContent` | what a list shows with nothing in it |
| `separator` | `Separator` | |
| `sonner` | `Toaster` | already in `main.jsx`; show one with `import { toast } from "sonner"`, `toast("Saved")`, `toast.error("…")` |

A component that isn't here (accordion, popover, calendar, command, chart, …): take it from
[ui.shadcn.com](https://ui.shadcn.com/docs/components), in the style these are written (JSX,
`import { X as XPrimitive } from "radix-ui"`, `cn` from `"cn"`), into `src/components/ui/<name>.jsx`,
and add the packages it needs to `package.json`. Change a component's file to change how it looks
everywhere; for one place, pass a `className` (it's merged, so it wins).

## Patterns

A form:

```jsx
<form onSubmit={save} className="flex flex-col gap-6">
  <FieldGroup>
    <Field>
      <FieldLabel htmlFor="title">Title</FieldLabel>
      <Input id="title" value={title} onChange={e => setTitle(e.target.value)} required />
      <FieldDescription>What people see first.</FieldDescription>
    </Field>
    <Field orientation="horizontal">
      <Switch id="done" checked={done} onCheckedChange={setDone} />
      <FieldLabel htmlFor="done">Done</FieldLabel>
    </Field>
  </FieldGroup>
  <Button type="submit" disabled={saving}>{saving && <Spinner />} Save</Button>
</form>
```

A list that loads: `Skeleton`s while it loads, an `Alert variant="destructive"` if it failed, `Empty`
when there's nothing, else the rows (a `Table`, or `Card`s in a `grid gap-4 sm:grid-cols-2`). Say what
happened after an action with `toast`.

## Data

- **The app's own server**, if it has one (`backend/`): `/api/…`, from the same address. With
  `src/lib/api.js` (the Python and Node starters have it), `await api("/api/items")`, or
  `api("/api/items", { method: "POST", body: { title } })`: JSON both ways, and a failed call throws
  an `Error` with what the server said, for `toast.error(error.message)`.
- **The app's database API**, if it's on: load its client from the API's port, with the comment that
  keeps Vite from bundling it:
  ```js
  const api = await import(/* @vite-ignore */ `${location.protocol}//${location.hostname}:${Number(location.port) + 100}/client.js`);
  ```
- Never put secrets or keys in the page: everything here is sent to the browser.

## Packages

Add them to `package.json` with an exact version (`"date-fns": "4.1.0"`, not `^4.1.0`). The build runs
`npm install`; there's no lockfile, since only running npm can make one, so never write one.
