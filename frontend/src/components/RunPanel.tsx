type RunPanelProps = {
  disabled: boolean
  busy: boolean
  running: boolean
  onRun: () => void
}

function RunPanel({ disabled, busy, running, onRun }: RunPanelProps) {
  return (
    <section className="panel compact-panel" aria-labelledby="run-heading">
      <div className="panel-heading">
        <h2 id="run-heading">Run</h2>
      </div>

      <button type="button" className="primary-button" disabled={disabled || busy} onClick={onRun}>
        {running ? 'Running' : 'Run Pipeline'}
      </button>
    </section>
  )
}

export default RunPanel
