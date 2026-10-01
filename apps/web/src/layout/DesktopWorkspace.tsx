import {
  Button,
  Dialog,
  DialogSurface,
  Input,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import {
  ArrowClockwiseRegular,
  ChatAddRegular,
  DismissRegular,
  NavigationRegular,
  PanelRightRegular,
  SearchRegular,
} from "@fluentui/react-icons";
import { useDeferredValue, useEffect, useRef, useState } from "react";

import {
  AnalysisRequestError,
  cancelAnalysis,
  createAnalysis,
  getFinalMessage,
  listSessionTodos,
  readAnalysisHistory,
  taskIsRunning,
  steerAnalysis,
  type AgentTodoItem,
  type SessionTask,
} from "../api/analysis";
import { type Activity, checkpointActivity } from "../chat/ActivityBlock";
import { blockedUploadMessage, Composer } from "../chat/Composer";
import { Conversation, type NarrativeMessage } from "../chat/Conversation";
import { mergeDataSteps, type DataStep } from "../chat/DataSteps";
import { threadTitle } from "../chat/thread-title";
import { useAnalysisUpload } from "../chat/useAnalysisUpload";
import {
  fabricSourceName,
  type FabricAvailability,
  type FabricCapacityView,
  type FabricSourceContext,
} from "../features/fabric/source-context";
import type { FabricAuthorizationStatus } from "../api/fabric";
import {
  initialTaskView,
  reduceEvent,
  type FabricAuthViewAction,
  type TaskEvent,
  type TaskViewState,
} from "../state/event-reducer";
import {
  TaskEventConnection,
  type TaskConnectionState,
  type TaskStreamEvent,
} from "../state/sse-connection";
import { useTaskDetails } from "./useTaskDetails";
import { useSessionHistory } from "./useSessionHistory";
import { WorkspaceDetails } from "./WorkspaceDetails";

const DEFAULT_ANALYSIS_TITLE = "New private analysis";

// A resume link is client-supplied, so only a well-formed id may revive a run.
function resumedTaskId(): string | null {
  const value = new URLSearchParams(window.location.search).get("task");
  return value && /^task_[A-Za-z0-9_-]{8,}$/.test(value) ? value : null;
}

function requestKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

type LiveTodo = Extract<
  TaskStreamEvent,
  { type: "todo.updated" }
>["payload"][number];

/** The plan from a `todo.updated` event, in the shape the Tasks panel shows. */
export function liveTodoItems(items: readonly LiveTodo[]): AgentTodoItem[] {
  return items.map((item, index) => {
    const id = Number(item.todoId);
    return {
      id: Number.isInteger(id) ? id : index,
      title: item.text,
      description: item.description ?? null,
      isComplete: item.completed,
    };
  });
}

export function DesktopWorkspace({
  fabricAvailability = "disabled",
  fabricAuthorization,
  fabricCapacity,
  onRunSettled,
  compact = false,
}: {
  fabricAvailability?: FabricAvailability;
  fabricAuthorization?: FabricAuthorizationStatus;
  fabricCapacity?: FabricCapacityView;
  /** A run just ended, so what it learned about the source is worth showing. */
  onRunSettled?: () => void;
  compact?: boolean;
}) {
  const fabricSource: FabricSourceContext = {
    availability: fabricAvailability,
    ...(fabricAuthorization === undefined
      ? {}
      : { authorization: fabricAuthorization }),
    ...(fabricCapacity === undefined ? {} : { capacity: fabricCapacity }),
  };
  const runSettled = useRef(onRunSettled);
  useEffect(() => {
    runSettled.current = onRunSettled;
  }, [onRunSettled]);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  const detailsTrigger = useRef<HTMLButtonElement>(null);
  const history = useSessionHistory();
  const restored = history.restored;
  const [sessionTasks, setSessionTasks] = useState<SessionTask[]>([]);
  const [sessionTaskIds, setSessionTaskIds] = useState<string[]>([]);
  const [showProvenance, setShowProvenance] = useState(false);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [sessionTitle, setSessionTitle] = useState<string | null>(null);
  const analysisTitle =
    sessionTitle ??
    (fabricAvailability === "disabled"
      ? DEFAULT_ANALYSIS_TITLE
      : fabricSourceName(fabricSource));
  const [status, setStatus] = useState(() =>
    resumedTaskId() ? "Restoring analysis" : "Ready to begin an analysis",
  );
  const [messages, setMessages] = useState<NarrativeMessage[]>([]);
  const [activities, setActivities] = useState<Activity[]>(() =>
    resumedTaskId()
      ? [{ id: "restore-task", label: "Restoring analysis", status: "running" }]
      : [],
  );
  const [dataSteps, setDataSteps] = useState<DataStep[]>([]);
  const clearDataSteps = (): void => {
    setDataSteps([]);
  };
  const [runStartedAt, setRunStartedAt] = useState<number>();
  const [todoItems, setTodoItems] = useState<AgentTodoItem[]>([]);
  const liveTodoRevision = useRef(0);
  const [announcement, setAnnouncement] = useState("Workspace ready");
  const [streamedText, setStreamedText] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRestoring, setIsRestoring] = useState(
    () => resumedTaskId() !== null,
  );
  const [isRunning, setIsRunning] = useState(() => resumedTaskId() !== null);
  const [connectionState, setConnectionState] = useState<
    TaskConnectionState | "idle"
  >("idle");
  const taskView = useRef<TaskViewState>(initialTaskView());
  const [fabricAuthAction, setFabricAuthAction] =
    useState<FabricAuthViewAction>();
  const [taskId, setTaskId] = useState<string | null>(resumedTaskId);
  const {
    artifacts,
    sourceQueries,
    artifactsStatus,
    provenanceStatus,
    refresh: refreshDetails,
  } = useTaskDetails(
    history.loading ? null : taskId,
    showProvenance,
    sessionTaskIds,
  );
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const inputUpload = useAnalysisUpload(
    activeSessionId,
    threadTitle(analysisTitle),
    setActiveSessionId,
  );
  const [threadKey, setThreadKey] = useState(0);
  const threadGeneration = useRef(0);
  useEffect(() => {
    if (!restored) return;
    const { session, history: saved, taskId: savedTaskId } = restored;
    threadGeneration.current += 1;
    setActiveSessionId(session.sessionId);
    setSessionTitle(session.title);
    setMessages((previous) => {
      const observed = new Map(
        previous.map((message) => [message.id, message.steps ?? []]),
      );
      return saved.messages.map((message) => ({
        id: message.messageId,
        role: message.role,
        text: message.text,
        steps: mergeDataSteps(
          message.steps ?? [],
          observed.get(message.messageId) ?? [],
        ),
      }));
    });
    setStreamedText("");
    taskView.current = initialTaskView();
    setFabricAuthAction(undefined);
    setConnectionState("idle");
    setSessionTasks(saved.tasks);
    setSessionTaskIds(saved.tasks.map((task) => task.taskId));
    setTaskId(savedTaskId);
    setIsRestoring(false);
    setIsSubmitting(false);
    const task = saved.tasks.find((item) => item.taskId === savedTaskId);
    setIsRunning(task ? taskIsRunning(task.status) : false);
    setActivities(task ? [checkpointActivity(task.taskId, task.status)] : []);
    setStatus(
      task ? checkpointActivity(task.taskId, task.status).label : "Ready",
    );
    window.history.replaceState(
      null,
      "",
      `/?session=${encodeURIComponent(session.sessionId)}${savedTaskId ? `&task=${encodeURIComponent(savedTaskId)}` : ""}`,
    );
  }, [restored]);

  useEffect(() => {
    if (!history.error) return;
    setIsRestoring(false);
    setIsRunning(false);
    setStatus("Session history could not be restored");
    setActivities([]);
  }, [history.error]);

  useEffect(() => {
    if (!taskId || history.loading || history.error) return;
    let active = true;
    const applyEvent = (event: TaskStreamEvent): void => {
      if (!active) return;
      setIsRestoring(false);
      if (
        event.type === "message.delta" ||
        event.type === "attempt.superseded" ||
        event.type === "run.completed" ||
        event.type === "auth.required" ||
        event.type === "task.checkpointed"
      ) {
        const next = reduceEvent(
          taskView.current,
          event as unknown as TaskEvent,
        );
        taskView.current = next;
        setFabricAuthAction(next.fabricAuthAction);
        setStreamedText(next.provisionalText);
      }
      if (event.type === "task.checkpointed") {
        const activity = checkpointActivity(
          event.eventId,
          event.payload.status,
        );
        setStatus(activity.label);
        setAnnouncement(activity.label);
        setActivities([activity]);
        setIsRunning(activity.status === "running");
      }
      if (event.type === "analysis_progress") {
        const label = event.payload.milestone;
        setAnnouncement(label);
        setActivities([
          {
            id: `stream-${event.eventId}`,
            label,
            status: "running",
            detail: event.payload.detail ?? "Live durable checkpoint",
          },
        ]);
        setStatus(label);
      }
      if (event.type === "artifact.ready") {
        refreshDetails();
      }
      if (event.type === "todo.updated") {
        // The plan as the agent writes it; the stored copy is only saved once the run ends.
        liveTodoRevision.current += 1;
        setTodoItems(liveTodoItems(event.payload));
      }
      if (event.type === "run.completed") {
        runSettled.current?.();
        taskView.current = {
          ...taskView.current,
          fabricAuthAction: undefined,
        };
        setFabricAuthAction(undefined);
        setStatus("Analysis completed");
        setIsRunning(false);
        refreshDetails();
        setActivities((current) =>
          current.map((activity) => ({ ...activity, status: "completed" })),
        );
        if (event.payload.finalMessageId) {
          void getFinalMessage(taskId, event.payload.finalMessageId)
            .then((message) => {
              if (!active) return;
              setMessages((current) =>
                current.some((item) => item.id === message.messageId)
                  ? current
                  : [
                      ...current,
                      {
                        id: message.messageId,
                        role: "assistant",
                        text: message.text,
                      },
                    ],
              );
              setStreamedText("");
            })
            .catch(() => {
              if (!active) return;
              setStatus("Final response could not be loaded");
              setAnnouncement("Final response could not be loaded");
              setActivities([
                {
                  id: "final-message-failed",
                  label: "Final response could not be loaded",
                  status: "failed",
                  detail:
                    "Refresh the analysis to retry the canonical response.",
                },
              ]);
            });
        }
      }
      if (event.type === "run.failed" || event.type === "run.cancelled") {
        runSettled.current?.();
        refreshDetails();
        setStatus(
          event.type === "run.failed"
            ? "Analysis failed"
            : "Analysis cancelled",
        );
        setIsRunning(false);
        setActivities((current) =>
          current.map((activity) => ({
            ...activity,
            status: event.type === "run.failed" ? "failed" : "completed",
          })),
        );
      }
    };
    const connection = new TaskEventConnection(taskId, {
      onEvent: applyEvent,
      onState: (nextState) => {
        if (!active) return;
        setConnectionState(nextState);
        if (nextState === "degraded") {
          setAnnouncement(
            "Live updates delayed; reconnecting to the canonical stream",
          );
        }
      },
    });
    connection.open();
    return () => {
      active = false;
      connection.close();
    };
  }, [taskId, refreshDetails, history.loading, history.error]);

  useEffect(() => {
    if (!activeSessionId) return;
    let active = true;
    const revision = liveTodoRevision.current;
    void listSessionTodos(activeSessionId)
      .then((items) => {
        // A live plan that arrived while this request was in flight is newer than the stored one.
        if (active && liveTodoRevision.current === revision)
          setTodoItems(items);
      })
      .catch(() => undefined);
    void readAnalysisHistory(activeSessionId)
      .then((saved) => {
        if (!active) return;
        setSessionTasks(saved.tasks);
        setSessionTaskIds((current) => [
          ...new Set([...current, ...saved.tasks.map((task) => task.taskId)]),
        ]);
        setMessages((current) => [
          ...current,
          ...saved.messages
            .filter(
              (message) =>
                !current.some((item) => item.id === message.messageId),
            )
            .map((message) => ({
              id: message.messageId,
              role: message.role,
              text: message.text,
            })),
        ]);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [activeSessionId, isRunning]);

  useEffect(() => {
    const task = sessionTasks.find((item) => item.taskId === taskId);
    if (!task || taskIsRunning(task.status)) return;
    setIsRunning(false);
    setIsRestoring(false);
    const activity = checkpointActivity(task.taskId, task.status);
    setStatus(activity.label);
    setAnnouncement(activity.label);
    setActivities([activity]);
    setStreamedText("");
  }, [sessionTasks, taskId]);

  const startNewChat = (): void => {
    const detachedBackgroundTask = isRunning && taskId !== null;
    threadGeneration.current += 1;
    history.clear();
    setNavigationOpen(false);
    setDetailsOpen(false);
    setTaskId(null);
    setActiveSessionId(null);
    inputUpload.clear();
    setThreadKey((current) => current + 1);
    window.history.replaceState(null, "", "/");
    setSessionTitle(null);
    setStatus("Ready");
    setMessages([]);
    setActivities([]);
    clearDataSteps();
    setSessionTasks([]);
    setSessionTaskIds([]);
    setTodoItems([]);
    setAnnouncement(
      detachedBackgroundTask
        ? "New chat ready. The previous analysis continues in the background."
        : "New chat ready",
    );
    setStreamedText("");
    setIsSubmitting(false);
    setIsRestoring(false);
    setIsRunning(false);
    setConnectionState("idle");
    taskView.current = initialTaskView();
    setFabricAuthAction(undefined);
  };

  const announceUploadBlocked = (): void => {
    const message = blockedUploadMessage(inputUpload.upload);
    setStatus(message);
    setAnnouncement(message);
  };

  const sendMessage = (text: string): void => {
    if (isRestoring || history.loading || history.error) return;
    if (inputUpload.upload && inputUpload.upload.state !== "clean") {
      announceUploadBlocked();
      return;
    }
    const generation = threadGeneration.current;
    const isCurrentThread = (): boolean =>
      threadGeneration.current === generation;
    // A durable run cannot be answered by starting a new turn; steer the one already going.
    if (isRunning && taskId !== null) {
      setMessages((current) => [
        ...current,
        { id: `user-${String(current.length + 1)}`, role: "user", text },
      ]);
      setAnnouncement("Sent to the running analysis");
      void steerAnalysis(taskId, text, requestKey("steer")).catch(() => {
        if (isCurrentThread())
          setAnnouncement("Steering could not be delivered");
      });
      return;
    }
    const turnTitle = messages.length === 0 ? threadTitle(text) : analysisTitle;
    if (messages.length === 0) setSessionTitle(turnTitle);
    const conversationHistory = messages.map(({ role, text: messageText }) => ({
      role,
      text: messageText,
    }));
    const questionId = requestKey("question");
    setMessages((current) => [
      ...current,
      { id: questionId, role: "user", text },
    ]);
    setActivities([]);
    clearDataSteps();
    setTaskId(null);
    setTodoItems([]);
    setStatus("Working");
    setAnnouncement("Request sent");
    setIsSubmitting(true);
    setIsRunning(true);
    setRunStartedAt(Date.now());
    const uploadIds = inputUpload.inputUploadIds;
    void createAnalysis(
      turnTitle,
      text,
      requestKey("analysis"),
      conversationHistory,
      activeSessionId ?? undefined,
      uploadIds,
    )
      .then((identity) => {
        if (!isCurrentThread()) return;
        setActiveSessionId(identity.sessionId);
        history.refreshList();
        setSessionTaskIds((current) => [
          ...new Set([...current, identity.taskId]),
        ]);
        setMessages((current) =>
          current.map((message) =>
            message.id === questionId
              ? { ...message, id: identity.messageId }
              : message,
          ),
        );
        setTaskId(identity.taskId);
        window.history.replaceState(
          null,
          "",
          `/?session=${encodeURIComponent(identity.sessionId)}&task=${encodeURIComponent(identity.taskId)}`,
        );
      })
      .catch((error: unknown) => {
        if (!isCurrentThread()) return;
        const message =
          error instanceof AnalysisRequestError && error.status === 429
            ? "The analysis queue is full. Try again shortly."
            : "Analysis could not start";
        setStatus(message);
        setAnnouncement(message);
        setIsRunning(false);
      })
      .finally(() => {
        if (isCurrentThread()) setIsSubmitting(false);
      });
  };

  const stop = (): void => {
    if (!taskId) return;
    void cancelAnalysis(taskId, requestKey("cancel"))
      .then(() => {
        setStatus("Cancelling analysis");
        setAnnouncement("Cancellation queued for the next checkpoint");
      })
      .catch(() => {
        setAnnouncement("Cancellation could not be queued");
      });
  };

  const filteredAnalyses = history.sessions.filter((session) =>
    session.title.toLowerCase().includes(deferredQuery.trim().toLowerCase()),
  );
  const openSession = (sessionId: string): void => {
    setNavigationOpen(false);
    threadGeneration.current += 1;
    setTaskId(null);
    setActiveSessionId(null);
    setMessages([]);
    setSessionTasks([]);
    setSessionTaskIds([]);
    setTodoItems([]);
    clearDataSteps();
    setStreamedText("");
    taskView.current = initialTaskView();
    setFabricAuthAction(undefined);
    setConnectionState("idle");
    inputUpload.clear();
    setIsRunning(false);
    setIsRestoring(true);
    setStatus("Restoring analysis");
    setActivities([
      { id: "restore-session", label: "Restoring analysis", status: "running" },
    ]);
    history.open(sessionId);
  };

  const navigation = (
    <>
      <div className="workspace-nav__header">
        <div className="panel-heading">
          <Text className="section-kicker" size={100} weight="semibold">
            Workspace
          </Text>
          <Text as="h2" className="panel-title" size={400} weight="semibold">
            Analyses
          </Text>
        </div>
        <Tooltip content="New chat" relationship="label">
          <Button
            appearance="subtle"
            className="icon-command"
            icon={<ChatAddRegular />}
            aria-label="New chat"
            onClick={startNewChat}
          />
        </Tooltip>
      </div>
      <Input
        aria-label="Search analyses"
        contentBefore={<SearchRegular />}
        value={query}
        onChange={(_, data) => {
          setQuery(data.value);
        }}
      />
      {!history.sessions.some(
        (session) => session.sessionId === activeSessionId,
      ) && (
        <Button
          appearance="subtle"
          aria-current="page"
          className="workspace-nav__item workspace-nav__item--active"
        >
          {analysisTitle}
        </Button>
      )}
      {history.listError && (
        <Button
          appearance="subtle"
          icon={<ArrowClockwiseRegular />}
          onClick={history.refreshList}
        >
          Retry history
        </Button>
      )}
      {filteredAnalyses.map((analysis) => (
        <Button
          appearance="subtle"
          aria-current={
            analysis.sessionId === activeSessionId ? "page" : undefined
          }
          className={`workspace-nav__item${analysis.sessionId === activeSessionId ? " workspace-nav__item--active" : ""}`}
          key={analysis.sessionId}
          onClick={() => {
            openSession(analysis.sessionId);
          }}
        >
          {analysis.title}
        </Button>
      ))}
    </>
  );
  const details = (
    <WorkspaceDetails
      key={activeSessionId ?? "new"}
      artifacts={artifacts}
      queries={sourceQueries}
      tasks={sessionTasks}
      todos={todoItems}
      activities={activities}
      messages={messages}
      liveSteps={dataSteps}
      taskId={taskId}
      fabricSource={fabricSource}
      upload={inputUpload.upload}
      artifactsStatus={artifactsStatus}
      provenanceStatus={provenanceStatus}
      refresh={refreshDetails}
      onProvenanceToggle={setShowProvenance}
    />
  );

  return (
    <section
      className={`workspace-grid${compact ? " workspace-grid--compact" : ""}`}
    >
      {!compact && (
        <nav aria-label="Analyses" className="workspace-nav">
          {navigation}
        </nav>
      )}
      <main className="workspace-main" id="workspace-main" tabIndex={-1}>
        <header className="workspace-main__header">
          {compact && (
            <Tooltip content="Analyses" relationship="label">
              <Button
                appearance="subtle"
                className="icon-command"
                icon={<NavigationRegular />}
                aria-label="Analyses"
                onClick={(event) => {
                  navigationTrigger.current = event.currentTarget;
                  setNavigationOpen(true);
                }}
              />
            </Tooltip>
          )}
          <div className="analysis-heading">
            <Text className="section-kicker" size={100} weight="semibold">
              Private analysis
            </Text>
            <Text
              as="h2"
              className="analysis-title"
              size={500}
              weight="semibold"
            >
              {analysisTitle}
            </Text>
            <div className="analysis-status">
              <span aria-hidden="true" className="analysis-status__signal" />
              <Text size={200}>{status}</Text>
            </div>
            {history.error && (
              <Button
                appearance="subtle"
                icon={<ArrowClockwiseRegular />}
                onClick={history.retry}
              >
                Retry session
              </Button>
            )}
            {connectionState === "connecting" && (
              <Text className="connection-note" size={200}>
                Live updates reconnecting
              </Text>
            )}
            {connectionState === "degraded" && (
              <Text
                className="connection-note connection-note--degraded"
                size={200}
              >
                Live updates delayed; reconnecting
              </Text>
            )}
          </div>
          <Tooltip content="New chat" relationship="label">
            <Button
              appearance="subtle"
              className="icon-command"
              icon={<ChatAddRegular />}
              aria-label="New chat"
              onClick={startNewChat}
            />
          </Tooltip>
          {compact && (
            <Tooltip content="Open workspace" relationship="label">
              <Button
                appearance="subtle"
                className="icon-command"
                icon={<PanelRightRegular />}
                aria-label="Open workspace"
                onClick={(event) => {
                  detailsTrigger.current = event.currentTarget;
                  setDetailsOpen(true);
                }}
              />
            </Tooltip>
          )}
        </header>
        <div
          className="workspace-main__content"
          key={`content-${String(threadKey)}`}
        >
          <Conversation
            messages={messages}
            activities={activities}
            liveSteps={dataSteps}
            provisionalText={streamedText}
            runStartedAt={runStartedAt}
            fabricAuthAction={
              taskId && fabricAuthAction
                ? { taskId, actionPath: fabricAuthAction.actionPath }
                : undefined
            }
          />
          {messages.length === 0 && (
            <section className="workspace-empty">
              <Text className="section-kicker" size={100} weight="semibold">
                Analysis desk / 01
              </Text>
              <Text
                as="h3"
                className="workspace-empty__title"
                weight="semibold"
              >
                New analysis
              </Text>
              <span aria-hidden="true" className="workspace-empty__rule" />
            </section>
          )}
        </div>
        <Composer
          key={`composer-${String(threadKey)}`}
          autoFocus={threadKey > 0}
          disabled={
            isSubmitting || isRestoring || history.loading || history.error
          }
          running={isRunning}
          attachment={inputUpload.upload}
          refreshingAttachment={inputUpload.refreshing}
          onRefreshAttachment={() => {
            void inputUpload.refresh();
          }}
          onClearAttachment={inputUpload.clear}
          onBlockedUploadSend={announceUploadBlocked}
          onAttach={(file) => {
            void inputUpload.attach(file);
          }}
          onSend={sendMessage}
          {...(taskId !== null ? { onStop: stop } : {})}
        />
      </main>
      {!compact && (
        <aside aria-label="Analysis details" className="workspace-details">
          <header className="workspace-details__header">
            <Text as="h2" className="panel-title" size={400} weight="semibold">
              Workspace
            </Text>
          </header>
          {details}
        </aside>
      )}
      {compact && (
        <>
          <Dialog
            open={navigationOpen}
            onOpenChange={(_, data) => {
              setNavigationOpen(data.open);
              if (!data.open)
                queueMicrotask(() => navigationTrigger.current?.focus());
            }}
          >
            <DialogSurface
              aria-label="Analyses drawer"
              className="workspace-overlay workspace-overlay--navigation"
            >
              <Button
                appearance="subtle"
                className="workspace-overlay__close"
                icon={<DismissRegular />}
                aria-label="Close analyses"
                onClick={() => {
                  setNavigationOpen(false);
                  queueMicrotask(() => navigationTrigger.current?.focus());
                }}
              />
              {navigation}
            </DialogSurface>
          </Dialog>
          <Dialog
            open={detailsOpen}
            onOpenChange={(_, data) => {
              setDetailsOpen(data.open);
              if (!data.open)
                queueMicrotask(() => detailsTrigger.current?.focus());
            }}
          >
            <DialogSurface
              aria-label="Workspace details"
              className="workspace-overlay workspace-overlay--details"
            >
              <header className="workspace-overlay__header">
                <Text as="h2" size={500} weight="semibold">
                  Workspace
                </Text>
                <Button
                  appearance="subtle"
                  icon={<DismissRegular />}
                  aria-label="Close workspace"
                  onClick={() => {
                    setDetailsOpen(false);
                    queueMicrotask(() => detailsTrigger.current?.focus());
                  }}
                />
              </header>
              {details}
            </DialogSurface>
          </Dialog>
        </>
      )}
      <div aria-live="polite" role="status" className="visually-hidden">
        {announcement}
      </div>
    </section>
  );
}
