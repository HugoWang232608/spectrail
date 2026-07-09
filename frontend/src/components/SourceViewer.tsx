import { useEffect, useState } from 'react'

import type { ApiError, DocumentBlock, RequirementIR, SourceSpan } from '../api/types'

type SourceViewerProps = {
  requirement: RequirementIR | null
  blocks: DocumentBlock[]
  blocksError: ApiError | null
}

function SourceViewer({ requirement, blocks, blocksError }: SourceViewerProps) {
  const [sourceIndex, setSourceIndex] = useState(0)
  const sources = requirement?.sources ?? []
  const source = sources[sourceIndex] ?? null
  const block = source ? blocks.find((item) => item.block_id === source.block_id) ?? null : null
  const page = source?.page ?? block?.page ?? null

  useEffect(() => {
    setSourceIndex(0)
  }, [requirement?.id])

  return (
    <section className="panel source-panel" aria-labelledby="source-heading">
      <div className="panel-heading">
        <h2 id="source-heading">Source</h2>
        {source ? <span className={`status-badge ${source.match_status}`}>{source.match_status}</span> : null}
      </div>

      {source ? (
        <div className="source-content">
          <div className="source-switcher">
            <button
              type="button"
              disabled={sourceIndex === 0}
              onClick={() => setSourceIndex((current) => Math.max(0, current - 1))}
            >
              Previous
            </button>
            <span>
              {sourceIndex + 1} / {sources.length}
            </span>
            <button
              type="button"
              disabled={sourceIndex >= sources.length - 1}
              onClick={() => setSourceIndex((current) => Math.min(sources.length - 1, current + 1))}
            >
              Next
            </button>
          </div>

          <dl className="source-meta">
            <SourceItem label="Block" value={source.block_id} />
            <SourceItem label="Page" value={page != null ? String(page) : 'None'} />
            <SourceItem label="Section" value={source.section_path?.join(' / ') || source.section || 'None'} />
            <SourceItem label="Score" value={source.match_score != null ? source.match_score.toFixed(3) : 'None'} />
          </dl>

          <div className="quote-box">
            <h3>Quote</h3>
            <p>{source.quote}</p>
          </div>

          <div className="block-box">
            <h3>Block Text</h3>
            {blocksError ? (
              <p className="muted-text">
                Block context unavailable: {blocksError.code} {blocksError.message}
              </p>
            ) : block ? (
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
