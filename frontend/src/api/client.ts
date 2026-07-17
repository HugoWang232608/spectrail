import type {
  ApiError,
  BlocksResponse,
  DocumentChunk,
  DocumentUploadResponse,
  ReqIRPackage,
  ReviewRequest,
  ReviewResponse,
  TableEvidenceResponse,
  TaskResponse,
  TaskRunResponse,
  TaskStatusResponse
} from './types'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export async function createTask(): Promise<TaskResponse> {
  return request<TaskResponse>('/tasks', {
    method: 'POST',
    body: JSON.stringify({})
  })
}

export async function uploadDocument(taskId: string, file: File): Promise<DocumentUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  return request<DocumentUploadResponse>(`/tasks/${taskId}/documents`, {
    method: 'POST',
    body: formData
  })
}

export async function runTask(taskId: string): Promise<TaskRunResponse> {
  return request<TaskRunResponse>(`/tasks/${taskId}/run`, { method: 'POST' })
}

export async function getTask(taskId: string): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>(`/tasks/${taskId}`)
}

export async function getReqIR(taskId: string): Promise<ReqIRPackage> {
  return request<ReqIRPackage>(`/tasks/${taskId}/reqir`)
}

export async function getBlocks(
  taskId: string,
  expectedEvidenceFingerprint: string
): Promise<BlocksResponse> {
  return request<BlocksResponse>(
    `/tasks/${encodeURIComponent(taskId)}/blocks` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}`
  )
}

export async function getTableEvidence(
  taskId: string,
  tableId: string,
  blockId: string,
  expectedEvidenceFingerprint: string,
  signal?: AbortSignal
): Promise<TableEvidenceResponse> {
  return request<TableEvidenceResponse>(
    `/tasks/${encodeURIComponent(taskId)}` +
      `/tables/${encodeURIComponent(tableId)}` +
      `/blocks/${encodeURIComponent(blockId)}/evidence` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}`,
    { signal }
  )
}

export async function getChunks(taskId: string): Promise<DocumentChunk[]> {
  return request<DocumentChunk[]>(`/tasks/${taskId}/chunks`)
}

export async function getQuarantined(taskId: string): Promise<ReqIRPackage> {
  return request<ReqIRPackage>(`/tasks/${taskId}/quarantined`)
}

export async function reviewRequirement(
  taskId: string,
  review: ReviewRequest
): Promise<ReviewResponse> {
  return request<ReviewResponse>(`/tasks/${taskId}/review`, {
    method: 'POST',
    body: JSON.stringify(review)
  })
}

export function getExportUrl(taskId: string, filename: 'reqir.json' | 'requirements.xlsx'): string {
  return `${trimTrailingSlash(API_BASE_URL)}/tasks/${taskId}/exports/${filename}`
}

export function getPagePreviewUrl(taskId: string, page: number): string {
  return `${trimTrailingSlash(API_BASE_URL)}/tasks/${taskId}/pages/${page}/preview.png`
}

export async function getPagePreview(
  taskId: string,
  page: number,
  expectedEvidenceFingerprint: string,
  attempt: number,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await fetch(
    `${getPagePreviewUrl(encodeURIComponent(taskId), page)}` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}` +
      `&attempt=${attempt}`,
    { signal }
  )
  if (!response.ok) {
    throw await readApiError(response)
  }
  const actualFingerprint = response.headers.get(
    'X-Spectrail-Evidence-Fingerprint'
  )
  if (actualFingerprint !== expectedEvidenceFingerprint) {
    throw {
      code: 'EVIDENCE_VERSION_CHANGED',
      message: 'PDF preview does not match the loaded ReqIR Evidence version'
    } satisfies ApiError
  }
  return response.blob()
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${trimTrailingSlash(API_BASE_URL)}${path}`, {
    ...init,
    headers
  })

  if (!response.ok) {
    throw await readApiError(response)
  }

  return response.json() as Promise<T>
}

async function readApiError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as { detail?: Partial<ApiError> | string }
    if (payload.detail && typeof payload.detail === 'object') {
      return {
        code: payload.detail.code ?? `HTTP_${response.status}`,
        message: payload.detail.message ?? response.statusText
      }
    }
    if (typeof payload.detail === 'string') {
      return { code: `HTTP_${response.status}`, message: payload.detail }
    }
  } catch {
    return { code: `HTTP_${response.status}`, message: response.statusText }
  }

  return { code: `HTTP_${response.status}`, message: response.statusText }
}

function trimTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value
}
