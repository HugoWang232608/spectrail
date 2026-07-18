import type {
  DocumentBlock,
  PageLocator,
  RequirementIR,
  SourceSpan,
  TableEvidenceResponse
} from './api/types'
import generatedPdfTableFixture from './fixtures/pdf-table-evidence.json' with {
  type: 'json'
}
import generatedPdfMergedTableFixture from './fixtures/pdf-merged-table-evidence.json' with {
  type: 'json'
}
import generatedPdfContinuationFixture from './fixtures/pdf-table-continuation-evidence.json' with {
  type: 'json'
}

export const VISUAL_TASK_ID = 'visual-task'

export type VisualFixture = {
  name: string
  requirement: RequirementIR
  blocks: DocumentBlock[]
  tableEvidence?: TableEvidenceResponse
  evidenceFingerprint?: string
}

type GeneratedVisualFixture = VisualFixture & {
  evidenceFingerprint: string
  tableEvidence: TableEvidenceResponse
}

const PDF_TABLE_VISUAL_FIXTURE = (
  generatedPdfTableFixture as unknown as GeneratedVisualFixture
)
const PDF_MERGED_TABLE_VISUAL_FIXTURE = (
  generatedPdfMergedTableFixture as unknown as GeneratedVisualFixture
)
const PDF_CONTINUATION_VISUAL_FIXTURE = (
  generatedPdfContinuationFixture as unknown as GeneratedVisualFixture
)

export const VISUAL_EVIDENCE_FINGERPRINT = (
  PDF_TABLE_VISUAL_FIXTURE.evidenceFingerprint
)

const PDF_GEOMETRY: Record<0 | 90 | 180 | 270, {
  pageWidth: number
  pageHeight: number
  bbox: [number, number, number, number]
}> = {
  0: {
    pageWidth: 240,
    pageHeight: 320,
    bbox: [30, 76, 192, 116]
  },
  90: {
    pageWidth: 320,
    pageHeight: 240,
    bbox: [58, 38, 276, 82]
  },
  180: {
    pageWidth: 240,
    pageHeight: 320,
    bbox: [44, 196, 210, 242]
  },
  270: {
    pageWidth: 320,
    pageHeight: 240,
    bbox: [38, 142, 246, 190]
  }
}

export function makePdfVisualFixture(
  rotation: 0 | 90 | 180 | 270
): VisualFixture {
  const blockId = `blk_pdf_${rotation}`
  const quote = `Rotation ${rotation} source evidence`
  const text = `Context before. ${quote}. Context after.`
  const start = Array.from(text.slice(0, text.indexOf(quote))).length
  const geometry = PDF_GEOMETRY[rotation]
  const locator: PageLocator = {
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
    source_rotation: rotation,
    coordinate_space: 'pdf_preview_rotated_points_top_left_v1',
    derivation: 'quote_span_union'
  }
  const source = baseSource({
    block_id: blockId,
    page: 1,
    quote,
    source_evidence_key: `src_${String(rotation).padStart(24, '0')}`,
    text_locator: {
      block_id: blockId,
      start,
      end: start + Array.from(quote).length,
      offset_encoding: 'unicode_code_point',
      match_basis: 'exact'
    },
    page_locator: locator,
    locator_status: 'PASS_STRUCTURED',
    capability_results: [
      { capability: 'text_range', status: 'PASS' },
      { capability: 'page_region', status: 'PASS' }
    ]
  })
  return {
    name: `PDF rotation ${rotation}°`,
    requirement: requirement(`req_pdf_${rotation}`, source),
    blocks: [block(blockId, text, 'paragraph', 1)]
  }
}

