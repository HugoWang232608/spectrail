import type { ApiError } from '../api/types'

type ErrorBannerProps = {
  error: ApiError | null
  onDismiss: () => void
}

function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  if (!error) {
    return null
  }

  return (
    <div className="error-banner" role="alert">
      <div>
        <strong>{error.code}</strong>
        <p>{error.message}</p>
      </div>
      <button type="button" className="ghost-button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  )
}

export default ErrorBanner

