import { useCallback, useEffect, useState } from "react";
import { type AnswerMode, MODELS_CHANGED, type Model, getModels } from "./api";

function saved(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable, e.g. in a private window
  }
}

// The models picked in chats, by thread, the latest last; only so many are kept
const CHAT_MODELS = "chatModels";
// The other choices a chat keeps, by thread: how it answers, Web, the browser and Don't ask
const CHAT_OPTIONS = "chatOptions";
const MAX_CHAT_MODELS = 500;

function savedChatModels(): Record<string, string> {
  try {
    const kept = JSON.parse(saved(CHAT_MODELS) ?? "{}");
    return kept && typeof kept === "object" ? kept : {};
  } catch {
    return {};
  }
}

function savedChatOptions(): Record<string, Partial<ChatOptions>> {
  try {
    const kept = JSON.parse(saved(CHAT_OPTIONS) ?? "{}");
    return kept && typeof kept === "object" ? kept : {};
  } catch {
    return {};
  }
}

const savedAnswerMode = (): AnswerMode => {
  const mode = saved("answerMode");
  return mode === "fast" || mode === "thinking" ? mode : "auto";
};

/** What a chat keeps of the message box beyond its model: it lasts as long as the chat does */
export type ChatOptions = {
  answerMode: AnswerMode;
  web: boolean;
  browser: boolean;
  browserAuto: boolean;
};

/**
 * The models the server offers, the answer mode and the Web toggle (remembered in this browser), and the
 * settings each chat keeps. A chat has its own, like its model: what was picked in it (`chosen`, by thread),
 * else the default for new chats. Picking in one chat changes no other.
 */
export function useModels() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState<string | null>(() => saved("model"));
  const [answerMode, setAnswerMode] = useState<AnswerMode>(savedAnswerMode);
  const [chosen, setChosen] = useState<Record<string, string>>(savedChatModels);
  // Searching the web and reading pages in replies (the message box's Web toggle)
  const [web, setWeb] = useState(() => saved("web") !== "off");
  // Letting replies use the browser (the Browser switch), off until it's turned on; only if the server has one
  const [browser, setBrowser] = useState(() => saved("browser") === "on");
  const [browserOffered, setBrowserOffered] = useState(false);
  // What each chat keeps besides its model
  const [options, setOptions] = useState<Record<string, Partial<ChatOptions>>>(savedChatOptions);

  useEffect(() => {
    const load = () =>
      getModels()
        .then(({ default: fallback, browser: offered, models }) => {
          setModels(models);
          setBrowserOffered(Boolean(offered));
          // A remembered model the server no longer has falls back to its default
          setModel(current => (current && models.some(m => m.spec === current) ? current : fallback));
        })
        .catch(() => setModels([]));
    load();
    // An admin changed the models in Settings
    window.addEventListener(MODELS_CHANGED, load);
    return () => window.removeEventListener(MODELS_CHANGED, load);
  }, []);

  const known = (spec: string | null | undefined) => (spec && models.some(m => m.spec === spec) ? spec : null);

  /** The default for new chats */
  const pick = (spec: string) => {
    setModel(spec);
    save("model", spec);
  };

  /** Pick the model for one chat only */
  const choose = useCallback((threadId: string, spec: string) => {
    // From what's saved, not this copy: the page and an app's chat each have one
    const { [threadId]: _, ...others } = savedChatModels();
    const next = Object.fromEntries(Object.entries({ ...others, [threadId]: spec }).slice(-MAX_CHAT_MODELS));
    save(CHAT_MODELS, JSON.stringify(next));
    setChosen(next);
  }, []);

  /** The model a chat uses: picked in it, else what its last reply used (`used`), else its agent's, else the default */
  const modelFor = (threadId: string | null, used?: string | null, agentModel?: string | null) =>
    known(threadId ? chosen[threadId] : null) ?? known(used) ?? known(agentModel) ?? model;

  /** What a chat keeps, else the default for new chats */
  const settingsFor = (threadId: string | null): ChatOptions => {
    const kept = (threadId ? options[threadId] : undefined) ?? {};
    return {
      answerMode: kept.answerMode ?? answerMode,
      web: kept.web ?? web,
      browser: kept.browser ?? browser,
      browserAuto: kept.browserAuto ?? false,
    };
  };

  /** Keep settings for one chat; picking them there changes no other chat (and no new chat's default) */
  const rememberSettings = useCallback((threadId: string, change: Partial<ChatOptions>) => {
    // From what's saved, not this copy: the page and an app's chat each have one
    const { [threadId]: kept, ...others } = savedChatOptions();
    const next = Object.fromEntries(Object.entries({ ...others, [threadId]: { ...kept, ...change } }).slice(-MAX_CHAT_MODELS));
    save(CHAT_OPTIONS, JSON.stringify(next));
    setOptions(next);
  }, []);

  const pickAnswerMode = (mode: AnswerMode) => {
    setAnswerMode(mode);
    save("answerMode", mode);
  };

  const pickWeb = (on: boolean) => {
    setWeb(on);
    save("web", on ? "on" : "off");
  };

  const pickBrowser = (on: boolean) => {
    setBrowser(on);
    save("browser", on ? "on" : "off");
  };

  return {
    models, model, pick, choose, modelFor,
    answerMode, pickAnswerMode,
    web, pickWeb,
    browser, pickBrowser,
    browserOffered,
    settingsFor, rememberSettings,
  };
}
