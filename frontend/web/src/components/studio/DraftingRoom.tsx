// A new workflow: describe it, answer what was unclear, shape the blueprint by chatting, try it once,
// then create it. Nothing is saved until Create. Or start blank, from a template, or from a file.
import { type ChangeEvent, type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { type Catalog, type Change, type Definition, type DraftQuestion, type DraftTurn, type Problem, type Template, studio } from "../../studioApi";
import { toast } from "../Toaster";
import { type Diff, diffSteps, inOrder, summary } from "./graph";
import { type Names, Preview, withRemoved } from "./Preview";
import { RunForm, RunPanel } from "./RunPanel";

type Stage = "describe" | "questions" | "blueprint" | "try" | "create";

type Version = {
  name: string;
  emoji: string;
  definition: Definition;
  changes: Change[];
  problems: Problem[];
  request?: string;
  reply?: string;
};

const STAGES: [Stage, string][] = [
  ["describe", "Describe it"], ["questions", "A few questions"], ["blueprint", "The blueprint"], ["try", "Try it"], ["create", "Create"],
];

const STARTERS = ["Every weekday morning, ", "When a webhook arrives, ", "When I upload a document, ", "Every Monday, "];

const DIFF_LABEL: Record<Diff, string> = { added: "New", changed: "Changed", removed: "Removed" };

export function DraftingRoom({ catalog, names, onMade, onCancel }: {
  catalog: Catalog;
  names: Names;
  onMade: (id: string) => void;
  onCancel: () => void;
}) {
  const [stage, setStage] = useState<Stage>("describe");
  const [reached, setReached] = useState<Set<Stage>>(new Set(["describe"]));
  const [description, setDescription] = useState("");
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [shown, setShown] = useState(0);
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [tryRun, setTryRun] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [templates, setTemplates] = useState<Template[]>([]);
  const describeBox = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    studio.templates().then(setTemplates, e => toast(e.message));
    describeBox.current?.focus();
  }, []);

  const go = (next: Stage) => {
    setStage(next);
    setReached(current => new Set([...current, next]));
  };

  const work = async (label: string, job: () => Promise<void>) => {
    setBusy(label);
    try {
      await job();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const blueprint = (turn: Extract<DraftTurn, { kind: "blueprint" }>, extra: Partial<Version> = {}): Version => ({
    name: turn.name ?? versions[shown]?.name ?? "New workflow",
    emoji: turn.emoji ?? versions[shown]?.emoji ?? "⚙️",
    definition: turn.definition, changes: turn.changes ?? [], problems: turn.problems ?? [], ...extra,
  });

  const firstBlueprint = (turn: Extract<DraftTurn, { kind: "blueprint" }>) => {
    const version = blueprint(turn);
    setVersions([version]);
    setShown(0);
    setName(version.name);
    setTryRun(null);
    go("blueprint");
  };

  const draftIt = () => work("Reading your description…", async () => {
    const turn = await studio.draft({ description });
    if (turn.kind === "questions") {
      setQuestions(turn.questions);
      setAnswers(turn.questions.map(q => q.options[0] ?? ""));
      go("questions");
    } else {
      setQuestions([]);
      firstBlueprint(turn);
    }
  });

  const makeBlueprint = (skip: boolean) => work("Drawing the blueprint…", async () => {
    const given = skip ? [] : questions.map((q, i) => ({ question: q.question, answer: answers[i] ?? "" })).filter(a => a.answer.trim());
    const turn = await studio.draft({ description, answers: given, ask: false });
    if (turn.kind === "blueprint") firstBlueprint(turn);
  });

  const current = versions[shown];
  const refine = (event?: FormEvent) => {
    event?.preventDefault();
    if (!current || !request.trim()) return;
    const asked = request.trim();
    work("Changing the blueprint…", async () => {
      const turn = await studio.draft({ current: current.definition, request: asked });
      if (turn.kind !== "blueprint") return;
      const next = blueprint(turn, { name: current.name, emoji: current.emoji, request: asked, reply: turn.reply });
      setVersions(list => [...list.slice(0, shown + 1), next]);
      setShown(shown + 1);
      setRequest("");
      setTryRun(null);
    });
  };

  const diffs = useMemo(() => (current && shown > 0 ? diffSteps(versions[shown - 1].definition, current.definition) : new Map<string, Diff>()),
    [versions, shown]);
  const before = shown > 0 ? versions[shown - 1].definition : null;
  const plan = useMemo(() => (current ? inOrder(withRemoved(current.definition, before, diffs)) : []), [current, before, diffs]);
  const labelOf = (id: string | null) => current?.definition.nodes.find(n => n.id === id)?.label;

  const startTry = (inputs: Record<string, unknown>) => current && work("Starting the test run…", async () => {
    setTryRun((await studio.tryDraft(name || current.name, current.definition, inputs)).id);
  });

  const create = () => current && work("Creating it…", async () => {
    onMade((await studio.create({ name: name.trim() || current.name, emoji: current.emoji, description: description.trim().slice(0, 300),
      draft: current.definition })).id);
  });

  const make = (fields: Parameters<typeof studio.create>[0]) => work("Creating it…", async () => onMade((await studio.create(fields)).id));
  const importFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) work("Importing…", async () => onMade((await studio.importWorkflow(JSON.parse(await file.text()))).id));
  };

  const open = (s: Stage) => reached.has(s) && (s !== "questions" || questions.length > 0);
  const order = STAGES.map(([s]) => s);

  return (
    <div className="new-workflow drafting">
      <div className="new-head">
        <h3>New workflow</h3>
        <button type="button" className="icon-button" aria-label="Close" onClick={onCancel}>×</button>
      </div>
      <ol className="draft-stepper">
        {STAGES.map(([s, label], i) => (
          <li key={s}>
            <button type="button" disabled={!open(s) || busy !== null} aria-current={stage === s ? "step" : undefined}
              className={order.indexOf(s) < order.indexOf(stage) && open(s) ? "done" : ""} onClick={() => setStage(s)}>
              <span className="step-no">0{i + 1}</span>
              <b>{label}</b>
            </button>
          </li>
        ))}
      </ol>
      {busy && <div className="draft-busy" role="status"><span className="draft-busy-bar" />{busy}</div>}

      {stage === "describe" && (
        <div className="draft-describe">
          <label className="field">
            <span className="field-label">What should it do? Say when it runs, what it looks at, and where the result goes.</span>
            <textarea ref={describeBox} rows={4} value={description} disabled={busy !== null}
              placeholder="Every weekday morning, find the top news stories about self-hosting, and give me a short summary with links in a chat."
              onChange={e => setDescription(e.target.value)}
              onKeyDown={e => e.key === "Enter" && (e.ctrlKey || e.metaKey) && description.trim() && draftIt()} />
          </label>
          <div className="row wrap">
            <span className="muted small">Start with:</span>
            {STARTERS.map(s => (
              <button key={s} type="button" className="chip-button" onClick={() => (setDescription(s), describeBox.current?.focus())}>{s.trim().replace(/,$/, "…")}</button>
            ))}
            <span className="spacer" />
            <button type="button" className="primary" disabled={busy !== null || !description.trim()} onClick={draftIt}>Draft it</button>
          </div>
          <p className="muted small">Nothing is saved until you create it. Bigger models (OpenAI, Anthropic) draft much better than small local ones.</p>
          <h4 className="draft-or">Or start from</h4>
          <div className="new-options">
            <button type="button" className="template-card blank" disabled={busy !== null} onClick={() => make({ name: "New workflow" })}>
              <span className="emoji">➕</span>
              <b>Blank</b>
              <span className="muted small">A Start step, and you take it from there.</span>
            </button>
            {templates.map(t => (
              <button key={t.id} type="button" className="template-card" disabled={busy !== null} onClick={() => make({ template: t.id })}>
                <span className="emoji">{t.emoji}</span>
                <b>{t.name}</b>
                <span className="muted small">{t.description}</span>
              </button>
            ))}
          </div>
          <label className="button-like">
            Import a file
            <input type="file" accept="application/json,.json" hidden onChange={importFile} />
          </label>
        </div>
      )}

      {stage === "questions" && (
        <div className="draft-questions">
          <p className="muted">{questions.length === 1 ? "One thing wasn't clear." : `${questions.length} things weren't clear.`} Pick an answer or write your own.</p>
          {questions.map((q, i) => (
            <fieldset key={i} className="draft-question">
              <legend>{q.question}</legend>
              {q.why && <p className="muted small">{q.why}</p>}
              <div className="row wrap">
                {q.options.map(option => (
                  <button key={option} type="button" className={`chip-button${answers[i] === option ? " on" : ""}`} aria-pressed={answers[i] === option}
                    onClick={() => setAnswers(a => a.map((x, j) => (j === i ? option : x)))}>{option}</button>
                ))}
                <input className="draft-own" placeholder="Or type your answer" aria-label={`Your answer to: ${q.question}`}
                  value={q.options.includes(answers[i]) ? "" : answers[i]} onChange={e => setAnswers(a => a.map((x, j) => (j === i ? e.target.value : x)))} />
              </div>
            </fieldset>
          ))}
          <div className="row">
            <button type="button" onClick={() => setStage("describe")}>Back</button>
            <span className="spacer" />
            <button type="button" disabled={busy !== null} onClick={() => makeBlueprint(true)}>Skip</button>
            <button type="button" className="primary" disabled={busy !== null} onClick={() => makeBlueprint(false)}>Make the blueprint</button>
          </div>
        </div>
      )}

      {stage === "blueprint" && current && (
        <div className="draft-room">
          <div className="draft-main">
            <div className="row">
              <h4 className="draft-name"><span aria-hidden>{current.emoji}</span> {current.name}</h4>
              <span className="spacer" />
              {versions.length > 1 && (
                <div className="draft-versions" role="group" aria-label="Versions">
                  <span className="muted small">Version</span>
                  {versions.map((_, i) => (
                    <button key={i} type="button" aria-pressed={i === shown} onClick={() => (setShown(i), setTryRun(null))}>v{i + 1}</button>
                  ))}
                </div>
              )}
            </div>
            <ol className="draft-plan">
              {plan.map(step => {
                const diff = diffs.get(step.id);
                return (
                  <li key={step.id} className={diff ? `diff-${diff}` : undefined}>
                    <span className="step-kind">{catalog.nodes[step.type]?.label ?? step.type}</span>
                    <b>{step.label}</b>
                    <span className="muted small">{summary(step, names)}</span>
                    {diff && <span className="diff-tag">{DIFF_LABEL[diff]} in v{shown + 1}</span>}
                  </li>
                );
              })}
            </ol>
            <div className="draft-talk">
              {versions.slice(1, shown + 1).map((v, i) => (
                <div key={i} className="draft-turn">
                  <div className="draft-asked">{v.request}</div>
                  <div className="draft-reply">
                    {v.reply || (v.changes.length ? "" : "Nothing changed: try saying it another way.")}
                    {v.changes.length > 0 && (
                      <ul>{v.changes.map((c, j) => <li key={j}><b>{c.label}</b>: {c.text}</li>)}</ul>
                    )}
                  </div>
                </div>
              ))}
              <form className="ask-line" onSubmit={refine}>
                <input value={request} onChange={e => setRequest(e.target.value)} disabled={busy !== null}
                  aria-label="Change the blueprint" placeholder="Change it: “also email it to me, but ask me first”" />
                <button type="submit" className="primary" disabled={busy !== null || !request.trim()}>Change it</button>
              </form>
            </div>
            <div className="row">
              <button type="button" onClick={() => setStage(questions.length ? "questions" : "describe")}>Back</button>
              <span className="spacer" />
              <button type="button" disabled={busy !== null} onClick={() => go("try")}>Try it</button>
              <button type="button" className="primary" disabled={busy !== null} onClick={() => go("create")}>Create</button>
            </div>
          </div>
          <div className="draft-side">
            <Preview definition={current.definition} before={before} diffs={diffs} catalog={catalog} names={names} />
            <div className="draft-needs">
              <h4>What it needs</h4>
              {current.problems.length === 0 ? (
                <p className="needs-ok">✓ Everything it uses is there, and every step is set up.</p>
              ) : (
                <ul>
                  {current.problems.map((p, i) => (
                    <li key={i}><span className="needs-mark">!</span><span>{labelOf(p.node) ? <b>{labelOf(p.node)}: </b> : null}{p.message}</span></li>
                  ))}
                </ul>
              )}
              {current.problems.length > 0 && <p className="muted small">Fix these by asking for a change, or in the builder after you create it.</p>}
            </div>
          </div>
        </div>
      )}

      {stage === "try" && current && (
        <div className="draft-try">
          <p className="muted">
            One real run of the unsaved blueprint: its steps really happen (messages are written, actions are called), and approvals wait for you below.
          </p>
          {current.problems.length > 0 ? (
            <p className="problem">It can't run yet: {current.problems[0].message}</p>
          ) : tryRun ? (
            <RunPanel runId={tryRun} onOpenRun={setTryRun} onClose={() => setTryRun(null)} />
          ) : (
            <RunForm definition={current.definition} busy={busy !== null} label="Run it once" onStart={startTry} onCancel={() => setStage("blueprint")} />
          )}
          <div className="row">
            <button type="button" onClick={() => setStage("blueprint")}>Back to the blueprint</button>
            <span className="spacer" />
            {tryRun && <button type="button" onClick={() => setTryRun(null)}>Run it again</button>}
            <button type="button" className="primary" onClick={() => go("create")}>Looks right</button>
          </div>
        </div>
      )}

      {stage === "create" && current && (
        <div className="draft-create">
          <label className="field">
            <span className="field-label">Name</span>
            <input value={name} maxLength={80} onChange={e => setName(e.target.value)} />
          </label>
          <p className="muted small">It's saved as a draft and opens in the builder. It doesn't run on its own until you publish it.</p>
          <div className="row">
            <button type="button" onClick={() => setStage("blueprint")}>Back</button>
            <span className="spacer" />
            <button type="button" className="primary" disabled={busy !== null || !(name.trim() || current.name)} onClick={create}>Create and open it</button>
          </div>
        </div>
      )}
    </div>
  );
}
