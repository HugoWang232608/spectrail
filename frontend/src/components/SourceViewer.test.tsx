// @vitest-environment jsdom

import type { ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CapabilityValidationResult,
  DocumentBlock,
  PageLocator,
  RequirementIR,
  SourceSpan,
  TableEvidenceResponse
} from '../api/types'
import SourceViewerComponent from './SourceViewer'

let previewObjectUrl = 0

beforeEach(() => {
  previewObjectUrl = 0
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () => pagePreviewResponse())
  )
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn(() => `blob:preview-${previewObjectUrl += 1}`),
    revokeObjectURL: vi.fn()
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SourceViewer', () => {
  it('renders the occurrence selected by the final TextLocator', () => {
    const block = makeBlock('blk_repeat', 'repeat / repeat')
    const source = makeSource({
      block_id: block.block_id,
      quote: 'repeat',
      text_locator: {
        block_id: block.block_id,
        start: 9,
        end: 15,
        offset_encoding: 'unicode_code_point',
        match_basis: 'exact'
      }
    })

    renderViewer(makeRequirement('req_repeat', [source]), [block])

    const mark = screen.getByText('repeat', { selector: 'mark' })
    expect(mark.textContent).toBe('repeat')
    expect(mark.parentElement?.textContent).toBe(block.text)
    expect(mark.previousSibling?.textContent).toBe('repeat / ')
  })

  it('renders normalized locator ranges after supplementary Unicode prefixes', () => {
    const block = makeBlock('blk_unicode', '😀𠀀The system shall respond')
    const source = makeSource({
      block_id: block.block_id,
      quote: 'The   system shall respond',
      match_status: 'PASS_NORMALIZED',
      text_locator: {
        block_id: block.block_id,
        start: 2,
        end: 26,
        offset_encoding: 'unicode_code_point',
        match_basis: 'normalized'
      }
    })

    renderViewer(makeRequirement('req_unicode', [source]), [block])

    const mark = screen.getByText('The system shall respond', { selector: 'mark' })
    expect(mark.previousSibling?.textContent).toBe('😀𠀀')
  })

  it('updates the highlighted evidence when the source and requirement change', async () => {
    const blocks = [
      makeBlock('blk_first', 'First evidence'),
      makeBlock('blk_second', 'Second evidence'),
      makeBlock('blk_replacement', 'Replacement evidence')
    ]
    const firstRequirement = makeRequirement('req_first', [
      locatedSource(blocks[0], 'First'),
      locatedSource(blocks[1], 'Second')
    ])
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={firstRequirement}
        blocks={blocks}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Second', { selector: 'mark' })).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_replacement', [
          locatedSource(blocks[2], 'Replacement')
        ])}
        blocks={blocks}
        blocksError={null}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Replacement', { selector: 'mark' })).toBeTruthy()
    })
    expect(screen.getByText('1 / 1')).toBeTruthy()
  })

  it('clamps the selected source when sources shrink under the same requirement', async () => {
    const blocks = [
      makeBlock('blk_kept', 'Kept evidence'),
      makeBlock('blk_removed', 'Removed evidence')
    ]
    const firstSource = locatedSource(blocks[0], 'Kept')
    const requirement = makeRequirement('req_edited', [
      firstSource,
      locatedSource(blocks[1], 'Removed')
    ])
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={requirement}
        blocks={blocks}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Removed', { selector: 'mark' })).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirement.id, [firstSource])}
        blocks={blocks}
        blocksError={null}
      />
    )

    expect(screen.queryByText('No source')).toBeNull()
    await waitFor(() => {
      expect(screen.getByText('Kept', { selector: 'mark' })).toBeTruthy()
    })
    expect(screen.getByText('1 / 1')).toBeTruthy()
  })

  it('keeps a selected source across reorder and falls back when it is replaced', () => {
    const blocks = [
      makeBlock('blk_a', 'Evidence A'),
      makeBlock('blk_b', 'Evidence B'),
      makeBlock('blk_c', 'Evidence C'),
      makeBlock('blk_d', 'Evidence D')
    ]
    const sources = blocks.map((block) => locatedSource(block, block.text))
    const requirementId = 'req_reordered'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [sources[0], sources[1]])}
        blocks={blocks}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Evidence B', { selector: 'mark' })).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [sources[1], sources[2]])}
        blocks={blocks}
        blocksError={null}
      />
    )

    expect(screen.getByText('Evidence B', { selector: 'mark' })).toBeTruthy()
    expect(screen.getByText('1 / 2')).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [sources[2], sources[3]])}
        blocks={blocks}
        blocksError={null}
      />
    )

    expect(screen.queryByText('No source')).toBeNull()
    expect(screen.getByText('Evidence C', { selector: 'mark' })).toBeTruthy()
    expect(screen.getByText('1 / 2')).toBeTruthy()
  })

  it('stabilizes the default source before a same-requirement reorder', () => {
    const blocks = [
      makeBlock('blk_default_a', 'Default evidence A'),
      makeBlock('blk_default_b', 'Default evidence B')
    ]
    const sources = blocks.map((block) => locatedSource(block, block.text))
    const requirementId = 'req_default_reorder'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, sources)}
        blocks={blocks}
        blocksError={null}
      />
    )

    expect(screen.getByText('Default evidence A', { selector: 'mark' })).toBeTruthy()
    expect(screen.getByText('1 / 2')).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [sources[1], sources[0]])}
        blocks={blocks}
        blocksError={null}
      />
    )

    expect(screen.getByText('Default evidence A', { selector: 'mark' })).toBeTruthy()
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('allows selecting a duplicate source occurrence', () => {
    const block = makeBlock('blk_duplicate', 'Duplicate evidence')
    const source = locatedSource(block, block.text)

    renderViewer(
      makeRequirement('req_duplicate', [source, { ...source }]),
      [block]
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByText('2 / 2')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(
      false
    )
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('isolates duplicate source selection between tasks', () => {
    const block = makeBlock('blk_cross_task', 'Cross-task evidence')
    const source = locatedSource(block, block.text)
    const requirement = makeRequirement('req_shared_task_id', [source, { ...source }])
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={requirement}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('2 / 2')).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-2"
        requirement={requirement}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(
      true
    )
  })

  it('keeps a legacy table-cell source selected across reorder', () => {
    const block = makeBlock('blk_legacy_table', 'Shared table quote')
    const textSource = locatedSource(block, block.text)
    const cellASource = makeSource({
      ...textSource,
      table_locator: {
        table_id: 'tbl_00000001',
        cell_ids: ['cell_00000001_r0001_c0001'],
        row_indices: [1],
        selected_row_index: 1,
        column_indices: [1]
      }
    })
    const cellBSource = makeSource({
      ...textSource,
      table_locator: {
        table_id: 'tbl_00000001',
        cell_ids: ['cell_00000001_r0001_c0002'],
        row_indices: [1],
        selected_row_index: 1,
        column_indices: [2]
      }
    })
    const requirementId = 'req_legacy_table'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, cellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Cells')).toBe(
      'tbl_00000001: cell_00000001_r0001_c0002'
    )

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellBSource, cellASource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Cells')).toBe(
      'tbl_00000001: cell_00000001_r0001_c0002'
    )
    expect(screen.getByText('1 / 2')).toBeTruthy()
  })

  it('keeps canonical table cells stable before TableLocator derivation', () => {
    const block = makeBlock('blk_pre_locator_table', 'Shared pre-locator quote')
    const textSource = locatedSource(block, block.text)
    const cellASource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_cell_a'],
      canonical_source_cell_ids: ['cell_00000001_r0001_c0001'],
      source_table_row_index: 1,
      locator_score: 0.1
    })
    const cellBSource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_cell_b'],
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1,
      locator_score: 0.2
    })
    const requirementId = 'req_pre_locator_table'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, cellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Locator')).toBe('0.200')

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellBSource, cellASource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.200')
    expect(screen.getByText('1 / 2')).toBeTruthy()
  })

  it('keeps canonical source selection when TableLocator is derived later', () => {
    const block = makeBlock('blk_locator_lifecycle', 'Table lifecycle quote')
    const textSource = locatedSource(block, block.text)
    const cellASource = makeSource({
      ...textSource,
      canonical_source_cell_ids: ['cell_00000001_r0001_c0001'],
      source_table_row_index: 1
    })
    const cellBSource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_alias_b'],
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1
    })
    const requirementId = 'req_locator_lifecycle'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, cellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('2 / 2')).toBeTruthy()

    const enrichedCellBSource = makeSource({
      ...cellBSource,
      table_locator: {
        table_id: 'tbl_00000001',
        cell_ids: ['cell_00000001_r0001_c0002'],
        row_indices: [1],
        selected_row_index: 1,
        column_indices: [2]
      }
    })
    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, enrichedCellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Cells')).toBe(
      'tbl_00000001: cell_00000001_r0001_c0002'
    )
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('keeps canonical source selection when raw cell aliases are normalized', () => {
    const block = makeBlock('blk_raw_lifecycle', 'Raw identity lifecycle quote')
    const textSource = locatedSource(block, block.text)
    const cellASource = makeSource({
      ...textSource,
      canonical_source_cell_ids: ['cell_00000001_r0001_c0001'],
      source_table_row_index: 1,
      locator_score: 0.1
    })
    const cellBSource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_alias_b'],
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1,
      locator_score: 0.2
    })
    const requirementId = 'req_raw_lifecycle'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, cellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Locator')).toBe('0.200')

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [
          cellASource,
          {
            ...cellBSource,
            source_cell_ids_raw: ['cell_00000001_r0001_c0002']
          }
        ])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.200')
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('migrates a selected raw-only source to canonical cell identity', () => {
    const block = makeBlock('blk_raw_to_canonical', 'Raw to canonical quote')
    const textSource = locatedSource(block, block.text)
    const rawASource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_alias_a'],
      source_table_row_index: 1,
      locator_score: 0.1
    })
    const rawBSource = makeSource({
      ...textSource,
      source_cell_ids_raw: ['raw_alias_b'],
      source_table_row_index: 1,
      locator_score: 0.2
    })
    const requirementId = 'req_raw_to_canonical'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [rawASource, rawBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Locator')).toBe('0.200')

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [
          rawASource,
          {
            ...rawBSource,
            canonical_source_cell_ids: ['cell_00000001_r0001_c0002']
          }
        ])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.200')
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('migrates canonical selection to a final evidence key across reorder', () => {
    const block = makeBlock('blk_final_key', 'Final key lifecycle quote')
    const textSource = locatedSource(block, block.text)
    const cellASource = makeSource({
      ...textSource,
      canonical_source_cell_ids: ['cell_00000001_r0001_c0001'],
      source_table_row_index: 1,
      locator_score: 0.1
    })
    const cellBSource = makeSource({
      ...textSource,
      canonical_source_cell_ids: ['cell_00000001_r0001_c0002'],
      source_table_row_index: 1,
      locator_score: 0.2
    })
    const requirementId = 'req_final_key'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, cellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Locator')).toBe('0.200')

    const finalCellBSource = {
      ...cellBSource,
      source_evidence_key: 'src_222222222222222222222222'
    }
    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [finalCellBSource, cellASource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.200')
    expect(screen.getByText('1 / 2')).toBeTruthy()

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, [cellASource, finalCellBSource])}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.200')
    expect(screen.getByText('2 / 2')).toBeTruthy()
  })

  it('resets selection when final evidence keys are replaced', () => {
    const block = makeBlock('blk_evidence_version', 'Evidence version quote')
    const textSource = locatedSource(block, block.text)
    const versionedSource = (
      cellId: string,
      locatorScore: number,
      sourceEvidenceKey: string
    ) => makeSource({
      ...textSource,
      canonical_source_cell_ids: [cellId],
      source_table_row_index: 1,
      locator_score: locatorScore,
      source_evidence_key: sourceEvidenceKey
    })
    const oldSources = [
      versionedSource('cell_00000001_r0001_c0001', 0.1, 'src_aaaaaaaaaaaaaaaaaaaaaaaa'),
      versionedSource('cell_00000001_r0001_c0002', 0.2, 'src_bbbbbbbbbbbbbbbbbbbbbbbb'),
      versionedSource('cell_00000001_r0001_c0003', 0.3, 'src_cccccccccccccccccccccccc')
    ]
    const requirementId = 'req_evidence_version'
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, oldSources)}
        blocks={[block]}
        blocksError={null}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(sourceMetadataValue('Locator')).toBe('0.200')

    const newSources = [
      versionedSource('cell_00000001_r0001_c0003', 0.3, 'src_333333333333333333333333'),
      versionedSource('cell_00000001_r0001_c0001', 0.1, 'src_111111111111111111111111'),
      versionedSource('cell_00000001_r0001_c0002', 0.2, 'src_222222222222222222222222')
    ]
    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement(requirementId, newSources)}
        blocks={[block]}
        blocksError={null}
      />
    )

    expect(sourceMetadataValue('Locator')).toBe('0.300')
    expect(screen.getByText('1 / 3')).toBeTruthy()
  })

  it('keeps block text unmarked when the locator belongs to another block', () => {
    const block = makeBlock('blk_current', 'Current evidence')
    const source = makeSource({
      block_id: block.block_id,
      quote: 'Current',
      text_locator: {
        block_id: 'blk_other',
        start: 0,
        end: 7,
        offset_encoding: 'unicode_code_point',
        match_basis: 'exact'
      }
    })

    const { container } = renderViewer(makeRequirement('req_mismatch', [source]), [block])

    expect(container.querySelector('mark')).toBeNull()
    expect(screen.getByText(block.text)).toBeTruthy()
  })

  it('shows preview failure and retries with a cache-busting URL', async () => {
    const block = makeBlock('blk_preview', 'Preview evidence')
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      source_evidence_key: 'src_111111111111111111111111',
      page_locator: makePageLocator(0),
      capability_results: [pageRegionResult('PASS')],
      text_locator: {
        block_id: block.block_id,
        start: 0,
        end: block.text.length,
        offset_encoding: 'unicode_code_point',
        match_basis: 'exact'
      }
    })

    renderViewer(makeRequirement('req_preview', [source]), [block])

    const image = await screen.findByRole('img', { name: 'PDF page 1' })
    expect(image.getAttribute('src')).toBe('blob:preview-1')
    expect(fetch).toHaveBeenCalledWith(
      '/api/tasks/task-1/pages/1/preview.png' +
      `?expected_evidence_fingerprint=${'a'.repeat(64)}&attempt=0`,
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    )
    fireEvent.error(image)

    expect(screen.getByText(/PDF preview unavailable:/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry preview' }))

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
      '/api/tasks/task-1/pages/1/preview.png' +
        `?expected_evidence_fingerprint=${'a'.repeat(64)}&attempt=1`,
      expect.objectContaining({ signal: expect.any(AbortSignal) })
      )
    })
    expect(await screen.findByRole('img', {
      name: 'PDF page 1'
    })).toBeTruthy()
  })

  it('resets a failed preview when switching between legacy sources on the same page', async () => {
    const blocks = [
      makeBlock('blk_legacy_a', 'Legacy evidence A'),
      makeBlock('blk_legacy_b', 'Legacy evidence B')
    ]
    const sources = blocks.map((block) => makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      page_locator: makePageLocator(0),
      capability_results: [pageRegionResult('PASS')]
    }))

    renderViewer(makeRequirement('req_legacy', sources), blocks)

    fireEvent.error(await screen.findByRole('img', { name: 'PDF page 1' }))
    expect(screen.getByText(/PDF preview unavailable:/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'PDF page 1' })).toBeTruthy()
    })
    expect(screen.queryByText('PDF preview unavailable.')).toBeNull()
  })

  it('binds preview requests to the ReqIR Evidence fingerprint', async () => {
    const block = makeBlock('blk_cache', 'Cache evidence')
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      source_evidence_key: 'src_111111111111111111111111',
      page_locator: makePageLocator(0),
      capability_results: [pageRegionResult('PASS')]
    })
    const { rerender } = render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_cache', [source])}
        blocks={[block]}
        blocksError={null}
        evidenceFingerprint={'a'.repeat(64)}
        blocksEvidenceFingerprint={'a'.repeat(64)}
      />
    )

    await screen.findByRole('img', { name: 'PDF page 1' })
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/tasks/task-1/pages/1/preview.png' +
        `?expected_evidence_fingerprint=${'a'.repeat(64)}&attempt=0`,
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    )

    rerender(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_cache', [
          {
            ...source,
            source_evidence_key: 'src_222222222222222222222222'
          }
        ])}
        blocks={[block]}
        blocksError={null}
        evidenceFingerprint={'b'.repeat(64)}
        blocksEvidenceFingerprint={'b'.repeat(64)}
      />
    )

    await waitFor(() => {
      expect(fetch).toHaveBeenLastCalledWith(
        '/api/tasks/task-1/pages/1/preview.png' +
          `?expected_evidence_fingerprint=${'b'.repeat(64)}&attempt=0`,
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      )
    })
  })

  it('withholds page context when the locator is invalid', () => {
    const block = {
      ...makeBlock('blk_invalid_page', 'Invalid page locator'),
      page: 3
    }
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 8,
      page_locator: {
        ...makePageLocator(0),
        page: 8
      },
      capability_results: [
        pageRegionResult('FAIL_INVALID_REFERENCE', 'SOURCE_PAGE_LOCATOR_INVALID')
      ]
    })

    renderViewer(makeRequirement('req_invalid_page', [source]), [block])

    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByLabelText('Source quote bounding box')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe(
      'Page locator invalid (FAIL_INVALID_REFERENCE). Preview withheld.'
    )
    expect(sourceMetadataValue('Block Page')).toBe('3')
    expect(sourceMetadataValue('Claimed Page')).toBe('8 (invalid)')
    expect(screen.getByText(block.text, { selector: 'mark' })).toBeTruthy()
  })

  it.each([
    ['UNVERIFIED', 'Page locator not verified. Preview withheld.'],
    ['WARNING_UNAVAILABLE', 'Page locator unavailable. Preview withheld.'],
    ['WARNING_AMBIGUOUS', 'Page locator ambiguous. Preview withheld.']
  ] as const)(
    'describes a %s page locator without calling it invalid',
    (status, expectedMessage) => {
      const block = makeBlock(`blk_${status.toLowerCase()}`, 'Untrusted page evidence')
      const source = makeSource({
        block_id: block.block_id,
        quote: block.text,
        page_locator: makePageLocator(0),
        capability_results: [pageRegionResult(status)]
      })

      renderViewer(makeRequirement(`req_${status.toLowerCase()}`, [source]), [block])

      expect(screen.queryByRole('img')).toBeNull()
      expect(screen.getByRole('status').textContent).toBe(expectedMessage)
    }
  )

  it.each([
    [0, ['10%', '10%', '25%', '30%']],
    [90, ['10%', '10%', '40%', '25%']],
    [180, ['25%', '20%', '50%', '30%']],
    [270, ['20%', '20%', '50%', '60%']]
  ] as const)(
    'uses canonical rotated preview dimensions for %s° page locators',
    async (rotation, expectedStyle) => {
      const block = makeBlock(`blk_rotation_${rotation}`, 'Rotated evidence')
      const source = makeSource({
        block_id: block.block_id,
        quote: block.text,
        page: 1,
        page_locator: makePageLocator(rotation),
        capability_results: [pageRegionResult('PASS')]
      })

      renderViewer(makeRequirement(`req_rotation_${rotation}`, [source]), [block])

      const overlay = await screen.findByLabelText('Source quote bounding box')
      expect([
        overlay.style.left,
        overlay.style.top,
        overlay.style.width,
        overlay.style.height
      ]).toEqual(expectedStyle)
    }
  )

  it('renders a verified table grid and highlights only selected cells', async () => {
    const block = {
      ...makeBlock('blk_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const source = makeTableSource(block.block_id)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(makeTableEvidenceResponse(block.block_id))
    )
    vi.stubGlobal('fetch', fetchMock)

    renderViewer(makeRequirement('req_table', [source]), [block])

    const grid = await screen.findByRole('grid', {
      name: 'Table evidence tbl_00000001'
    })
    expect(grid).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks/task-1/tables/tbl_00000001/blocks/blk_table/evidence' +
        `?expected_evidence_fingerprint=${'a'.repeat(64)}`,
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    )
    expect(screen.getByText('repeated header')).toBeTruthy()

    const selected = screen.getByRole('gridcell', {
      name: /cell_00000001_r0002_c0002/
    })
    const unselected = screen.getByRole('gridcell', {
      name: /cell_00000001_r0002_c0001/
    })
    expect(selected.getAttribute('aria-selected')).toBe('true')
    expect(selected.className).toContain('selected')
    expect(unselected.getAttribute('aria-selected')).toBe('false')
    const header = screen.getByRole('columnheader', {
      name: /cell_00000001_r0001_c0001/
    })
    expect(header.getAttribute('colspan')).toBe('2')
    expect(header.getAttribute('scope')).toBe('col')
    expect(screen.getAllByText(/repeated_header \[/).length).toBeGreaterThan(0)
  })

  it('shows verified multi-page table continuation lineage', async () => {
    const block = {
      ...makeBlock('blk_continued_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const source = makeTableSource(block.block_id)
    const response = {
      ...makeTableEvidenceResponse(block.block_id),
      continuation_role: 'continuation' as const,
      continuation_group_id: 'tblcont_00000001',
      continuation_sequence: 2,
      continuation_of_table_id: 'tbl_00000000',
      continuation_label: 'table 1',
      continuation_basis: 'explicit_marker_page_edge_header_match' as const,
      continued_header_cell_ids: {
        cell_00000001_r0001_c0001: 'cell_00000000_r0001_c0001'
      }
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(response)))

    renderViewer(makeRequirement('req_continued_table', [source]), [block])

    await screen.findByRole('grid', {
      name: 'Table evidence tbl_00000001'
    })
    expect(
      screen.getByText('table 1 · continued from tbl_00000000 · sequence 2')
    ).toBeTruthy()
  })

  it('withholds the table grid when table-cell validation failed', () => {
    const block = {
      ...makeBlock('blk_invalid_table', 'A | B'),
      type: 'table' as const
    }
    const source = {
      ...makeTableSource(block.block_id),
      capability_results: [
        tableCellResult('FAIL_INVALID_REFERENCE', 'SOURCE_TABLE_LOCATOR_INVALID')
      ]
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderViewer(makeRequirement('req_invalid_table', [source]), [block])

    expect(screen.queryByRole('grid')).toBeNull()
    expect(screen.getByText(
      'Table locator invalid (FAIL_INVALID_REFERENCE). Grid withheld.'
    )).toBeTruthy()
    expect(screen.getByText(block.text, { selector: '.block-box p' })).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('retries a failed table evidence request', async () => {
    const block = {
      ...makeBlock('blk_retry_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const source = makeTableSource(block.block_id)
    const fetchMock = vi.fn()
      .mockRejectedValueOnce({
        code: 'TABLE_EVIDENCE_UNAVAILABLE',
        message: 'EvidenceIndex is invalid'
      })
      .mockResolvedValueOnce(jsonResponse(makeTableEvidenceResponse(block.block_id)))
    vi.stubGlobal('fetch', fetchMock)

    renderViewer(makeRequirement('req_retry_table', [source]), [block])

    expect((await screen.findByRole('alert')).textContent).toContain(
      'TABLE_EVIDENCE_UNAVAILABLE'
    )
    fireEvent.click(screen.getByRole('button', { name: 'Retry table evidence' }))

    expect(await screen.findByRole('grid', {
      name: 'Table evidence tbl_00000001'
    })).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('withholds a mismatched table evidence response', async () => {
    const block = {
      ...makeBlock('blk_mismatch_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const source = makeTableSource(block.block_id)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({
        ...makeTableEvidenceResponse(block.block_id),
        block_id: 'blk_other'
      })
    ))

    renderViewer(makeRequirement('req_mismatch_table', [source]), [block])

    expect((await screen.findByRole('alert')).textContent).toContain(
      'does not match the validated locator'
    )
    expect(screen.queryByRole('grid')).toBeNull()
  })

  it('withholds table cells when the projection Evidence version changed', async () => {
    const block = {
      ...makeBlock('blk_version_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const source = makeTableSource(block.block_id)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({
        ...makeTableEvidenceResponse(block.block_id),
        evidence_fingerprint: 'b'.repeat(64)
      })
    ))

    const reload = vi.fn()
    renderViewer(
      makeRequirement('req_version_table', [source]),
      [block],
      'a'.repeat(64),
      'a'.repeat(64),
      reload
    )

    expect((await screen.findByRole('alert')).textContent).toContain(
      'Evidence version changed. Reload ReqIR'
    )
    expect(screen.queryByRole('grid')).toBeNull()
    fireEvent.click(screen.getByRole('button', {
      name: 'Reload task evidence'
    }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('requires ReqIR Evidence metadata before requesting table evidence', () => {
    const block = {
      ...makeBlock('blk_unbound_table', 'Header | Status\nREQ-1 | Approved'),
      type: 'table' as const
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_unbound_table', [
          makeTableSource(block.block_id)
        ])}
        blocks={[block]}
        blocksError={null}
        evidenceFingerprint={null}
      />
    )

    expect(screen.getByText(
      /EVIDENCE_VERSION_UNAVAILABLE/
    )).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('highlights a row-span projection on its selected physical row', async () => {
    const block = {
      ...makeBlock('blk_row_span_table', 'Merged | A\nMerged | B'),
      type: 'table' as const
    }
    const source = makeSource({
      block_id: block.block_id,
      quote: 'Merged',
      canonical_source_cell_ids: ['cell_00000001_r0002_c0001'],
      source_cell_ids_raw: ['cell_00000001_r0002_c0001'],
      source_table_row_index: 3,
      table_locator: {
        table_id: 'tbl_00000001',
        cell_ids: ['cell_00000001_r0002_c0001'],
        row_indices: [2],
        selected_row_index: 3,
        column_indices: [1],
        bbox: null
      },
      locator_status: 'PASS_STRUCTURED',
      capability_results: [tableCellResult('PASS')]
    })
    const response = makeTableEvidenceResponse(block.block_id)
    response.rows = [
      {
        physical_row_index: 2,
        rendered_start: 0,
        rendered_end: 10,
        repeated_header: false,
        cells: [{
          ...response.rows[1].cells[0],
          cell_id: 'cell_00000001_r0002_c0001',
          text: 'Merged',
          row_span: 2
        }]
      },
      {
        physical_row_index: 3,
        rendered_start: 11,
        rendered_end: 21,
        repeated_header: false,
        cells: [{
          ...response.rows[1].cells[0],
          cell_id: 'cell_00000001_r0002_c0001',
          text: 'Merged',
          row_span: 2,
          occurrences: [{
            occurrence_id: 'occ_projection',
            occurrence_role: 'row_span_projection',
            canonical_start: 11,
            canonical_end: 17
          }]
        }]
      }
    ]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(response)))

    renderViewer(makeRequirement('req_row_span_table', [source]), [block])

    const projected = await screen.findByRole('gridcell', {
      name: /cell_00000001_r0002_c0001, physical row 3/
    })
    expect(projected.getAttribute('aria-selected')).toBe('true')
    expect(projected.textContent).toContain('row_span_projection [11, 17)')
    expect(screen.getByRole('gridcell', {
      name: /cell_00000001_r0002_c0001, physical row 2/
    }).getAttribute('aria-selected')).toBe('false')
  })

  it('withholds the table grid when canonical blocks are unavailable', async () => {
    const block = {
      ...makeBlock('blk_untrusted_table', 'Header\nRequirement'),
      type: 'table' as const
    }
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(makeTableEvidenceResponse(block.block_id))
    )
    const reload = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_untrusted_table', [
          makeTableSource(block.block_id)
        ])}
        blocks={[block]}
        blocksError={{
          code: 'BLOCKS_UNAVAILABLE',
          message: 'blocks do not match EvidenceIndex'
        }}
        evidenceFingerprint={'a'.repeat(64)}
        blocksEvidenceFingerprint={null}
        onReloadEvidence={reload}
      />
    )

    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByRole('grid')).toBeNull()
    expect(screen.getAllByRole('button', {
      name: 'Reload task evidence'
    })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', {
      name: 'Reload task evidence'
    }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('requires a task rerun when canonical blocks use legacy continuation Evidence', async () => {
    const block = makeBlock('blk_legacy_blocks', 'Legacy block evidence')
    const rerun = vi.fn()
    const reload = vi.fn()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(
      <SourceViewer
        taskId="task-1"
        requirement={makeRequirement('req_legacy_blocks', [
          locatedSource(block, 'Legacy')
        ])}
        blocks={[block]}
        blocksError={{
          code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
          message: 'legacy Evidence must be rebuilt'
        }}
        evidenceFingerprint={'a'.repeat(64)}
        blocksEvidenceFingerprint={null}
        onReloadEvidence={reload}
        onRerunEvidence={rerun}
      />
    )

    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/Evidence was produced by an older parser/)).toBeTruthy()
    expect(screen.queryByRole('button', {
      name: 'Reload task evidence'
    })).toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Rerun task' }))
    expect(rerun).toHaveBeenCalledTimes(1)
    expect(reload).not.toHaveBeenCalled()
  })

  it('requires a task rerun instead of retrying a legacy page preview', async () => {
    const block = makeBlock('blk_legacy_preview', 'Legacy preview evidence')
    const rerun = vi.fn()
    const reload = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      apiErrorResponse('EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED')
    ))
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      page_locator: makePageLocator(0),
      capability_results: [pageRegionResult('PASS')]
    })

    renderViewer(
      makeRequirement('req_legacy_preview', [source]),
      [block],
      'a'.repeat(64),
      'a'.repeat(64),
      reload,
      rerun
    )

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(
      'Evidence was produced by an older parser'
    )
    expect(screen.queryByRole('button', { name: 'Retry preview' })).toBeNull()
    expect(screen.queryByRole('button', {
      name: 'Reload task evidence'
    })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Rerun task' }))
    expect(rerun).toHaveBeenCalledTimes(1)
    expect(reload).not.toHaveBeenCalled()
  })

  it('requires a task rerun instead of retrying legacy table evidence', async () => {
    const block = {
      ...makeBlock(
        'blk_legacy_table',
        'Header | Status\nREQ-1 | Approved'
      ),
      type: 'table' as const
    }
    const rerun = vi.fn()
    const reload = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      apiErrorResponse('EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED')
    ))

    renderViewer(
      makeRequirement('req_legacy_table', [
        makeTableSource(block.block_id)
      ]),
      [block],
      'a'.repeat(64),
      'a'.repeat(64),
      reload,
      rerun
    )

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(
      'Evidence was produced by an older parser'
    )
    expect(screen.queryByRole('button', {
      name: 'Retry table evidence'
    })).toBeNull()
    expect(screen.queryByRole('button', {
      name: 'Reload task evidence'
    })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Rerun task' }))
    expect(rerun).toHaveBeenCalledTimes(1)
    expect(reload).not.toHaveBeenCalled()
  })

  it('withholds a preview whose response fingerprint does not match ReqIR', async () => {
    const block = makeBlock('blk_stale_preview', 'Stale preview evidence')
    const reload = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(pagePreviewResponse('b'.repeat(64)))
    )
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      page_locator: makePageLocator(0),
      capability_results: [pageRegionResult('PASS')]
    })

    renderViewer(
      makeRequirement('req_stale_preview', [source]),
      [block],
      'a'.repeat(64),
      'a'.repeat(64),
      reload
    )

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Evidence version changed')
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByLabelText('Source quote bounding box')).toBeNull()
    fireEvent.click(screen.getByRole('button', {
      name: 'Reload task evidence'
    }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('withholds block text from a different Evidence version and reloads', () => {
    const block = makeBlock(
      'blk_stale_context',
      'Current generation block text'
    )
    const source = locatedSource(block, 'Current generation')
    const reload = vi.fn()

    renderViewer(
      makeRequirement('req_stale_context', [source]),
      [block],
      'a'.repeat(64),
      'b'.repeat(64),
      reload
    )

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('EVIDENCE_VERSION_CHANGED')
    expect(screen.queryByText('Current generation', {
      selector: '.block-box mark'
    })).toBeNull()
    fireEvent.click(screen.getByRole('button', {
      name: 'Reload task evidence'
    }))
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

type SourceViewerTestProps = Omit<
  ComponentProps<typeof SourceViewerComponent>,
  'evidenceFingerprint' | 'blocksEvidenceFingerprint'
> & Partial<Pick<
  ComponentProps<typeof SourceViewerComponent>,
  'evidenceFingerprint' | 'blocksEvidenceFingerprint'
>>

function SourceViewer(props: SourceViewerTestProps) {
  return (
    <SourceViewerComponent
      evidenceFingerprint={'a'.repeat(64)}
      blocksEvidenceFingerprint={'a'.repeat(64)}
      {...props}
    />
  )
}

function renderViewer(
  requirement: RequirementIR,
  blocks: DocumentBlock[],
  evidenceFingerprint = 'a'.repeat(64),
  blocksEvidenceFingerprint = evidenceFingerprint,
  onReloadEvidence?: () => void,
  onRerunEvidence?: () => void
) {
  return render(
    <SourceViewer
      taskId="task-1"
      requirement={requirement}
      blocks={blocks}
      blocksError={null}
      evidenceFingerprint={evidenceFingerprint}
      blocksEvidenceFingerprint={blocksEvidenceFingerprint}
      onReloadEvidence={onReloadEvidence}
      onRerunEvidence={onRerunEvidence}
    />
  )
}

function locatedSource(block: DocumentBlock, quote: string): SourceSpan {
  const utf16Start = block.text.indexOf(quote)
  const start = Array.from(block.text.slice(0, utf16Start)).length
  return makeSource({
    block_id: block.block_id,
    quote,
    text_locator: {
      block_id: block.block_id,
      start,
      end: start + Array.from(quote).length,
      offset_encoding: 'unicode_code_point',
      match_basis: 'exact'
    }
  })
}

function makeSource(overrides: Partial<SourceSpan> = {}): SourceSpan {
  return {
    document_id: 'doc_001',
    block_id: 'blk_001',
    quote: 'Evidence',
    match_status: 'PASS_EXACT',
    locator_status: 'PASS_DERIVED',
    capability_results: [],
    ...overrides
  }
}

function makePageLocator(sourceRotation: 0 | 90 | 180 | 270): PageLocator {
  const geometry = {
    0: {
      pageWidth: 200,
      pageHeight: 300,
      bbox: [20, 30, 70, 120]
    },
    90: {
      pageWidth: 300,
      pageHeight: 200,
      bbox: [30, 20, 150, 70]
    },
    180: {
      pageWidth: 200,
      pageHeight: 300,
      bbox: [50, 60, 150, 150]
    },
    270: {
      pageWidth: 300,
      pageHeight: 200,
      bbox: [60, 40, 210, 160]
    }
  }[sourceRotation]

  return {
    page: 1,
    bbox: {
      x0: geometry.bbox[0],
      y0: geometry.bbox[1],
      x1: geometry.bbox[2],
      y1: geometry.bbox[3],
      coordinate_space: 'pdf_preview_rotated_points_top_left_v1'
    },
    page_width: geometry.pageWidth,
    page_height: geometry.pageHeight,
    source_rotation: sourceRotation,
    coordinate_space: 'pdf_preview_rotated_points_top_left_v1',
    derivation: 'quote_span_union'
  }
}

function makeTableSource(blockId: string): SourceSpan {
  return makeSource({
    block_id: blockId,
    quote: 'Approved',
    canonical_source_cell_ids: ['cell_00000001_r0002_c0002'],
    source_cell_ids_raw: ['cell_00000001_r0002_c0002'],
    source_table_row_index: 2,
    table_locator: {
      table_id: 'tbl_00000001',
      cell_ids: ['cell_00000001_r0002_c0002'],
      row_indices: [2],
      selected_row_index: 2,
      column_indices: [2],
      bbox: null
    },
    text_locator: {
      block_id: blockId,
      start: 24,
      end: 32,
      offset_encoding: 'unicode_code_point',
      match_basis: 'exact'
    },
    locator_status: 'PASS_STRUCTURED',
    capability_results: [tableCellResult('PASS')]
  })
}

function makeTableEvidenceResponse(blockId: string): TableEvidenceResponse {
  return {
    schema_version: 'table_evidence_view_v1',
    task_id: 'task-1',
    evidence_fingerprint: 'a'.repeat(64),
    table_id: 'tbl_00000001',
    block_id: blockId,
    row_count: 2,
    column_count: 3,
    topology_status: 'complete',
    page: null,
    bbox: null,
    primary_row_start: 1,
    primary_row_end: 2,
    warnings: [],
    rows: [
      {
        physical_row_index: 1,
        rendered_start: 0,
        rendered_end: 15,
        repeated_header: true,
        cells: [
          {
            cell_id: 'cell_00000001_r0001_c0001',
            row_index: 1,
            column_index: 1,
            row_span: 1,
            column_span: 2,
            text: 'Header',
            is_header: true,
            page: null,
            bbox: null,
            occurrences: [
              {
                occurrence_id: 'occ_1',
                occurrence_role: 'repeated_header',
                canonical_start: 0,
                canonical_end: 6
              }
            ]
          },
          {
            cell_id: 'cell_00000001_r0001_c0003',
            row_index: 1,
            column_index: 3,
            row_span: 1,
            column_span: 1,
            text: 'Status',
            is_header: true,
            page: null,
            bbox: null,
            occurrences: [
              {
                occurrence_id: 'occ_2',
                occurrence_role: 'repeated_header',
                canonical_start: 9,
                canonical_end: 15
              }
            ]
          }
        ]
      },
      {
        physical_row_index: 2,
        rendered_start: 16,
        rendered_end: 32,
        repeated_header: false,
        cells: [
          {
            cell_id: 'cell_00000001_r0002_c0001',
            row_index: 2,
            column_index: 1,
            row_span: 1,
            column_span: 1,
            text: 'REQ-1',
            is_header: false,
            page: null,
            bbox: null,
            occurrences: [
              {
                occurrence_id: 'occ_3',
                occurrence_role: 'original',
                canonical_start: 16,
                canonical_end: 21
              }
            ]
          },
          {
            cell_id: 'cell_00000001_r0002_c0002',
            row_index: 2,
            column_index: 2,
            row_span: 1,
            column_span: 2,
            text: 'Approved',
            is_header: false,
            page: null,
            bbox: null,
            occurrences: [
              {
                occurrence_id: 'occ_4',
                occurrence_role: 'original',
                canonical_start: 24,
                canonical_end: 32
              }
            ]
          }
        ]
      }
    ]
  }
}

function tableCellResult(
  status: CapabilityValidationResult['status'],
  issueCode?: string
): CapabilityValidationResult {
  return {
    capability: 'table_cell',
    status,
    issue_code: issueCode
  }
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  })
}

function apiErrorResponse(code: string): Response {
  return new Response(JSON.stringify({
    detail: {
      code,
      message: 'legacy Evidence must be rebuilt'
    }
  }), {
    status: 409,
    headers: { 'Content-Type': 'application/json' }
  })
}

function pagePreviewResponse(
  evidenceFingerprint = 'a'.repeat(64)
): Response {
  return new Response(new Blob(['preview'], { type: 'image/png' }), {
    status: 200,
    headers: {
      'Content-Type': 'image/png',
      'X-Spectrail-Evidence-Fingerprint': evidenceFingerprint
    }
  })
}

function pageRegionResult(
  status: CapabilityValidationResult['status'],
  issueCode: string | null = null
): CapabilityValidationResult {
  return {
    capability: 'page_region',
    status,
    issue_code: issueCode
  }
}

function sourceMetadataValue(label: string): string | null | undefined {
  const term = screen.getByText(label, { selector: 'dt' })
  return term.parentElement?.querySelector('dd')?.textContent
}

function makeBlock(blockId: string, text: string): DocumentBlock {
  return {
    block_id: blockId,
    document_id: 'doc_001',
    type: 'paragraph',
    text,
    section_path: [],
    order: 1,
    metadata: {}
  }
}

function makeRequirement(id: string, sources: SourceSpan[]): RequirementIR {
  return {
    id,
    version: 1,
    title: null,
    type: 'functional',
    ears_pattern: 'ubiquitous',
    statement: 'The system shall provide evidence.',
    priority: 'must',
    verification_method: 'inspection',
    sources,
    confidence: 1,
    review_status: 'pending',
    possible_duplicate_ids: [],
    derived_from: [],
    tags: [],
    review_log: [],
    metadata: {}
  }
}