export function makeMergedDocxVisualFixture(): VisualFixture {
  const blockId = 'blk_docx_merged'
  const tableId = 'tbl_00000001'
  const mergedId = 'cell_00000001_r0002_c0001'
  const selectedId = 'cell_00000001_r0003_c0002'
  const source = baseSource({
    block_id: blockId,
    quote: 'Safety | Fail-safe mode',
    source_evidence_key: 'src_bbbbbbbbbbbbbbbbbbbbbbbb',
    source_cell_ids_raw: [mergedId, selectedId],
    canonical_source_cell_ids: [mergedId, selectedId],
    source_table_row_index: 3,
    text_locator: {
      block_id: blockId,
      start: 49,
      end: 72,
      offset_encoding: 'unicode_code_point',
      match_basis: 'exact'
    },
    table_locator: {
      table_id: tableId,
      cell_ids: [mergedId, selectedId],
      row_indices: [2, 3],
      selected_row_index: 3,
      column_indices: [1, 2],
      bbox: null
    },
    locator_status: 'PASS_STRUCTURED',
    capability_results: [
      { capability: 'text_range', status: 'PASS' },
      { capability: 'table_cell', status: 'PASS' }
    ]
  })
  const tableEvidence = validateVisualTableEvidence({
    schema_version: 'table_evidence_view_v1',
    task_id: VISUAL_TASK_ID,
    run_generation: 1,
    evidence_fingerprint: VISUAL_EVIDENCE_FINGERPRINT,
    table_id: tableId,
    block_id: blockId,
    row_count: 3,
    column_count: 3,
    topology_status: 'complete',
    page: null,
    bbox: null,
    primary_row_start: 1,
    primary_row_end: 3,
    warnings: [],
    rows: [
      {
        physical_row_index: 1,
        rendered_start: 0,
        rendered_end: 22,
        repeated_header: false,
        cells: [
          tableCell('cell_00000001_r0001_c0001', 1, 1, 'Category', {
            header: true,
            columnSpan: 1,
            occurrence: ['original', 0, 8]
          }),
          tableCell('cell_00000001_r0001_c0002', 1, 2, 'Requirement', {
            header: true,
            columnSpan: 2,
            occurrence: ['original', 11, 22]
          })
        ]
      },
      {
        physical_row_index: 2,
        rendered_start: 23,
        rendered_end: 48,
        repeated_header: false,
        cells: [
          tableCell(mergedId, 2, 1, 'Safety', {
            rowSpan: 2,
            occurrence: ['original', 23, 29]
          }),
          tableCell('cell_00000001_r0002_c0002', 2, 2, 'Normal operation', {
            columnSpan: 2,
            occurrence: ['original', 32, 48]
          })
        ]
      },
      {
        physical_row_index: 3,
        rendered_start: 49,
        rendered_end: 72,
        repeated_header: false,
        cells: [
          tableCell(mergedId, 2, 1, 'Safety', {
            rowSpan: 2,
            occurrence: ['row_span_projection', 49, 55]
          }),
          tableCell(selectedId, 3, 2, 'Fail-safe mode', {
            columnSpan: 2,
            occurrence: ['original', 58, 72]
          })
        ]
      }
    ]
  })
  return {
    name: 'DOCX merged cell projection',
    requirement: requirement('req_docx_merged', source),
    blocks: [
      block(
        blockId,
        'Category | Requirement\nSafety | Normal operation\nSafety | Fail-safe mode',
        'table'
      )
    ],
    tableEvidence
  }
}

