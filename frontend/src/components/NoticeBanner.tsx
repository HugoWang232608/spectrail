import type { ApiError } from '../api/types'

type NoticeBannerProps = {
  notice: ApiError | null
  onDismiss: () => void
}

function NoticeBanner({ notice, onDismiss }: NoticeBannerProps) {
  if (!notice) {
    return null
  }

  return (
    <div className="notice-banner" role="status">
      <div>
        <strong>{notice.code}</strong>
        <p>{notice.message}</p>
      </div>
      <button type="button" className="ghost-button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  )
}

export default NoticeBanner
