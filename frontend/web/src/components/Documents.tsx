import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLimits } from "../useLimits";
import { type DocumentInfo, type DocumentText, addDocument, deleteDocument, getDocumentText, listDocuments } from "../api";
import { useSpeech } from "../speech";
import { relativeTime } from "../format";

// While something is indexing, the list is reloaded this often
const POLL_MS = 1500;

function base64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result as string;
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error(`Couldn't read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function size(bytes: number) {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

function status(doc: DocumentInfo) {
  if (doc.status === "indexing") return <span className="hint">indexing…</span>;
  if (doc.status === "error") return <span className="problem-text" title={doc.error ?? undefined}>failed</span>;
  return `${doc.chunks.toLocaleString()} passage${doc.chunks === 1 ? "" : "s"}`;
}

// How much of the text a cited passage covers (the server's chunks are 1,000 characters)
const PASSAGE_CHARS = 1000;

/** One document to read, at /documents/<id>; a passage the chat cited (?page=3&at=1200) is marked */
function DocumentView({ id, onBack }: { id: string; onBack: () => void }) {
  const params = new URLSearchParams(window.location.search);
  const at = Number(params.get("at") ?? NaN);
  const [doc, setDoc] = useState<DocumentText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(Math.max(1, Number(params.get("page") ?? 1) || 1));
  const marked = useRef<HTMLElement>(null);

  useEffect(() => {
    getDocumentText(id).then(setDoc, e => setError((e as Error).message));
  }, [id]);

  useLayoutEffect(() => {
    marked.current?.scrollIntoView({ block: "center" });
  }, [doc, page]);

  if (error) return <div className="problem">{error}</div>;
  if (!doc) return <p className="hint">Loading…</p>;
  const shown = Math.min(page, doc.pages.length);
  const text = doc.pages[shown - 1] ?? "";
  // The cited passage, on the page it was cited from
  const cited = Number.isFinite(at) && shown === Math.max(1, Number(params.get("page") ?? 1) || 1);
  return (
    <div className="document-view">
      <div className="document-head">
        <button type="button" onClick={onBack}>
          ← All documents
        </button>
        <b>{doc.name}</b>
        {doc.paged && (
          <span className="document-pages">
            <button type="button" disabled={shown <= 1} onClick={() => setPage(shown - 1)} aria-label="Previous page">
              ‹
            </button>
            Page {shown} of {doc.pages.length}
            <button type="button" disabled={shown >= doc.pages.length} onClick={() => setPage(shown + 1)} aria-label="Next page">
              ›
            </button>
          </span>
        )}
      </div>
      <pre className="document-text">
        {cited ? (
          <>
            {text.slice(0, at)}
            <mark ref={marked}>{text.slice(at, at + PASSAGE_CHARS)}</mark>
            {text.slice(at + PASSAGE_CHARS)}
          </>
        ) : (
          text
        )}
      </pre>
    </div>
  );
}

/** Your documents, which the chat (and labb on your PCs) can search with `search_documents` */
export function DocumentsTab({ opened, onOpen }: { opened: string | null; onOpen: (id: string | null) => void }) {
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const limits = useLimits();

  const speech = useSpeech();
  const reload = useCallback(() => {
    listDocuments().then(setDocs, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  const indexing = docs?.some(d => d.status === "indexing");
  useEffect(() => {
    if (!indexing) return;
    const timer = setInterval(reload, POLL_MS);
    return () => clearInterval(timer);
  }, [indexing, reload]);

  const add = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    setAdding(files.length);
    const problems: string[] = [];
    for (const file of Array.from(files)) {
      try {
        if (file.size > limits.document_mb * 1_000_000) throw new Error(`${file.name} is over ${limits.document_mb} MB`);
        await addDocument(file.name, await base64(file));
      } catch (e) {
        problems.push((e as Error).message);
      }
      setAdding(n => n - 1);
      reload();
    }
    if (problems.length) setError(problems.join("\n"));
    if (input.current) input.current.value = "";
  };

  if (opened) return <DocumentView id={opened} onBack={() => onOpen(null)} />;

  return (
    <>
      <p className="hint">
        Add PDFs, Word documents, notes and other text files{speech.listen ? ", and recordings (written down as they're added)" : ""}, and the
        chat can search them by meaning when you ask about them. labb on your PCs searches them too. Only you can see your documents.
      </p>
      <div className="inline-form">
        <input
          ref={input}
          type="file"
          multiple
          accept={
            ".pdf,.docx,.txt,.md,.markdown,.rst,.csv,.json,.yaml,.yml,.html,.htm,.xml,.log,text/*,application/pdf" +
            (speech.listen ? ",.mp3,.m4a,.wav,.webm,.ogg,.oga,.opus,.flac,.mpga,audio/*" : "")
          }
          onChange={e => add(e.target.files)}
          disabled={adding > 0}
        />
        {adding > 0 && <span className="hint">Uploading {adding}… (a recording takes a while to write down)</span>}
      </div>
      {error && <div className="problem">{error}</div>}
      {docs &&
        (docs.length ? (
          <table className="settings-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>Added</th>
                <th>Search</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {docs.map(doc => (
                <tr key={doc.id}>
                  <td>
                    {doc.status === "ready" ? (
                      <a
                        href={`/documents/${doc.id}`}
                        onClick={event => {
                          if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
                          event.preventDefault();
                          onOpen(doc.id);
                        }}
                      >
                        {doc.name}
                      </a>
                    ) : (
                      doc.name
                    )}
                  </td>
                  <td>
                    {size(doc.bytes)}
                    {doc.pages ? `, ${doc.pages} page${doc.pages === 1 ? "" : "s"}` : ""}
                  </td>
                  <td>{relativeTime(doc.created_at)}</td>
                  <td>{status(doc)}</td>
                  <td>
                    <button
                      type="button"
                      className="danger"
                      onClick={async () => {
                        if (!confirm(`Delete “${doc.name}”? The chat can't search it any more.`)) return;
                        await deleteDocument(doc.id).catch(e => setError((e as Error).message));
                        reload();
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="hint">No documents yet.</p>
        ))}
    </>
  );
}
