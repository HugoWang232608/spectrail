type EvidenceRerunRecoveryProps = {
  disabled: boolean
  rerunning: boolean
  onRerun?: () => void
}

function EvidenceRerunRecovery({
  disabled,
  rerunning,
  onRerun
}: EvidenceRerunRecoveryProps) {
  return (
    <>
      <p className="muted-text">
        Evidence was produced by an older parser. Rerun the task to rebuild
        review evidence with the current parser.
      </p>
      <p className="muted-text">
        Warning: rerunning deletes existing review decisions, edits, review
        history, and exports.
      </p>
      {onRerun ? (
        <button
          type="button"
          disabled={disabled || rerunning}
          onClick={onRerun}
        >
          {rerunning ? 'Rerunning task…' : 'Rerun task'}
        </button>
      ) : null}
    </>
  )
}

export default EvidenceRerunRecovery
