// How each step of a todo list looks (the server's tools/todo.py)
const TODO_MARKS: Record<string, string> = { done: "✔", in_progress: "◼", pending: "◻", blocked: "✗", skipped: "–" };

export type TodoItem = { id?: number; task?: string; status?: string; note?: string };

/** A todo list: under an update_todos call, and in the context panel's Work tab */
export function TodoList({ items }: { items: TodoItem[] }) {
  if (!items.length) return null;
  return (
    <ul className="todo-list">
      {items.map((item, i) => (
        <li key={item.id ?? i} className={`todo ${item.status ?? "pending"}`}>
          <span className="mark">{TODO_MARKS[item.status ?? "pending"] ?? "◻"}</span> {item.task}
          {item.note && <span className="muted"> ({item.note})</span>}
        </li>
      ))}
    </ul>
  );
}
