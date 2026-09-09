import type { TaskSummary } from "@eda/contracts";
import { Badge, Button, Spinner, Text } from "@fluentui/react-components";
import { ChevronDownRegular, ChevronRightRegular } from "@fluentui/react-icons";
import { useEffect, useRef, useState } from "react";

export type Activity = {
  id: string;
  label: string;
  status: "running" | "completed" | "failed";
  detail?: string;
};

const CHECKPOINT_LABELS: Record<TaskSummary["status"], string> = {
  planning: "Planning analysis",
  acquiring_data: "Reading source data",
  analyzing: "Analyzing data",
  generating: "Generating outputs",
  validating: "Validating outputs",
  publishing: "Publishing outputs",
  blocked_auth: "Waiting for sign-in",
  cancelling: "Cancelling analysis",
  completed: "Analysis completed",
  cancelled: "Analysis cancelled",
  failed: "Analysis failed",
  failed_cancellation: "Cancellation failed",
};

export function checkpointActivity(
  eventId: string,
  status: TaskSummary["status"],
): Activity {
  return {
    id: `checkpoint-${eventId}`,
    label: CHECKPOINT_LABELS[status],
    status:
      status === "completed" || status === "cancelled"
        ? "completed"
        : status === "failed" || status === "failed_cancellation"
          ? "failed"
          : "running",
  };
}

// The stream states its own outcome. Reading it from the label is how activities used to get stuck.
export function activityStatus(state: string | undefined): Activity["status"] {
  return state === "completed" || state === "failed" ? state : "running";
}

// A failure the next milestone overwrites is a failure nobody saw, so failures stay on screen.
export function withLatestActivity(
  current: Activity[],
  label: string,
  state: string | undefined,
  detail: string | undefined,
): Activity[] {
  const failures = current.filter((activity) => activity.status === "failed");
  return [
    ...failures,
    {
      id: `interactive-status-${String(failures.length)}`,
      label,
      status: activityStatus(state),
      ...(detail ? { detail } : {}),
    },
  ];
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${String(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

// Measured from the turn start so durable milestones do not restart the clock.
function useElapsedSeconds(
  startedAt: number | undefined,
  running: boolean,
): number | null {
  const [now, setNow] = useState(() => Date.now());
  const settled = useRef<number | null>(null);

  useEffect(() => {
    if (startedAt === undefined || !running) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [startedAt, running]);

  if (startedAt === undefined) return null;
  const seconds = Math.max(0, Math.round((now - startedAt) / 1000));
  if (running) return seconds;
  settled.current ??= seconds;
  return settled.current;
}

function elapsedLabel(status: Activity["status"], seconds: number): string {
  if (status === "running") return `${formatDuration(seconds)} elapsed`;
  if (status === "completed") return `in ${formatDuration(seconds)}`;
  return `after ${formatDuration(seconds)}`;
}

export function ActivityBlock({
  activity,
  startedAt,
}: {
  activity: Activity;
  startedAt?: number | undefined;
}) {
  const [expanded, setExpanded] = useState(false);
  const elapsed = useElapsedSeconds(startedAt, activity.status === "running");
  const appearance =
    activity.status === "failed"
      ? "danger"
      : activity.status === "completed"
        ? "success"
        : "informative";
  return (
    <article className={`activity-block activity-block--${activity.status}`}>
      <div className="activity-block__summary">
        <Button
          appearance="subtle"
          aria-expanded={expanded}
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => {
            setExpanded((current) => !current);
          }}
        >
          {activity.label}
        </Button>
        <div className="activity-block__state">
          {activity.status === "running" && (
            <Spinner aria-label="Query in progress" size="tiny" />
          )}
          {elapsed !== null && (
            <Text className="activity-block__elapsed" size={100}>
              {elapsedLabel(activity.status, elapsed)}
            </Text>
          )}
          <Badge appearance="tint" color={appearance}>
            {activity.status}
          </Badge>
        </div>
      </div>
      {expanded && activity.detail && (
        <Text className="activity-block__detail" size={200}>
          {activity.detail}
        </Text>
      )}
    </article>
  );
}
