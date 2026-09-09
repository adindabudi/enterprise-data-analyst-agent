/* Generated from canonical Pydantic JSON Schema. Do not edit. */

export type ActivityEvent =
  | MessageDeltaEvent
  | AnalysisProgressEvent
  | AttemptSupersededEvent
  | OperationEvent
  | TodoUpdatedEvent
  | TaskCheckpointedEvent
  | SteeringQueuedEvent
  | ProvenanceAddedEvent
  | ArtifactEvent
  | AuthRequiredEvent
  | RunTerminalEvent
  | StreamResumedEvent;
export type Attemptsequence = number;
export type Eventid = string;
export type Occurredat = string;
export type Delta = string;
export type Provenancerefs = string[];
export type Responseattemptid = string;
export type Sequence = number;
export type Sessionid = string;
export type Taskid = string;
export type Type = "message.delta";
export type Eventid1 = string;
export type Occurredat1 = string;
export type Detail = string | null;
export type Milestone = string;
export type State = "running" | "completed" | "failed";
export type Provenancerefs1 = string[];
export type Sequence1 = number;
export type Sessionid1 = string;
export type Taskid1 = string;
export type Type1 = "analysis_progress";
export type Eventid2 = string;
export type Occurredat2 = string;
export type Replacementattemptid = string;
export type Supersededattemptid = string;
export type Provenancerefs2 = string[];
export type Sequence2 = number;
export type Sessionid2 = string;
export type Taskid2 = string;
export type Type2 = "attempt.superseded";
export type Attemptsequence1 = number;
export type Eventid3 = string;
export type Occurredat3 = string;
export type Artifactid = string;
export type ArtifactKind =
  | "input"
  | "xlsx"
  | "xlsm"
  | "html"
  | "svg"
  | "png"
  | "mmd"
  | "pptx"
  | "docx"
  | "pdf"
  | "manifest"
  | "script"
  | "data";
export type Sha256 = string;
export type Version = number;
export type Artifactrefs = ArtifactRef[];
export type Capability = string;
export type Durationms = number | null;
export type Exitcode = number | null;
export type Operationid = string;
export type Summary = string | null;
export type Provenancerefs3 = string[];
export type Responseattemptid1 = string;
export type Sequence3 = number;
export type Sessionid3 = string;
export type Taskid3 = string;
export type Type3 =
  | "tool.started"
  | "tool.progress"
  | "tool.completed"
  | "code.started"
  | "code.stdout"
  | "code.stderr"
  | "code.completed";
export type Eventid4 = string;
export type Occurredat4 = string;
export type Completed = boolean;
export type Text = string;
export type Todoid = string;
export type Payload = TodoItem[];
export type Provenancerefs4 = string[];
export type Sequence4 = number;
export type Sessionid4 = string;
export type Taskid4 = string;
export type Type4 = "todo.updated";
export type Eventid5 = string;
export type Occurredat5 = string;
export type Checkpointsequence = number;
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
export type Provenancerefs5 = string[];
export type Sequence5 = number;
export type Sessionid5 = string;
export type Taskid5 = string;
export type Type5 = "task.checkpointed";
export type Eventid6 = string;
export type Occurredat6 = string;
export type Commandid = string;
export type Inboxsequence = number;
export type Label = "Queued for next checkpoint";
export type Provenancerefs6 = string[];
export type Sequence6 = number;
export type Sessionid6 = string;
export type Taskid6 = string;
export type Type6 = "task.steering_queued";
export type Eventid7 = string;
export type Occurredat7 = string;
export type Referenceid = string;
export type Referencekind =
  "input" | "query" | "execution" | "artifact" | "claim";
export type Provenancerefs7 = string[];
export type Sequence7 = number;
export type Sessionid7 = string;
export type Taskid7 = string;
export type Type7 = "provenance.added";
export type Eventid8 = string;
export type Occurredat8 = string;
export type ArtifactStatus =
  | "created"
  | "generating"
  | "validating"
  | "repairing"
  | "ready"
  | "rejected"
  | "incomplete";
export type Provenancerefs8 = string[];
export type Sequence8 = number;
export type Sessionid8 = string;
export type Taskid8 = string;
export type Type8 =
  "artifact.created" | "artifact.validating" | "artifact.ready";
export type Eventid9 = string;
export type Occurredat9 = string;
export type Actionpath = "/api/fabric/auth/start";
export type Provenancerefs9 = string[];
export type Sequence9 = number;
export type Sessionid9 = string;
export type Taskid9 = string;
export type Type9 = "auth.required";
export type Eventid10 = string;
export type Occurredat10 = string;
export type Finalmessageid = string | null;
export type Provenancerefs10 = string[];
export type Sequence10 = number;
export type Sessionid10 = string;
export type Taskid10 = string;
export type Type10 = "run.completed" | "run.failed" | "run.cancelled";
export type Eventid11 = string;
export type Occurredat11 = string;
export type Lastdurablesequence = number;
export type Omittedfromsequence = number | null;
export type Omittedtosequence = number | null;
export type Provenancerefs11 = string[];
export type Sequence11 = number;
export type Sessionid11 = string;
export type Taskid11 = string;
export type Type11 = "stream.resumed";

