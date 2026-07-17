import { describe, expect, it } from 'vitest'

import type { SourceSpan } from '../api/types'
import { resolveTextHighlight } from './textHighlight'

type HighlightSource = Pick<
  SourceSpan,
  'block_id' | 'match_status' | 'quote' | 'text_locator'
>

function source(
  patch: Partial<HighlightSource> = {}
): HighlightSource {
  return {
    block_id: 'blk_0001',
    match_status: 'PASS_EXACT',
    quote: 'target',
    text_locator: null,
    ...patch
  }
}

describe('resolveTextHighlight', () => {
  it('uses the locator occurrence when the quote is repeated', () => {
    const text = 'target first; target second'
    const secondStart = Array.from('target first; ').length

    const result = resolveTextHighlight(
      text,
      source({
        text_locator: {
          block_id: 'blk_0001',
          start: secondStart,
          end: secondStart + Array.from('target').length,
          offset_encoding: 'unicode_code_point',
          match_basis: 'exact'
        }
      })
    )

    expect(result).toEqual({
      before: 'target first; ',
      highlighted: 'target',
      after: ' second',
      basis: 'text_locator'
    })
  })

  it('counts emoji and CJK Extension B prefixes as Unicode code points', () => {
    const text = '😀𠀀前缀目标后缀'

    const result = resolveTextHighlight(
      text,
      source({
        quote: '目标',
        text_locator: {
          block_id: 'blk_0001',
          start: 4,
          end: 6,
          offset_encoding: 'unicode_code_point',
          match_basis: 'exact'
        }
      })
    )

    expect(result?.before).toBe('😀𠀀前缀')
    expect(result?.highlighted).toBe('目标')
    expect(result?.after).toBe('后缀')
  })

  it('highlights the canonical range for a normalized quote match', () => {
    const text = 'The   system shall respond.'

    const result = resolveTextHighlight(
      text,
      source({
        match_status: 'PASS_NORMALIZED',
        quote: 'The system shall respond.',
        text_locator: {
          block_id: 'blk_0001',
          start: 0,
          end: Array.from(text).length,
          offset_encoding: 'unicode_code_point',
          match_basis: 'normalized'
        }
      })
    )

    expect(result).toEqual({
      before: '',
      highlighted: text,
      after: '',
      basis: 'text_locator'
    })
  })

  it('keeps the exact-quote fallback for legacy sources without a locator', () => {
    const result = resolveTextHighlight(
      'before target after',
      source()
    )

    expect(result?.highlighted).toBe('target')
    expect(result?.basis).toBe('legacy_exact_quote')
  })
})
