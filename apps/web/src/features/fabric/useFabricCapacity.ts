import { useCallback, useEffect, useRef, useState } from "react";

import { getFabricCapacityState } from "../../api/fabric";
import type { FabricCapacityView } from "./source-context";

// While paused, look again now and then, so resuming the capacity shows up without a reload.
export const PAUSED_RECHECK_MS = 60_000;
// Focus and visibility often fire together; one look covers both.
const MIN_PASSIVE_RECHECK_MS = 5_000;

export type FabricCapacity = {
  capacity: FabricCapacityView | undefined;
  /** Look again now, for when a run has just read the source. */
  refresh: () => void;
};

/** The linked source's capacity state, kept current while the workspace is open. */
export function useFabricCapacity(enabled: boolean): FabricCapacity {
  const [capacity, setCapacity] = useState<FabricCapacityView>();
  const live = useRef(false);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const lastCheck = useRef(0);

  const check = useCallback((force: boolean): void => {
    if (!live.current) return;
    if (inFlight.current) {
      // An answer already on its way may predate what the caller just saw, so look once more after it.
      if (force) queued.current = true;
      return;
    }
    const now = Date.now();
    if (!force && now - lastCheck.current < MIN_PASSIVE_RECHECK_MS) return;
    inFlight.current = true;
    lastCheck.current = now;
    void getFabricCapacityState()
      .then((state) => {
        if (live.current) setCapacity(state);
      })
      .catch(() => {
        // A failed look proves nothing, so the last answer stands; only a first look falls back.
        if (live.current)
          setCapacity((current) =>
            current === undefined || current === "checking"
              ? "unknown"
              : current,
          );
      })
      .finally(() => {
        inFlight.current = false;
        if (queued.current) {
          queued.current = false;
          check(true);
        }
      });
  }, []);

  useEffect(() => {
    live.current = enabled;
    if (!enabled) {
      setCapacity(undefined);
      return;
    }
    setCapacity("checking");
    check(true);
    const lookAgain = (): void => {
      if (document.visibilityState === "visible") check(false);
    };
    document.addEventListener("visibilitychange", lookAgain);
    window.addEventListener("focus", lookAgain);
    return () => {
      live.current = false;
      queued.current = false;
      document.removeEventListener("visibilitychange", lookAgain);
      window.removeEventListener("focus", lookAgain);
    };
  }, [enabled, check]);

  useEffect(() => {
    if (!enabled || capacity !== "paused") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") check(true);
    }, PAUSED_RECHECK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [enabled, capacity, check]);

  const refresh = useCallback(() => {
    check(true);
  }, [check]);

  return { capacity, refresh };
}
