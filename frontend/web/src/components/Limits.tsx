import { type FormEvent, useEffect, useState } from "react";
import { type LimitInfo, type LimitsPage, getLimits, saveLimits } from "../api";
import { forgetLimits } from "../useLimits";

/** Settings → Limits (admins): how far replies, tasks and workflows may go, and how many of everything people may have */
export function LimitsTab() {
  const [limits, setLimits] = useState<LimitInfo[] | null>(null);
  // What's typed, by key, until it's saved
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  // Why a service's limits (the apps', the published pages') aren't here
  const [problems, setProblems] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const show = (loaded: LimitsPage) => {
    setLimits(loaded.limits);
    setProblems(loaded.problems);
    setValues(Object.fromEntries(loaded.limits.map(limit => [limit.key, String(limit.value)])));
  };

  useEffect(() => {
    getLimits().then(show, e => setError((e as Error).message));
  }, []);

  if (!limits) return error ? <div className="problem">{error}</div> : <p className="hint">Loading…</p>;

  const changed = limits.filter(limit => values[limit.key] !== String(limit.value));
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      show(await saveLimits(Object.fromEntries(changed.map(limit => [limit.key, Number(values[limit.key])]))));
      forgetLimits();
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const groups = [...new Set(limits.map(limit => limit.group))];
  return (
    <form className="limits" onSubmit={save}>
      <p className="hint">
        How far replies, scheduled tasks and workflows may go, and how many of everything people may have. A change applies to the
        next reply, run, upload or app start.
      </p>
      {problems.map(problem => (
        <div key={problem} className="problem">
          {problem}
        </div>
      ))}
      {groups.map(group => (
        <fieldset key={group}>
          <legend>{group}</legend>
          {limits
            .filter(limit => limit.group === group)
            .map(limit => {
              const value = values[limit.key];
              const isDefault = Number(value) === limit.default;
              return (
                <div key={limit.key} className="limit">
                  <label htmlFor={`limit-${limit.key}`}>
                    <b>{limit.label}</b>
                    <span className="hint">{limit.description}</span>
                  </label>
                  <div className="limit-value">
                    <input
                      id={`limit-${limit.key}`}
                      type="number"
                      min={limit.min}
                      max={limit.max}
                      step={1}
                      required
                      value={value}
                      onChange={e => {
                        setSaved(false);
                        setValues({ ...values, [limit.key]: e.target.value });
                      }}
                    />
                    {limit.unit && <span className="hint">{limit.unit}</span>}
                    <button
                      type="button"
                      className="link limit-default"
                      disabled={isDefault}
                      title={`Back to the default, ${limit.default}`}
                      onClick={() => setValues({ ...values, [limit.key]: String(limit.default) })}
                    >
                      {isDefault ? "default" : `default: ${limit.default}`}
                    </button>
                  </div>
                </div>
              );
            })}
        </fieldset>
      ))}
      {error && <div className="problem">{error}</div>}
      <div className="auth-actions">
        {saved && !changed.length && <span className="ok-note">Saved.</span>}
        <button type="submit" className="primary" disabled={saving || !changed.length}>
          {saving ? "Saving…" : changed.length ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"}` : "Save"}
        </button>
      </div>
    </form>
  );
}
