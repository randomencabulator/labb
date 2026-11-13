import { useEffect, useState } from "react";
import { type ConversationWork, type Goal, getWork } from "../../api";
import { Markdown } from "../Markdown";
import { TodoList } from "../TodoList";

const POLL_MS = 2000;

const GOAL_STATUS: Record<Goal["status"], string> = {
  active: "Working on it",
  complete: "Done",
  blocked: "Blocked",
  paused: "Paused",
  budget_limited: "Out of budget",
};

/** What the chat is working on now: its goal, its todo list as it stands and the plan you approved */
export function WorkTab({ threadId, streaming }: { threadId: string; streaming: boolean }) {
  const [work, setWork] = useState<ConversationWork | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    const load = () =>
      getWork(threadId).then(
        found => {
          if (!current) return;
          setWork(found);
          setError(null);
        },
        e => current && setError(e instanceof Error ? e.message : String(e)),
      );
    load();
    // While a reply runs, the agent changes its list as it goes
    const timer = streaming ? setInterval(load, POLL_MS) : undefined;
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [threadId, streaming]);

  if (error) return <p className="error">{error}</p>;
  if (!work) return <p className="muted">Loading…</p>;
  if (!work.goal && !work.todos.length && !work.plan) {
    return <p className="muted">Nothing yet. The agent writes a todo list for a job of 3 steps or more; a goal or an approved plan shows here too.</p>;
  }
  const { goal } = work;
  const done = work.todos.filter(item => item.status === "done").length;
  return (
    <div className="context-work">
      {goal && (
        <>
          <h3 className="context-heading">Goal</h3>
          <p>
            <span className={`goal-status ${goal.status}`}>{GOAL_STATUS[goal.status] ?? goal.status}</span>
            <span className="muted">
              {" "}
              · {goal.used} {goal.budget ? `of ${goal.budget} ` : ""}model calls
            </span>
          </p>
          <blockquote className="goal-objective">{goal.objective}</blockquote>
          {goal.note && <p className="muted">{goal.status === "complete" ? "How it checked: " : "Note: "}{goal.note}</p>}
        </>
      )}
      {work.todos.length > 0 && (
        <>
          <h3 className="context-heading">
            Todo list <span className="muted">{done} of {work.todos.length} done{work.open ? `, ${work.open} open` : ""}</span>
          </h3>
          <TodoList items={work.todos} />
        </>
      )}
      {work.plan && (
        <>
          <h3 className="context-heading">
            Approved plan {work.plan_file && <span className="muted">{work.plan_file}</span>}
          </h3>
          <Markdown text={work.plan} />
        </>
      )}
    </div>
  );
}
