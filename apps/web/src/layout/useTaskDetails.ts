import { useEffect, useReducer, useState } from "react";

import {
  listTaskArtifacts,
  readTaskProvenance,
  type PublishedArtifact,
  type SourceQuery,
} from "../api/analysis";

export type DetailLoadStatus = "loading" | "ready" | "error";

type Snapshot<Item> = {
  taskId: string | null;
  revision: number;
  status: "ready" | "error";
  items: Item[];
};

export type TaskArtifact = PublishedArtifact & { taskId: string };
type TaskQuery = SourceQuery & { taskId: string };

export function useTaskDetails(
  taskId: string | null,
  showProvenance: boolean,
  sessionTaskIds: string[] = [],
) {
  const taskKey = [...new Set([...sessionTaskIds, ...(taskId ? [taskId] : [])])]
    .sort()
    .join("|");
  const [revision, refresh] = useReducer((current: number) => current + 1, 0);
  const [artifactSnapshot, setArtifactSnapshot] = useState<
    Snapshot<TaskArtifact>
  >({ taskId: null, revision: -1, status: "ready", items: [] });
  const [querySnapshot, setQuerySnapshot] = useState<Snapshot<TaskQuery>>({
    taskId: null,
    revision: -1,
    status: "ready",
    items: [],
  });

  useEffect(() => {
    if (!taskKey) return;
    const taskIds = taskKey.split("|");
    let active = true;
    void Promise.allSettled(
      taskIds.map(async (id) =>
        (await listTaskArtifacts(id)).map((artifact) => ({
          ...artifact,
          taskId: id,
        })),
      ),
    ).then((results) => {
      if (!active) return;
      setArtifactSnapshot((previous) => ({
        taskId: taskKey,
        revision,
        status: results.every((result) => result.status === "fulfilled")
          ? "ready"
          : "error",
        items: results.flatMap((result, index) =>
          result.status === "fulfilled"
            ? result.value
            : previous.items.filter((item) => item.taskId === taskIds[index]),
        ),
      }));
    });
    return () => {
      active = false;
    };
  }, [taskKey, revision]);

  useEffect(() => {
    if (
      !taskKey ||
      !showProvenance ||
      (querySnapshot.taskId === taskKey && querySnapshot.revision === revision)
    )
      return;
    const taskIds = taskKey.split("|");
    let active = true;
    void Promise.allSettled(
      taskIds.map(async (id) =>
        (await readTaskProvenance(id)).sourceQueries.map((query) => ({
          ...query,
          taskId: id,
        })),
      ),
    ).then((results) => {
      if (!active) return;
      setQuerySnapshot((previous) => ({
        taskId: taskKey,
        revision,
        status: results.every((result) => result.status === "fulfilled")
          ? "ready"
          : "error",
        items: results.flatMap((result, index) =>
          result.status === "fulfilled"
            ? result.value
            : previous.items.filter((item) => item.taskId === taskIds[index]),
        ),
      }));
    });
    return () => {
      active = false;
    };
  }, [
    taskKey,
    showProvenance,
    revision,
    querySnapshot.taskId,
    querySnapshot.revision,
  ]);

  const status = <Item>(snapshot: Snapshot<Item>): DetailLoadStatus => {
    if (!taskKey) return "ready";
    return snapshot.taskId === taskKey && snapshot.revision === revision
      ? snapshot.status
      : "loading";
  };

  return {
    artifacts: artifactSnapshot.items.filter((item) =>
      taskKey.split("|").includes(item.taskId),
    ),
    sourceQueries: querySnapshot.items.filter((item) =>
      taskKey.split("|").includes(item.taskId),
    ),
    artifactsStatus: status(artifactSnapshot),
    provenanceStatus: status(querySnapshot),
    refresh,
  };
}
