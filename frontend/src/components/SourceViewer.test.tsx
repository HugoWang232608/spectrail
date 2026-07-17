// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type {
  DocumentBlock,
  PageLocator,
  RequirementIR,
  SourceSpan
} from '../api/types'
import SourceViewer from './SourceViewer'

afterEach(cleanup)

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

  it('shows preview failure and retries with a cache-busting URL', () => {
    const block = makeBlock('blk_preview', 'Preview evidence')
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      page_locator: makePageLocator(0),
      text_locator: {
        block_id: block.block_id,
        start: 0,
        end: block.text.length,
        offset_encoding: 'unicode_code_point',
        match_basis: 'exact'
      }
    })

    renderViewer(makeRequirement('req_preview', [source]), [block])

    const image = screen.getByRole('img', { name: 'PDF page 1' })
    expect(image.getAttribute('src')).toBe('/api/tasks/task-1/pages/1/preview.png?attempt=0')
    fireEvent.error(image)

    expect(screen.getByText('PDF preview unavailable.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry preview' }))

    expect(screen.getByRole('img', { name: 'PDF page 1' }).getAttribute('src')).toBe(
      '/api/tasks/task-1/pages/1/preview.png?attempt=1'
    )
  })

  it.each([0, 90, 180, 270] as const)(
    'applies proportional overlay geometry for %s° page locators',
    (rotation) => {
      const block = makeBlock(`blk_rotation_${rotation}`, 'Rotated evidence')
      const source = makeSource({
        block_id: block.block_id,
        quote: block.text,
        page: 1,
        page_locator: makePageLocator(rotation)
      })

      renderViewer(makeRequirement(`req_rotation_${rotation}`, [source]), [block])

      const overlay = screen.getByLabelText('Source quote bounding box')
      expect(overlay.style.left).toBe('10%')
      expect(overlay.style.top).toBe('10%')
      expect(overlay.style.width).toBe('25%')
      expect(overlay.style.height).toBe('30%')
    }
  )
})

function renderViewer(requirement: RequirementIR, blocks: DocumentBlock[]) {
  return render(
    <SourceViewer
      taskId="task-1"
      requirement={requirement}
      blocks={blocks}
      blocksError={null}
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
  return {
    page: 1,
    bbox: {
      x0: 20,
      y0: 30,
      x1: 70,
      y1: 120,
      coordinate_space: 'pdf_preview_rotated_points_top_left_v1'
    },
    page_width: 200,
    page_height: 300,
    source_rotation: sourceRotation,
    coordinate_space: 'pdf_preview_rotated_points_top_left_v1',
    derivation: 'quote_span_union'
  }
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
