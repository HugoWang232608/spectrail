import { useEffect, useMemo, useState } from 'react'

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
import type { ApiError, DocumentBlock, ReqIRPackage, ReviewRequest, TaskStatusResponse } from './api/types'
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

type BusyAction = 'create' | 'load' | 'upload' | 'run' | 'review' | null

function App() {
  const [taskIdInput, setTaskIdInput] = useState('')
  const [task, setTask] = useState<TaskStatusResponse | null>(null)
  const [reqir, setReqir] = useState<ReqIRPackage | null>(null)
  const [blocks, setBlocks] = useState<DocumentBlock[]>([])
  const [selectedRequirementId, setSelectedRequirementId] = useState<string | null>(null)
  const [reviewStatusFilter, setReviewStatusFilter] = useState<ReviewStatusFilter>('all')
  const [requirementSearch, setRequirementSearch] = useState('')
  const [error, setError] = useState<ApiError | null>(null)
  const [busyAction, setBusyAction] = useState<BusyAction>(null)

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
    if (!filename.endsWith('.md') && !filename.endsWith('.markdown')) {
      setError({ code: 'INVALID_DOCUMENT', message: 'only .md and .markdown files are supported' })
      return
    }

    await perform('upload', async () => {
      await uploadDocument(task.task_id, file)
      setTask(await getTask(task.task_id))
      setReqir(null)
      setBlocks([])
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

  async function loadReqIRIfCompleted(loaded: TaskStatusResponse) {
    if (loaded.status !== 'completed') {
      setReqir(null)
      setBlocks([])
      setSelectedRequirementId(null)
      return
    }

    const [packagePayload, blocksPayload] = await Promise.all([
      getReqIR(loaded.task_id),
      getBlocks(loaded.task_id)
    ])
    setReqir(packagePayload)
    setBlocks(blocksPayload.items)
    setSelectedRequirementId(packagePayload.items[0]?.id ?? null)
  }

  async function perform(action: Exclude<BusyAction, null>, taskAction: () => Promise<void>) {
    setBusyAction(action)
    setError(null)
    try {
      await taskAction()
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusyAction(null)
    }
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">P1b</p>
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
            busy={busyAction === 'upload'}
            onUpload={handleUpload}
          />
          <RunPanel
            disabled={!task || !task.task.input_document}
            busy={busy}
            running={busyAction === 'run'}
            onRun={handleRun}
          />
          <ExportPanel taskId={task?.task_id ?? null} completed={task?.status === 'completed'} />
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
              <SourceViewer requirement={selectedRequirement} blocks={blocks} />
            </div>
          </div>
        </div>
      </section>
    </main>
  )
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
