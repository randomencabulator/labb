import { useCallback, useEffect, useState } from "react";
import { type Skill, listSkills } from "./api";

/** Your skills and the shared ones, one per name (yours first), for /name in the message box */
export function useSkills() {
  const [skills, setSkills] = useState<Skill[]>([]);

  const refresh = useCallback(async () => {
    try {
      const all = await listSkills();
      const seen = new Set<string>();
      setSkills(all.filter(s => !seen.has(s.name) && seen.add(s.name)));
    } catch {
      // The Skills page shows its own errors
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { skills, refresh };
}
