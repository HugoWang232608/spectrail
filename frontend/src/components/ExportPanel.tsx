import { getExportUrl } from '../api/client'

type ExportPanelProps = {
  taskId: string | null
  completed: boolean
}

function ExportPanel({ taskId, completed }: ExportPanelProps) {
  const disabled = !taskId || !completed

  return (
    <section className="panel compact-panel export-panel" aria-labelledby="export-heading">
      <div className="panel-heading">
        <h2 id="export-heading">Export</h2>
      </div>

      <div className="export-row">
        {disabled ? (
          <>
            <button type="button" disabled>
              Download reqir.json
            </button>
            <button type="button" disabled>
              Download requirements.xlsx
            </button>
          </>
        ) : (
          <>
            <a className="download-button" href={getExportUrl(taskId, 'reqir.json')}>
              Download reqir.json
            </a>
            <a className="download-button" href={getExportUrl(taskId, 'requirements.xlsx')}>
              Download requirements.xlsx
            </a>
          </>
        )}
      </div>
    </section>
  )
}

export default ExportPanel
