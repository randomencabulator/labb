import { useEffect, useState } from "react";
import { type PublicLimits, getPublicLimits } from "./api";

// What labb used before these could be changed: used until the server's arrive, or if it can't say
export const DEFAULT_LIMITS: PublicLimits = {
  attachments_per_message: 10,
  attachment_image_mb: 5,
  attachment_text_chars: 200_000,
  memory_chars: 500,
  document_mb: 20,
  agent_instructions_chars: 8000,
  task_prompt_chars: 8000,
};

let loaded: Promise<PublicLimits> | null = null;
let current = DEFAULT_LIMITS;

/** The limits people meet when they attach or type (Settings → Limits), checked here before sending */
export function useLimits(): PublicLimits {
  const [limits, setLimits] = useState(current);
  useEffect(() => {
    let live = true;
    loaded ??= getPublicLimits().then(
      found => (current = { ...DEFAULT_LIMITS, ...found }),
      () => {
        loaded = null;
        return current;
      },
    );
    loaded.then(found => live && setLimits(found));
    return () => {
      live = false;
    };
  }, []);
  return limits;
}

/** After an admin saves them, so the pages check the new ones */
export function forgetLimits() {
  loaded = null;
}
