import { useEffect, useState } from 'react'

import { getPagePreviewUrl } from '../api/client'
import type {
  ApiError,
  CapabilityValidationResult,
  DocumentBlock,
  RequirementIR,
  SourceSpan
} from '../api/types'
import { resolveTextHighlight } from '../evidence/textHighlight'

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
  const pageRegionStatus = source ? getPageRegionStatus(source) : undefined
  const pageRegionPassed = pageRegionStatus === 'PASS'
  const displayedPage = pageRegionPassed
    ? source?.page ?? block?.page ?? null
    : block?.page ?? null
  const showClaimedPage = (
    !pageRegionPassed
    && source?.page != null
    && source.page !== block?.page
  )

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
            <SourceItem
              label={pageRegionPassed ? 'Page' : 'Block Page'}
              value={displayedPage != null ? String(displayedPage) : 'None'}
            />
            {showClaimedPage ? (
              <SourceItem
                label="Claimed Page"
                value={`${source.page} (${pageLocatorTrustDescription(pageRegionStatus)})`}
              />
            ) : null}
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
              pageRegionStatus={pageRegionStatus}
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
  pageRegionStatus,
  failed,
  attempt,
  onError,
  onRetry
}: {
  taskId: string
  source: SourceSpan
  pageRegionStatus: CapabilityValidationResult['status'] | undefined
  failed: boolean
  attempt: number
  onError: () => void
  onRetry: () => void
}) {
  const locator = source.page_locator
  if (!locator) {
    return null
  }

  const evidenceVersion = source.source_evidence_key ?? 'legacy'
  const pageLocatorValidated = pageRegionStatus === 'PASS'
  if (!pageLocatorValidated) {
    return (
      <div className="page-evidence-box">
        <div className="page-evidence-heading">
          <h3>Page evidence</h3>
        </div>
        <div className="preview-unavailable" role="status">
          <p className="muted-text">{pageLocatorNotice(pageRegionStatus)} Preview withheld.</p>
        </div>
      </div>
    )
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
            src={
              `${getPagePreviewUrl(taskId, locator.page)}` +
              `?evidence=${encodeURIComponent(evidenceVersion)}` +
              `&attempt=${attempt}`
            }
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

function getPageRegionStatus(
  source: SourceSpan
): CapabilityValidationResult['status'] | undefined {
  return source.capability_results?.find(
    (result) => result.capability === 'page_region'
  )?.status
}

function pageLocatorNotice(
  status: CapabilityValidationResult['status'] | undefined
): string {
  switch (status) {
    case 'WARNING_UNAVAILABLE':
      return 'Page locator unavailable.'
    case 'WARNING_AMBIGUOUS':
      return 'Page locator ambiguous.'
    case 'FAIL_INVALID_REFERENCE':
    case 'FAIL_DERIVATION':
      return `Page locator invalid (${status}).`
    case 'UNVERIFIED':
    default:
      return 'Page locator not verified.'
  }
}

function pageLocatorTrustDescription(
  status: CapabilityValidationResult['status'] | undefined
): string {
  switch (status) {
    case 'WARNING_UNAVAILABLE':
      return 'unavailable'
    case 'WARNING_AMBIGUOUS':
      return 'ambiguous'
    case 'FAIL_INVALID_REFERENCE':
    case 'FAIL_DERIVATION':
      return 'invalid'
    case 'UNVERIFIED':
    default:
      return 'not verified'
  }
}

function renderHighlightedBlock(text: string, source: SourceSpan) {
  const highlight = resolveTextHighlight(text, source)
  if (!highlight) {
    return text
  }

  return (
    <>
      {highlight.before}
      <mark>{highlight.highlighted}</mark>
      {highlight.after}
    </>
  )
}

export default SourceViewer
