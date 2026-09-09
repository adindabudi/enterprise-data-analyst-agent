/* Generated from canonical Pydantic JSON Schema. Do not edit. */

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
export type Mediatype = string;
export type Sessionid = string;
export type Sha256 = string;
export type Sizebytes = number;
export type ArtifactStatus =
  | "created"
  | "generating"
  | "validating"
  | "repairing"
  | "ready"
  | "rejected"
  | "incomplete";
export type Taskid = string;
export type Artifactid1 = string;
export type Sha2561 = string;
export type Version = number;
export type Version1 = number;

export interface ArtifactRecord {
  artifactId: Artifactid;
  kind: ArtifactKind;
  mediaType: Mediatype;
  sessionId: Sessionid;
  sha256: Sha256;
  sizeBytes: Sizebytes;
  status: ArtifactStatus;
  taskId: Taskid;
  validationReportRef?: ArtifactRef | null;
  version: Version1;
}
export interface ArtifactRef {
  artifactId: Artifactid1;
  kind: ArtifactKind;
  sha256: Sha2561;
  version: Version;
}
