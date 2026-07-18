type ExportPanelProps = {
  taskId: string | null
  runGeneration: number | null
  available: boolean
  busy: boolean
  onDownload: (
    filename: 'reqir.json' | 'requirements.xlsx'
  ) => void
}

function ExportPanel({
  taskId,
  runGeneration,
  available,
  busy,
  onDownload
}: ExportPanelProps) {
  const disabled = !taskId || runGeneration == null || !available || busy

  return (
    <section className="panel compact-panel export-panel" aria-labelledby="export-heading">
      <div className="panel-heading">
        <h2 id="export-heading">Export</h2>
      </div>

      <div className="export-row">
        <button
          type="button"
          className="download-button"
          disabled={disabled}
          onClick={() => onDownload('reqir.json')}
        >
          Download reqir.json
        </button>
        <button
          type="button"
          className="download-button"
          disabled={disabled}
          onClick={() => onDownload('requirements.xlsx')}
        >
          Download requirements.xlsx
        </button>
      </div>
    </section>
  )
}

export default ExportPanel
