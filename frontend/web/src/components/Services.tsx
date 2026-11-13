import { type FormEvent, useEffect, useState } from "react";
import {
  type ServiceSetting,
  type ServicesPage,
  getServices,
  saveServices,
} from "../api";

type Draft = Record<string, boolean | string>;

const asDraft = (setting: ServiceSetting) =>
  setting.kind === "bool" ? Boolean(setting.value) : String(setting.value);

function same(setting: ServiceSetting, draft: boolean | string) {
  return setting.kind === "bool"
    ? draft === setting.value
    : Number(draft) === setting.value;
}

/** Settings → Services (admins): how the server, the crawler, the browser and the sandbox behave,
 * beside Limits' how far they go; a switch or a number each, with its default to go back to */
export function ServicesTab() {
  const [settings, setSettings] = useState<ServiceSetting[] | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [values, setValues] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const show = (loaded: ServicesPage) => {
    setSettings(loaded.settings);
    setProblems(loaded.problems);
    setValues(
      Object.fromEntries(
        loaded.settings.map((setting) => [setting.key, asDraft(setting)]),
      ),
    );
  };

  useEffect(() => {
    getServices().then(show, (e) => setError((e as Error).message));
  }, []);

  if (!settings)
    return error ? (
      <div className="problem">{error}</div>
    ) : (
      <p className="hint">Loading…</p>
    );

  const changed = settings.filter(
    (setting) => !same(setting, values[setting.key]),
  );
  const set = (key: string, value: boolean | string) => {
    setSaved(false);
    setValues({ ...values, [key]: value });
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const sent = Object.fromEntries(
        changed.map((setting) => [
          setting.key,
          setting.kind === "bool"
            ? Boolean(values[setting.key])
            : Number(values[setting.key]),
        ]),
      );
      show(await saveServices(sent));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const groups = [...new Set(settings.map((setting) => setting.group))];
  return (
    <form className="limits" onSubmit={save}>
      <p className="hint">
        How the server and its services behave. Each starts as its environment
        variable set it; a change applies at once, to the next page read,
        browser action or Python step.
      </p>
      {problems.map((problem) => (
        <div key={problem} className="problem">
          {problem}
        </div>
      ))}
      {groups.map((group) => (
        <fieldset key={group}>
          <legend>{group}</legend>
          {settings
            .filter((setting) => setting.group === group)
            .map((setting) => {
              const value = values[setting.key];
              const isDefault = same(
                { ...setting, value: setting.default },
                value,
              );
              const id = `service-${setting.key}`;
              const shownDefault =
                setting.kind === "bool"
                  ? setting.default
                    ? "on"
                    : "off"
                  : String(setting.default);
              return (
                <div key={setting.key} className="limit">
                  <label htmlFor={id}>
                    <b>{setting.label}</b>
                    <span className="hint">{setting.description}</span>
                  </label>
                  <div className="limit-value">
                    {setting.kind === "bool" ? (
                      <>
                        <span className="hint">{value ? "On" : "Off"}</span>
                        <label className="switch">
                          <input
                            id={id}
                            type="checkbox"
                            role="switch"
                            checked={Boolean(value)}
                            onChange={(e) => set(setting.key, e.target.checked)}
                          />
                          <span />
                        </label>
                      </>
                    ) : (
                      <>
                        <input
                          id={id}
                          type="number"
                          min={setting.min}
                          max={setting.max}
                          step={setting.kind === "int" ? 1 : "any"}
                          required
                          value={String(value)}
                          onChange={(e) => set(setting.key, e.target.value)}
                        />
                        {setting.unit && (
                          <span className="hint">{setting.unit}</span>
                        )}
                      </>
                    )}
                    <button
                      type="button"
                      className="link limit-default"
                      disabled={isDefault}
                      title={`Back to the default, ${shownDefault}`}
                      onClick={() =>
                        set(
                          setting.key,
                          setting.kind === "bool"
                            ? Boolean(setting.default)
                            : String(setting.default),
                        )
                      }
                    >
                      {isDefault ? "default" : `default: ${shownDefault}`}
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
        <button
          type="submit"
          className="primary"
          disabled={saving || !changed.length}
        >
          {saving
            ? "Saving…"
            : changed.length
              ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"}`
              : "Save"}
        </button>
      </div>
    </form>
  );
}
