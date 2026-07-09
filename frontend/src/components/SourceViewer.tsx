import type { DocumentBlock, RequirementIR, SourceSpan } from '../api/types'

type SourceViewerProps = {
  requirement: RequirementIR | null
  blocks: DocumentBlock[]
}

function SourceViewer({ requirement, blocks }: SourceViewerProps) {
  const source = requirement?.sources[0] ?? null
  const block = source ? blocks.find((item) => item.block_id === source.block_id) ?? null : null

  return (
    <section className="panel source-panel" aria-labelledby="source-heading">
      <div className="panel-heading">
        <h2 id="source-heading">Source</h2>
        {source ? <span className={`status-badge ${source.match_status}`}>{source.match_status}</span> : null}
      </div>

      {source ? (
        <div className="source-content">
          <dl className="source-meta">
            <SourceItem label="Block" value={source.block_id} />
            <SourceItem label="Section" value={source.section_path?.join(' / ') || source.section || 'None'} />
            <SourceItem label="Score" value={source.match_score != null ? source.match_score.toFixed(3) : 'None'} />
          </dl>

          <div className="quote-box">
            <h3>Quote</h3>
            <p>{source.quote}</p>
          </div>

          <div className="block-box">
            <h3>Block Text</h3>
            {block ? (
              <p>{renderHighlightedBlock(block.text, source)}</p>
            ) : (
              <p className="muted-text">Block text not found.</p>
            )}
          </div>
        </div>
      ) : (
        <div className="empty-state compact">No source</div>
      )}
    </section>
  )
}

function SourceItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function renderHighlightedBlock(text: string, source: SourceSpan) {
  if (source.match_status !== 'PASS_EXACT' || !source.quote) {
    return text
  }

  const start = text.indexOf(source.quote)
  if (start < 0) {
    return text
  }

  const end = start + source.quote.length
  return (
    <>
      {text.slice(0, start)}
      <mark>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </>
  )
}

export default SourceViewer

