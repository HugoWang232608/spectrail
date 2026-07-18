import { useEffect, useState } from 'react'

import type {
  RequirementIR,
  ReviewActionRequest
} from '../api/types'

type ReviewActionsProps = {
  requirement: RequirementIR | null
  busy: boolean
  onReview: (request: ReviewActionRequest) => void
}

const PRIORITIES = ['high', 'medium', 'low', 'unknown']

function ReviewActions({ requirement, busy, onReview }: ReviewActionsProps) {
  const [statement, setStatement] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [priority, setPriority] = useState('unknown')

  useEffect(() => {
    setStatement(requirement?.statement ?? '')
    setTagsText(requirement?.tags.join(', ') ?? '')
    setPriority(requirement?.priority ?? 'unknown')
  }, [requirement])

  if (!requirement) {
    return (
      <section className="panel actions-panel" aria-labelledby="review-actions-heading">
        <div className="panel-heading">
          <h2 id="review-actions-heading">Review</h2>
        </div>
        <div className="empty-state compact">Select a requirement</div>
      </section>
    )
  }

  const canApprove = requirement.review_status === 'pending' || requirement.review_status === 'needs_recheck'
  const canReject =
    requirement.review_status === 'pending' ||
    requirement.review_status === 'needs_recheck' ||
    requirement.review_status === 'approved'
  const canRestore = requirement.review_status === 'rejected'
  const statementChanged = statement.trim() !== requirement.statement
  const tags = parseTags(tagsText)
  const tagsChanged = tags.join('\n') !== requirement.tags.join('\n')
  const priorityChanged = priority !== requirement.priority

  return (
    <section className="panel actions-panel" aria-labelledby="review-actions-heading">
      <div className="panel-heading">
        <h2 id="review-actions-heading">Review</h2>
        <span className={`status-badge ${requirement.review_status}`}>
          {requirement.review_status}
        </span>
      </div>

      <div className="action-row">
        <button
          type="button"
          disabled={busy || !canApprove}
          onClick={() => onReview(baseRequest(requirement.id, 'approve'))}
        >
          Approve
        </button>
        <button
          type="button"
          disabled={busy || !canReject}
          onClick={() => onReview(baseRequest(requirement.id, 'reject'))}
        >
          Reject
        </button>
        <button
          type="button"
          disabled={busy || !canRestore}
          onClick={() => onReview(baseRequest(requirement.id, 'restore'))}
        >
          Restore
        </button>
      </div>

      <div className="edit-form">
        <label className="field-label" htmlFor="statement-edit">
          Statement
        </label>
        <textarea
          id="statement-edit"
          value={statement}
          rows={5}
          disabled={busy}
          onChange={(event) => setStatement(event.target.value)}
        />
        <button
          type="button"
          className="primary-button"
          disabled={busy || !statementChanged || !statement.trim()}
          onClick={() =>
            onReview({
              ...baseRequest(requirement.id, 'edit'),
              patch: { statement: statement.trim() }
            })
          }
        >
          Save Statement
        </button>
      </div>

      <div className="edit-grid">
        <div>
          <label className="field-label" htmlFor="tags-edit">
            Tags
          </label>
          <input
            id="tags-edit"
            value={tagsText}
            disabled={busy}
            placeholder="security, api"
            onChange={(event) => setTagsText(event.target.value)}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="priority-edit">
            Priority
          </label>
          <select
            id="priority-edit"
            value={priority}
            disabled={busy}
            onChange={(event) => setPriority(event.target.value)}
          >
            {PRIORITIES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="primary-button span-two"
          disabled={busy || (!tagsChanged && !priorityChanged)}
          onClick={() =>
            onReview({
              ...baseRequest(requirement.id, 'edit'),
              patch: {
                tags,
                priority
              }
            })
          }
        >
          Save Tags / Priority
        </button>
      </div>
    </section>
  )
}

function baseRequest(
  requirementId: string,
  action: ReviewActionRequest['action']
): ReviewActionRequest {
  return {
    requirement_id: requirementId,
    action,
    reviewer: 'local'
  }
}

function parseTags(value: string): string[] {
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
}

export default ReviewActions
