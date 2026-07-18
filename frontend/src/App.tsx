import { useEffect, useMemo, useRef, useState } from 'react'

import {
  API_BASE_URL,
  createTask,
  getBlocks,
  getReqIR,
  getTask,
  runTask,
  uploadDocument,
  reviewRequirement
} from './api/client'
import type {
  ApiError,
  DocumentBlock,
  ReqIRPackage,
  ReviewRequest,
  TaskRunResponse,
  TaskStatusResponse
} from './api/types'
import ErrorBanner from './components/ErrorBanner'
import ExportPanel from './components/ExportPanel'
import ReqIRDetail from './components/ReqIRDetail'
import ReqIRTable, { type ReviewStatusFilter } from './components/ReqIRTable'
import ReviewActions from './components/ReviewActions'
import ReviewSummary from './components/ReviewSummary'
import RunPanel from './components/RunPanel'
import SourceViewer from './components/SourceViewer'
import StatusPanel from './components/StatusPanel'
import TaskPanel from './components/TaskPanel'
import UploadPanel from './components/UploadPanel'
import { EVIDENCE_RERUN_CONFIRMATION } from './evidence/evidenceRecovery'

type BusyAction = 'create' | 'load' | 'upload' | 'run' | 'review' | null

function App() {
  const [taskIdInput, setTaskIdInput] = useState('')
  const [task, setTask] = useState<TaskStatusResponse | null>(null)
  const [reqir, setReqir] = useState<ReqIRPackage | null>(null)
  const [blocks, setBlocks] = useState<DocumentBlock[]>([])
  const [blocksEvidenceFingerprint, setBlocksEvidenceFingerprint] = useState<string | null>(null)
  const [blocksError, setBlocksError] = useState<ApiError | null>(null)
  const [selectedRequirementId, setSelectedRequirementId] = useState<string | null>(null)
  const [reviewStatusFilter, setReviewStatusFilter] = useState<ReviewStatusFilter>('all')
  const [requirementSearch, setRequirementSearch] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [busyAction, setBusyAction] = useState<BusyAction>(null)
  const busyActionRef = useRef<BusyAction>(null)

  const busy = busyAction !== null
  const requirements = reqir?.items ?? []
  const filteredRequirements = useMemo(
    () => filterRequirements(requirements, reviewStatusFilter, requirementSearch),
    [requirements, reviewStatusFilter, requirementSearch]
  )
  const selectedRequirement =
    requirements.find((requirement) => requirement.id === selectedRequirementId) ?? null

  useEffect(() => {
    if (requirements.length === 0) {
      setSelectedRequirementId(null)
      return
    }

    if (!selectedRequirementId || !requirements.some((requirement) => requirement.id === selectedRequirementId)) {
      setSelectedRequirementId(filteredRequirements[0]?.id ?? requirements[0].id)
    }
  }, [filteredRequirements, requirements, selectedRequirementId])

  async function handleCreateTask() {
    await perform('create', async () => {
      const created = await createTask()
      const loaded = await getTask(created.task_id)
      setTask(loaded)
      setTaskIdInput(created.task_id)
      setReqir(null)
      setBlocks([])
      setBlocksEvidenceFingerprint(null)
      setBlocksError(null)
      setSelectedRequirementId(null)
    })
  }

  async function handleLoadTask() {
    const nextTaskId = taskIdInput.trim()
    if (!nextTaskId) {
      return
    }

    await perform('load', async () => {
      const loaded = await getTask(nextTaskId)
      setTask(loaded)
      await loadReqIRIfCompleted(loaded)
    })
  }

  async function handleUpload(file: File) {
    if (!task) {
      return
    }

    const filename = file.name.toLowerCase()
    if (!isSupportedDocument(filename)) {
      setError({
        code: 'INVALID_DOCUMENT',
        message: 'only .md, .markdown, .docx, and text-based .pdf files are supported'
      })
      return
    }

    await perform('upload', async () => {
      await uploadDocument(task.task_id, file)
      setTask(await getTask(task.task_id))
      setReqir(null)
      setBlocks([])
      setBlocksEvidenceFingerprint(null)
      setBlocksError(null)
      setSelectedRequirementId(null)
    })
  }

  async function handleRun() {
    if (!task) {
      return
    }

    await perform('run', async () => {
      await runTask(task.task_id)
      const loaded = await getTask(task.task_id)
      setTask(loaded)
      await loadReqIRIfCompleted(loaded)
    })
  }

  async function handleRerunEvidence() {
    if (
      !task
      || busyActionRef.current !== null
      || !window.confirm(EVIDENCE_RERUN_CONFIRMATION)
    ) {
      return
    }

    const taskId = task.task_id
    await perform('run', async () => {
      clearReviewEvidence()
      let runResult: TaskRunResponse | null = null
      let runFailed = false
      let runFailure: unknown
      try {
        runResult = await runTask(taskId)
      } catch (caught) {
        runFailed = true
        runFailure = caught
      }

      let refreshed: TaskStatusResponse | null = null
      try {
        refreshed = await getTask(taskId)
      } catch (caught) {
        if (runResult) {
          refreshed = taskSnapshotFromRun(task, runResult)
        } else {
          setTask(unavailableTaskSnapshot(task))
          if (!runFailed) {
            throw caught
          }
        }
      }

      if (refreshed) {
        setTask(refreshed)
        try {
          await loadReqIRIfCompleted(refreshed)
        } catch (caught) {
          if (!runFailed) {
            throw caught
          }
        }
      }

      if (runFailed) {
        throw runFailure
      }
    })
  }

  async function handleReview(request: ReviewRequest) {
    if (!task) {
      return
    }

    await perform('review', async () => {
      await reviewRequirement(task.task_id, request)
      const packagePayload = await getReqIR(task.task_id)
      setReqir(packagePayload)
      setSelectedRequirementId(request.requirement_id)
    })
  }

  async function handleReloadEvidence() {
    if (!task) {
      return
    }
    await perform('load', async () => {
      const loaded = await getTask(task.task_id)
      setTask(loaded)
      await loadReqIRIfCompleted(loaded)
    })
  }

  function clearReviewEvidence() {
    setReqir(null)
    setBlocks([])
    setBlocksEvidenceFingerprint(null)
    setBlocksError(null)
    setSelectedRequirementId(null)
  }

  async function loadReqIRIfCompleted(loaded: TaskStatusResponse) {
    if (!isReadableStatus(loaded.status)) {
      setReqir(null)
      setBlocks([])
      setBlocksEvidenceFingerprint(null)
      setBlocksError(null)
      setSelectedRequirementId(null)
      return
    }

    const packagePayload = await getReqIR(loaded.task_id)
    const evidenceFingerprint = reqirEvidenceFingerprint(packagePayload)
    let nextBlocks: DocumentBlock[] = []
    let nextBlocksEvidenceFingerprint: string | null = null
    let nextBlocksError: ApiError | null = null
    if (!evidenceFingerprint) {
      nextBlocksError = {
        code: 'EVIDENCE_VERSION_UNAVAILABLE',
        message: 'ReqIR package has no valid Evidence fingerprint'
      }
    } else {
      try {
        const blocksPayload = await getBlocks(
          loaded.task_id,
          evidenceFingerprint
        )
        if (blocksPayload.evidence_fingerprint !== evidenceFingerprint) {
          throw {
            code: 'EVIDENCE_VERSION_CHANGED',
            message: 'blocks do not match the loaded ReqIR Evidence version'
          } satisfies ApiError
        }
        nextBlocks = blocksPayload.items
        nextBlocksEvidenceFingerprint = blocksPayload.evidence_fingerprint
      } catch (caught) {
        nextBlocksError = toApiError(caught)
      }
    }
    setBlocks(nextBlocks)
    setBlocksEvidenceFingerprint(nextBlocksEvidenceFingerprint)
    setBlocksError(nextBlocksError)
    setReqir(packagePayload)
    setSelectedRequirementId(packagePayload.items[0]?.id ?? null)
  }

  async function perform(action: Exclude<BusyAction, null>, taskAction: () => Promise<void>) {
    if (busyActionRef.current !== null) {
      return
    }
    busyActionRef.current = action
    setBusyAction(action)
    setError(null)
    try {
      await taskAction()
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      busyActionRef.current = null
      setBusyAction(null)
    }
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">P5 Evidence Review</p>
          <h1>SpecTrail Review UI</h1>
        </div>
        <span className="api-pill">{API_BASE_URL}</span>
      </header>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <section className="workspace-grid" aria-label="Task workflow">
        <div className="sidebar-stack">
          <TaskPanel
            taskIdInput={taskIdInput}
            currentTask={task}
            busy={busy}
            onTaskIdInputChange={setTaskIdInput}
            onCreateTask={handleCreateTask}
            onLoadTask={handleLoadTask}
          />
          <UploadPanel
            disabled={!task}
            filename={task?.task.original_filename ?? null}
            busy={busy}
            onUpload={handleUpload}
          />
          <RunPanel
            disabled={!task || !task.task.input_document}
            busy={busy}
            running={busyAction === 'run'}
            onRun={handleRun}
          />
          <ExportPanel
            taskId={task?.task_id ?? null}
            available={isReadableStatus(task?.status)}
          />
        </div>

        <div className="main-stack">
          <StatusPanel task={task} reqir={reqir} />
          <ReviewSummary requirements={requirements} />

          <div className="review-grid">
            <ReqIRTable
              requirements={filteredRequirements}
              selectedId={selectedRequirementId}
              statusFilter={reviewStatusFilter}
              searchQuery={requirementSearch}
              onStatusFilterChange={setReviewStatusFilter}
              onSearchQueryChange={setRequirementSearch}
              onSelect={(requirement) => setSelectedRequirementId(requirement.id)}
            />
            <div className="detail-stack">
              <ReviewActions
                requirement={selectedRequirement}
                busy={busy}
                onReview={handleReview}
              />
              <ReqIRDetail requirement={selectedRequirement} />
              <SourceViewer
                taskId={task?.task_id ?? null}
                requirement={selectedRequirement}
                blocks={blocks}
                blocksError={blocksError}
                evidenceFingerprint={reqirEvidenceFingerprint(reqir)}
                blocksEvidenceFingerprint={blocksEvidenceFingerprint}
                reloadingEvidence={busyAction === 'load'}
                onReloadEvidence={() => void handleReloadEvidence()}
                rerunningEvidence={busyAction === 'run'}
                evidenceRecoveryDisabled={busy}
                onRerunEvidence={() => void handleRerunEvidence()}
              />
            </div>
          </div>
        </div>
      </section>
    </main>
  )
}

