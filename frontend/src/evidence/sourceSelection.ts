import type { SourceSpan } from '../api/types'

export type SourceIdentitySelection = {
  sourceIdentity: string
  sourceOccurrence: number
}

export function sourceSelectionAt(
  sources: SourceSpan[],
  index: number
): SourceIdentitySelection | null {
  const source = sources[index]
  if (!source) {
    return null
  }
  const sourceIdentity = sourceSelectionIdentities(source)[0]
  let sourceOccurrence = 0
  for (let current = 0; current < index; current += 1) {
    if (sourceSelectionIdentities(sources[current]).includes(sourceIdentity)) {
      sourceOccurrence += 1
    }
  }
  return { sourceIdentity, sourceOccurrence }
}

export function findSourceSelectionIndex(
  sources: SourceSpan[],
  sourceIdentity: string,
  sourceOccurrence: number
): number {
  let currentOccurrence = 0
  for (let index = 0; index < sources.length; index += 1) {
    if (!sourceSelectionIdentities(sources[index]).includes(sourceIdentity)) {
      continue
    }
    if (currentOccurrence === sourceOccurrence) {
      return index
    }
    currentOccurrence += 1
  }
  return -1
}

export function sourceSelectionIdentities(source: SourceSpan): string[] {
  const canonicalBase = [
    source.document_id,
    source.block_id,
    source.quote
  ]
  const canonicalCellIds = source.canonical_source_cell_ids ?? []
  const rawCellIds = source.source_cell_ids_raw ?? []
  const identities: string[] = []
  if (source.source_evidence_key) {
    identities.push(source.source_evidence_key)
  }
  if (canonicalCellIds.length > 0) {
    identities.push(JSON.stringify([
      ...canonicalBase,
      'canonical_cells',
      source.source_table_row_index ?? null,
      canonicalCellIds
    ]))
  }
  if (rawCellIds.length > 0) {
    identities.push(JSON.stringify([
      ...canonicalBase,
      'raw_cells',
      source.source_table_row_index ?? null,
      rawCellIds
    ]))
  }
  if (source.table_locator) {
    identities.push(JSON.stringify([
      ...canonicalBase,
      'table_locator',
      [
        source.table_locator.table_id,
        source.table_locator.selected_row_index,
        source.table_locator.cell_ids,
        source.table_locator.row_indices,
        source.table_locator.column_indices
      ]
    ]))
  }
  identities.push(JSON.stringify([
    ...canonicalBase,
    'text_occurrence',
    source.text_locator?.start ?? null,
    source.text_locator?.end ?? null
  ]))
  return [...new Set(identities)]
}
