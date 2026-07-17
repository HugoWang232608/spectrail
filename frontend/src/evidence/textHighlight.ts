import type { SourceSpan } from '../api/types'

export type TextHighlightSegments = {
  before: string
  highlighted: string
  after: string
  basis: 'text_locator' | 'legacy_exact_quote'
}

type HighlightSource = Pick<
  SourceSpan,
  'block_id' | 'match_status' | 'quote' | 'text_locator'
>

export function resolveTextHighlight(
  text: string,
  source: HighlightSource
): TextHighlightSegments | null {
  const locator = source.text_locator
  if (locator) {
    if (
      locator.block_id !== source.block_id ||
      locator.offset_encoding !== 'unicode_code_point'
    ) {
      return null
    }
    const points = Array.from(text)
    if (
      !Number.isInteger(locator.start) ||
      !Number.isInteger(locator.end) ||
      locator.start < 0 ||
      locator.end <= locator.start ||
      locator.end > points.length
    ) {
      return null
    }
    return {
      before: points.slice(0, locator.start).join(''),
      highlighted: points.slice(locator.start, locator.end).join(''),
      after: points.slice(locator.end).join(''),
      basis: 'text_locator'
    }
  }

  if (source.match_status !== 'PASS_EXACT' || !source.quote) {
    return null
  }
  const start = text.indexOf(source.quote)
  if (start < 0) {
    return null
  }
  return {
    before: text.slice(0, start),
    highlighted: source.quote,
    after: text.slice(start + source.quote.length),
    basis: 'legacy_exact_quote'
  }
}
