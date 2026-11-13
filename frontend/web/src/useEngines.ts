import { useCallback, useEffect, useState } from "react";
import { type EngineList, engineApi } from "./api";

const EMPTY: EngineList = { engines: [], my_default: null, server_default: null };

/** The engines to pick from, and the defaults */
export function useEngines() {
  const [list, setList] = useState<EngineList>(EMPTY);

  const refresh = useCallback(() => {
    engineApi.list().then(setList, () => {});
  }, []);

  useEffect(refresh, [refresh]);

  return { ...list, refresh };
}
