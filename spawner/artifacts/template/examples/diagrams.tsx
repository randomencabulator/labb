"use client";

// An example to copy from: a system's parts, and a process with a decision.

import { Diagram, defineDiagram } from "@/components/diagram";

const system = defineDiagram({
  direction: "right",
  nodes: {
    visitor: { label: "Visitor", kind: "person" },
    web: { label: "Web app", kind: "service", detail: "Next.js" },
    api: { label: "API", kind: "service", detail: "FastAPI" },
    queue: { label: "Jobs", kind: "service" },
    db: { label: "Orders", kind: "data", detail: "Postgres" },
  },
  edges: [
    { from: "visitor", to: "web", label: "visits" },
    { from: "web", to: "api", label: "calls" },
    { from: "api", to: "db", label: "reads and writes" },
    { from: "api", to: "queue", label: "sends", dashed: true },
    { from: "queue", to: "db", animated: true },
  ],
});

const refund = defineDiagram({
  direction: "down",
  nodes: {
    asked: { label: "Refund asked", kind: "start" },
    check: { label: "Within 30 days?", kind: "decision" },
    pay: { label: "Pay it back" },
    refuse: { label: "Explain why not" },
    done: { label: "Done", kind: "end" },
  },
  edges: [
    { from: "asked", to: "check" },
    { from: "check", to: "pay", label: "yes" },
    { from: "check", to: "refuse", label: "no" },
    { from: "pay", to: "done" },
    { from: "refuse", to: "done" },
  ],
});

export default function Diagrams() {
  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">How it fits together</h1>
      <Diagram diagram={system} height={300} />
      <Diagram diagram={refund} height={460} />
    </main>
  );
}
