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

    const image = screen.getByRole('img', { name: 'PDF page 1' })
    expect(image.getAttribute('src')).toBe(
      '/api/tasks/task-1/pages/1/preview.png' +
      '?evidence=src_111111111111111111111111&attempt=0'
    )
    fireEvent.error(image)

    expect(screen.getByText('PDF preview unavailable.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry preview' }))

    expect(screen.getByRole('img', { name: 'PDF page 1' }).getAttribute('src')).toBe(
      '/api/tasks/task-1/pages/1/preview.png' +
      '?evidence=src_111111111111111111111111&attempt=1'
    )
  })

  it('changes the preview cache key when source evidence changes', () => {
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
      />
    )

    expect(screen.getByRole('img', { name: 'PDF page 1' }).getAttribute('src')).toContain(
      'evidence=src_111111111111111111111111'
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
      />
    )

    expect(screen.getByRole('img', { name: 'PDF page 1' }).getAttribute('src')).toContain(
      'evidence=src_222222222222222222222222'
    )
  })

  it('shows page context without drawing an invalid locator', () => {
    const block = makeBlock('blk_invalid_page', 'Invalid page locator')
    const source = makeSource({
      block_id: block.block_id,
      quote: block.text,
      page: 1,
      page_locator: makePageLocator(0),
      capability_results: [
        pageRegionResult('FAIL_INVALID_REFERENCE', 'SOURCE_PAGE_LOCATOR_INVALID')
      ]
    })

    renderViewer(makeRequirement('req_invalid_page', [source]), [block])

    expect(screen.getByRole('img', { name: 'PDF page 1' })).toBeTruthy()
    expect(screen.queryByLabelText('Source quote bounding box')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe(
      'Page locator invalid (FAIL_INVALID_REFERENCE).'
    )
  })

  it.each([
    [0, ['10%', '10%', '25%', '30%']],
    [90, ['10%', '10%', '40%', '25%']],
    [180, ['25%', '20%', '50%', '30%']],
    [270, ['20%', '20%', '50%', '60%']]
  ] as const)(
    'uses canonical rotated preview dimensions for %s° page locators',
    (rotation, expectedStyle) => {
      const block = makeBlock(`blk_rotation_${rotation}`, 'Rotated evidence')
      const source = makeSource({
        block_id: block.block_id,
        quote: block.text,
        page: 1,
        page_locator: makePageLocator(rotation),
        capability_results: [pageRegionResult('PASS')]
      })

      renderViewer(makeRequirement(`req_rotation_${rotation}`, [source]), [block])

      const overlay = screen.getByLabelText('Source quote bounding box')
      expect([
        overlay.style.left,
        overlay.style.top,
        overlay.style.width,
        overlay.style.height
      ]).toEqual(expectedStyle)
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

function pageRegionResult(
  status: 'PASS' | 'FAIL_INVALID_REFERENCE',
  issueCode: string | null = null
) {
  return {
    capability: 'page_region' as const,
    status,
    issue_code: issueCode
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
