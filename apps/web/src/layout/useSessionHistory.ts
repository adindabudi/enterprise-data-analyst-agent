import { useEffect, useReducer, useState } from "react";

import {
  listAnalysisSessions,
  readAnalysisHistory,
  readAnalysisSession,
  readAnalysisTask,
  taskIsRunning,
  type AnalysisHistory,
  type AnalysisSession,
} from "../api/analysis";

type Selection = { sessionId?: string; taskId?: string };
type RestoredSession = {
  session: AnalysisSession;
  history: AnalysisHistory;
  taskId: string | null;
};

function initialSelection(): Selection | null {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session");
  const taskId = params.get("task");
  if (sessionId && /^ses_[A-Za-z0-9_-]{8,}$/.test(sessionId))
    return {
      sessionId,
      ...(taskId && /^task_[A-Za-z0-9_-]{8,}$/.test(taskId) ? { taskId } : {}),
    };
  if (taskId && /^task_[A-Za-z0-9_-]{8,}$/.test(taskId)) return { taskId };
  return null;
}

export function useSessionHistory() {
  const [sessions, setSessions] = useState<AnalysisSession[]>([]);
  const [listError, setListError] = useState(false);
  const [revision, refreshList] = useReducer((value: number) => value + 1, 0);
  const [selection, setSelection] = useState<Selection | null>(
    initialSelection,
  );
  const [snapshot, setSnapshot] = useState<{
    selection: Selection | null;
    value: RestoredSession | null;
    error: boolean;
  }>({ selection: null, value: null, error: false });

  useEffect(() => {
    let active = true;
    void listAnalysisSessions()
      .then((items) => {
        if (!active) return;
        setSessions(items);
        setListError(false);
      })
      .catch(() => {
        if (active) setListError(true);
      });
    return () => {
      active = false;
    };
  }, [revision]);

  useEffect(() => {
    if (!selection) return;
    let active = true;
    const restore = async (): Promise<void> => {
      const sessionId =
        selection.sessionId ??
        (selection.taskId
          ? (await readAnalysisTask(selection.taskId)).sessionId
          : null);
      if (!sessionId) throw new Error("Session is unavailable");
      const [session, history] = await Promise.all([
        readAnalysisSession(sessionId),
        readAnalysisHistory(sessionId),
      ]);
      const selectedTask =
        history.tasks.find((task) => task.taskId === selection.taskId) ??
        history.tasks.findLast((task) => taskIsRunning(task.status)) ??
        history.tasks.at(-1);
      if (active)
        setSnapshot({
          selection,
          value: { session, history, taskId: selectedTask?.taskId ?? null },
          error: false,
        });
    };
    void restore().catch(() => {
      if (active) setSnapshot({ selection, value: null, error: true });
    });
    return () => {
      active = false;
    };
  }, [selection]);

  return {
    sessions,
    listError,
    refreshList,
    loading: selection !== null && snapshot.selection !== selection,
    error:
      selection !== null && snapshot.selection === selection && snapshot.error,
    restored:
      selection !== null && snapshot.selection === selection
        ? snapshot.value
        : null,
    open: (sessionId: string) => {
      setSelection({ sessionId });
    },
    clear: () => {
      setSelection(null);
    },
    retry: () => {
      setSelection((current) => (current ? { ...current } : null));
    },
  };
}
