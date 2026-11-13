"use client";

import { useState, type ReactNode } from "react";
import {
  Approval,
  Badge,
  Button,
  Card,
  Code,
  Dialog,
  DialogClose,
  DialogPanel,
  Dot,
  Field,
  Input,
  Kbd,
  Label,
  Logo,
  Menu,
  MenuItem,
  MenuSeparator,
  Message,
  Segmented,
  Switch,
  Textarea,
  Tile,
  Toggle,
  ToolLine,
  Wordmark,
  useToast,
} from "@labb/ui";

function ToggleDemo() {
  const [web, setWeb] = useState(true);
  return (
    <Toggle pressed={web} onPressedChange={setWeb}>
      🌐 Web
    </Toggle>
  );
}

function SegmentedDemo() {
  const [theme, setTheme] = useState("system");
  return (
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
  );
}

function SwitchDemo() {
  const [on, setOn] = useState(true);
  return (
    <>
      <Switch checked={on} onCheckedChange={setOn} aria-label="Memories" />
      <Switch aria-label="Notes" />
      <Switch disabled aria-label="Browser" />
      <span className="docs-muted docs-small">Memories are {on ? "on" : "off"}</span>
    </>
  );
}

function MenuDemo() {
  const toast = useToast();
  return (
    <Menu trigger={<Button>Account ▾</Button>}>
      <MenuItem onClick={() => toast("Settings would open here.")}>Settings</MenuItem>
      <MenuItem onClick={() => toast("labb would download here.")}>Download labb</MenuItem>
      <MenuItem disabled>Billing</MenuItem>
      <MenuSeparator />
      <MenuItem danger onClick={() => toast("You'd be logged out.", { tone: "error" })}>
        Log out
      </MenuItem>
    </Menu>
  );
}

function DialogDemo() {
  return (
    <Dialog
      trigger={<Button>Delete chat…</Button>}
      title="Delete chat?"
      description="This can't be undone."
      footer={
        <>
          <DialogClose render={<Button />}>Cancel</DialogClose>
          <DialogClose render={<Button variant="danger" />}>Delete</DialogClose>
        </>
      }
    />
  );
}

function ControlledDialogDemo() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("Trip to Lisbon");
  const toast = useToast();
  return (
    <>
      <Button onClick={() => setOpen(true)}>Rename “{title}”</Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Rename chat"
        footer={
          <>
            <DialogClose render={<Button />}>Cancel</DialogClose>
            <Button
              variant="primary"
              onClick={() => {
                setOpen(false);
                toast(`Renamed to “${title}”.`);
              }}
            >
              Save
            </Button>
          </>
        }
      >
        <Field label="Title">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </Dialog>
    </>
  );
}

function ToastDemo() {
  const toast = useToast();
  return (
    <>
      <Button onClick={() => toast("Copied the link.")}>Show a toast</Button>
      <Button variant="danger" onClick={() => toast("Couldn't reach the server.", { tone: "error" })}>
        Show an error
      </Button>
      <Button onClick={() => toast("labb 0.5 is ready.", { title: "Update", timeout: 0 })}>With a title, until closed</Button>
    </>
  );
}

const noop = () => {};

