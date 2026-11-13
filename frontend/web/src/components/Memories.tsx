import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useLimits } from "../useLimits";
import { type Memory, addMemory, deleteAllMemories, deleteMemory, listMemories, updateMemory } from "../api";
import { relativeTime } from "../format";

/** One memory: its text (click Edit to change it), pinned or not, and Forget */
function MemoryRow({ memory, onChange, onError }: { memory: Memory; onChange: () => void; onError: (message: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.text);
  const limits = useLimits();

  const run = (action: () => Promise<unknown>) => action().then(onChange, e => onError((e as Error).message));
  const save = (event: FormEvent) => {
    event.preventDefault();
    run(() => updateMemory(memory.id, { text })).then(() => setEditing(false));
  };

  return (
    <tr>
      <td>
        {editing ? (
          <form className="inline-form" onSubmit={save}>
            <input value={text} onChange={e => setText(e.target.value)} maxLength={limits.memory_chars} autoFocus required />
            <button type="submit" className="primary">
              Save
            </button>
            <button type="button" onClick={() => (setText(memory.text), setEditing(false))}>
              Cancel
            </button>
          </form>
        ) : (
          memory.text
        )}
      </td>
      <td>{relativeTime(memory.updated_at)}</td>
      <td className="row-actions">
        {!memory.reviewed && (
          <button
            type="button"
            className="primary"
            title="The chat saved this on its own: keep it if it's right"
            onClick={() => run(() => updateMemory(memory.id, { reviewed: true }))}
          >
            👍 Keep
          </button>
        )}
        <button
          type="button"
          aria-pressed={memory.pinned}
          title={memory.pinned ? "In every conversation; click to recall it only when relevant" : "Put it in every conversation"}
          onClick={() => run(() => updateMemory(memory.id, { pinned: !memory.pinned }))}
        >
          {memory.pinned ? "📌 Pinned" : "Pin"}
        </button>
        {!editing && (
          <button type="button" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
        <button type="button" className="danger" onClick={() => run(() => deleteMemory(memory.id))}>
          Forget
        </button>
      </td>
    </tr>
  );
}

/** What the chat (and labb on your PCs) remembers about you */
export function MemoriesTab() {
  const limits = useLimits();
  const [items, setItems] = useState<Memory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);

  const reload = useCallback(() => {
    listMemories().then(setItems, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await addMemory(text, pinned);
      setText("");
      setPinned(false);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <p className="hint">
        The chat saves lasting facts and preferences you share, and you can add your own. <b>Pinned</b> memories are in every
        conversation; the others come up when a message is about them. labb on your PCs uses the same memories. Only you can see
        them.
      </p>
      <form className="inline-form" onSubmit={add}>
        <input placeholder="e.g. Prefers short answers with code examples" value={text} onChange={e => setText(e.target.value)} maxLength={limits.memory_chars} required />
        <label className="check">
          <input type="checkbox" checked={pinned} onChange={e => setPinned(e.target.checked)} /> Pin
        </label>
        <button type="submit" className="primary">
          Remember
        </button>
      </form>
      {error && <div className="problem">{error}</div>}
      {items && items.some(memory => !memory.reviewed) && (
        <>
          <h3 className="memories-review">To review</h3>
          <p className="hint">
            The chat saved these on its own. Keep the ones that are right, and edit or forget the others. Until you keep one, it's only
            recalled when a message is about it, even if it's pinned.
          </p>
          <table className="settings-table">
            <tbody>
              {items
                .filter(memory => !memory.reviewed)
                .map(memory => (
                  <MemoryRow key={`${memory.id}-${memory.updated_at}`} memory={memory} onChange={reload} onError={setError} />
                ))}
            </tbody>
          </table>
          <h3 className="memories-review">Kept</h3>
        </>
      )}
      {items &&
        (items.length ? (
          <>
            <table className="settings-table">
              <thead>
                <tr>
                  <th>Memory</th>
                  <th>Changed</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items
                  .filter(memory => memory.reviewed)
                  .map(memory => (
                    <MemoryRow key={`${memory.id}-${memory.updated_at}`} memory={memory} onChange={reload} onError={setError} />
                  ))}
              </tbody>
            </table>
            <button
              type="button"
              className="danger log-out"
              onClick={async () => {
                if (!confirm(`Forget all ${items.length} memories? This can't be undone.`)) return;
                await deleteAllMemories().catch(e => setError((e as Error).message));
                reload();
              }}
            >
              Forget everything
            </button>
          </>
        ) : (
          <p className="hint">No memories yet.</p>
        ))}
    </>
  );
}
