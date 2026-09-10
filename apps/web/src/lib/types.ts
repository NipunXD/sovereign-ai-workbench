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

export interface ResidencySnapshot {
  max_resident_gb: number;
  resident_gb: number;
  allow_swap: boolean;
  models: Array<{
    logical_name: string;
    size_gb: number;
    pinned: boolean;
    idle_s: number;
    mean_load_s: number;
  }>;
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

export interface RetrievalHit {
  chunk_id: string;
  doc_title: string;
  page: number;
  score: number;
  method: string;
  confidence: number;
}

export interface ValidationReport {
  grounded_ratio: number;
  unsupported: string[];
  unresolved_citations: string[];
  schema_errors: string[];
  is_refusal: boolean;
  acceptable: boolean;
  budget_exhausted?: boolean;
  reason?: string;
}

export type TraceItem =
  | { kind: "route"; at: number; data: RouteDecision }
  | { kind: "plan"; at: number; steps: PlanStep[]; rationale: string }
  | { kind: "step"; at: number; stepId: string; intent: string; description: string }
  | { kind: "retrieval"; at: number; query: string; hits: RetrievalHit[]; total: number }
  | { kind: "tool"; at: number; tool: string; args?: Record<string, unknown>; ok?: boolean; error?: string; metrics?: Record<string, unknown> }
  | { kind: "validation"; at: number; data: ValidationReport }
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
