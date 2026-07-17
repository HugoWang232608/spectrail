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

type SourceSelection = {
  requirementId: string | null
  sourceIdentity: string | null
  sourceOccurrence: number | null
}

function SourceViewer({ taskId, requirement, blocks, blocksError }: SourceViewerProps) {
  const [sourceSelection, setSourceSelection] = useState<SourceSelection>({
    requirementId: null,
    sourceIdentity: null,
    sourceOccurrence: null
  })
  const [previewFailed, setPreviewFailed] = useState(false)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const sources = requirement?.sources ?? []
  const requirementId = requirement?.id ?? null
  const selectedIdentity = sourceSelection.requirementId === requirementId
    ? sourceSelection.sourceIdentity
    : null
  const selectedOccurrence = sourceSelection.requirementId === requirementId
    ? sourceSelection.sourceOccurrence
    : null
  const selectedIdentityIndex = selectedIdentity && selectedOccurrence != null
    ? findSourceSelectionIndex(sources, selectedIdentity, selectedOccurrence)
    : -1
  const effectiveSourceIndex = selectedIdentityIndex >= 0 ? selectedIdentityIndex : 0
  const source = sources[effectiveSourceIndex] ?? null
  const effectiveSelection = sourceSelectionAt(sources, effectiveSourceIndex)
  const effectiveSourceIdentity = effectiveSelection?.sourceIdentity ?? null
  const effectiveSourceOccurrence = effectiveSelection?.sourceOccurrence ?? null
  const block = source ? blocks.find((item) => item.block_id === source.block_id) ?? null : null
  const sourcePreviewIdentity = effectiveSelection
    ? JSON.stringify([
      effectiveSelection.sourceIdentity,
      effectiveSelection.sourceOccurrence
    ])
    : ''
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
    if (
      sourceSelection.requirementId !== requirementId
      || sourceSelection.sourceIdentity !== effectiveSourceIdentity
      || sourceSelection.sourceOccurrence !== effectiveSourceOccurrence
    ) {
      setSourceSelection({
        requirementId,
        sourceIdentity: effectiveSourceIdentity,
        sourceOccurrence: effectiveSourceOccurrence
      })
    }
  }, [
    effectiveSourceIdentity,
    effectiveSourceOccurrence,
    requirementId,
    sourceSelection.requirementId,
    sourceSelection.sourceIdentity,
    sourceSelection.sourceOccurrence
  ])

  useEffect(() => {
    setPreviewFailed(false)
    setPreviewAttempt(0)
  }, [source?.page_locator?.page, sourcePreviewIdentity, taskId])

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
              disabled={effectiveSourceIndex === 0}
              onClick={() => selectSource(
                effectiveSourceIndex - 1,
                requirementId,
                sources,
                setSourceSelection
              )}
            >
              Previous
            </button>
            <span>
              {effectiveSourceIndex + 1} / {sources.length}
            </span>
            <button
              type="button"
              disabled={effectiveSourceIndex >= sources.length - 1}
              onClick={() => selectSource(
                effectiveSourceIndex + 1,
                requirementId,
                sources,
                setSourceSelection
              )}
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

function selectSource(
  index: number,
  requirementId: string | null,
  sources: SourceSpan[],
  setSelection: (selection: SourceSelection) => void
) {
  const selection = sourceSelectionAt(sources, index)
  if (!selection) {
    return
  }
  setSelection({
    requirementId,
    ...selection
  })
}

function sourceSelectionAt(
  sources: SourceSpan[],
  index: number
): Omit<SourceSelection, 'requirementId'> | null {
  const source = sources[index]
  if (!source) {
    return null
  }
  const sourceIdentity = sourceSelectionIdentity(source)
  let sourceOccurrence = 0
  for (let current = 0; current < index; current += 1) {
    if (sourceSelectionIdentity(sources[current]) === sourceIdentity) {
      sourceOccurrence += 1
    }
  }
  return { sourceIdentity, sourceOccurrence }
}

function findSourceSelectionIndex(
  sources: SourceSpan[],
  sourceIdentity: string,
  sourceOccurrence: number
): number {
  let currentOccurrence = 0
  for (let index = 0; index < sources.length; index += 1) {
    if (sourceSelectionIdentity(sources[index]) !== sourceIdentity) {
      continue
    }
    if (currentOccurrence === sourceOccurrence) {
      return index
    }
    currentOccurrence += 1
  }
  return -1
}

function sourceSelectionIdentity(source: SourceSpan): string {
  if (source.source_evidence_key) {
    return source.source_evidence_key
  }
  return JSON.stringify([
    source.block_id,
    source.text_locator?.start ?? null,
    source.text_locator?.end ?? null,
    source.quote
  ])
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
