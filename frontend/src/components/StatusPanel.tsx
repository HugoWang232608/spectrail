import type { ReqIRPackage, TaskManifest, TaskStatusResponse } from '../api/types'

type StatusPanelProps = {
  task: TaskStatusResponse | null
  reqir: ReqIRPackage | null
}

const COUNT_KEYS = [
  'blocks',
  'chunks',
  'chunks_failed',
  'model_items_rejected',
  'collapsed_overlap_duplicates',
  'validated_requirements',
  'quarantined_requirements',
  'field_conflicts'
]

function StatusPanel({ task, reqir }: StatusPanelProps) {
  const manifest = task?.manifest

  return (
    <section className="panel wide status-panel" aria-labelledby="status-heading">
      <div className="panel-heading">
        <h2 id="status-heading">Status</h2>
        <span className={`status-badge ${task?.status ?? 'idle'}`}>{task?.status ?? 'idle'}</span>
      </div>

      {manifest ? (
        <>
          {manifest.warning_codes?.length ? (
            <div className="warning-summary" role="status">
              <strong>Completed with warnings</strong>
              <span>{manifest.warning_codes.join(', ')}</span>
            </div>
          ) : null}
          {manifest.zero_result_reason ? (
            <div className="warning-summary" role="status">
              <strong>No validated results</strong>
              <span>{manifest.zero_result_reason}</span>
            </div>
          ) : null}
          <div className="summary-grid">
            {COUNT_KEYS.map((key) => (
              <div className="metric" key={key}>
                <span>{labelFor(key)}</span>
                <strong>{manifest.counts[key] ?? 0}</strong>
              </div>
            ))}
            <div className="metric">
              <span>ReqIR</span>
              <strong>{reqir?.items.length ?? 0}</strong>
            </div>
          </div>

          <ManifestDetails manifest={manifest} />
        </>
      ) : (
        <div className="empty-state">No run manifest</div>
      )}
    </section>
  )
}

function ManifestDetails({ manifest }: { manifest: TaskManifest }) {
  return (
    <dl className="manifest-list">
      <div>
        <dt>Started</dt>
        <dd>{formatDate(manifest.started_at)}</dd>
      </div>
      <div>
        <dt>Completed</dt>
        <dd>{manifest.completed_at ? formatDate(manifest.completed_at) : 'None'}</dd>
      </div>
      <div>
        <dt>Outputs</dt>
        <dd>{Object.keys(manifest.outputs).length}</dd>
      </div>
      {manifest.error ? (
        <div>
          <dt>Error</dt>
          <dd>{manifest.error}</dd>
        </div>
      ) : null}
    </dl>
  )
}

function labelFor(key: string): string {
  return key
    .split('_')
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(' ')
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium'
  }).format(new Date(value))
}

export default StatusPanel
