import type { ApiError, AgentTraceSnapshot, TaskStatusResponse } from '../api/types'

type AgentTracePanelProps = {
  task: TaskStatusResponse | null
  trace: AgentTraceSnapshot | null
  error: ApiError | null
  loading: boolean
}

function AgentTracePanel({
  task,
  trace,
  error,
  loading
}: AgentTracePanelProps) {
  const isAgent = task?.manifest?.orchestration?.mode === 'agent'

  return (
    <section className="panel agent-trace-panel" aria-labelledby="agent-trace-heading">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Read-only orchestration record</p>
          <h2 id="agent-trace-heading">Agent Trace</h2>
        </div>
        <span className={`status-badge ${trace?.final_state.outcome ?? 'idle'}`}>
          {isAgent ? trace?.final_state.outcome ?? 'agent' : 'fixed'}
        </span>
      </div>

      {!task ? (
        <div className="empty-state">Load a task to inspect orchestration.</div>
      ) : !isAgent ? (
        <div className="empty-state">Fixed orchestration has no Agent trace.</div>
      ) : loading ? (
        <div className="empty-state" role="status">Loading Agent trace…</div>
      ) : error ? (
        <div className="warning-summary" role="alert">
          <strong>{error.code}</strong>
          <span>{error.message}</span>
        </div>
      ) : trace ? (
        <>
          <div className="summary-grid trace-summary">
            <TraceMetric label="Steps" value={trace.final_state.steps_used} />
            <TraceMetric label="Planner calls" value={trace.final_state.planner_calls} />
            <TraceMetric label="Tool calls" value={trace.final_state.tool_invocations} />
            <TraceMetric label="Attempts" value={trace.final_state.pipeline_attempts} />
          </div>

          <div className="trace-section">
            <h3>Events</h3>
            <ol className="trace-timeline">
              {trace.events.map((event) => (
                <li key={event.sequence}>
                  <div className="trace-event-heading">
                    <span className="trace-sequence">#{event.sequence}</span>
                    <strong>{event.event_type}</strong>
                    <span>Step {event.step}</span>
                    {event.tool ? <code>{event.tool}</code> : null}
                    <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
                  </div>
                  {Object.keys(event.payload).length ? (
                    <details>
                      <summary>Payload</summary>
                      <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                    </details>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>

          <div className="trace-section">
            <h3>Pipeline attempts</h3>
            {trace.attempts.length ? (
              <div className="trace-attempts">
                {trace.attempts.map((attempt) => (
                  <article key={attempt.attempt}>
                    <div>
                      <strong>Attempt {attempt.attempt}</strong>
                      <span className={`status-badge ${attempt.pipeline_status}`}>
                        {attempt.pipeline_status}
                      </span>
                    </div>
                    <code>{JSON.stringify(attempt.arguments)}</code>
                    {attempt.error_code ? <span>{attempt.error_code}</span> : null}
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-state compact-empty">No extraction attempts.</div>
            )}
          </div>
        </>
      ) : (
        <div className="empty-state">Agent trace is not available.</div>
      )}
    </section>
  )
}

function TraceMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function formatTime(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(parsed)
}

export default AgentTracePanel