function isSupportedDocument(filename: string) {
  return ['.md', '.markdown', '.docx', '.pdf'].some((suffix) => filename.endsWith(suffix))
}

function taskSnapshotFromRun(
  previous: TaskStatusResponse,
  run: TaskRunResponse
): TaskStatusResponse {
  return {
    task_id: run.task_id,
    status: run.status,
    task: {
      ...previous.task,
      task_id: run.task_id,
      status: run.status,
      updated_at: run.manifest.completed_at ?? previous.task.updated_at
    },
    manifest: run.manifest
  }
}

function unavailableTaskSnapshot(
  previous: TaskStatusResponse
): TaskStatusResponse {
  const status = 'status_unavailable'
  return {
    ...previous,
    status,
    task: {
      ...previous.task,
      status
    },
    manifest: null
  }
}

function isReadableStatus(status: string | undefined): boolean {
  return status === 'completed' || status === 'completed_with_warnings'
}

function reqirEvidenceFingerprint(reqir: ReqIRPackage | null): string | null {
  const value = reqir?.metadata.evidence_fingerprint
  return (
    typeof value === 'string'
    && /^[0-9a-f]{64}$/.test(value)
  ) ? value : null
}

function filterRequirements(
  requirements: ReqIRPackage['items'],
  statusFilter: ReviewStatusFilter,
  searchQuery: string
) {
  const normalizedQuery = searchQuery.trim().toLowerCase()

  return requirements.filter((requirement) => {
    if (statusFilter !== 'all' && requirement.review_status !== statusFilter) {
      return false
    }

    if (!normalizedQuery) {
      return true
    }

    return (
      (requirement.title ?? '').toLowerCase().includes(normalizedQuery) ||
      requirement.statement.toLowerCase().includes(normalizedQuery)
    )
  })
}

function toApiError(value: unknown): ApiError {
  if (isApiError(value)) {
    return value
  }

  if (value instanceof Error) {
    return { code: 'CLIENT_ERROR', message: value.message }
  }

  return { code: 'CLIENT_ERROR', message: 'Unexpected client error' }
}

function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    'message' in value &&
    typeof value.code === 'string' &&
    typeof value.message === 'string'
  )
}

export default App
