"use client";

// An example to copy from: a form with every control, and the popups.

import { useState } from "react";
import { DotsThreeIcon, InfoIcon, PencilSimpleIcon, TrashIcon } from "@phosphor-icons/react";
import {
  Accordion,
  Avatar,
  Button,
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
  Checkbox,
  Dialog,
  Field,
  Input,
  Menu,
  Popover,
  Progress,
  Select,
  Separator,
  Slider,
  Switch,
  Textarea,
  Tooltip,
} from "@/components/ui";

export default function Form() {
  const [email, setEmail] = useState("");
  const [plan, setPlan] = useState<"free" | "pro">("free");
  const [updates, setUpdates] = useState(true);
  const [terms, setTerms] = useState(false);
  const [seats, setSeats] = useState(3);
  const invalid = email.length > 0 && !email.includes("@");

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-6">
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div className="flex items-center gap-3">
            <Avatar name="Ada Lovelace" />
            <CardTitle>Your account</CardTitle>
          </div>
          <Menu
            trigger={<Button variant="ghost" size="icon" aria-label="More"><DotsThreeIcon /></Button>}
            items={[{ label: "Edit", icon: <PencilSimpleIcon /> }, "separator", { label: "Delete", icon: <TrashIcon />, danger: true }]}
          />
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field label="Email" description="We never share it." error={invalid ? "That isn't an email address" : undefined}>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </Field>
          <Field label="Plan">
            <Select items={[{ value: "free", label: "Free" }, { value: "pro", label: "Pro" }]} value={plan} onValueChange={setPlan} />
          </Field>
          <Field label={`Seats: ${seats}`}>
            <Slider value={seats} onValueChange={(value) => setSeats(value as number)} min={1} max={20} />
          </Field>
          <Field label="Note">
            <Textarea placeholder="Anything we should know?" />
          </Field>
          <Switch label="Product updates" checked={updates} onCheckedChange={setUpdates} />
          <Checkbox label="I agree to the terms" checked={terms} onCheckedChange={setTerms} />
          <Progress label="Profile complete" value={terms ? 100 : 60} />
        </CardContent>
        <Separator />
        <CardFooter className="justify-end">
          <Popover trigger={<Button variant="ghost" size="icon" aria-label="About plans"><InfoIcon /></Button>}>
            Pro adds unlimited projects and priority support.
          </Popover>
          <Tooltip content="Saves nothing: it's an example">
            <Button variant="outline">Cancel</Button>
          </Tooltip>
          <Dialog trigger={<Button disabled={!terms}>Save</Button>} title="Saved" description="Your account is up to date.">
            <Button onClick={() => undefined}>Close</Button>
          </Dialog>
        </CardFooter>
      </Card>
      <Accordion
        items={[
          { title: "Can I change plans later?", content: "Yes, any time, from this page." },
          { title: "What happens to my data?", content: "It stays yours. Export it whenever you like." },
        ]}
      />
    </main>
  );
}
