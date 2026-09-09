/* Generated from canonical Pydantic JSON Schema. Do not edit. */

export type Activeattemptid = string | null;
export type Checkpointsequence = number;
export type Sessionid = string;
export type TaskStatus =
  | "planning"
  | "acquiring_data"
  | "analyzing"
  | "generating"
  | "validating"
  | "publishing"
  | "blocked_auth"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "failed"
  | "failed_cancellation";
export type Statusdetail = string | null;
export type Taskid = string;

export interface TaskSummary {
  activeAttemptId?: Activeattemptid;
  checkpointSequence: Checkpointsequence;
  sessionId: Sessionid;
  status: TaskStatus;
  statusDetail?: Statusdetail;
  taskId: Taskid;
}
