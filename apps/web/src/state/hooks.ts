import { useSyncExternalStore } from "react";

import { type WorkspaceSnapshot, workspaceStore } from "./workspace-store";

export function useWorkspaceSelector<T>(
  selector: (snapshot: WorkspaceSnapshot) => T,
): T {
  return useSyncExternalStore(
    workspaceStore.subscribe,
    () => selector(workspaceStore.getSnapshot()),
    () => selector({ connection: "idle", selectedTab: "inputs" }),
  );
}