const demos: Record<string, (() => ReactNode)[]> = {
  button: [
    () => (
      <>
        <Button variant="primary">New chat</Button>
        <Button>Cancel</Button>
        <Button variant="danger">Delete</Button>
        <Button variant="ghost">More</Button>
        <Button variant="link">Show details</Button>
      </>
    ),
    () => (
      <>
        <Button size="sm">Small</Button>
        <Button>Medium</Button>
        <Button size="lg" variant="primary">
          Send
        </Button>
      </>
    ),
    () => (
      <>
        <Button variant="primary" disabled>
          Sending…
        </Button>
        <Button variant="primary" disabled focusableWhenDisabled>
          Focusable while disabled
        </Button>
        <Button icon aria-label="Settings">
          ⚙
        </Button>
      </>
    ),
  ],
  toggle: [() => <ToggleDemo />, () => <SegmentedDemo />],
  switch: [() => <SwitchDemo />],
  field: [
    () => (
      <Field label="Name" hint="Shown to people you share with." className="demo-field">
        <Input placeholder="Research helper" />
      </Field>
    ),
    () => (
      <Field label="Email" error="That doesn't look like an email." className="demo-field">
        <Input defaultValue="ada@" />
      </Field>
    ),
    () => (
      <Field label="Instructions" className="demo-field">
        <Textarea placeholder="What should this agent do?" />
      </Field>
    ),
  ],
  badge: [
    () => (
      <>
        <Badge>shared</Badge>
        <Badge tone="accent">3</Badge>
        <Badge tone="outline">old</Badge>
        <Badge tone="error">failed</Badge>
        <Badge mono>v0.4.2</Badge>
        <Dot />
      </>
    ),
  ],
  card: [
    () => (
      <div className="demo-grid">
        <Card eyebrow="01" title="Summarize a PDF" onClick={noop}>
          Upload it and ask for the key points
        </Card>
        <Card eyebrow="02" title="Plan a trip" onClick={noop}>
          Three days in Lisbon, on foot
        </Card>
      </div>
    ),
    () => (
      <div className="demo-grid">
        <Tile label="Replies" value="1,284" note="last 30 days" />
        <Tile label="Tokens" value="3.2M" />
      </div>
    ),
  ],
  menu: [() => <MenuDemo />],
  dialog: [() => <DialogDemo />, () => <ControlledDialogDemo />],
  toast: [() => <ToastDemo />],
  chat: [
    () => (
      <div className="demo-chat">
        <Message from="user">What&apos;s in my calendar tomorrow?</Message>
        <ToolLine name="calendar_events">tomorrow</ToolLine>
        <Message from="assistant">Two things: standup at 9:30 and lunch with Sam at 12.</Message>
      </div>
    ),
  ],
  approval: [
    () => (
      <div className="demo-wide">
        <Approval
          title="Run this command?"
          actions={
            <>
              <Button variant="primary">Run</Button>
              <Button>Always for npm</Button>
              <Button variant="danger">Don&apos;t run</Button>
            </>
          }
        >
          <Code>npm install</Code>
        </Approval>
      </div>
    ),
  ],
  brand: [
    () => (
      <span className="demo-brand">
        <Logo size={40} /> <Wordmark />
      </span>
    ),
    () => (
      <>
        <Label>Recent</Label>
        <Code>labb --login</Code>
        <span>
          <Kbd>⌘</Kbd> <Kbd>K</Kbd>
        </span>
      </>
    ),
  ],
};

// What the components index shows for those that open over the page: their popup, drawn in place
const previews: Record<string, () => ReactNode> = {
  menu: () => (
    <div className="lb-menu">
      <span className="lb-menu-item">Settings</span>
      <span className="lb-menu-item" data-highlighted="">
        Download labb
      </span>
      <div className="lb-menu-sep" />
      <span className="lb-menu-item danger">Log out</span>
    </div>
  ),
  dialog: () => (
    <DialogPanel
      title="Rename chat"
      footer={
        <>
          <span className="lb-button">Cancel</span>
          <span className="lb-button primary">Save</span>
        </>
      }
    >
      <span className="lb-field">
        <span className="lb-field-label">Title</span>
        <span className="lb-input">Trip to Lisbon</span>
      </span>
    </DialogPanel>
  ),
  toast: () => (
    <div className="demo-stack">
      <div className="lb-toast">
        <span className="lb-toast-content">Copied the link.</span>
      </div>
      <div className="lb-toast error">
        <span className="lb-toast-content">Couldn&apos;t reach the server.</span>
      </div>
    </div>
  ),
};

export function Demo({ slug, index }: { slug: string; index: number }) {
  const draw = demos[slug]?.[index];
  return <>{draw ? draw() : null}</>;
}

export function Preview({ slug }: { slug: string }) {
  const draw = previews[slug] ?? demos[slug]?.[0];
  return <>{draw ? draw() : null}</>;
}
