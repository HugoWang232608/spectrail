import { getExportUrl } from '../api/client'

type ExportPanelProps = {
  taskId: string | null
  available: boolean
}

function ExportPanel({ taskId, available }: ExportPanelProps) {
  const disabled = !taskId || !available

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
            <a className="download-button" href={getExportUrl(taskId, 'reqir.json')} download>
              Download reqir.json
            </a>
            <a className="download-button" href={getExportUrl(taskId, 'requirements.xlsx')} download>
              Download requirements.xlsx
            </a>
          </>
        )}
      </div>
    </section>
  )
}

export default ExportPanel
