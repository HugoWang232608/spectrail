import { useState } from 'react'

import {
  API_BASE_URL,
  createTask,
  getReqIR,
  getTask,
  runTask,
  uploadDocument
} from './api/client'
import type { ApiError, ReqIRPackage, TaskStatusResponse } from './api/types'
import ErrorBanner from './components/ErrorBanner'
import RunPanel from './components/RunPanel'
import StatusPanel from './components/StatusPanel'
import TaskPanel from './components/TaskPanel'
import UploadPanel from './components/UploadPanel'

type BusyAction = 'create' | 'load' | 'upload' | 'run' | null

function App() {
  const [taskIdInput, setTaskIdInput] = useState('')
  const [task, setTask] = useState<TaskStatusResponse | null>(null)
  const [reqir, setReqir] = useState<ReqIRPackage | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busyAction, setBusyAction] = useState<BusyAction>(null)

  const busy = busyAction !== null

  async function handleCreateTask() {
    await perform('create', async () => {
      const created = await createTask()
      const loaded = await getTask(created.task_id)
      setTask(loaded)
      setTaskIdInput(created.task_id)
      setReqir(null)
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
      setReqir(loaded.status === 'completed' ? await getReqIR(nextTaskId) : null)
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
      setReqir(loaded.status === 'completed' ? await getReqIR(task.task_id) : null)
    })
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
            busy={busyAction === 'run'}
            onRun={handleRun}
          />
        </div>

        <StatusPanel task={task} reqir={reqir} />
      </section>
    </main>
  )
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
