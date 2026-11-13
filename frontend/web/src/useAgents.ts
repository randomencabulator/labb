import { useCallback, useEffect, useState } from "react";
import { type Agent, listAgents } from "./api";

/** Your agents and the shared ones; `loaded` once the server answered */
export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setAgents(await listAgents());
      setLoaded(true);
    } catch {
      // The Agents page shows its own errors
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { agents, loaded, refresh };
}
