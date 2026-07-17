import { useEffect, useState } from 'react'

import { getPagePreview } from '../api/client'
import type {
  ApiError,
  CapabilityValidationResult,
  DocumentBlock,
  RequirementIR,
  SourceSpan
} from '../api/types'
import {
  findSourceSelectionIndex,
  sourceSelectionAt
} from '../evidence/sourceSelection'
import type { SourceIdentitySelection } from '../evidence/sourceSelection'
import { resolveTextHighlight } from '../evidence/textHighlight'
import TableEvidenceView from './TableEvidenceView'

type SourceViewerProps = {
  taskId: string | null
  requirement: RequirementIR | null
  blocks: DocumentBlock[]
  blocksError: ApiError | null
  evidenceFingerprint?: string | null
  blocksEvidenceFingerprint?: string | null
  reloadingEvidence?: boolean
  onReloadEvidence?: () => void
}

type SourceSelection = {
  taskId: string | null
  requirementId: string | null
  sourceIdentity: SourceIdentitySelection['sourceIdentity'] | null
  sourceOccurrence: SourceIdentitySelection['sourceOccurrence'] | null
}

function SourceViewer({
  taskId,
  requirement,
  blocks,
  blocksError,
  evidenceFingerprint,
  blocksEvidenceFingerprint,
  reloadingEvidence = false,
  onReloadEvidence
}: SourceViewerProps) {
  const [sourceSelection, setSourceSelection] = useState<SourceSelection>({
    taskId: null,
    requirementId: null,
    sourceIdentity: null,
    sourceOccurrence: null
  })
  const sources = requirement?.sources ?? []
  const requirementId = requirement?.id ?? null
  const selectionContextMatches = (
    sourceSelection.taskId === taskId
    && sourceSelection.requirementId === requirementId
  )
  const selectedIdentity = selectionContextMatches
    ? sourceSelection.sourceIdentity
    : null
  const selectedOccurrence = selectionContextMatches
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
  const versionBindingProvided = (
    evidenceFingerprint !== undefined
    || blocksEvidenceFingerprint !== undefined
  )
  const blocksVersionMatches = (
    !versionBindingProvided
    || (
      evidenceFingerprint != null
      && blocksEvidenceFingerprint === evidenceFingerprint
    )
  )
  const evidenceContextTrusted = blocksVersionMatches && blocksError === null
  const blockContextError = blocksError ?? (
    blocksVersionMatches
      ? null
      : {
        code: evidenceFingerprint
          ? 'EVIDENCE_VERSION_CHANGED'
          : 'EVIDENCE_VERSION_UNAVAILABLE',
        message: evidenceFingerprint
          ? 'blocks do not match the loaded ReqIR Evidence version'
          : 'ReqIR package has no valid Evidence fingerprint'
      }
  )
  const block = source && evidenceContextTrusted
    ? blocks.find((item) => item.block_id === source.block_id) ?? null
    : null
  const sourcePreviewIdentity = effectiveSelection
    ? JSON.stringify([
      effectiveSelection.sourceIdentity,
      effectiveSelection.sourceOccurrence
    ])
    : ''
  const pageRegionStatus = source ? getPageRegionStatus(source) : undefined
  const tableCellStatus = source
    ? getCapabilityStatus(source, 'table_cell')
    : undefined
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
      sourceSelection.taskId !== taskId
      || sourceSelection.requirementId !== requirementId
      || sourceSelection.sourceIdentity !== effectiveSourceIdentity
      || sourceSelection.sourceOccurrence !== effectiveSourceOccurrence
    ) {
      setSourceSelection({
        taskId,
        requirementId,
        sourceIdentity: effectiveSourceIdentity,
        sourceOccurrence: effectiveSourceOccurrence
      })
    }
  }, [
    effectiveSourceIdentity,
    effectiveSourceOccurrence,
    requirementId,
    taskId,
    sourceSelection.taskId,
    sourceSelection.requirementId,
    sourceSelection.sourceIdentity,
    sourceSelection.sourceOccurrence
  ])

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
                taskId,
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
                taskId,
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
              key={`${taskId}:${sourcePreviewIdentity}`}
              taskId={taskId}
              source={source}
              pageRegionStatus={pageRegionStatus}
              expectedEvidenceFingerprint={evidenceFingerprint ?? null}
              reloadingEvidence={reloadingEvidence}
              onReloadEvidence={onReloadEvidence}
            />
          ) : null}

          {taskId
          && evidenceContextTrusted
          && (source.table_locator || tableCellStatus) ? (
            <TableEvidenceView
              key={`${taskId}:${sourcePreviewIdentity}`}
              taskId={taskId}
              source={source}
              tableCellStatus={tableCellStatus}
              expectedEvidenceFingerprint={evidenceFingerprint ?? null}
              reloadingEvidence={reloadingEvidence}
              onReloadEvidence={onReloadEvidence}
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
            {blockContextError ? (
              <div className="block-context-unavailable" role="alert">
                <p className="muted-text">
                  Block context unavailable: {blockContextError.code}{' '}
                  {blockContextError.message}
                </p>
                {onReloadEvidence ? (
                  <button
                    type="button"
                    disabled={reloadingEvidence}
                    onClick={onReloadEvidence}
                  >
                    {reloadingEvidence ? 'Reloading…' : 'Reload task evidence'}
                  </button>
                ) : null}
              </div>
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
  expectedEvidenceFingerprint,
  reloadingEvidence,
  onReloadEvidence
}: {
  taskId: string
  source: SourceSpan
  pageRegionStatus: CapabilityValidationResult['status'] | undefined
  expectedEvidenceFingerprint: string | null
  reloadingEvidence: boolean
  onReloadEvidence?: () => void
}) {
  const locator = source.page_locator
  const [attempt, setAttempt] = useState(0)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(false)
  const pageLocatorValidated = pageRegionStatus === 'PASS'

  useEffect(() => {
    if (
      !locator
      || !pageLocatorValidated
      || !expectedEvidenceFingerprint
    ) {
      setPreviewUrl(null)
      setPreviewError(null)
      setLoading(false)
      return
    }

    const controller = new AbortController()
    let objectUrl: string | null = null
    setPreviewUrl(null)
    setPreviewError(null)
    setLoading(true)
    void getPagePreview(
      taskId,
      locator.page,
      expectedEvidenceFingerprint,
      attempt,
      controller.signal
    ).then((blob) => {
      if (controller.signal.aborted) {
        return
      }
      objectUrl = URL.createObjectURL(blob)
      setPreviewUrl(objectUrl)
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) {
        setPreviewError(pagePreviewError(caught))
      }
    }).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    })

    return () => {
      controller.abort()
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [
    attempt,
    expectedEvidenceFingerprint,
    locator,
    pageLocatorValidated,
    taskId
  ])

  if (!locator) {
    return null
  }

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
  if (!expectedEvidenceFingerprint) {
    return (
      <div className="page-evidence-box">
        <div className="page-evidence-heading">
          <h3>Page evidence</h3>
        </div>
        <div className="preview-unavailable" role="alert">
          <p className="muted-text">
            ReqIR Evidence version is unavailable. Preview withheld.
          </p>
          {onReloadEvidence ? (
            <button
              type="button"
              disabled={reloadingEvidence}
              onClick={onReloadEvidence}
            >
              {reloadingEvidence ? 'Reloading…' : 'Reload task evidence'}
            </button>
          ) : null}
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
      {loading ? (
        <p className="muted-text" role="status">Loading PDF preview…</p>
      ) : previewError ? (
        <div className="preview-unavailable" role="alert">
          <p className="muted-text">
            {previewError.code === 'EVIDENCE_VERSION_CHANGED'
              ? 'Evidence version changed. Reload task evidence before reviewing this page.'
              : `PDF preview unavailable: ${previewError.code} ${previewError.message}`}
          </p>
          {previewError.code === 'EVIDENCE_VERSION_CHANGED'
            ? onReloadEvidence ? (
                <button
                  type="button"
                  disabled={reloadingEvidence}
                  onClick={onReloadEvidence}
                >
                  {reloadingEvidence ? 'Reloading…' : 'Reload task evidence'}
                </button>
              ) : null
            : (
              <button
                type="button"
                onClick={() => setAttempt((current) => current + 1)}
              >
                Retry preview
              </button>
            )}
        </div>
      ) : previewUrl ? (
        <div
          className="page-preview"
          style={{ aspectRatio: `${locator.page_width} / ${locator.page_height}` }}
        >
          <img
            src={previewUrl}
            alt={`PDF page ${locator.page}`}
            onError={() => setPreviewError({
              code: 'PAGE_PREVIEW_UNAVAILABLE',
              message: 'browser could not decode the PDF page preview'
            })}
          />
          <span
            className="page-locator-overlay"
            style={overlayStyle}
            aria-label="Source quote bounding box"
          />
        </div>
      ) : null}
    </div>
  )
}

function pagePreviewError(value: unknown): ApiError {
  if (
    typeof value === 'object'
    && value !== null
    && 'code' in value
    && 'message' in value
    && typeof value.code === 'string'
    && typeof value.message === 'string'
  ) {
    return { code: value.code, message: value.message }
  }
  return {
    code: 'PAGE_PREVIEW_UNAVAILABLE',
    message: value instanceof Error ? value.message : 'Unexpected preview error'
  }
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
  taskId: string | null,
  requirementId: string | null,
  sources: SourceSpan[],
  setSelection: (selection: SourceSelection) => void
) {
  const selection = sourceSelectionAt(sources, index)
  if (!selection) {
    return
  }
  setSelection({
    taskId,
    requirementId,
    ...selection
  })
}

function getPageRegionStatus(
  source: SourceSpan
): CapabilityValidationResult['status'] | undefined {
  return getCapabilityStatus(source, 'page_region')
}

function getCapabilityStatus(
  source: SourceSpan,
  capability: CapabilityValidationResult['capability']
): CapabilityValidationResult['status'] | undefined {
  return source.capability_results?.find(
    (result) => result.capability === capability
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
