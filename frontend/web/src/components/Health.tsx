import { useCallback, useEffect, useState } from "react";
import { type HealthCheck, getHealth } from "../api";
import { Resources } from "./Resources";

const MARK = { true: ["✓", "ok-note"], false: ["✗", "problem-text"], null: ["!", "warn-text"] } as const;

/** Settings → Health (admins): whether each part of the server is working, and what to do if not; then what
 * everything uses (Resources) */
export function HealthTab() {
  const [checks, setChecks] = useState<HealthCheck[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const refresh = useCallback(() => {
    setChecking(true);
    setError(null);
    getHealth()
      .then(found => setChecks(found.checks), e => setError((e as Error).message))
      .finally(() => setChecking(false));
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <>
      <p className="hint">
        Ollama and the models, the crawler, the sandbox for Studio's Python, the database, the search index of documents and memories,
        the PCs' labb versions and the MCP servers.
      </p>
      {error && <div className="problem">{error}</div>}
      {checks && (
        <table className="settings-table health">
          <tbody>
            {checks.map(found => {
              const [mark, style] = MARK[String(found.ok) as "true" | "false" | "null"];
              return (
                <tr key={found.name}>
                  <td className={style} aria-label={found.ok ? "Working" : found.ok === false ? "Not working" : "Warning"}>
                    {mark}
                  </td>
                  <td>
                    <b>{found.name}</b>
                  </td>
                  <td>{found.detail}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="auth-actions">
        <button type="button" onClick={refresh} disabled={checking}>
          {checking ? "Checking…" : "Check again"}
        </button>
      </div>
      <Resources />
    </>
  );
}
