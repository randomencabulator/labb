# labb's React components

From `@labb/ui`, with `@labb/ui/styles.css` and `@labb/tokens/tokens.css` loaded once at the root, and `<body className="lb-root">`. Toasts also need a `ToastProvider` around the app.

## Button

Hairline buttons for most things; one yellow primary per view, which sits on a hard shadow, lifts on hover and presses down when clicked.

- In the web app: `button, button.primary, button.danger, button.link`
- Built on Base UI's [Button](https://base-ui.com/react/components/button)
- React, from `@labb/ui`: `import { Button } from "@labb/ui";`

### Variants

```tsx
<Button variant="primary">New chat</Button>
<Button>Cancel</Button>
<Button variant="danger">Delete</Button>
<Button variant="ghost">More</Button>
<Button variant="link">Show details</Button>
```

### Sizes

```tsx
<Button size="sm">Small</Button>
<Button>Medium</Button>
<Button size="lg" variant="primary">Send</Button>
```

### Disabled and icon

focusableWhenDisabled keeps a disabled button in the tab order, for one that's waiting rather than unavailable.

```tsx
<Button variant="primary" disabled>Sending…</Button>
<Button variant="primary" disabled focusableWhenDisabled>
  Focusable while disabled
</Button>
<Button icon aria-label="Settings">⚙</Button>
```

---

## Toggle and segmented

Chips that turn something on, like Web next to the model picker, and a segmented control for picking one of a few, like the theme.

- In the web app: `.toggle-pill, .menu-theme`
- Built on Base UI's [Toggle and Toggle Group](https://base-ui.com/react/components/toggle-group)
- React, from `@labb/ui`: `import { Segmented, Toggle } from "@labb/ui";`

### Toggle

Click it, or tab to it and press Space.

```tsx
<Toggle pressed={web} onPressedChange={setWeb}>
  🌐 Web
</Toggle>
```

### Segmented

One is always picked; the arrow keys move between them.

```tsx
<Segmented
  label="Theme"
  value={theme}
  onChange={setTheme}
  options={[
    { value: "system", label: "System" },
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
  ]}
/>
```

---

## Switch

An on/off switch, yellow when on. It takes part in forms like a checkbox.

- In the web app: `.switch`
- Built on Base UI's [Switch](https://base-ui.com/react/components/switch)
- React, from `@labb/ui`: `import { Switch } from "@labb/ui";`

### States

```tsx
<Switch checked={on} onCheckedChange={setOn} aria-label="Memories" />
<Switch aria-label="Notes" />
<Switch disabled aria-label="Browser" />
```

---

## Field and inputs

A label over its control, with a hint or an error under it. Inputs sit on the page colour and take an outline in the accent when focused.

- In the web app: `.field, .auth-form input, textarea`
- Built on Base UI's [Field and Input](https://base-ui.com/react/components/field)
- React, from `@labb/ui`: `import { Field, Input, Textarea } from "@labb/ui";`

### Input

```tsx
<Field label="Name" hint="Shown to people you share with.">
  <Input placeholder="Research helper" />
</Field>
```

### Error

An error marks the field and its control invalid, and says so to screen readers.

```tsx
<Field label="Email" error="That doesn't look like an email.">
  <Input defaultValue="ada@" />
</Field>
```

### Textarea

```tsx
<Field label="Instructions">
  <Textarea placeholder="What should this agent do?" />
</Field>
```

---

## Badge

Small labels and counts; the yellow one for what needs looking at, like unread tasks.

- In the web app: `.badge, aside .pages .badge, .unread-dot`
- Plain markup; no Base UI primitive
- React, from `@labb/ui`: `import { Badge, Dot } from "@labb/ui";`

### Tones

```tsx
<Badge>shared</Badge>
<Badge tone="accent">3</Badge>
<Badge tone="outline">old</Badge>
<Badge tone="error">failed</Badge>
<Badge mono>v0.4.2</Badge>
<Dot />
```

---

## Card and tile

Cards that are clicked lift onto a yellow shadow; numbered like sample cards in the empty chat. Tiles show one number with a mono label.

- In the web app: `.suggestions button, .workflow-card, .tile`
- Plain markup; no Base UI primitive
- React, from `@labb/ui`: `import { Card, Tile } from "@labb/ui";`

### Cards

```tsx
<Card eyebrow="01" title="Summarize a PDF" onClick={() => ask("summarize")}>
  Upload it and ask for the key points
</Card>
<Card eyebrow="02" title="Plan a trip" onClick={() => ask("trip")}>
  Three days in Lisbon, on foot
</Card>
```

### Tiles

```tsx
<Tile label="Replies" value="1,284" note="last 30 days" />
<Tile label="Tokens" value="3.2M" />
```

---

## Menu

