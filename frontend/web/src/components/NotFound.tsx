/** An address in a page that leads nowhere: something deleted, or not yours */
export function NotFound({ text, back, onBack }: { text: string; back: string; onBack: () => void }) {
  return (
    <div className="not-found">
      <p className="hint">{text}</p>
      <button type="button" onClick={onBack}>
        {back}
      </button>
    </div>
  );
}
