import { useEffect, useState } from 'react'

import { getPagePreviewUrl } from '../api/client'
import type { ApiError, DocumentBlock, RequirementIR, SourceSpan } from '../api/types'

type SourceViewerProps = {
  taskId: string | null
  requirement: RequirementIR | null
  blocks: DocumentBlock[]
  blocksError: ApiError | null
}

function SourceViewer({ taskId, requirement, blocks, blocksError }: SourceViewerProps) {
  const [sourceIndex, setSourceIndex] = useState(0)
  const [previewFailed, setPreviewFailed] = useState(false)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const sources = requirement?.sources ?? []
  const source = sources[sourceIndex] ?? null
  const block = source ? blocks.find((item) => item.block_id === source.block_id) ?? null : null
  const page = source?.page ?? block?.page ?? null

  useEffect(() => {
    setSourceIndex(0)
  }, [requirement?.id])

  useEffect(() => {
    setPreviewFailed(false)
    setPreviewAttempt(0)
  }, [source?.page_locator?.page, source?.source_evidence_key, taskId])

  return (
    <section className="panel source-panel" aria-labelledby="source-heading">
      <div className="panel-heading">
        <h2 id="source-heading">Source</h2>
        {source ? (
          <div className="source-badges">
            <span className={`status-badge ${source.match_status}`}>{source.match_status}</span>
            <span className={`status-badge ${source.locator_status ?? 'UNVERIFIED'}`}>
              {source.locator_status ?? 'UNVERIFIED'}
            </span>
          </div>
        ) : null}
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
            <SourceItem
              label="Locator"
              value={source.locator_score != null ? source.locator_score.toFixed(3) : 'None'}
            />
            {source.table_locator ? (
              <SourceItem
                label="Cells"
                value={`${source.table_locator.table_id}: ${source.table_locator.cell_ids.join(', ')}`}
              />
            ) : null}
          </dl>

          <div className="quote-box">
            <h3>Quote</h3>
            <p>{source.quote}</p>
          </div>

          {source.page_locator && taskId ? (
            <PageEvidencePreview
              taskId={taskId}
              source={source}
              failed={previewFailed}
              attempt={previewAttempt}
              onError={() => setPreviewFailed(true)}
              onRetry={() => {
                setPreviewAttempt((current) => current + 1)
                setPreviewFailed(false)
              }}
            />
          ) : null}

          {(source.capability_results ?? []).length > 0 ? (
            <div className="capability-box">
              <h3>Evidence capabilities</h3>
              <ul className="capability-list">
                {(source.capability_results ?? []).map((result) => (
                  <li key={result.capability}>
                    <span>{result.capability}</span>
                    <span className={`capability-status ${result.status}`}>{result.status}</span>
                    {result.message ? <small>{result.message}</small> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

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

function PageEvidencePreview({
  taskId,
  source,
  failed,
  attempt,
  onError,
  onRetry
}: {
  taskId: string
  source: SourceSpan
  failed: boolean
  attempt: number
  onError: () => void
  onRetry: () => void
}) {
  const locator = source.page_locator
  if (!locator) {
    return null
  }

  const { bbox } = locator
  const overlayStyle = {
    left: `${(bbox.x0 / locator.page_width) * 100}%`,
    top: `${(bbox.y0 / locator.page_height) * 100}%`,
    width: `${((bbox.x1 - bbox.x0) / locator.page_width) * 100}%`,
    height: `${((bbox.y1 - bbox.y0) / locator.page_height) * 100}%`
  }

  return (
    <div className="page-evidence-box">
      <div className="page-evidence-heading">
        <h3>Page evidence</h3>
        <span>
          Page {locator.page} · {locator.derivation}
        </span>
      </div>
      {failed ? (
        <div className="preview-unavailable">
          <p className="muted-text">PDF preview unavailable.</p>
          <button type="button" onClick={onRetry}>
            Retry preview
          </button>
        </div>
      ) : (
        <div
          className="page-preview"
          style={{ aspectRatio: `${locator.page_width} / ${locator.page_height}` }}
        >
          <img
            src={`${getPagePreviewUrl(taskId, locator.page)}?attempt=${attempt}`}
            alt={`PDF page ${locator.page}`}
            onError={onError}
          />
          <span
            className="page-locator-overlay"
            style={overlayStyle}
            aria-label="Source quote bounding box"
          />
        </div>
      )}
    </div>
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