Menus open from their trigger, outlined in the text colour on a hard shadow. The arrow keys move through the items and typing jumps to one.

- In the web app: `.menu, .model-list`
- Built on Base UI's [Menu](https://base-ui.com/react/components/menu)
- React, from `@labb/ui`: `import { Button, Menu, MenuItem, MenuSeparator } from "@labb/ui";`

### Menu

Open it with a click, or Enter and the arrow keys.

```tsx
<Menu trigger={<Button>Account ▾</Button>}>
  <MenuItem onClick={openSettings}>Settings</MenuItem>
  <MenuItem onClick={download}>Download labb</MenuItem>
  <MenuItem disabled>Billing</MenuItem>
  <MenuSeparator />
  <MenuItem danger onClick={logOut}>Log out</MenuItem>
</Menu>
```

---

## Dialog

Dialogs have a head with a heavy title, a body, and an optional footer with the actions. Focus stays inside while one is open; Escape and a click outside close it.

- In the web app: `.modal, .modal-head, .modal-body`
- Built on Base UI's [Dialog](https://base-ui.com/react/components/dialog)
- React, from `@labb/ui`: `import { Button, Dialog, DialogClose, Field, Input } from "@labb/ui";`

### With a trigger

DialogClose closes it; give it a Button to look like.

```tsx
<Dialog
  trigger={<Button>Delete chat…</Button>}
  title="Delete chat?"
  description="This can't be undone."
  footer={
    <>
      <DialogClose render={<Button />}>Cancel</DialogClose>
      <DialogClose render={<Button variant="danger" />} onClick={remove}>
        Delete
      </DialogClose>
    </>
  }
/>
```

### Controlled

Opened and closed from your own state, e.g. to close only once a save worked.

```tsx
<Button onClick={() => setOpen(true)}>Rename</Button>
<Dialog
  open={open}
  onOpenChange={setOpen}
  title="Rename chat"
  footer={
    <>
      <DialogClose render={<Button />}>Cancel</DialogClose>
      <Button variant="primary" onClick={save}>Save</Button>
    </>
  }
>
  <Field label="Title">
    <Input value={title} onChange={(e) => setTitle(e.target.value)} />
  </Field>
</Dialog>
```

---

## Toast

Messages instead of alert(): inverted, on a hard shadow, under the top of the page; errors get a red edge. They queue, pause while hovered, and are announced to screen readers.

- In the web app: `.toast`
- Built on Base UI's [Toast](https://base-ui.com/react/components/toast)
- React, from `@labb/ui`: `import { ToastProvider, useToast } from "@labb/ui";`

### Showing toasts

Put a ToastProvider around the app once, then call useToast() anywhere under it.

```tsx
// Once, around the app
<ToastProvider>
  <App />
</ToastProvider>

// Anywhere under it
const toast = useToast();

toast("Copied the link.");
toast("Couldn't reach the server.", { tone: "error" });
toast("labb 0.5 is ready.", { title: "Update", timeout: 0 });
```

---

## Messages and tools

Your messages are inverted and square-cut at one corner; replies are lab notes with a yellow margin. Tool calls are little tagged mono lines.

- In the web app: `.msg.user, .msg.assistant, .tool-line`
- Plain markup; no Base UI primitive
- React, from `@labb/ui`: `import { Message, ToolLine } from "@labb/ui";`

### A turn

```tsx
<Message from="user">What's in my calendar tomorrow?</Message>
<ToolLine name="calendar_events">tomorrow</ToolLine>
<Message from="assistant">
  Two things: standup at 9:30 and lunch with Sam at 12.
</Message>
```

---

## Approval

When labb needs a yes before it acts: hazard stripes along the top, a yellow frame.

- In the web app: `.approval`
- Plain markup; no Base UI primitive
- React, from `@labb/ui`: `import { Approval, Button, Code } from "@labb/ui";`

### Approval

```tsx
<Approval
  title="Run this command?"
  actions={
    <>
      <Button variant="primary">Run</Button>
      <Button>Always for npm</Button>
      <Button variant="danger">Don't run</Button>
    </>
  }
>
  <Code>npm install</Code>
</Approval>
```

---

## Logo and type bits

The flask logo and the wordmark, mono labels over sections, inline code and keys.

- In the web app: `Logo, Wordmark (Icons.tsx), kbd, code`
- Plain markup; no Base UI primitive
- React, from `@labb/ui`: `import { Code, Kbd, Label, Logo, Wordmark } from "@labb/ui";`

### Logo and wordmark

```tsx
<Logo size={40} /> <Wordmark />
```

### Labels, code and keys

```tsx
<Label>Recent</Label>
<Code>labb --login</Code>
<Kbd>⌘</Kbd> <Kbd>K</Kbd>
```
