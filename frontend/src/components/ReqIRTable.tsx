import type { RequirementIR, ReviewStatus } from '../api/types'

export type ReviewStatusFilter = ReviewStatus | 'all'

type ReqIRTableProps = {
  requirements: RequirementIR[]
  selectedId: string | null
  statusFilter: ReviewStatusFilter
  searchQuery: string
  onStatusFilterChange: (value: ReviewStatusFilter) => void
  onSearchQueryChange: (value: string) => void
  onSelect: (requirement: RequirementIR) => void
}

const STATUS_OPTIONS: ReviewStatusFilter[] = [
  'all',
  'pending',
  'approved',
  'rejected',
  'needs_recheck'
]

function ReqIRTable({
  requirements,
  selectedId,
  statusFilter,
  searchQuery,
  onStatusFilterChange,
  onSearchQueryChange,
  onSelect
}: ReqIRTableProps) {
  return (
    <section className="panel requirements-panel" aria-labelledby="requirements-heading">
      <div className="panel-heading requirements-heading">
        <h2 id="requirements-heading">ReqIR</h2>
        <span className="count-pill">{requirements.length} items</span>
      </div>

      <div className="table-toolbar">
        <label className="field-label" htmlFor="reqir-search">
          Search
        </label>
        <input
          id="reqir-search"
          value={searchQuery}
          placeholder="Title or statement"
          onChange={(event) => onSearchQueryChange(event.target.value)}
        />

        <label className="field-label" htmlFor="review-status-filter">
          Status
        </label>
        <select
          id="review-status-filter"
          value={statusFilter}
          onChange={(event) => onStatusFilterChange(event.target.value as ReviewStatusFilter)}
        >
          {STATUS_OPTIONS.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </div>

      <div className="table-scroll">
        <table className="reqir-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Title</th>
              <th>Type</th>
              <th>EARS Pattern</th>
              <th>Statement</th>
              <th>Confidence</th>
              <th>Review Status</th>
              <th>Source Match Status</th>
            </tr>
          </thead>
          <tbody>
            {requirements.map((requirement) => (
              <tr
                key={requirement.id}
                className={requirement.id === selectedId ? 'selected-row' : undefined}
                onClick={() => onSelect(requirement)}
              >
                <td>{requirement.id}</td>
                <td>{requirement.title ?? 'Untitled'}</td>
                <td>{requirement.type}</td>
                <td>{requirement.ears_pattern}</td>
                <td className="statement-cell">{requirement.statement}</td>
                <td>{formatConfidence(requirement.confidence)}</td>
                <td>
                  <span className={`status-badge ${requirement.review_status}`}>
                    {requirement.review_status}
                  </span>
                </td>
                <td>{sourceMatchStatus(requirement)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {requirements.length === 0 ? <div className="empty-state compact">No requirements</div> : null}
    </section>
  )
}

function sourceMatchStatus(requirement: RequirementIR): string {
  if (requirement.sources.length === 0) {
    return 'UNVERIFIED'
  }

  return requirement.sources.map((source) => source.match_status).join(', ')
}

function formatConfidence(value: number): string {
  return value.toFixed(2)
}

export default ReqIRTable

