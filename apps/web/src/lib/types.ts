/** Shapes mirroring the FastAPI schemas. Hand-written for now; `make types-gen`
 *  regenerates these from the live OpenAPI document. */

export type Classification = "public" | "internal" | "confidential" | "restricted";

export interface Principal {
  user_id: string;
  username: string;
  full_name: string;
  roles: string[];
  permissions: string[];
  clearance: Classification;
  departments: string[];
}

export interface LoginResponse {
  access_token: string;
  expires_in: number;
  username: string;
  roles: string[];
  permissions: string[];
  clearance: Classification;
}

export interface DocumentSummary {
  id: string;
  title: string;
  filename: string;
  doc_type: string;
  mime: string;
  page_count: number;
  size_bytes: number;
  classification: Classification;
  departments: string[];
  tags: string[];
  status: string;
  mean_confidence: number;
  degraded: boolean;
  chunk_count: number;
  created_at: string | null;
}

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface DocumentBlock {
  block_id: string;
  type: string;
  text: string;
  bbox: BBox;
  confidence: number;
  source: "native" | "office" | "ocr" | "vlm";
  attrs: Record<string, unknown>;
}

export interface PageBlocks {
  page_no: number;
  width: number;
  height: number;
  mean_confidence: number;
  blocks: DocumentBlock[];
}

export interface Citation {
  n: number;
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  doc_type: string;
  page_no: number;
  bbox: BBox;
  snippet: string;
  section_path: string[];
  score: number;
  retrieval_method: string;
  confidence: number;
}

export interface SearchHit {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  doc_type: string;
  page_from: number;
  page_to: number;
  text: string;
  score: number;
  retrieval_method: string;
  confidence: number;
  section_path: string[];
  bbox: BBox;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  access_filter: string;
  clearance: Classification;
}

export interface ModelInfo {
  logical_name: string;
  provider: string;
  physical_id: string;
  capabilities: string[];
  context_window: number;
  approx_ram_gb: number;
  quality_tier: number;
  speed_tier: number;
  pinned: boolean;
  resident: boolean;
}

export interface ResidentModel {
  logical_name: string;
  physical_id?: string;
  /** Other logical names served by the same loaded weights. */
  also_serves?: string[];
  size_gb: number;
  pinned: boolean;
  idle_s?: number;
  mean_load_s?: number;
}

export interface ResidencySnapshot {
  max_resident_gb: number;
  resident_gb: number;
  allow_swap: boolean;
  /** One entry per physical model, not per logical name. */
  models: ResidentModel[];
}

export interface ModelsResponse {
  profile: string;
  manifest_digest: string;
  providers: Record<string, { healthy: boolean; detail: string; latency_ms: number }>;
  lanes: Record<string, string[]>;
  residency: ResidencySnapshot | null;
  models: ModelInfo[];
}

export interface EgressReport {
  destinations: string[];
  all_private: boolean;
  external_ai_services: string[];
  note: string;
}

export interface ReadinessReport {
  status: "ready" | "degraded";
  components: {
    providers: Record<string, { healthy: boolean; detail?: string; latency_ms?: number }>;
    database: { healthy: boolean; detail?: string; latency_ms?: number };
    vector_store: { healthy: boolean; detail?: string; latency_ms?: number };
    residency?: ResidencySnapshot;
    models_configured?: number;
  };
}

// --- agent trace ------------------------------------------------------------

export type TraceEventName =
  | "run_started"
  | "route_decision"
  | "plan_created"
  | "step_started"
  | "step_finished"
  | "retrieval_result"
  | "tool_call"
  | "tool_result"
  | "token"
  | "reasoning"
  | "answer"
  | "citation"
  | "artifact_created"
  | "validation"
  | "approval_required"
  | "error"
  | "run_finished";

export interface RouteDecision {
  node?: string;
  lane: string;
  model: string;
  physical_model: string;
  provider: string;
  stage: number;
  confidence: number;
  reason: string;
  decide_ms: number;
  resident: boolean;
  swap_cost_s: number;
  alternatives: string[];
  requires_chunking: boolean;
}

