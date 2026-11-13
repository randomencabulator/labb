import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type ChosenModel,
  type From,
  MODELS_CHANGED,
  type ModelSettings,
  type Provider,
  type ProviderFields,
  type ProviderTest,
  type Price,
  type PriceSettings,
  type SpeechSettings,
  type EmbeddingSettings,
  getEmbeddingSettings,
  saveEmbeddingModel,
  getModelSettings,
  getPrices,
  repriceUsage,
  savePrices,
  suggestPrices,
  getSpeechSettings,
  saveSpeechSettings,
  testSpeech,
  listProviders,
  removeProvider,
  saveModelSettings,
  saveProvider,
  testProvider,
} from "../api";

const KINDS: Record<Provider["kind"], string> = {
  ollama: "Ollama",
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI-compatible",
};

const CLOUD = "ollama-cloud";
const kindName = (provider: Provider) => (provider.name === CLOUD ? "Ollama Cloud" : KINDS[provider.kind]);

const FROM: Record<From, string> = { saved: "saved here", ".env": "from .env", default: "default" };

const changed = () => window.dispatchEvent(new Event(MODELS_CHANGED));

/** What a Test found: the models, or why it failed */
function TestResult({ result }: { result: ProviderTest | "testing" | null }) {
  if (!result) return null;
  if (result === "testing") return <div className="hint">Asking for its models…</div>;
  if (!result.ok) return <div className="problem">{result.error}</div>;
  const shown = result.models.slice(0, 6).map(spec => spec.slice(spec.indexOf(":") + 1));
  return (
    <div className="ok-note">
      Works: {result.count} model{result.count === 1 ? "" : "s"}
      {shown.length > 0 && ` (${shown.join(", ")}${result.count > shown.length ? ", …" : ""})`}
    </div>
  );
}

