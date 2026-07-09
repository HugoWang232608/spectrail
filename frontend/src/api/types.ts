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
  items: DocumentBlock[]
}

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
