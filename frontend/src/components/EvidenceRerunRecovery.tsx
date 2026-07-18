type EvidenceRerunRecoveryProps = {
  rerunning: boolean
  onRerun?: () => void
}

function EvidenceRerunRecovery({
  rerunning,
  onRerun
}: EvidenceRerunRecoveryProps) {
  return (
    <>
      <p className="muted-text">
        Evidence was produced by an older parser. Rerun the task to rebuild
        review evidence with the current parser.
      </p>
      {onRerun ? (
        <button type="button" disabled={rerunning} onClick={onRerun}>
          {rerunning ? 'Rerunning task…' : 'Rerun task'}
        </button>
      ) : null}
    </>
  )
}

export default EvidenceRerunRecovery
