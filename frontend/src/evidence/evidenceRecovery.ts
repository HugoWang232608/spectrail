import type { ApiError } from '../api/types'

export const LEGACY_CONTINUATION_REBUILD_CODE =
  'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED'

export const EVIDENCE_RERUN_CONFIRMATION =
  'Rerunning this task deletes existing review decisions, edits, review ' +
  'history, and exports. Continue?'

export const PIPELINE_RERUN_CONFIRMATION =
  'Running the pipeline again deletes existing review decisions, edits, ' +
  'review history, and exports. Continue?'

export function requiresTaskRerun(error: ApiError | null): boolean {
  return error?.code === LEGACY_CONTINUATION_REBUILD_CODE
}