export interface PlanStep {
  id: string;
  intent: "retrieve" | "tool" | "synthesize";
  description: string;
  tool: string | null;
  done: boolean;
}

/** A plan step with what the run has since done about it. */
export interface StepProgress extends PlanStep {
  state: "pending" | "active" | "done";
  startedAt?: number;
  finishedAt?: number;
}

export interface RetrievalHit {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  page: number;
  score: number;
  method: string;
  confidence: number;
  bbox: BBox;
  section_path: string[];
  snippet: string;
}

/** A document the run asked for, as it exists once the tool has produced it. */
export interface GeneratedArtifact {
  artifact_id: string;
  filename: string;
  kind: string;
  mime: string;
  sha256: string;
  size_bytes: number;
  download_url: string;
  provenance: ArtifactProvenance;
}

/**
 * The human gate, as seen from the chat.
 *
 * `pending` is the state the person asking actually needs to notice: the run
 * has stopped and is waiting on somebody else. Everything about this shape is
 * in service of making that unmissable rather than a line in the trace.
 */
export interface ApprovalState {
  approval_id: string;
  tool: string;
  status: "pending" | "approved" | "rejected" | "expired";
  requested_at: number;
  expires_at: string | null;
  /** How long the run itself will wait before carrying on without a decision. */
  waiting_s: number;
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
}

/** One measurement from an answer, checked against the passage it cites. */
export interface CheckedFigure {
  /** As written in the answer, e.g. "18.0 barg". */
  text: string;
  value: string;
  unit: string;
  /** Whether this number appears in a cited passage, character for character. */
  found: boolean;
  /** Citation numbers whose passage contains it. */
  sources: number[];
}

export interface ValidationReport {
  grounded_ratio: number;
  unsupported: string[];
  figures: CheckedFigure[];
  unresolved_citations: string[];
  schema_errors: string[];
  is_refusal: boolean;
  acceptable: boolean;
  budget_exhausted?: boolean;
  reason?: string;
}

/** A completed engineering calculation, with its working. */
export interface CalculationDisplay {
  kind: "calculation";
  calculation: string;
  formatted: string;
  value: number;
  unit: string;
  standard_ref: string;
  inputs: Record<string, string>;
  steps: Array<{ description: string; expression: string; result: string }>;
  assumptions: string[];
  caveats: string[];
}

export type TraceItem =
  | { kind: "route"; at: number; data: RouteDecision }
  | { kind: "plan"; at: number; steps: PlanStep[]; rationale: string }
  | { kind: "step"; at: number; stepId: string; intent: string; description: string }
  | { kind: "retrieval"; at: number; query: string; hits: RetrievalHit[]; total: number }
  | {
      kind: "tool";
      at: number;
      tool: string;
      args?: Record<string, unknown>;
      ok?: boolean;
      error?: string;
      /** The tool declined its inputs rather than breaking on them. */
      refused?: boolean;
      metrics?: Record<string, unknown>;
      /** A formatted view of the result, when the tool provides one. */
      display?: CalculationDisplay | Record<string, unknown>;
    }
  | { kind: "validation"; at: number; data: ValidationReport }
  | { kind: "artifact"; at: number; artifact: GeneratedArtifact }
  | { kind: "approval"; at: number; approval: ApprovalState }
  | { kind: "error"; at: number; code: string; message: string; recoverable: boolean };

export interface RunSummary {
  run_id: string;
  status: string;
  wall_ms: number;
  budget: {
    tool_calls_used: number;
    max_tool_calls: number;
    tokens_used: number;
    max_tokens: number;
    elapsed_s: number;
  };
  citations: number;
  evidence_used: number;
}


// --- audit -------------------------------------------------------------------

export interface AuditEvent {
  seq: number;
  ts: string;
  actor_username: string | null;
  actor_roles: string[];
  action: string;
  decision: "allow" | "deny" | "error";
  reason: string | null;
  resource_type: string | null;
  resource_id: string | null;
  run_id: string | null;
  model_used: string | null;
  lane: string | null;
  tool_name: string | null;
  latency_ms: number | null;
  severity: number;
  metadata: Record<string, unknown>;
  hash: string;
  prev_hash: string;
}

