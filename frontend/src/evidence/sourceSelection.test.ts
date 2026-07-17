import { describe, expect, it } from 'vitest'

import type { SourceSpan } from '../api/types'
import {
  findSourceSelectionIndex,
  sourceSelectionAt,
  sourceSelectionIdentities
} from './sourceSelection'

describe('source selection identity', () => {
  it('matches raw, canonical, and final evidence-key lifecycle aliases', () => {
    const rawSource = makeSource({
      source_cell_ids_raw: ['raw_alias_b'],
      source_table_row_index: 1
    })
    const rawSelection = sourceSelectionAt([rawSource], 0)
    expect(rawSelection).not.toBeNull()

    const canonicalSource = {
      ...rawSource,
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002']
    }
    expect(findSourceSelectionIndex(
      [canonicalSource],
      rawSelection!.sourceIdentity,
      rawSelection!.sourceOccurrence
    )).toBe(0)
    const canonicalSelection = sourceSelectionAt([canonicalSource], 0)
    expect(canonicalSelection?.sourceIdentity).not.toBe(rawSelection?.sourceIdentity)

    const finalSource = {
      ...canonicalSource,
      source_evidence_key: 'src_222222222222222222222222'
    }
    expect(findSourceSelectionIndex(
      [finalSource],
      canonicalSelection!.sourceIdentity,
      canonicalSelection!.sourceOccurrence
    )).toBe(0)
    expect(sourceSelectionAt([finalSource], 0)?.sourceIdentity).toBe(
      finalSource.source_evidence_key
    )
  })

  it('preserves duplicate occurrence ordinals', () => {
    const source = makeSource()
    const sources = [source, { ...source }]
    const second = sourceSelectionAt(sources, 1)

    expect(second?.sourceOccurrence).toBe(1)
    expect(findSourceSelectionIndex(
      sources,
      second!.sourceIdentity,
      second!.sourceOccurrence
    )).toBe(1)
  })

  it('keeps lower-priority aliases after selecting the final key', () => {
    const source = makeSource({
      source_cell_ids_raw: ['raw_alias_b'],
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1,
      source_evidence_key: 'src_222222222222222222222222'
    })
    const identities = sourceSelectionIdentities(source)

    expect(identities[0]).toBe(source.source_evidence_key)
    expect(identities).toHaveLength(4)
  })

  it('does not match a replaced final evidence key through canonical aliases', () => {
    const oldSource = makeSource({
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1,
      source_evidence_key: 'src_111111111111111111111111'
    })
    const oldSelection = sourceSelectionAt([oldSource], 0)
    const newSource = {
      ...oldSource,
      source_evidence_key: 'src_222222222222222222222222'
    }

    expect(findSourceSelectionIndex(
      [newSource],
      oldSelection!.sourceIdentity,
      oldSelection!.sourceOccurrence
    )).toBe(-1)
  })
})

function makeSource(overrides: Partial<SourceSpan> = {}): SourceSpan {
  return {
    document_id: 'doc_001',
    block_id: 'blk_001',
    quote: 'Shared quote',
    match_status: 'PASS_EXACT',
    locator_status: 'PASS_DERIVED',
    capability_results: [],
    ...overrides
  }
}
