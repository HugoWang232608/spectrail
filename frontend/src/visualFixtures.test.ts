import { describe, expect, it } from 'vitest'

import {
  makeLargeRowGroupVisualFixture,
  makePdfMergedTableVisualFixture,
  makePdfTableVisualFixture,
  VISUAL_EVIDENCE_FINGERPRINT,
  validateVisualTableEvidence
} from './visualFixtures'

describe('visual table evidence fixtures', () => {
  it('uses the row ranges produced by the backend projection contract', () => {
    const response = makeLargeRowGroupVisualFixture().tableEvidence
    expect(response).toBeDefined()
    expect(response?.rows.map((row) => [
      row.physical_row_index,
      row.rendered_start,
      row.rendered_end
    ])).toEqual([
      [1, 0, 35],
      [21, 36, 65],
      [22, 66, 110]
    ])
  })

  it('rejects a row range that cannot be produced by the backend builder', () => {
    const response = structuredClone(
      makeLargeRowGroupVisualFixture().tableEvidence
    )
    expect(response).toBeDefined()
    if (!response) {
      return
    }
    response.rows[0].rendered_end = 29

    expect(() => validateVisualTableEvidence(response)).toThrow(
      'visual table row 1 range [0, 29) does not match occurrence range [0, 35)'
    )
  })

  it('loads the checked backend PDF table projection', () => {
    const fixture = makePdfTableVisualFixture()
    const source = fixture.requirement.sources[0]

    expect(VISUAL_EVIDENCE_FINGERPRINT).toMatch(/^[0-9a-f]{64}$/)
    expect(source.locator_status).toBe('PASS_STRUCTURED')
    expect(source.page_locator?.derivation).toBe('table_cell_union')
    expect(source.table_locator?.cell_ids).toEqual([
      'cell_00000001_r0002_c0001',
      'cell_00000001_r0002_c0002'
    ])
    expect(fixture.tableEvidence?.rows).toHaveLength(3)
    expect(fixture.tableEvidence?.rows[1].cells[1].text).toBe(
      'Approved within 2 seconds'
    )
  })

  it('loads the checked backend PDF merged-cell projection', () => {
    const fixture = makePdfMergedTableVisualFixture()
    const source = fixture.requirement.sources[0]
    const projected = fixture.tableEvidence?.rows[1].cells[0]

    expect(fixture.evidenceFingerprint).toMatch(/^[0-9a-f]{64}$/)
    expect(source.locator_status).toBe('PASS_STRUCTURED')
    expect(source.page_locator?.derivation).toBe('table_cell_union')
    expect(source.table_locator?.selected_row_index).toBe(2)
    expect(projected?.row_span).toBe(2)
    expect(projected?.occurrences[0].occurrence_role).toBe(
      'row_span_projection'
    )
  })
})