export interface AuditPage {
  events: AuditEvent[];
  total: number;
  actions: string[];
  actors: string[];
}

export interface ChainReport {
  valid: boolean;
  events_checked: number;
  first_invalid_seq: number | null;
  detail: string;
}

export interface AuditSummary {
  since_hours: number;
  by_action: Array<{ action: string; count: number }>;
  by_decision: Record<string, number>;
  recent_denials: AuditEvent[];
}

// --- ingestion ---------------------------------------------------------------

export interface IngestProgress {
  stage: string;
  progress: number;
  message: string;
}

export interface IngestComplete {
  document_id: string;
  title: string;
  pages: number;
  chunks: number;
  vectors: number;
  mean_confidence: number;
  degraded: boolean;
  warnings: string[];
  ocr_pages: number;
  vlm_pages: number;
}


// --- approvals and artifacts -------------------------------------------------

export interface Approval {
  id: string;
  run_id: string | null;
  kind: "tool" | "artifact" | "final";
  subject_type: string;
  subject_id: string;
  requested_by: string | null;
  requested_by_name: string | null;
  requested_at: string;
  payload_summary: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "expired";
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
  expires_at: string | null;
  /** True when the viewer raised this request and therefore may not decide it. */
  is_own_request: boolean;
}

export interface ArtifactSource {
  title: string;
  doc_type: string;
  pages: number[];
  lowest_confidence: number;
}

export interface ArtifactProvenance {
  run_id?: string;
  generated_by?: string;
  generated_at?: string;
  models?: Record<string, string>;
  tools_used?: string[];
  approved_by?: string | null;
  approved_at?: string | null;
  sha256?: string;
  sources?: ArtifactSource[];
  has_uncertain_sources?: boolean;
}

export interface Artifact {
  id: string;
  run_id: string | null;
  kind: string;
  filename: string;
  mime: string;
  sha256: string;
  size_bytes: number;
  status: "draft" | "pending_approval" | "approved" | "rejected" | "superseded";
  version: number;
  created_by: string | null;
  created_at: string;
  provenance: ArtifactProvenance;
}


// --- saved conversations ----------------------------------------------------

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  artifact_count: number;
  preview: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  run_id: string | null;
  model_used: string | null;
  latency_ms: number | null;
  created_at: string;
  citations: Citation[];
}

/** One trace event as stored: the same shape the live stream carries. */
export interface StoredEvent {
  name: TraceEventName | "limitation";
  data: Record<string, unknown>;
  at_ms: number;
}

export interface StoredRun {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  wall_ms: number | null;
  plan: Record<string, unknown>;
  validation: Record<string, unknown>;
  trace: StoredEvent[];
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: StoredMessage[];
  runs: StoredRun[];
  artifacts: Array<GeneratedArtifact & { run_id: string | null; status: string }>;
}


// --- evaluation harness -----------------------------------------------------

export interface EvalMetric {
  key: string;
  value: number;
  /** The bar this metric had to clear, e.g. ">= 0.85". Absent when tracked but not gated. */
  threshold: string | null;
  passed: boolean | null;
  lower_is_better: boolean;
}

/** A case that did not pass, with enough context to judge it. */
export interface EvalCase {
  case_id: string;
  /** The question as written in the gold set. */
  prompt: string;
  /** What the case was supposed to do. */
  expectation: string;
  /** The harness's verdict — why this counted as a failure. */
  detail: string;
  /** What the system actually produced. */
  actual: string;
  /** Where the case is defined, so it can be found and changed. */
  source: string;
  metrics: Record<string, number>;
}

export interface EvalSuite {
  suite: string;
  blurb: string;
  passed: boolean;
  ran_at: string;
  duration_s: number;
  cases: number;
  failed_cases: string[];
  metrics: EvalMetric[];
  failures: EvalCase[];
  error: string;
}

export interface EvalsReport {
  suites: EvalSuite[];
  never_run: string[];
}
