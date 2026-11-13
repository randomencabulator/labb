import { useEffect, useState } from "react";
import { CloseIcon } from "./Icons";

type Toast = { id: number; text: string; kind: "error" | "info" };

const EVENT = "labb:toast";
const SHOWN_MS = 6000;
let nextId = 1;

/** Show a short message at the bottom of the page, instead of an alert() that blocks it */
export function toast(text: string, kind: Toast["kind"] = "error") {
  window.dispatchEvent(new CustomEvent<Toast>(EVENT, { detail: { id: nextId++, text, kind } }));
}

export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const onToast = (event: Event) => {
      const added = (event as CustomEvent<Toast>).detail;
      setToasts(current => [...current, added]);
      setTimeout(() => setToasts(current => current.filter(t => t.id !== added.id)), SHOWN_MS);
    };
    window.addEventListener(EVENT, onToast);
    return () => window.removeEventListener(EVENT, onToast);
  }, []);

  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <span>{t.text}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setToasts(current => current.filter(x => x.id !== t.id))}>
            <CloseIcon />
          </button>
        </div>
      ))}
    </div>
  );
}