export function makeLargeRowGroupVisualFixture(): VisualFixture {
  const blockId = 'blk_docx_rows_0021_0040'
  const tableId = 'tbl_00000002'
  const selectedId = 'cell_00000002_r0022_c0002'
  const source = baseSource({
    block_id: blockId,
    quote: 'REQ-022 | Approved within 2 seconds',
    source_evidence_key: 'src_cccccccccccccccccccccccc',
    source_cell_ids_raw: [selectedId],
    canonical_source_cell_ids: [selectedId],
    source_table_row_index: 22,
    text_locator: {
      block_id: blockId,
      start: 66,
      end: 101,
      offset_encoding: 'unicode_code_point',
      match_basis: 'exact'
    },
    table_locator: {
      table_id: tableId,
      cell_ids: [selectedId],
      row_indices: [22],
      selected_row_index: 22,
      column_indices: [2],
      bbox: null
    },
    locator_status: 'PASS_STRUCTURED',
    capability_results: [
      { capability: 'text_range', status: 'PASS' },
      { capability: 'table_cell', status: 'PASS' }
    ]
  })
  const tableEvidence = validateVisualTableEvidence({
    schema_version: 'table_evidence_view_v1',
    task_id: VISUAL_TASK_ID,
    run_generation: 1,
    evidence_fingerprint: VISUAL_EVIDENCE_FINGERPRINT,
    table_id: tableId,
    block_id: blockId,
    row_count: 80,
    column_count: 3,
    topology_status: 'complete',
    page: null,
    bbox: null,
    primary_row_start: 21,
    primary_row_end: 40,
    warnings: [],
    rows: [
      {
        physical_row_index: 1,
        rendered_start: 0,
        rendered_end: 35,
        repeated_header: true,
        cells: [
          tableCell('cell_00000002_r0001_c0001', 1, 1, 'Requirement ID', {
            header: true,
            occurrence: ['repeated_header', 0, 14]
          }),
          tableCell('cell_00000002_r0001_c0002', 1, 2, 'Acceptance', {
            header: true,
            occurrence: ['repeated_header', 17, 27]
          }),
          tableCell('cell_00000002_r0001_c0003', 1, 3, 'Owner', {
            header: true,
            occurrence: ['repeated_header', 30, 35]
          })
        ]
      },
      {
        physical_row_index: 21,
        rendered_start: 36,
        rendered_end: 65,
        repeated_header: false,
        cells: [
          tableCell('cell_00000002_r0021_c0001', 21, 1, 'REQ-021', {
            occurrence: ['original', 36, 43]
          }),
          tableCell('cell_00000002_r0021_c0002', 21, 2, 'Pending review', {
            occurrence: ['original', 46, 60]
          }),
          tableCell('cell_00000002_r0021_c0003', 21, 3, 'QA', {
            occurrence: ['original', 63, 65]
          })
        ]
      },
      {
        physical_row_index: 22,
        rendered_start: 66,
        rendered_end: 110,
        repeated_header: false,
        cells: [
          tableCell('cell_00000002_r0022_c0001', 22, 1, 'REQ-022', {
            occurrence: ['original', 66, 73]
          }),
          tableCell(selectedId, 22, 2, 'Approved within 2 seconds', {
            occurrence: ['original', 76, 101]
          }),
          tableCell('cell_00000002_r0022_c0003', 22, 3, 'Safety', {
            occurrence: ['original', 104, 110]
          })
        ]
      }
    ]
  })
  return {
    name: 'DOCX large-table second row-group',
    requirement: requirement('req_docx_row_group', source),
    blocks: [
      block(
        blockId,
        'Requirement ID | Acceptance | Owner\n' +
          'REQ-021 | Pending review | QA\n' +
          'REQ-022 | Approved within 2 seconds | Safety',
        'table'
      )
    ],
    tableEvidence
  }
}

export function makePdfTableVisualFixture(): VisualFixture {
  return {
    name: PDF_TABLE_VISUAL_FIXTURE.name,
    requirement: PDF_TABLE_VISUAL_FIXTURE.requirement,
    blocks: PDF_TABLE_VISUAL_FIXTURE.blocks,
    evidenceFingerprint: PDF_TABLE_VISUAL_FIXTURE.evidenceFingerprint,
    tableEvidence: validateVisualTableEvidence(
      {
        ...PDF_TABLE_VISUAL_FIXTURE.tableEvidence,
        run_generation: 1
      }
    )
  }
}

export function makePdfMergedTableVisualFixture(): VisualFixture {
  return {
    name: PDF_MERGED_TABLE_VISUAL_FIXTURE.name,
    requirement: PDF_MERGED_TABLE_VISUAL_FIXTURE.requirement,
    blocks: PDF_MERGED_TABLE_VISUAL_FIXTURE.blocks,
    evidenceFingerprint: PDF_MERGED_TABLE_VISUAL_FIXTURE.evidenceFingerprint,
    tableEvidence: validateVisualTableEvidence(
      {
        ...PDF_MERGED_TABLE_VISUAL_FIXTURE.tableEvidence,
        run_generation: 1
      }
    )
  }
}

export function makePdfContinuationVisualFixture(): VisualFixture {
  return {
    name: PDF_CONTINUATION_VISUAL_FIXTURE.name,
    requirement: PDF_CONTINUATION_VISUAL_FIXTURE.requirement,
    blocks: PDF_CONTINUATION_VISUAL_FIXTURE.blocks,
    evidenceFingerprint: PDF_CONTINUATION_VISUAL_FIXTURE.evidenceFingerprint,
    tableEvidence: validateVisualTableEvidence(
      {
        ...PDF_CONTINUATION_VISUAL_FIXTURE.tableEvidence,
        run_generation: 1
      }
    )
  }
}

