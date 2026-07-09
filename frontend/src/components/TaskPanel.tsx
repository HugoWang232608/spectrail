import type { TaskStatusResponse } from '../api/types'

type TaskPanelProps = {
  taskIdInput: string
  currentTask: TaskStatusResponse | null
  busy: boolean
  onTaskIdInputChange: (value: string) => void
  onCreateTask: () => void
  onLoadTask: () => void
}

function TaskPanel({
  taskIdInput,
  currentTask,
  busy,
  onTaskIdInputChange,
  onCreateTask,
  onLoadTask
}: TaskPanelProps) {
  return (
    <section className="panel task-panel" aria-labelledby="task-heading">
      <div className="panel-heading">
        <h2 id="task-heading">Task</h2>
        <span className={`status-badge ${currentTask?.status ?? 'idle'}`}>
          {currentTask?.status ?? 'idle'}
        </span>
      </div>

      <div className="stack">
        <button type="button" className="primary-button" disabled={busy} onClick={onCreateTask}>
          Create Task
        </button>

        <label className="field-label" htmlFor="task-id">
          Task ID
        </label>
        <div className="inline-form">
          <input
            id="task-id"
            value={taskIdInput}
            placeholder="task_..."
            onChange={(event) => onTaskIdInputChange(event.target.value)}
          />
          <button type="button" disabled={busy || !taskIdInput.trim()} onClick={onLoadTask}>
            Load
          </button>
        </div>

        {currentTask ? (
          <dl className="meta-list">
            <div>
              <dt>Current</dt>
              <dd>{currentTask.task_id}</dd>
            </div>
            <div>
              <dt>Document</dt>
              <dd>{currentTask.task.original_filename ?? 'None'}</dd>
            </div>
          </dl>
        ) : null}
      </div>
    </section>
  )
}

export default TaskPanel

