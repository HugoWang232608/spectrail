import type { ApiError } from '../api/types'

type ErrorBannerProps = {
  error: ApiError | null
  onDismiss: () => void
  actionLabel?: string
  actionDisabled?: boolean
  onAction?: () => void
}

function ErrorBanner({
  error,
  onDismiss,
  actionLabel,
  actionDisabled = false,
  onAction
}: ErrorBannerProps) {
  if (!error) {
    return null
  }

  return (
    <div className="error-banner" role="alert">
      <div>
        <strong>{error.code}</strong>
        <p>{error.message}</p>
      </div>
      <div className="banner-actions">
        {actionLabel && onAction ? (
          <button
            type="button"
            disabled={actionDisabled}
            onClick={onAction}
          >
            {actionLabel}
          </button>
        ) : null}
        <button type="button" className="ghost-button" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  )
}

export default ErrorBanner
