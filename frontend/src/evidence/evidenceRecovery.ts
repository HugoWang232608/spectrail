import type { ApiError } from '../api/types'

export const LEGACY_CONTINUATION_REBUILD_CODE =
  'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED'

export function requiresTaskRerun(error: ApiError | null): boolean {
  return error?.code === LEGACY_CONTINUATION_REBUILD_CODE
}
