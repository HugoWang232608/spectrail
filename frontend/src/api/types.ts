export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'needs_recheck'

export type TaskResponse = {
  task_id: string
  status: string
  output_dir: string
}

export type TaskRecord = {
  task_id: string
  goal: string
  model_mode: string
  status: string
  created_at: string
  updated_at: string
  input_document: string | null
  original_filename: string | null
  output_dir: string
  pipeline_config: {
    chunking_mode?: 'auto' | 'force' | 'off'
    max_rendered_prompt_chars?: number
    overlap_blocks?: number
    validation_policy?: 'strict' | 'quarantine'
    fail_fast?: boolean
  }
}

export type TaskManifest = {
  task_id: string
  status: string
  input_document: string
  output_dir: string
  model_mode: string
  started_at: string
  completed_at: string | null
  counts: Record<string, number>
  outputs: Record<string, string>
  error: string | null
  warning_codes: string[]
  zero_result_reason: string | null
}

export type DocumentUploadResponse = {
  task_id: string
  status: string
  filename: string
}

export type TaskStatusResponse = {
  task_id: string
  status: string
  task: TaskRecord
  manifest: TaskManifest | null
}

export type TaskRunResponse = {
  task_id: string
  status: string
  manifest: TaskManifest
}

export type BoundingBox = {
  x0: number
  y0: number
  x1: number
  y1: number
  coordinate_space: 'pdf_preview_rotated_points_top_left_v1'
}

export type TextLocator = {
  block_id: string
  start: number
  end: number
  offset_encoding: 'unicode_code_point'
  match_basis: 'exact' | 'normalized'
}

export type PageLocator = {
  page: number
  bbox: BoundingBox
  page_width: number
  page_height: number
  source_rotation: 0 | 90 | 180 | 270
  coordinate_space: 'pdf_preview_rotated_points_top_left_v1'
  derivation: 'block_bbox' | 'quote_span_union' | 'table_cell_union'
}

export type TableLocator = {
  table_id: string
  cell_ids: string[]
  row_indices: number[]
  selected_row_index: number
  column_indices: number[]
  bbox?: BoundingBox | null
}

export type TableEvidenceOccurrence = {
  occurrence_id: string
  occurrence_role:
    | 'original'
    | 'repeated_header'
    | 'row_span_projection'
    | 'duplicate_text_occurrence'
  canonical_start: number
  canonical_end: number
}

export type TableEvidenceCell = {
  cell_id: string
  row_index: number
  column_index: number
  row_span: number
  column_span: number
  text: string
  is_header: boolean
  page?: number | null
  bbox?: BoundingBox | null
  occurrences: TableEvidenceOccurrence[]
}

export type TableEvidenceRow = {
  physical_row_index: number
  rendered_start: number
  rendered_end: number
  repeated_header: boolean
  cells: TableEvidenceCell[]
}

export type TableEvidenceResponse = {
  schema_version: 'table_evidence_view_v1'
  task_id: string
  evidence_fingerprint: string
  table_id: string
  block_id: string
  row_count: number
  column_count: number
  topology_status: 'complete' | 'sparse'
  page?: number | null
  bbox?: BoundingBox | null
  primary_row_start: number
  primary_row_end: number
  continuation_role?: 'single' | 'start' | 'continuation'
  continuation_group_id?: string | null
  continuation_sequence?: number | null
  continuation_of_table_id?: string | null
  continuation_label?: string | null
  continuation_basis?:
    | 'legacy_header_geometry_heuristic'
    | 'explicit_marker_page_edge_header_match'
    | null
  continued_header_cell_ids?: Record<string, string>
  warnings: string[]
  rows: TableEvidenceRow[]
}

export type CapabilityValidationResult = {
  capability: 'text_range' | 'page_region' | 'table_cell'
  status:
    | 'UNVERIFIED'
    | 'PASS'
    | 'WARNING_UNAVAILABLE'
    | 'WARNING_AMBIGUOUS'
    | 'FAIL_INVALID_REFERENCE'
    | 'FAIL_DERIVATION'
  issue_code?: string | null
  message?: string | null
}

export type SourceSpan = {
  document_id: string
  document_name?: string | null
  page?: number | null
  section?: string | null
  section_path?: string[]
  block_id: string
  quote: string
  match_status: 'UNVERIFIED' | 'PASS_EXACT' | 'PASS_NORMALIZED' | 'WARNING_FUZZY' | 'FAIL_NOT_FOUND'
  match_score?: number | null
  bbox?: number[] | null
  table_cell?: string | null
  image_region?: string | null
  source_cell_ids_raw?: string[]
  canonical_source_cell_ids?: string[]
  source_table_row_index?: number | null
  text_locator?: TextLocator | null
  page_locator?: PageLocator | null
  table_locator?: TableLocator | null
  provisional_text_locator?: TextLocator | null
  locator_status:
    | 'UNVERIFIED'
    | 'PASS_DERIVED'
    | 'PASS_STRUCTURED'
    | 'WARNING_UNAVAILABLE'
    | 'WARNING_AMBIGUOUS'
    | 'FAIL_INVALID_REFERENCE'
    | 'FAIL_DERIVATION'
  capability_results: CapabilityValidationResult[]
  locator_score?: number | null
  source_evidence_key?: string | null
}

export type ReviewRecord = {
  action: 'approve' | 'edit' | 'reject' | 'request_recheck' | 'restore'
  reviewer?: string | null
  before: Record<string, unknown>
  after: Record<string, unknown>
  reason?: string | null
  created_at: string
}

export type RequirementIR = {
  id: string
  version: number
  title?: string | null
  type: string
  ears_pattern: string
  statement: string
  subject?: string | null
  condition?: string | null
  response?: string | null
  priority: string
  verification_method: string
  sources: SourceSpan[]
  confidence: number
  grounding_score?: number | null
  review_status: ReviewStatus
  duplicate_group_id?: string | null
  possible_duplicate_ids: string[]
  derived_from: string[]
  tags: string[]
  review_log: ReviewRecord[]
  metadata: Record<string, unknown>
}

export type ReqIRPackage = {
  metadata: Record<string, unknown>
  items: RequirementIR[]
}

export type DocumentBlock = {
  block_id: string
  document_id: string
  type: 'heading' | 'paragraph' | 'table' | 'list' | 'code' | 'blockquote'
  text: string
  page?: number | null
  section_path: string[]
  order: number
  metadata: Record<string, unknown>
}

export type BlocksResponse = {
  task_id: string
  evidence_fingerprint: string
  items: DocumentBlock[]
}

export type DocumentChunk = {
  chunk_id: string
  index: number
  block_ids: string[]
  overlap_block_ids: string[]
  chunk_fingerprint: string
  rendered_prompt_chars: number
  warnings: string[]
}

export type QuarantinedReqIRPackage = ReqIRPackage

export type ReviewRequest = {
  requirement_id: string
  action: 'approve' | 'reject' | 'edit' | 'restore' | 'request_recheck'
  patch?: Record<string, unknown>
  reviewer?: string | null
  reason?: string | null
}

export type ReviewResponse = {
  task_id: string
  requirement_id: string
  action: string
  review_status: ReviewStatus
}

export type ApiError = {
  code: string
  message: string
}
