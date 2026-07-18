import type {
  ApiError,
  BlocksResponse,
  DocumentChunk,
  DocumentUploadResponse,
  ReqIRPackage,
  ReqIRResponse,
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

export async function getReqIR(
  taskId: string,
  expectedRunGeneration: number
): Promise<ReqIRResponse> {
  const { payload, runGeneration } = await versionedRequest<ReqIRPackage>(
    `/tasks/${taskId}/reqir` +
      `?expected_run_generation=${expectedRunGeneration}`,
    expectedRunGeneration
  )
  return {
    run_generation: runGeneration,
    package: payload
  }
}

export async function getBlocks(
  taskId: string,
  expectedEvidenceFingerprint: string,
  expectedRunGeneration: number
): Promise<BlocksResponse> {
  const { payload, runGeneration } = await versionedRequest<BlocksResponse>(
    `/tasks/${encodeURIComponent(taskId)}/blocks` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}` +
      `&expected_run_generation=${expectedRunGeneration}`,
    expectedRunGeneration
  )
  if (payload.run_generation !== runGeneration) {
    throw runGenerationChangedError(
      'blocks response generation does not match its response header'
    )
  }
  return payload
}

export async function getTableEvidence(
  taskId: string,
  tableId: string,
  blockId: string,
  expectedEvidenceFingerprint: string,
  expectedRunGeneration: number,
  signal?: AbortSignal
): Promise<TableEvidenceResponse> {
  const { payload, runGeneration } = await versionedRequest<
    Omit<TableEvidenceResponse, 'run_generation'>
  >(
    `/tasks/${encodeURIComponent(taskId)}` +
      `/tables/${encodeURIComponent(tableId)}` +
      `/blocks/${encodeURIComponent(blockId)}/evidence` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}` +
      `&expected_run_generation=${expectedRunGeneration}`,
    expectedRunGeneration,
    { signal }
  )
  return {
    ...payload,
    run_generation: runGeneration
  }
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
  expectedRunGeneration: number,
  attempt: number,
  signal?: AbortSignal
): Promise<Blob> {
  const response = await fetch(
    `${getPagePreviewUrl(encodeURIComponent(taskId), page)}` +
      `?expected_evidence_fingerprint=${encodeURIComponent(expectedEvidenceFingerprint)}` +
      `&expected_run_generation=${expectedRunGeneration}` +
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
  requireResponseRunGeneration(response, expectedRunGeneration)
  return response.blob()
}

async function versionedRequest<T>(
  path: string,
  expectedRunGeneration: number,
  init: RequestInit = {}
): Promise<{ payload: T; runGeneration: number }> {
  const response = await requestResponse(path, init)
  const runGeneration = requireResponseRunGeneration(
    response,
    expectedRunGeneration
  )
  return {
    payload: await response.json() as T,
    runGeneration
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await requestResponse(path, init)
  return response.json() as Promise<T>
}

async function requestResponse(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
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

  return response
}

function requireResponseRunGeneration(
  response: Response,
  expectedRunGeneration: number
): number {
  const value = response.headers.get('X-Spectrail-Run-Generation')
  const runGeneration = value == null ? Number.NaN : Number(value)
  if (
    !Number.isSafeInteger(runGeneration)
    || runGeneration < 0
    || runGeneration !== expectedRunGeneration
  ) {
    throw runGenerationChangedError(
      'response does not match the expected task run generation'
    )
  }
  return runGeneration
}

function runGenerationChangedError(message: string): ApiError {
  return {
    code: 'RUN_GENERATION_CHANGED',
    message
  }
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
