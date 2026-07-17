import { describe, expect, it } from 'vitest'

import {
  makeLargeRowGroupVisualFixture,
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
})
