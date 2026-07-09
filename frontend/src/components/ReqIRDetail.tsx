import type { RequirementIR } from '../api/types'

type ReqIRDetailProps = {
  requirement: RequirementIR | null
}

function ReqIRDetail({ requirement }: ReqIRDetailProps) {
  if (!requirement) {
    return (
      <section className="panel detail-panel" aria-labelledby="detail-heading">
        <div className="panel-heading">
          <h2 id="detail-heading">Detail</h2>
        </div>
        <div className="empty-state compact">Select a requirement</div>
      </section>
    )
  }

  return (
    <section className="panel detail-panel" aria-labelledby="detail-heading">
      <div className="panel-heading">
        <h2 id="detail-heading">Detail</h2>
        <span className={`status-badge ${requirement.review_status}`}>
          {requirement.review_status}
        </span>
      </div>

      <dl className="detail-list">
        <DetailItem label="ID" value={requirement.id} />
        <DetailItem label="Version" value={requirement.version} />
        <DetailItem label="Title" value={requirement.title ?? 'Untitled'} />
        <DetailItem label="Type" value={requirement.type} />
        <DetailItem label="EARS Pattern" value={requirement.ears_pattern} />
        <DetailItem label="Statement" value={requirement.statement} wide />
        <DetailItem label="Subject" value={requirement.subject ?? 'None'} />
        <DetailItem label="Condition" value={requirement.condition ?? 'None'} />
        <DetailItem label="Response" value={requirement.response ?? 'None'} wide />
        <DetailItem label="Priority" value={requirement.priority} />
        <DetailItem label="Verification" value={requirement.verification_method} />
        <DetailItem label="Confidence" value={requirement.confidence.toFixed(3)} />
        <DetailItem
          label="Grounding"
          value={requirement.grounding_score != null ? requirement.grounding_score.toFixed(3) : 'None'}
        />
        <DetailItem label="Duplicate Group" value={requirement.duplicate_group_id ?? 'None'} />
        <DetailItem label="Possible Duplicates" value={requirement.possible_duplicate_ids.join(', ') || 'None'} />
        <DetailItem label="Derived From" value={requirement.derived_from.join(', ') || 'None'} />
        <DetailItem label="Tags" value={requirement.tags.join(', ') || 'None'} />
      </dl>

      <JsonSection title="Sources" value={requirement.sources} />
      <JsonSection title="Metadata" value={requirement.metadata} />
      <JsonSection title="Review Log" value={requirement.review_log} />
    </section>
  )
}

function DetailItem({
  label,
  value,
  wide = false
}: {
  label: string
  value: string | number
  wide?: boolean
}) {
  return (
    <div className={wide ? 'wide-detail' : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function JsonSection({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="json-section">
      <h3>{title}</h3>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </div>
  )
}

export default ReqIRDetail