/** The address and key of a provider, to change (or of a new custom one) */
function ProviderForm({
  provider,
  onSaved,
  onCancel,
}: {
  provider?: Provider;
  onSaved: (providers: Provider[]) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(provider?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url_from === "saved" || !provider?.built_in ? (provider?.base_url ?? "") : "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [result, setResult] = useState<ProviderTest | "testing" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fields = (): ProviderFields => ({ base_url: baseUrl, ...(apiKey ? { api_key: apiKey } : {}), ...(clearKey ? { clear_key: true } : {}) });

  const test = async () => {
    if (!name.trim()) return setError("Give it a name first");
    setResult("testing");
    setResult(await testProvider(name.trim().toLowerCase(), fields()).catch(e => ({ ok: false as const, error: (e as Error).message })));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onSaved(await saveProvider(name.trim().toLowerCase(), fields()));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const keyHint = provider?.key_set ? `Set (${FROM[provider.key_from ?? "saved"]}): leave empty to keep it` : "None yet";
  return (
    <form className="auth-form provider-form" onSubmit={submit}>
      {!provider && (
        <label>
          Name, used in the models' names (<code>lmstudio:qwen2.5-7b</code>)
          <input value={name} onChange={e => setName(e.target.value)} placeholder="lmstudio, openrouter, groq…" required autoFocus />
        </label>
      )}
      <label>
        Address
        <input
          value={baseUrl}
          onChange={e => setBaseUrl(e.target.value)}
          placeholder={provider?.built_in ? `${provider.base_url} (${FROM[provider.base_url_from]})` : "http://192.168.1.20:1234/v1"}
          required={!provider?.built_in}
        />
      </label>
      {(!provider || provider.takes_key) && (
        <label>
          API key{provider?.needs_key ? "" : provider?.name === CLOUD ? " (optional, from ollama.com → Settings → Keys)" : " (if it wants one)"}
          <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder={keyHint} autoComplete="off" />
        </label>
      )}
      {provider?.key_from === "saved" && (
        <label className="check">
          <input type="checkbox" checked={clearKey} onChange={e => setClearKey(e.target.checked)} />
          Remove the saved key{provider.built_in ? " (back to .env's)" : ""}
        </label>
      )}
      <TestResult result={result} />
      {error && <div className="problem">{error}</div>}
      <div className="auth-actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" onClick={test}>
          Test
        </button>
        <button type="submit" className="primary">
          {provider ? "Save" : "Add"}
        </button>
      </div>
    </form>
  );
}

function ProviderCard({ provider, onChange }: { provider: Provider; onChange: (providers?: Provider[]) => void }) {
  const [editing, setEditing] = useState(false);
  const [result, setResult] = useState<ProviderTest | "testing" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const missingKey = provider.needs_key && !provider.key_set;
  const status = !provider.enabled ? "off" : missingKey ? "needs a key" : "on";
  const saved = provider.base_url_from === "saved" || provider.key_from === "saved";

  const run = async (action: () => Promise<Provider[] | void>) => {
    try {
      setError(null);
      onChange((await action()) ?? undefined);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className={`provider-card${provider.enabled ? "" : " off"}`}>
      <div className="provider-head">
        <div>
          <b>{provider.name}</b> <span className="hint">{kindName(provider)}</span>
        </div>
        <span className={`provider-status ${status === "on" ? "on" : ""}`}>{status}</span>
      </div>
      {editing ? (
        <ProviderForm
          provider={provider}
          onSaved={providers => {
            setEditing(false);
            onChange(providers);
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <>
          <div className="provider-detail">
            <code>{provider.base_url || "no address"}</code>
            {provider.built_in && <span className="hint"> · {FROM[provider.base_url_from]}</span>}
          </div>
          {provider.takes_key && (
            <div className="provider-detail hint">
              Key: {provider.key_set ? `set, ${FROM[provider.key_from ?? "saved"]}` : provider.needs_key ? "none: its models aren't offered" : "none"}
            </div>
          )}
          {provider.name === CLOUD && (
            <div className="provider-detail hint">
              {provider.key_set
                ? "Its models run on ollama.com with the key. Turned on, all of them are in the pickers."
                : "Without a key its models run through your Ollama server, once it's signed in (ollama signin). Turned on, all of them are in the pickers."}
            </div>
          )}
          {provider.can_be_strict && (
            <label className="provider-detail hint">
              Strict tool calls{" "}
              <select
                value={provider.strict_tools === null ? "default" : provider.strict_tools ? "on" : "off"}
                onChange={e => {
                  const value = e.target.value;
                  run(() => saveProvider(provider.name, { strict_tools: value === "default" ? null : value === "on" }));
                }}
                title="The provider keeps tool arguments to their schema (constrained decoding). Turn it off if its models or server don't support it."
              >
                <option value="default">default ({provider.strict_default ? "on" : "off"})</option>
                <option value="on">on</option>
                <option value="off">off</option>
              </select>
            </label>
          )}
          <TestResult result={result} />
          {error && <div className="problem">{error}</div>}
          <div className="provider-actions">
            <button
              type="button"
              onClick={async () => {
                setResult("testing");
                setResult(await testProvider(provider.name, {}).catch(e => ({ ok: false as const, error: (e as Error).message })));
              }}
            >
              Test
            </button>
            <button type="button" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button type="button" onClick={() => run(() => saveProvider(provider.name, { enabled: !provider.enabled }))}>
              {provider.enabled ? "Turn off" : "Turn on"}
            </button>
            {provider.built_in
              ? saved && (
                  <button type="button" onClick={() => run(() => removeProvider(provider.name))} title="Forget what's saved here">
                    Use .env
                  </button>
                )
              : (
                  <button
                    type="button"
                    className="danger"
                    onClick={() => confirm(`Remove ${provider.name}? Its models stop working.`) && run(() => removeProvider(provider.name))}
                  >
                    Remove
                  </button>
                )}
          </div>
        </>
      )}
    </div>
  );
}

function Providers({ onChange }: { onChange: () => void }) {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    listProviders().then(setProviders, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  const update = (next?: Provider[]) => {
    if (next) setProviders(next);
    else reload();
    changed();
    onChange();
  };

  return (
    <>
      <h3>Providers</h3>
      <p className="hint">
        Where models come from. <code>.env</code> sets the built-in ones up; what's saved here wins, and keys are stored encrypted and never
        shown again. Any server that speaks OpenAI's API (LM Studio, vLLM, llama.cpp, OpenRouter, Groq…) can be added.
      </p>
      {error && <div className="problem">{error}</div>}
      <div className="provider-grid">
        {providers?.map(provider => <ProviderCard key={provider.name} provider={provider} onChange={update} />)}
        <div className="provider-card add">
          {adding ? (
            <ProviderForm
              onSaved={next => {
                setAdding(false);
                update(next);
              }}
              onCancel={() => setAdding(false)}
            />
          ) : (
            <button type="button" onClick={() => setAdding(true)}>
              + Add a provider
            </button>
          )}
        </div>
      </div>
    </>
  );
}

function Offered({ settings, onSaved }: { settings: ModelSettings; onSaved: (settings: ModelSettings) => void }) {
  const [fallback, setFallback] = useState(settings.default ?? "");
  const [windowSize, setWindowSize] = useState(settings.context_window?.toString() ?? "");
  const [numCtx, setNumCtx] = useState(settings.ollama_num_ctx?.toString() ?? "");
  const [offer, setOffer] = useState(settings.offer);
  const [chosen, setChosen] = useState<ChosenModel[]>(settings.chosen);
  const [adding, setAdding] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const dirty =
    fallback !== (settings.default ?? "") ||
    windowSize !== (settings.context_window?.toString() ?? "") ||
    numCtx !== (settings.ollama_num_ctx?.toString() ?? "") ||
    offer !== settings.offer || JSON.stringify(chosen) !== JSON.stringify(settings.chosen);
  const choices = settings.available.filter(spec => !chosen.some(c => c.spec === spec));

  const change = (spec: string, part: Partial<ChosenModel>) => setChosen(list => list.map(c => (c.spec === spec ? { ...c, ...part } : c)));
  const add = (spec: string) => {
    spec = spec.trim();
    if (!spec.includes(":") || chosen.some(c => c.spec === spec)) return;
    setChosen(list => [...list, { spec, label: "", admins_only: false }]);
    setAdding("");
  };

  const save = async () => {
    try {
      const context_window = windowSize.trim() ? Number(windowSize) : null;
      const ollama_num_ctx = numCtx.trim() ? Number(numCtx) : null;
      onSaved(await saveModelSettings({ default: fallback || null, context_window, ollama_num_ctx, offer, chosen }));
      changed();
      setMessage({ ok: true, text: "Saved. The pickers show it now." });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <>
      <h3>Default model</h3>
      <p className="hint">For chats, agents and tasks that don't pick one. Now: <code>{settings.current_default}</code>.</p>
      <div className="inline-form">
        <select value={fallback} onChange={e => setFallback(e.target.value)} aria-label="Default model">
          <option value="">MODEL from .env</option>
          {[...new Set([...(settings.default ? [settings.default] : []), ...settings.available])].map(spec => (
            <option key={spec} value={spec}>
              {spec}
            </option>
          ))}
        </select>
      </div>

      <h3>Context window</h3>
      <p className="hint">
        Tokens a chat may fill before older turns are summarized (at 75%); lower it to summarize earlier. Empty uses{" "}
        {settings.env_window ? <>CONTEXT_WINDOW from .env ({settings.env_window.toLocaleString()})</> : "each model's own window"}. Now for the
        default model: {settings.current_window.toLocaleString()} tokens.
      </p>
      <div className="inline-form">
        <input
          type="number"
          min={1000}
          step={1024}
          inputMode="numeric"
          placeholder={settings.env_window ? `${settings.env_window} (from .env)` : "the model's own"}
          value={windowSize}
          onChange={e => setWindowSize(e.target.value)}
          aria-label="Context window in tokens"
          className="context-window-input"
        />
      </div>

      <h3>Ollama context size</h3>
      <p className="hint">
        Tokens Ollama keeps in memory for a model (its <code>num_ctx</code>): more holds longer chats and files, but takes more
        memory. Local Ollama models compact within it too.
        Empty uses OLLAMA_NUM_CTX from .env ({settings.env_num_ctx.toLocaleString()}). Now: {settings.current_num_ctx.toLocaleString()} tokens.
      </p>
      <div className="inline-form">
        <input
          type="number"
          min={2048}
          step={2048}
          inputMode="numeric"
          placeholder={`${settings.env_num_ctx} (from .env)`}
          value={numCtx}
          onChange={e => setNumCtx(e.target.value)}
          aria-label="Ollama context size in tokens"
          className="context-window-input"
        />
      </div>

      <h3>Models people can use</h3>
      <div className="segmented" role="radiogroup" aria-label="Which models">
        {(["all", "chosen"] as const).map(mode => (
          <button key={mode} type="button" role="radio" aria-checked={offer === mode} onClick={() => setOffer(mode)}>
            {mode === "all" ? "Every model the providers have" : "Only the ones below"}
          </button>
        ))}
      </div>
      <p className="hint">
        {offer === "all"
          ? "The models below get their labels, and admins-only ones aren't offered to members."
          : "Members can use only these (and the default); admins can use any model."}
      </p>
      {chosen.length > 0 && (
        <table className="settings-table chosen-models">
          <thead>
            <tr>
              <th>Model</th>
              <th>Label in the pickers</th>
              <th>Admins only</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {chosen.map(c => (
              <tr key={c.spec}>
                <td>
                  <code>{c.spec}</code>
                </td>
                <td>
                  <input
                    className="cell-input"
                    value={c.label}
                    maxLength={60}
                    placeholder={c.spec.slice(c.spec.indexOf(":") + 1)}
                    onChange={e => change(c.spec, { label: e.target.value })}
                    aria-label={`Label of ${c.spec}`}
                  />
                </td>
                <td>
                  <input type="checkbox" checked={c.admins_only} onChange={e => change(c.spec, { admins_only: e.target.checked })} aria-label={`${c.spec} for admins only`} />
                </td>
                <td className="row-actions">
                  <button type="button" onClick={() => setChosen(list => list.filter(x => x.spec !== c.spec))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="inline-form"
        onSubmit={e => {
          e.preventDefault();
          add(adding);
        }}
      >
        <input list="model-choices" value={adding} onChange={e => setAdding(e.target.value)} placeholder="Add a model, e.g. openai:gpt-5.5" />
        <datalist id="model-choices">
          {choices.map(spec => (
            <option key={spec} value={spec} />
          ))}
        </datalist>
        <button type="submit">Add</button>
      </form>
      {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
      <div className="auth-actions">
        <button type="button" className="primary" disabled={!dirty} onClick={save}>
          Save models
        </button>
      </div>
    </>
  );
}

// A price's row as it's edited: dollars per million tokens as typed, and where a suggestion came from
type PriceRow = { key: string; input: string; output: string; cacheRead: string; cacheWrite: string; suggested?: string };
const PRICE_FIELDS = [
  ["input", "Input"],
  ["output", "Output"],
  ["cacheRead", "Cached input"],
  ["cacheWrite", "Cache write"],
] as const;

const asText = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));
const toRow = (key: string, price: Price, suggested?: string): PriceRow => ({
  key,
  input: asText(price[0]),
  output: asText(price[1]),
  cacheRead: asText(price[2]),
  cacheWrite: asText(price[3]),
  suggested,
});
const rowsOf = (prices: Record<string, Price>) => Object.entries(prices).map(([key, price]) => toRow(key, price));
/** A row as the server takes it; null if a number can't be read */
function priceOf(row: PriceRow): Price | null {
  const read = (text: string, optional: boolean) => {
    if (!text.trim()) return optional ? null : NaN;
    return Number(text);
  };
  const price = [read(row.input, false), read(row.output, false), read(row.cacheRead, true), read(row.cacheWrite, true)];
  return price.some(p => p !== null && (Number.isNaN(p) || p < 0)) ? null : price;
}

/** What models cost, for the usage stats: each reply is costed at these as it's recorded */
function Prices() {
  const [settings, setSettings] = useState<PriceSettings | null>(null);
  const [rows, setRows] = useState<PriceRow[]>([]);
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const show = (next: PriceSettings) => {
    setSettings(next);
    setRows(rowsOf(next.prices));
  };
  useEffect(() => {
    getPrices().then(show, e => setMessage({ ok: false, text: (e as Error).message }));
  }, []);
  if (!settings) return null;

  const priced = new Set(rows.map(r => r.key));
  const unpriced = settings.models.filter(m => !priced.has(m));
  const dirty = JSON.stringify(rows.map(r => [r.key, priceOf(r)])) !== JSON.stringify(rowsOf(settings.prices).map(r => [r.key, priceOf(r)]));
  const change = (key: string, fields: Partial<PriceRow>) => setRows(list => list.map(r => (r.key === key ? { ...r, ...fields, suggested: undefined } : r)));
  const add = (key: string) => {
    key = key.trim();
    if (!key || priced.has(key)) return;
    setRows(list => [...list, { key, input: "", output: "", cacheRead: "", cacheWrite: "" }]);
    setAdding("");
  };

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await work();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const save = () =>
    run(async () => {
      const prices: Record<string, Price> = {};
      for (const row of rows) {
        const price = priceOf(row);
        if (!price) throw new Error(`${row.key}: input and output are needed, in dollars per million tokens`);
        prices[row.key] = price;
      }
      show(await savePrices(prices));
      setMessage({ ok: true, text: "Saved. Replies from now on are costed at these prices." });
    });
  const suggest = () =>
    run(async () => {
      const wanted = unpriced.length ? unpriced : rows.map(r => r.key);
      const { suggestions } = await suggestPrices(wanted);
      const found = Object.entries(suggestions);
      if (!found.length) {
        setMessage({ ok: false, text: "LiteLLM's list has none of these models." });
        return;
      }
      setRows(list => {
        const next = list.filter(r => !(r.key in suggestions));
        return [...next, ...found.map(([key, s]) => toRow(key, s.price, s.from))];
      });
      setMessage({ ok: true, text: `${found.length} suggested from LiteLLM's list: check them against the provider's prices, then save.` });
    });
  const fill = (everything: boolean) =>
    run(async () => {
      if (everything && !window.confirm("Cost every reply recorded so far at today's prices? What earlier replies cost changes to match.")) return;
      const { updated } = await repriceUsage(everything);
      show(await getPrices());
      setMessage({ ok: true, text: `${updated.toLocaleString()} repl${updated === 1 ? "y" : "ies"} costed.` });
    });

  return (
    <>
      <h3>Prices</h3>
      <p className="hint">
        What paid models cost, in dollars per million tokens, for the usage stats. No provider says through its API, so they're set here (from
        the provider's pricing page). A model, a name without the provider, or a pattern like <code>anthropic:claude-*</code>. Cached input and
        cache writes are what reading from and writing to the provider's prompt cache cost; left empty, they're the input price. Models
        without a price count as free. Each reply is costed when it's recorded, so a new price doesn't change what was spent before.
      </p>
      {rows.length > 0 && (
        <table className="settings-table prices-table">
          <thead>
            <tr>
              <th>Model</th>
              {PRICE_FIELDS.map(([, label]) => (
                <th key={label}>{label}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.key} className={row.suggested ? "suggested" : undefined}>
                <td>
                  <code>{row.key}</code>
                  {row.suggested && <div className="hint">Suggested ({row.suggested} in LiteLLM's list)</div>}
                </td>
                {PRICE_FIELDS.map(([field, label]) => (
                  <td key={field}>
                    <span className="price-input">
                      <span aria-hidden="true">$</span>
                      <input
                        className="cell-input"
                        inputMode="decimal"
                        value={row[field]}
                        placeholder={field === "cacheRead" || field === "cacheWrite" ? row.input || "input" : ""}
                        onChange={e => change(row.key, { [field]: e.target.value })}
                        aria-label={`${label} price of ${row.key}, dollars per million tokens`}
                      />
                    </span>
                  </td>
                ))}
                <td className="row-actions">
                  <button type="button" onClick={() => setRows(list => list.filter(r => r.key !== row.key))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="inline-form"
        onSubmit={e => {
          e.preventDefault();
          add(adding);
        }}
      >
        <input list="price-models" value={adding} onChange={e => setAdding(e.target.value)} placeholder="Add a model, e.g. openai:gpt-5.5 or anthropic:claude-*" />
        <datalist id="price-models">
          {unpriced.map(model => (
            <option key={model} value={model} />
          ))}
        </datalist>
        <button type="submit">Add</button>
        <button type="button" onClick={suggest} disabled={busy} title="Fill in prices from LiteLLM's public price list, to check before saving">
          Suggest prices
        </button>
      </form>
      {settings.fillable > 0 && !dirty && (
        <p className="hint">
          {settings.fillable.toLocaleString()} earlier repl{settings.fillable === 1 ? "y has" : "ies have"} no cost but {settings.fillable === 1 ? "its model has" : "their models have"} a
          price now.{" "}
          <button type="button" className="link" disabled={busy} onClick={() => fill(false)}>
            Cost them at these prices
          </button>
        </p>
      )}
      {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
      <div className="auth-actions">
        <button type="button" disabled={busy || dirty} onClick={() => fill(true)} title={dirty ? "Save first" : "Cost every reply so far at today's prices"}>
          Cost all usage again
        </button>
        <button type="button" className="primary" disabled={!dirty || busy} onClick={save}>
          Save prices
        </button>
      </div>
    </>
  );
}

/** Which models listen and speak (the mic, read aloud, voice mode, recordings, Studio's steps) */
function Speech() {
  const [settings, setSettings] = useState<SpeechSettings | null>(null);
  const [listen, setListen] = useState("");
  const [speak, setSpeak] = useState("");
  const [voice, setVoice] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const show = (next: SpeechSettings) => {
    setSettings(next);
    setListen(next.listen ?? "");
    setSpeak(next.speak ?? "");
    setVoice(next.voice);
  };
  useEffect(() => {
    getSpeechSettings().then(show, e => setMessage({ ok: false, text: (e as Error).message }));
  }, []);
  if (!settings) return null;

  const dirty = listen !== (settings.listen ?? "") || speak !== (settings.speak ?? "") || voice !== settings.voice;
  const save = async () => {
    setMessage(null);
    try {
      show(await saveSpeechSettings({ listen: listen.trim() || null, speak: speak.trim() || null, voice: voice.trim() }));
      changed();
      setMessage({ ok: true, text: "Saved." });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };
  const test = async () => {
    setTesting(true);
    setMessage(null);
    try {
      const result = await testSpeech();
      if (result.error) setMessage({ ok: false, text: result.error });
      else if (result.heard !== null) setMessage({ ok: true, text: `Said a sentence (${(result.spoke ?? 0).toLocaleString()} bytes of audio) and heard: “${result.heard}”` });
      else setMessage({ ok: true, text: `Said a sentence: ${(result.spoke ?? 0).toLocaleString()} bytes of audio.` });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <h3>Speech</h3>
      <p className="hint">
        A model that listens gives the chat a mic and lets people add recordings as documents; one that speaks reads replies aloud. With both,
        the chat has a voice mode. They're models of OpenAI or of a server with its audio API, like{" "}
        <a href="https://github.com/speaches-ai/speaches" target="_blank" rel="noopener noreferrer">speaches</a> (docker compose runs it as the
        provider speaches, and downloads the models picked of it). Ollama has no speech models.
      </p>
      <div className="speech-form">
        <label>
          <span>Listens (speech to text)</span>
          <input list="speech-listen" value={listen} onChange={e => setListen(e.target.value)} placeholder="Off; e.g. openai:gpt-4o-transcribe" />
        </label>
        <label>
          <span>Speaks (text to speech)</span>
          <input list="speech-speak" value={speak} onChange={e => setSpeak(e.target.value)} placeholder="Off; e.g. openai:gpt-4o-mini-tts" />
        </label>
        <label>
          <span>Voice</span>
          <input list="speech-voices" value={voice} onChange={e => setVoice(e.target.value)} placeholder="alloy" />
        </label>
        <datalist id="speech-listen">{settings.listen_options.map(spec => <option key={spec} value={spec} />)}</datalist>
        <datalist id="speech-speak">{settings.speak_options.map(spec => <option key={spec} value={spec} />)}</datalist>
        <datalist id="speech-voices">{settings.voices_options.map(name => <option key={name} value={name} />)}</datalist>
      </div>
      {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
      <div className="auth-actions">
        <button type="button" disabled={dirty || testing || !settings.speak} onClick={test} title={dirty ? "Save first" : undefined}>
          {testing ? "Testing…" : "Test"}
        </button>
        <button type="button" className="primary" disabled={!dirty} onClick={save}>
          Save speech
        </button>
      </div>
    </>
  );
}

/** "12 of 40 documents and 3 of 10 memories", or null when everything is in the current model's index */
function embeddingProgress(progress: EmbeddingSettings["progress"]) {
  const parts = (["documents", "memories"] as const)
    .filter(kind => progress[kind].done < progress[kind].total)
    .map(kind => `${progress[kind].done.toLocaleString()} of ${progress[kind].total.toLocaleString()} ${kind}`);
  return parts.length ? parts.join(" and ") : null;
}

/** The model documents and memories are embedded with, to be found by meaning */
function Search() {
  const [settings, setSettings] = useState<EmbeddingSettings | null>(null);
  const [model, setModel] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getEmbeddingSettings().then(
      next => {
        setSettings(next);
        setModel(next.model);
      },
      e => setMessage({ ok: false, text: (e as Error).message }),
    );
  }, []);
  // While everything is embedded again, how far it's got
  const busy = settings ? embeddingProgress(settings.progress) : null;
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => getEmbeddingSettings().then(setSettings, () => {}), 3000);
    return () => clearInterval(timer);
  }, [Boolean(busy)]);
  if (!settings) return message ? <div className="problem">{message.text}</div> : null;

  const picked = model.trim();
  const option = settings.options.find(o => o.model === picked);
  const dirty = picked !== settings.model;
  const save = async () => {
    setMessage(null);
    setSaving(true);
    try {
      const next = await saveEmbeddingModel(picked);
      setSettings(next);
      setModel(next.model);
      setMessage({ ok: true, text: `Saved. Documents and memories are being embedded again with ${next.model}.` });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <h3>Search</h3>
      <p className="hint">
        The Ollama model that turns documents and memories into vectors, so they're found by meaning, not only by their words. Pull it on the
        Ollama server first (<code>ollama pull {picked || settings.default}</code>). Changing it embeds every document and memory again in the
        background: until then, documents say they're indexing and memories are found by their words.
      </p>
      <div className="speech-form">
        <label>
          <span>Embedding model</span>
          <input list="embedding-models" value={model} onChange={e => setModel(e.target.value)} placeholder={settings.default} />
        </label>
        <datalist id="embedding-models">
          {settings.options.map(o => (
            <option key={o.model} value={o.model} label={[o.about, settings.ollama ? (o.pulled ? "pulled" : "not pulled") : ""].filter(Boolean).join(" · ")} />
          ))}
        </datalist>
      </div>
      <ul className="hint embedding-options">
        {settings.options.map(o => (
          <li key={o.model}>
            <button type="button" className="link" onClick={() => setModel(o.model)}>
              {o.model}
            </button>
            {o.about && ` — ${o.about}`}
            {settings.ollama && !o.pulled && " (not pulled)"}
            {o.model === settings.model && " · in use"}
            {o.model === settings.default && o.model !== settings.model && " · the default"}
          </li>
        ))}
      </ul>
      {dirty && option && settings.ollama && !option.pulled && (
        <p className="hint">
          Not pulled yet: <code>ollama pull {picked}</code> on the Ollama server, then save.
        </p>
      )}
      {busy && <p className="hint">Embedding again: {busy} so far.</p>}
      {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
      <div className="auth-actions">
        <button type="button" className="primary" disabled={!dirty || !picked || saving} onClick={save}>
          {saving ? "Checking…" : "Save search model"}
        </button>
      </div>
    </>
  );
}

/** Settings → Models (admins): the providers, the default model, which models people can use, what they cost, search and speech */
export function ModelsTab() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped to start the models' form again from what's saved
  const [version, setVersion] = useState(0);

  const reload = useCallback(() => {
    getModelSettings().then(
      next => {
        setSettings(next);
        setVersion(v => v + 1);
      },
      e => setError((e as Error).message),
    );
  }, []);
  useEffect(reload, [reload]);

  return (
    <div className="models-settings">
      <Providers onChange={reload} />
      {error && <div className="problem">{error}</div>}
      {settings ? <Offered key={version} settings={settings} onSaved={setSettings} /> : <p className="hint">Loading the models…</p>}
      <Prices />
      <Search />
      <Speech />
    </div>
  );
}