export function visualFixture(name: string): VisualFixture {
  switch (name) {
    case 'pdf-0':
      return makePdfVisualFixture(0)
    case 'pdf-90':
      return makePdfVisualFixture(90)
    case 'pdf-180':
      return makePdfVisualFixture(180)
    case 'pdf-270':
      return makePdfVisualFixture(270)
    case 'pdf-table':
      return makePdfTableVisualFixture()
    case 'pdf-merged-table':
      return makePdfMergedTableVisualFixture()
    case 'pdf-table-continuation':
      return makePdfContinuationVisualFixture()
    case 'docx-merged':
      return makeMergedDocxVisualFixture()
    case 'docx-row-group':
      return makeLargeRowGroupVisualFixture()
    default:
      throw new Error(`unknown visual fixture: ${name}`)
  }
}

function requirement(id: string, source: SourceSpan): RequirementIR {
  return {
    id,
    version: 1,
    title: 'Visual evidence acceptance',
    type: 'functional',
    ears_pattern: 'ubiquitous',
    statement: 'The system shall preserve auditable source evidence.',
    subject: 'The system',
    condition: null,
    response: 'preserve auditable source evidence',
    priority: 'must',
    verification_method: 'inspection',
    sources: [source],
    confidence: 1,
    grounding_score: 1,
    review_status: 'pending',
    duplicate_group_id: null,
    possible_duplicate_ids: [],
    derived_from: [],
    tags: ['visual-acceptance'],
    review_log: [],
    metadata: {}
  }
}

function baseSource(overrides: Partial<SourceSpan>): SourceSpan {
  return {
    document_id: 'doc_visual',
    document_name: 'visual-fixture',
    block_id: 'blk_visual',
    quote: 'Evidence',
    match_status: 'PASS_EXACT',
    match_score: 1,
    locator_status: 'PASS_DERIVED',
    locator_score: 1,
    capability_results: [],
    ...overrides
  }
}

function block(
  blockId: string,
  text: string,
  type: DocumentBlock['type'],
  page: number | null = null
): DocumentBlock {
  return {
    block_id: blockId,
    document_id: 'doc_visual',
    type,
    text,
    page,
    section_path: ['Visual acceptance'],
    order: 1,
    metadata: {}
  }
}

function tableCell(
  cellId: string,
  rowIndex: number,
  columnIndex: number,
  text: string,
  options: {
    header?: boolean
    rowSpan?: number
    columnSpan?: number
    occurrence: [
      'original' | 'repeated_header' | 'row_span_projection' | 'duplicate_text_occurrence',
      number,
      number
    ]
  }
) {
  const [occurrenceRole, canonicalStart, canonicalEnd] = options.occurrence
  return {
    cell_id: cellId,
    row_index: rowIndex,
    column_index: columnIndex,
    row_span: options.rowSpan ?? 1,
    column_span: options.columnSpan ?? 1,
    text,
    is_header: options.header ?? false,
    page: null,
    bbox: null,
    occurrences: [
      {
        occurrence_id: `occ_${cellId}_${occurrenceRole}_${canonicalStart}`,
        occurrence_role: occurrenceRole,
        canonical_start: canonicalStart,
        canonical_end: canonicalEnd
      }
    ]
  }
}

export function validateVisualTableEvidence(
  response: TableEvidenceResponse
): TableEvidenceResponse {
  for (const row of response.rows) {
    const occurrences = row.cells.flatMap((cell) => cell.occurrences)
    if (occurrences.length === 0) {
      throw new Error(
        `visual table row ${row.physical_row_index} has no cell occurrences`
      )
    }
    const expectedStart = Math.min(
      ...occurrences.map((occurrence) => occurrence.canonical_start)
    )
    const expectedEnd = Math.max(
      ...occurrences.map((occurrence) => occurrence.canonical_end)
    )
    if (
      row.rendered_start !== expectedStart
      || row.rendered_end !== expectedEnd
    ) {
      throw new Error(
        `visual table row ${row.physical_row_index} range ` +
        `[${row.rendered_start}, ${row.rendered_end}) does not match ` +
        `occurrence range [${expectedStart}, ${expectedEnd})`
      )
    }
  }
  return response
}
