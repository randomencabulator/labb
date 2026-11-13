// What the components pages show: each component's examples, in the order demos.tsx draws them.
export type Example = { title: string; about?: string; code: string };
export type ComponentDoc = {
  slug: string;
  name: string;
  about: string;
  web: string;
  // The Base UI primitive it's built on, and its docs page
  base?: { name: string; url: string };
  examples: Example[];
};

const baseUi = (name: string, page: string) => ({ name, url: `https://base-ui.com/react/components/${page}` });

export const components: ComponentDoc[] = [
  {
    slug: "button",
    name: "Button",
    about: "Hairline buttons for most things; one yellow primary per view, which sits on a hard shadow, lifts on hover and presses down when clicked.",
    web: "button, button.primary, button.danger, button.link",
    base: baseUi("Button", "button"),
    examples: [
      {
        title: "Variants",
        code: `<Button variant="primary">New chat</Button>
<Button>Cancel</Button>
<Button variant="danger">Delete</Button>
<Button variant="ghost">More</Button>
<Button variant="link">Show details</Button>`,
      },
      {
        title: "Sizes",
        code: `<Button size="sm">Small</Button>
<Button>Medium</Button>
<Button size="lg" variant="primary">Send</Button>`,
      },
      {
        title: "Disabled and icon",
        about: "focusableWhenDisabled keeps a disabled button in the tab order, for one that's waiting rather than unavailable.",
        code: `<Button variant="primary" disabled>Sending…</Button>
<Button variant="primary" disabled focusableWhenDisabled>
  Focusable while disabled
</Button>
<Button icon aria-label="Settings">⚙</Button>`,
      },
    ],
  },
  {
    slug: "toggle",
    name: "Toggle and segmented",
    about: "Chips that turn something on, like Web next to the model picker, and a segmented control for picking one of a few, like the theme.",
    web: ".toggle-pill, .menu-theme",
    base: baseUi("Toggle and Toggle Group", "toggle-group"),
    examples: [
      {
        title: "Toggle",
        about: "Click it, or tab to it and press Space.",
        code: `<Toggle pressed={web} onPressedChange={setWeb}>
  🌐 Web
</Toggle>`,
      },
      {
        title: "Segmented",
        about: "One is always picked; the arrow keys move between them.",
        code: `<Segmented
  label="Theme"
  value={theme}
  onChange={setTheme}
  options={[
    { value: "system", label: "System" },
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
  ]}
/>`,
      },
    ],
  },
  {
    slug: "switch",
    name: "Switch",
    about: "An on/off switch, yellow when on. It takes part in forms like a checkbox.",
    web: ".switch",
    base: baseUi("Switch", "switch"),
    examples: [
      {
        title: "States",
        code: `<Switch checked={on} onCheckedChange={setOn} aria-label="Memories" />
<Switch aria-label="Notes" />
<Switch disabled aria-label="Browser" />`,
      },
    ],
  },
  {
    slug: "field",
    name: "Field and inputs",
    about: "A label over its control, with a hint or an error under it. Inputs sit on the page colour and take an outline in the accent when focused.",
    web: ".field, .auth-form input, textarea",
    base: baseUi("Field and Input", "field"),
    examples: [
      {
        title: "Input",
        code: `<Field label="Name" hint="Shown to people you share with.">
  <Input placeholder="Research helper" />
</Field>`,
      },
      {
        title: "Error",
        about: "An error marks the field and its control invalid, and says so to screen readers.",
        code: `<Field label="Email" error="That doesn't look like an email.">
  <Input defaultValue="ada@" />
</Field>`,
      },
      {
        title: "Textarea",
        code: `<Field label="Instructions">
  <Textarea placeholder="What should this agent do?" />
</Field>`,
      },
    ],
  },
  {
    slug: "badge",
    name: "Badge",
    about: "Small labels and counts; the yellow one for what needs looking at, like unread tasks.",
    web: ".badge, aside .pages .badge, .unread-dot",
    examples: [
      {
        title: "Tones",
        code: `<Badge>shared</Badge>
<Badge tone="accent">3</Badge>
<Badge tone="outline">old</Badge>
<Badge tone="error">failed</Badge>
<Badge mono>v0.4.2</Badge>
<Dot />`,
      },
    ],
  },
  {
    slug: "card",
    name: "Card and tile",
    about: "Cards that are clicked lift onto a yellow shadow; numbered like sample cards in the empty chat. Tiles show one number with a mono label.",
    web: ".suggestions button, .workflow-card, .tile",
    examples: [
      {
        title: "Cards",
        code: `<Card eyebrow="01" title="Summarize a PDF" onClick={() => ask("summarize")}>
  Upload it and ask for the key points
</Card>
<Card eyebrow="02" title="Plan a trip" onClick={() => ask("trip")}>
  Three days in Lisbon, on foot
</Card>`,
      },
      {
        title: "Tiles",
        code: `<Tile label="Replies" value="1,284" note="last 30 days" />
<Tile label="Tokens" value="3.2M" />`,
      },
    ],
  },
  {
    slug: "menu",
    name: "Menu",
    about: "Menus open from their trigger, outlined in the text colour on a hard shadow. The arrow keys move through the items and typing jumps to one.",
    web: ".menu, .model-list",
    base: baseUi("Menu", "menu"),
    examples: [
      {
        title: "Menu",
        about: "Open it with a click, or Enter and the arrow keys.",
        code: `<Menu trigger={<Button>Account ▾</Button>}>
  <MenuItem onClick={openSettings}>Settings</MenuItem>
  <MenuItem onClick={download}>Download labb</MenuItem>
  <MenuItem disabled>Billing</MenuItem>
  <MenuSeparator />
  <MenuItem danger onClick={logOut}>Log out</MenuItem>
</Menu>`,
      },
    ],
  },
  {
    slug: "dialog",
    name: "Dialog",
    about: "Dialogs have a head with a heavy title, a body, and an optional footer with the actions. Focus stays inside while one is open; Escape and a click outside close it.",
    web: ".modal, .modal-head, .modal-body",
    base: baseUi("Dialog", "dialog"),
    examples: [
      {
        title: "With a trigger",
        about: "DialogClose closes it; give it a Button to look like.",
        code: `<Dialog
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
/>`,
      },
      {
        title: "Controlled",
        about: "Opened and closed from your own state, e.g. to close only once a save worked.",
        code: `<Button onClick={() => setOpen(true)}>Rename</Button>
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
</Dialog>`,
      },
    ],
  },
  {
    slug: "toast",
    name: "Toast",
    about: "Messages instead of alert(): inverted, on a hard shadow, under the top of the page; errors get a red edge. They queue, pause while hovered, and are announced to screen readers.",
    web: ".toast",
    base: baseUi("Toast", "toast"),
    examples: [
      {
        title: "Showing toasts",
        about: "Put a ToastProvider around the app once, then call useToast() anywhere under it.",
        code: `// Once, around the app
<ToastProvider>
  <App />
</ToastProvider>

// Anywhere under it
const toast = useToast();

toast("Copied the link.");
toast("Couldn't reach the server.", { tone: "error" });
toast("labb 0.5 is ready.", { title: "Update", timeout: 0 });`,
      },
    ],
  },
  {
    slug: "chat",
    name: "Messages and tools",
    about: "Your messages are inverted and square-cut at one corner; replies are lab notes with a yellow margin. Tool calls are little tagged mono lines.",
    web: ".msg.user, .msg.assistant, .tool-line",
    examples: [
      {
        title: "A turn",
        code: `<Message from="user">What's in my calendar tomorrow?</Message>
<ToolLine name="calendar_events">tomorrow</ToolLine>
<Message from="assistant">
  Two things: standup at 9:30 and lunch with Sam at 12.
</Message>`,
      },
    ],
  },
  {
    slug: "approval",
    name: "Approval",
    about: "When labb needs a yes before it acts: hazard stripes along the top, a yellow frame.",
    web: ".approval",
    examples: [
      {
        title: "Approval",
        code: `<Approval
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
</Approval>`,
      },
    ],
  },
  {
    slug: "brand",
    name: "Logo and type bits",
    about: "The flask logo and the wordmark, mono labels over sections, inline code and keys.",
    web: "Logo, Wordmark (Icons.tsx), kbd, code",
    examples: [
      { title: "Logo and wordmark", code: `<Logo size={40} /> <Wordmark />` },
      {
        title: "Labels, code and keys",
        code: `<Label>Recent</Label>
<Code>labb --login</Code>
<Kbd>⌘</Kbd> <Kbd>K</Kbd>`,
      },
    ],
  },
];
