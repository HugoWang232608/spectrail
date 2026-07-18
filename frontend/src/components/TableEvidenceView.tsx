import { useEffect, useState } from 'react'

import { getTableEvidence } from '../api/client'
import type {
  ApiError,
  CapabilityValidationResult,
  SourceSpan,
  TableEvidenceCell,
  TableEvidenceResponse,
  TableEvidenceRow
} from '../api/types'

type TableEvidenceViewProps = {
  taskId: string
  source: SourceSpan
  tableCellStatus: CapabilityValidationResult['status'] | undefined
  expectedEvidenceFingerprint: string | null
  reloadingEvidence: boolean
  onReloadEvidence?: () => void
}

function TableEvidenceView({
  taskId,
  source,
  tableCellStatus,
  expectedEvidenceFingerprint,
  reloadingEvidence,
  onReloadEvidence
}: TableEvidenceViewProps) {
  const locator = source.table_locator
  const validated = tableCellStatus === 'PASS'
  const [data, setData] = useState<TableEvidenceResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    setData(null)
    setError(null)
    if (!validated || !locator || !expectedEvidenceFingerprint) {
      setLoading(false)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    getTableEvidence(
      taskId,
      locator.table_id,
      source.block_id,
      expectedEvidenceFingerprint,
      controller.signal
    )
      .then((response) => {
        setData(response)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setError(toApiError(reason))
        setLoading(false)
      })
    return () => controller.abort()
  }, [
    attempt,
    locator?.table_id,
    source.block_id,
    source.source_evidence_key,
    taskId,
    validated,
    expectedEvidenceFingerprint
  ])

  if (validated && locator && !expectedEvidenceFingerprint) {
    return (
      <section className="table-evidence-box">
        <h3>Table evidence</h3>
        <div className="preview-unavailable" role="alert">
          <p className="muted-text">
            ReqIR Evidence version is unavailable. Reload or migrate the ReqIR
            package before reviewing table evidence.
          </p>
          {onReloadEvidence ? (
            <ReloadEvidenceButton
              reloading={reloadingEvidence}
              onReload={onReloadEvidence}
            />
          ) : null}
        </div>
      </section>
    )
  }

  if (!validated || !locator) {
    return (
      <section className="table-evidence-box">
        <h3>Table evidence</h3>
        <div className="preview-unavailable" role="status">
          <p className="muted-text">
            {tableLocatorNotice(tableCellStatus, Boolean(locator))} Grid withheld.
          </p>
        </div>
      </section>
    )
  }

  const responseMatches = data
    ? tableEvidenceMatchesLocator(
      data,
      source,
      taskId,
      expectedEvidenceFingerprint
    )
    : false
  return (
    <section className="table-evidence-box">
      <div className="table-evidence-heading">
        <h3>Table evidence</h3>
        <span>
          {locator.table_id} · physical row {locator.selected_row_index}
        </span>
      </div>
      {loading ? (
        <p className="muted-text" role="status">Loading table evidence…</p>
      ) : error ? (
        <div className="preview-unavailable" role="alert">
          {error.code === 'EVIDENCE_VERSION_CHANGED' ? (
            <>
              <p className="muted-text">
                Evidence version changed. Reload ReqIR before reviewing table evidence.
              </p>
              {onReloadEvidence ? (
                <ReloadEvidenceButton
                  reloading={reloadingEvidence}
                  onReload={onReloadEvidence}
                />
              ) : null}
            </>
          ) : (
            <>
              <p className="muted-text">
                Table evidence unavailable: {error.code} {error.message}
              </p>
              <button type="button" onClick={() => setAttempt((current) => current + 1)}>
                Retry table evidence
              </button>
            </>
          )}
        </div>
      ) : data && !responseMatches ? (
        <div className="preview-unavailable" role="alert">
          <p className="muted-text">
            {data.evidence_fingerprint !== expectedEvidenceFingerprint
              ? 'Evidence version changed. Reload ReqIR before reviewing table evidence.'
              : 'Table evidence response does not match the validated locator. Grid withheld.'}
          </p>
          {data.evidence_fingerprint !== expectedEvidenceFingerprint
          && onReloadEvidence ? (
              <ReloadEvidenceButton
                reloading={reloadingEvidence}
                onReload={onReloadEvidence}
              />
            ) : null}
        </div>
      ) : data ? (
        <>
          <div className="table-evidence-meta">
            <span>
              {data.row_count} rows × {data.column_count} columns
            </span>
            <span>{data.topology_status} topology</span>
            <span>
              primary rows {data.primary_row_start}–{data.primary_row_end}
            </span>
            {data.continuation_role && data.continuation_role !== 'single' ? (
              <span>
                {data.continuation_basis === 'explicit_marker_page_edge_header_match'
                  ? data.continuation_role === 'start'
                    ? `${data.continuation_label} · continuation start`
                    : `${data.continuation_label} · continued from ${data.continuation_of_table_id}`
                  : data.continuation_role === 'start'
                    ? 'possible continuation start'
                    : `possible continuation from ${data.continuation_of_table_id}`}
                {' · sequence '}
                {data.continuation_sequence}
              </span>
            ) : null}
          </div>
          <div className="table-evidence-scroll">
            <table
              className="table-evidence-grid"
              role="grid"
              aria-label={`Table evidence ${data.table_id}`}
            >
              <tbody>
                {data.rows.map((row) => (
                  <tr key={`${row.physical_row_index}-${row.rendered_start}`}>
                    <th scope="row" className="table-evidence-row-heading">
                      <span>Row {row.physical_row_index}</span>
                      {row.repeated_header ? <small>repeated header</small> : null}
                    </th>
                    {renderRowCells(
                      row,
                      data.column_count,
                      new Set(locator.cell_ids),
                      locator.selected_row_index
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.warnings.length > 0 ? (
            <ul className="table-evidence-warnings">
              {data.warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  )
}

function ReloadEvidenceButton({
  reloading,
  onReload
}: {
  reloading: boolean
  onReload: () => void
}) {
  return (
    <button type="button" disabled={reloading} onClick={onReload}>
      {reloading ? 'Reloading…' : 'Reload task evidence'}
    </button>
  )
}

function renderRowCells(
  row: TableEvidenceRow,
  columnCount: number,
  selectedCellIds: Set<string>,
  selectedRowIndex: number
) {
  const rendered = []
  let column = 1
  for (const cell of row.cells) {
    if (cell.column_index > column) {
      rendered.push(
        <td
          className="table-evidence-gap"
          colSpan={cell.column_index - column}
          key={`gap-${row.physical_row_index}-${column}`}
          aria-label={`Unavailable columns ${column} to ${cell.column_index - 1}`}
        />
      )
    }
    const selected = (
      row.physical_row_index === selectedRowIndex
      && selectedCellIds.has(cell.cell_id)
    )
    rendered.push(
      <TableEvidenceCellElement
        cell={cell}
        physicalRowIndex={row.physical_row_index}
        selected={selected}
        key={cell.cell_id}
      />
    )
    column = Math.max(column, cell.column_index + cell.column_span)
  }
  if (column <= columnCount) {
    rendered.push(
      <td
        className="table-evidence-gap"
        colSpan={columnCount - column + 1}
        key={`gap-${row.physical_row_index}-${column}`}
        aria-label={`Unavailable columns ${column} to ${columnCount}`}
      />
    )
  }
  return rendered
}

function TableEvidenceCellElement({
  cell,
  physicalRowIndex,
  selected
}: {
  cell: TableEvidenceCell
  physicalRowIndex: number
  selected: boolean
}) {
  const className = selected
    ? 'table-evidence-cell selected'
    : 'table-evidence-cell'
  const ariaLabel = (
    `Cell ${cell.cell_id}, physical row ${physicalRowIndex}, ` +
    `column ${cell.column_index}`
  )
  const content = (
    <>
      <span className={cell.text ? 'table-cell-text' : 'table-cell-text empty'}>
        {cell.text || 'empty'}
      </span>
      <small>
        logical r{cell.row_index} c{cell.column_index}
        {cell.row_span > 1 ? ` · row span ${cell.row_span}` : ''}
        {cell.column_span > 1 ? ` · column span ${cell.column_span}` : ''}
      </small>
      <small className="table-cell-occurrences">
        {cell.occurrences.map((occurrence) => (
          `${occurrence.occurrence_role} ` +
          `[${occurrence.canonical_start}, ${occurrence.canonical_end})`
        )).join(' · ')}
      </small>
      <code>{cell.cell_id}</code>
    </>
  )
  if (cell.is_header) {
    return (
      <th
        scope="col"
        role="columnheader"
        colSpan={cell.column_span}
        className={className}
        aria-label={ariaLabel}
        aria-selected={selected}
      >
        {content}
      </th>
    )
  }
  return (
    <td
      role="gridcell"
      colSpan={cell.column_span}
      className={className}
      aria-label={ariaLabel}
      aria-selected={selected}
    >
      {content}
    </td>
  )
}

function tableEvidenceMatchesLocator(
  data: TableEvidenceResponse,
  source: SourceSpan,
  taskId: string,
  expectedEvidenceFingerprint: string | null
): boolean {
  const locator = source.table_locator
  if (
    !locator
    || !expectedEvidenceFingerprint
    || data.task_id !== taskId
    || data.evidence_fingerprint !== expectedEvidenceFingerprint
    || data.table_id !== locator.table_id
    || data.block_id !== source.block_id
    || locator.row_indices.length !== locator.cell_ids.length
    || locator.column_indices.length !== locator.cell_ids.length
    || (
      source.source_table_row_index != null
      && source.source_table_row_index !== locator.selected_row_index
    )
    || (
      (source.canonical_source_cell_ids?.length ?? 0) > 0
      && source.canonical_source_cell_ids?.join('\u0000')
        !== locator.cell_ids.join('\u0000')
    )
  ) {
    return false
  }
  const selectedRow = data.rows.find(
    (row) => row.physical_row_index === locator.selected_row_index
  )
  if (!selectedRow) {
    return false
  }
  const cellsInSelectedRow = new Map(
    selectedRow.cells.map((cell) => [cell.cell_id, cell])
  )
  return locator.cell_ids.every((cellId, index) => {
    const cell = cellsInSelectedRow.get(cellId)
    return (
      cell?.row_index === locator.row_indices[index]
      && cell.column_index === locator.column_indices[index]
    )
  })
}

function tableLocatorNotice(
  status: CapabilityValidationResult['status'] | undefined,
  hasLocator: boolean
): string {
  if (status === 'PASS' && !hasLocator) {
    return 'Table locator missing despite a passing capability result.'
  }
  switch (status) {
    case 'WARNING_UNAVAILABLE':
      return 'Table locator unavailable.'
    case 'WARNING_AMBIGUOUS':
      return 'Table locator ambiguous.'
    case 'FAIL_INVALID_REFERENCE':
    case 'FAIL_DERIVATION':
      return `Table locator invalid (${status}).`
    case 'UNVERIFIED':
    default:
      return 'Table locator not verified.'
  }
}

function toApiError(reason: unknown): ApiError {
  if (
    typeof reason === 'object'
    && reason !== null
    && 'code' in reason
    && 'message' in reason
    && typeof reason.code === 'string'
    && typeof reason.message === 'string'
  ) {
    return { code: reason.code, message: reason.message }
  }
  return {
    code: 'TABLE_EVIDENCE_REQUEST_FAILED',
    message: reason instanceof Error ? reason.message : 'Unknown error'
  }
}

export default TableEvidenceView
