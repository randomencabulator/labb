// A step on the canvas: its kind, name, key, what it does, and how it went in the run shown
import { Handle, type NodeProps, type Node, Position } from "@xyflow/react";
import { memo } from "react";
import type { StepNode, StepState } from "../../studioApi";
import { StepIcon } from "../Icons";

export type StepBoxData = {
  step: StepNode;
  category: string;
  kindLabel: string;
  summary: string;
  handles: (string | null)[];
  problems: string[];
  status?: StepState["status"];
  // In a preview of a change: whether this step is new, changed or goes away
  diff?: "added" | "changed" | "removed";
  // Picked for Ask labb: the change is only about the picked steps
  picked?: boolean;
};

export type StepBoxNode = Node<StepBoxData, "step">;

const STATUS_LABEL: Record<string, string> = {
  running: "Running", done: "Done", skipped: "Skipped", failed: "Failed", waiting: "Waiting", stopped: "Stopped",
};

function StepBoxView({ data, selected }: NodeProps<StepBoxNode>) {
  const { step, category, kindLabel, summary, handles, problems, status, diff, picked } = data;
  const classes = ["step-box", `cat-${category}`, selected ? "selected" : "", status ? `is-${status}` : "", problems.length ? "has-problems" : "",
    diff ? `diff-${diff}` : "", picked ? "picked" : ""];
  return (
    <div className={classes.filter(Boolean).join(" ")} title={problems.join("\n") || undefined}>
      {diff && <span className="diff-badge">{diff === "added" ? "New" : diff === "changed" ? "Changed" : "Removed"}</span>}
      {step.type !== "trigger" && <Handle type="target" position={Position.Left} className="step-in" />}
      <div className="step-head">
        <span className="step-icon" aria-hidden>
          <StepIcon type={step.type} />
        </span>
        <span className="step-label">{step.label || kindLabel}</span>
        {status && <span className={`step-status ${status}`}>{STATUS_LABEL[status]}</span>}
        {!status && problems.length > 0 && (
          <span className="step-problem" aria-label={`${problems.length} problem(s)`}>
            !
          </span>
        )}
      </div>
      <div className="step-meta">
        <span className="step-kind">{kindLabel}</span>
        {step.type !== "trigger" && <code className="step-key">{`{{${step.key}}}`}</code>}
      </div>
      {summary && <div className="step-summary">{summary}</div>}
      {handles.length === 1 && handles[0] === null ? (
        <Handle type="source" position={Position.Right} id="out" className="step-out" />
      ) : (
        <div className="step-ways">
          {handles.map(handle => (
            <div key={handle ?? "out"} className="step-way">
              <span>{handle}</span>
              <Handle type="source" position={Position.Right} id={handle ?? "out"} className="step-out" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const StepBox = memo(StepBoxView);
