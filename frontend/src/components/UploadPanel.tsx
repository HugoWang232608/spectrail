type UploadPanelProps = {
  disabled: boolean
  filename: string | null
  busy: boolean
  onUpload: (file: File) => void
}

function UploadPanel({ disabled, filename, busy, onUpload }: UploadPanelProps) {
  return (
    <section className="panel" aria-labelledby="upload-heading">
      <div className="panel-heading">
        <h2 id="upload-heading">Upload</h2>
      </div>

      <label className="file-drop">
        <input
          type="file"
          accept=".md,.markdown,text/markdown"
          disabled={disabled || busy}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) {
              onUpload(file)
              event.target.value = ''
            }
          }}
        />
        <span>{filename ?? 'Select Markdown'}</span>
      </label>
    </section>
  )
}

export default UploadPanel

