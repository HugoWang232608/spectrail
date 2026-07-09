import type { RequirementIR, ReviewStatus } from '../api/types'

type ReviewSummaryProps = {
  requirements: RequirementIR[]
}

const REVIEW_STATUSES: ReviewStatus[] = ['pending', 'approved', 'rejected', 'needs_recheck']

function ReviewSummary({ requirements }: ReviewSummaryProps) {
  const counts = REVIEW_STATUSES.reduce(
    (accumulator, status) => {
      accumulator[status] = requirements.filter((item) => item.review_status === status).length
      return accumulator
    },
    {
      total: requirements.length,
      pending: 0,
      approved: 0,
      rejected: 0,
      needs_recheck: 0
    } as Record<ReviewStatus | 'total', number>
  )

  return (
    <section className="review-summary" aria-label="Review summary">
      <SummaryCard label="Total" value={counts.total} />
      <SummaryCard label="Pending" value={counts.pending} />
      <SummaryCard label="Approved" value={counts.approved} />
      <SummaryCard label="Rejected" value={counts.rejected} />
      <SummaryCard label="Needs Recheck" value={counts.needs_recheck} />
    </section>
  )
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="summary-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

export default ReviewSummary