export interface MessageDeltaEvent {
  attemptSequence: Attemptsequence;
  eventId: Eventid;
  occurredAt: Occurredat;
  payload: MessageDeltaPayload;
  provenanceRefs?: Provenancerefs;
  responseAttemptId: Responseattemptid;
  sequence: Sequence;
  sessionId: Sessionid;
  taskId: Taskid;
  type: Type;
}
export interface MessageDeltaPayload {
  delta: Delta;
}
export interface AnalysisProgressEvent {
  eventId: Eventid1;
  occurredAt: Occurredat1;
  payload: ProgressPayload;
  provenanceRefs?: Provenancerefs1;
  sequence: Sequence1;
  sessionId: Sessionid1;
  taskId: Taskid1;
  type: Type1;
}
export interface ProgressPayload {
  detail?: Detail;
  milestone: Milestone;
  state?: State;
}
export interface AttemptSupersededEvent {
  eventId: Eventid2;
  occurredAt: Occurredat2;
  payload: AttemptSupersededPayload;
  provenanceRefs?: Provenancerefs2;
  sequence: Sequence2;
  sessionId: Sessionid2;
  taskId: Taskid2;
  type: Type2;
}
export interface AttemptSupersededPayload {
  replacementAttemptId: Replacementattemptid;
  supersededAttemptId: Supersededattemptid;
}
export interface OperationEvent {
  attemptSequence: Attemptsequence1;
  eventId: Eventid3;
  occurredAt: Occurredat3;
  payload: OperationPayload;
  provenanceRefs?: Provenancerefs3;
  responseAttemptId: Responseattemptid1;
  sequence: Sequence3;
  sessionId: Sessionid3;
  taskId: Taskid3;
  type: Type3;
}
export interface OperationPayload {
  artifactRefs?: Artifactrefs;
  capability: Capability;
  durationMs?: Durationms;
  exitCode?: Exitcode;
  operationId: Operationid;
  summary?: Summary;
}
export interface ArtifactRef {
  artifactId: Artifactid;
  kind: ArtifactKind;
  sha256: Sha256;
  version: Version;
}
export interface TodoUpdatedEvent {
  eventId: Eventid4;
  occurredAt: Occurredat4;
  payload: Payload;
  provenanceRefs?: Provenancerefs4;
  sequence: Sequence4;
  sessionId: Sessionid4;
  taskId: Taskid4;
  type: Type4;
}
export interface TodoItem {
  completed: Completed;
  text: Text;
  todoId: Todoid;
}
export interface TaskCheckpointedEvent {
  eventId: Eventid5;
  occurredAt: Occurredat5;
  payload: TaskCheckpointPayload;
  provenanceRefs?: Provenancerefs5;
  sequence: Sequence5;
  sessionId: Sessionid5;
  taskId: Taskid5;
  type: Type5;
}
export interface TaskCheckpointPayload {
  checkpointSequence: Checkpointsequence;
  status: TaskStatus;
}
export interface SteeringQueuedEvent {
  eventId: Eventid6;
  occurredAt: Occurredat6;
  payload: SteeringQueuedPayload;
  provenanceRefs?: Provenancerefs6;
  sequence: Sequence6;
  sessionId: Sessionid6;
  taskId: Taskid6;
  type: Type6;
}
export interface SteeringQueuedPayload {
  commandId: Commandid;
  inboxSequence: Inboxsequence;
  label?: Label;
}
export interface ProvenanceAddedEvent {
  eventId: Eventid7;
  occurredAt: Occurredat7;
  payload: ProvenanceAddedPayload;
  provenanceRefs?: Provenancerefs7;
  sequence: Sequence7;
  sessionId: Sessionid7;
  taskId: Taskid7;
  type: Type7;
}
export interface ProvenanceAddedPayload {
  referenceId: Referenceid;
  referenceKind: Referencekind;
}
export interface ArtifactEvent {
  eventId: Eventid8;
  occurredAt: Occurredat8;
  payload: ArtifactEventPayload;
  provenanceRefs?: Provenancerefs8;
  sequence: Sequence8;
  sessionId: Sessionid8;
  taskId: Taskid8;
  type: Type8;
}
export interface ArtifactEventPayload {
  artifact: ArtifactRef;
  status: ArtifactStatus;
}
export interface AuthRequiredEvent {
  eventId: Eventid9;
  occurredAt: Occurredat9;
  payload: AuthRequiredPayload;
  provenanceRefs?: Provenancerefs9;
  sequence: Sequence9;
  sessionId: Sessionid9;
  taskId: Taskid9;
  type: Type9;
}
export interface AuthRequiredPayload {
  actionPath?: Actionpath;
}
export interface RunTerminalEvent {
  eventId: Eventid10;
  occurredAt: Occurredat10;
  payload: RunTerminalPayload;
  provenanceRefs?: Provenancerefs10;
  sequence: Sequence10;
  sessionId: Sessionid10;
  taskId: Taskid10;
  type: Type10;
}
export interface RunTerminalPayload {
  diagnosticRef?: ArtifactRef | null;
  finalMessageId?: Finalmessageid;
  status: TaskStatus;
}
export interface StreamResumedEvent {
  eventId: Eventid11;
  occurredAt: Occurredat11;
  payload: StreamResumedPayload;
  provenanceRefs?: Provenancerefs11;
  sequence: Sequence11;
  sessionId: Sessionid11;
  taskId: Taskid11;
  type: Type11;
}
export interface StreamResumedPayload {
  lastDurableSequence: Lastdurablesequence;
  omittedFromSequence?: Omittedfromsequence;
  omittedToSequence?: Omittedtosequence;
}
