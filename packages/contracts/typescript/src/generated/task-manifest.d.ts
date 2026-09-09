/* Generated from canonical Pydantic JSON Schema. Do not edit. */

export type Keytotalsreconciled = boolean;
export type Missingvaluesreviewed = boolean;
export type Schemavalid = boolean;
export type Claimid = string;
export type Executionids = string[];
export type Inputrefs = string[];
export type Outputrefs = string[];
export type Queryids = string[];
export type Claims = ClaimReference[];
export type Createdat = string;
export type Executionid = string;
export type Exitcode = number;
export type Runtimeimagedigest = string;
export type Scriptartifactid = string;
export type Scriptsha256 = string;
export type Executions = ExecutionRecord[];
export type Attempts = number;
export type Executedat = string;
export type Providerschemasha256 = string | null;
export type Purpose = ("schema" | "aggregate" | "control_total") | null;
export type Queryid = string;
export type Querysha256 = string;
export type Reconciliationstatus =
  "not_required" | "pending" | "matched" | "mismatched";
export type Resultartifactid = string;
export type Resultsha256 = string | null;
export type Resultshape = string | null;
export type Rowcount = number;
export type Semanticmodelid = string;
export type Sourcealias = string | null;
export type Fabricqueries = FabricQueryRecord[];
export type Artifactid = string;
export type Sha256 = string;
export type Version = number;
export type Inputs = ManifestArtifact[];
export type Basemodel = string;
export type Contextsnapshotversion = number;
export type Deployment = string;
export type Effort = "medium" | "high" | "xhigh";
export type Hosting = "azure" | "anthropic";
export type Modelprofile = string;
export type Promptsha256 = string;
export type Prompttemplateversion = string;
export type Provider = "foundry";
export type Reasoningmode = "adaptive" | "standard" | "disabled";
export type Requestoptionssha256 = string;
export type Artifactid1 = string;
export type Sha2561 = string;
export type Status = "ready" | "rejected" | "incomplete";
export type Version1 = number;
export type Outputs = OutputArtifact[];
export type Schemaversion = "1.0";
export type Taskid = string;

export interface TaskManifest {
  checks: CheckRecord;
  claims?: Claims;
  createdAt: Createdat;
  executions: Executions;
  fabricQueries: Fabricqueries;
  inputs: Inputs;
  model: ModelRecord;
  outputs: Outputs;
  schemaVersion?: Schemaversion;
  taskId: Taskid;
}
export interface CheckRecord {
  keyTotalsReconciled: Keytotalsreconciled;
  missingValuesReviewed: Missingvaluesreviewed;
  schemaValid: Schemavalid;
}
export interface ClaimReference {
  claimId: Claimid;
  executionIds?: Executionids;
  inputRefs?: Inputrefs;
  outputRefs?: Outputrefs;
  queryIds?: Queryids;
}
export interface ExecutionRecord {
  executionId: Executionid;
  exitCode: Exitcode;
  parameters: Parameters;
  runtimeImageDigest: Runtimeimagedigest;
  scriptArtifactId: Scriptartifactid;
  scriptSha256: Scriptsha256;
}
export interface Parameters {
  [k: string]: string | number | boolean | null;
}
export interface FabricQueryRecord {
  attempts?: Attempts;
  executedAt: Executedat;
  providerSchemaSha256?: Providerschemasha256;
  purpose?: Purpose;
  queryId: Queryid;
  querySha256: Querysha256;
  reconciliationStatus?: Reconciliationstatus;
  resultArtifactId: Resultartifactid;
  resultSha256?: Resultsha256;
  resultShape?: Resultshape;
  rowCount: Rowcount;
  semanticModelId: Semanticmodelid;
  sourceAlias?: Sourcealias;
}
export interface ManifestArtifact {
  artifactId: Artifactid;
  sha256: Sha256;
  version: Version;
}
export interface ModelRecord {
  baseModel: Basemodel;
  contextSnapshotVersion: Contextsnapshotversion;
  deployment: Deployment;
  effort: Effort;
  hosting: Hosting;
  modelProfile: Modelprofile;
  promptSha256: Promptsha256;
  promptTemplateVersion: Prompttemplateversion;
  provider: Provider;
  reasoningMode: Reasoningmode;
  requestOptionsSha256: Requestoptionssha256;
}
export interface OutputArtifact {
  artifactId: Artifactid1;
  sha256: Sha2561;
  status: Status;
  version: Version1;
}
