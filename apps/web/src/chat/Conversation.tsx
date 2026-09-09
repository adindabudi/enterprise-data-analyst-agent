import { Text } from "@fluentui/react-components";

import { FabricAuthAction } from "../features/task/FabricAuthAction";
import { ActivityBlock, type Activity } from "./ActivityBlock";
import { DataStepsBlock, type DataStep } from "./DataSteps";
import { MarkdownText } from "./MarkdownText";

export type NarrativeMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  steps?: DataStep[];
};

export function Conversation({
  messages,
  activities,
  fabricAuthAction,
  provisionalText = "",
  runStartedAt,
  liveSteps = [],
}: {
  messages: NarrativeMessage[];
  activities: Activity[];
  provisionalText?: string;
  runStartedAt?: number | undefined;
  liveSteps?: DataStep[];
  fabricAuthAction?:
    | {
        taskId: string;
        actionPath: string;
      }
    | undefined;
}) {
  return (
    <section aria-label="Analysis conversation" className="conversation">
      {messages.map((message) => (
        <article
          className={`conversation__message conversation__message--${message.role}`}
          key={message.id}
        >
          <Text className="conversation__role" size={100} weight="semibold">
            {message.role === "assistant" ? "Analyst" : "You"}
          </Text>
          {message.steps && message.steps.length > 0 && (
            <DataStepsBlock running={false} steps={message.steps} />
          )}
          <Text className="conversation__body">
            {message.role === "assistant" ? (
              <MarkdownText text={message.text} />
            ) : (
              message.text
            )}
          </Text>
        </article>
      ))}
      {provisionalText && (
        <article
          className="conversation__message conversation__message--assistant"
          aria-live="polite"
        >
          <Text className="conversation__role" size={100} weight="semibold">
            Analyst
          </Text>
          <Text className="conversation__body">
            <MarkdownText text={provisionalText} />
          </Text>
        </article>
      )}
      {liveSteps.length > 0 && (
        <DataStepsBlock
          running={liveSteps.some((step) => step.state === "running")}
          steps={liveSteps}
        />
      )}
      {activities.map((activity) => (
        <ActivityBlock
          activity={activity}
          key={activity.id}
          startedAt={runStartedAt}
        />
      ))}
      {fabricAuthAction && (
        <FabricAuthAction
          actionPath={fabricAuthAction.actionPath}
          taskId={fabricAuthAction.taskId}
        />
      )}
    </section>
  );
}
