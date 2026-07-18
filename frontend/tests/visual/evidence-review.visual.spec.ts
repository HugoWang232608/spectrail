import { expect, test } from '@playwright/test'
import type { Locator } from '@playwright/test'
import path from 'node:path'

import {
  makeLargeRowGroupVisualFixture,
  makeMergedDocxVisualFixture,
  makePdfMergedTableVisualFixture,
  makePdfTableVisualFixture,
  makePdfVisualFixture,
  VISUAL_EVIDENCE_FINGERPRINT
} from '../../src/visualFixtures'

const ROTATIONS = [0, 90, 180, 270] as const

test.beforeEach(({ page }) => {
  expect(page.viewportSize()).toEqual({ width: 1180, height: 940 })
})

for (const rotation of ROTATIONS) {
  test(`PDF ${rotation}° preview aligns the validated locator`, async ({ page }) => {
    const fixture = makePdfVisualFixture(rotation)
    const locator = fixture.requirement.sources[0].page_locator
    if (!locator) {
      throw new Error('PDF visual fixture must provide a page locator')
    }
    await page.route('**/api/tasks/**/pages/**/preview.png**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        headers: {
          'X-Spectrail-Evidence-Fingerprint': VISUAL_EVIDENCE_FINGERPRINT
        },
        body: previewSvg(
          locator.page_width,
          locator.page_height,
          locator.bbox.x0,
          locator.bbox.y0,
          locator.bbox.x1,
          locator.bbox.y1,
          rotation
        )
      })
    })

    await page.goto(`/visual.html?fixture=pdf-${rotation}`)
    const preview = page.locator('.page-preview')
    const image = preview.locator('img')
    const overlay = preview.locator('.page-locator-overlay')
    await expect(image).toBeVisible()
    await expect(overlay).toBeVisible()
    await image.evaluate((element: HTMLImageElement) => {
      if (!element.complete) {
        throw new Error('preview image is not decoded')
      }
    })

    const previewBox = await preview.boundingBox()
    const overlayBox = await overlay.boundingBox()
    expect(previewBox).not.toBeNull()
    expect(overlayBox).not.toBeNull()
    if (previewBox && overlayBox) {
      expectPixelClose(
        overlayBox.x - previewBox.x,
        (locator.bbox.x0 / locator.page_width) * previewBox.width
      )
      expectPixelClose(
        overlayBox.y - previewBox.y,
        (locator.bbox.y0 / locator.page_height) * previewBox.height
      )
      expectPixelClose(
        overlayBox.width,
        ((locator.bbox.x1 - locator.bbox.x0) / locator.page_width) * previewBox.width
      )
      expectPixelClose(
        overlayBox.height,
        ((locator.bbox.y1 - locator.bbox.y0) / locator.page_height) * previewBox.height
      )
    }

    await expectLinuxScreenshot(
      page.locator('.visual-harness'),
      `pdf-rotation-${rotation}.png`
    )
  })
}

test('DOCX merged cells render row-span projection and selected anchors', async ({ page }) => {
  const fixture = makeMergedDocxVisualFixture()
  await routeTableEvidence(page, fixture.tableEvidence)
  await page.goto('/visual.html?fixture=docx-merged')

  const grid = page.getByRole('grid', { name: /Table evidence/ })
  await expect(grid).toBeVisible()
  await expect(grid.getByText(/row_span_projection/)).toBeVisible()
  await expect(grid.locator('[aria-selected="true"]')).toHaveCount(2)
  await expectLinuxScreenshot(
    page.locator('.visual-harness'),
    'docx-merged-cells.png'
  )
})

test('DOCX large-table row-group renders repeated header and selected primary row', async ({ page }) => {
  const fixture = makeLargeRowGroupVisualFixture()
  await routeTableEvidence(page, fixture.tableEvidence)
  await page.goto('/visual.html?fixture=docx-row-group')

  const grid = page.getByRole('grid', { name: /Table evidence/ })
  await expect(grid).toBeVisible()
  await expect(grid.getByText('repeated header')).toBeVisible()
  await expect(grid.getByText('Row 22')).toBeVisible()
  await expect(grid.locator('[aria-selected="true"]')).toHaveCount(1)
  await expectLinuxScreenshot(
    page.locator('.visual-harness'),
    'docx-large-row-group.png'
  )
})

test('PDF table renders validated page region and structured cell grid', async ({ page }) => {
  const fixture = makePdfTableVisualFixture()
  const locator = fixture.requirement.sources[0].page_locator
  if (!locator) {
    throw new Error('PDF table visual fixture must provide a page locator')
  }
  await page.route('**/api/tasks/**/pages/**/preview.png**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      headers: {
        'X-Spectrail-Evidence-Fingerprint': VISUAL_EVIDENCE_FINGERPRINT
      },
      path: path.resolve('tests/visual/fixtures/pdf-table-page.png')
    })
  })
  await routeTableEvidence(page, fixture.tableEvidence)
  await page.goto('/visual.html?fixture=pdf-table')

  const preview = page.locator('.page-preview')
  await expect(preview.locator('img')).toBeVisible()
  await expect(preview.locator('.page-locator-overlay')).toBeVisible()
  const grid = page.getByRole('grid', { name: /Table evidence/ })
  await expect(grid).toBeVisible()
  await expect(grid.getByRole('columnheader')).toHaveCount(3)
  await expect(grid.getByText('Approved within 2 seconds')).toBeVisible()
  await expect(grid.locator('[aria-selected="true"]')).toHaveCount(2)
  await expectLinuxScreenshot(
    page.locator('.visual-harness'),
    'pdf-table-structured-evidence.png'
  )
})

test('PDF merged table renders row-span projection with page evidence', async ({ page }) => {
  const fixture = makePdfMergedTableVisualFixture()
  const locator = fixture.requirement.sources[0].page_locator
  if (!locator || !fixture.evidenceFingerprint) {
    throw new Error('PDF merged-table fixture must provide trusted page evidence')
  }
  await page.route('**/api/tasks/**/pages/**/preview.png**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      headers: {
        'X-Spectrail-Evidence-Fingerprint': fixture.evidenceFingerprint ?? ''
      },
      path: path.resolve('tests/visual/fixtures/pdf-merged-table-page.png')
    })
  })
  await routeTableEvidence(page, fixture.tableEvidence)
  await page.goto('/visual.html?fixture=pdf-merged-table')

  const preview = page.locator('.page-preview')
  await expect(preview.locator('img')).toBeVisible()
  await expect(preview.locator('.page-locator-overlay')).toBeVisible()
  const grid = page.getByRole('grid', { name: /Table evidence/ })
  await expect(grid).toBeVisible()
  await expect(grid.getByText(/row_span_projection/)).toBeVisible()
  await expect(grid.getByText(/row span 2/).first()).toBeVisible()
  await expect(grid.locator('[aria-selected="true"]')).toHaveCount(2)
  await expectLinuxScreenshot(
    page.locator('.visual-harness'),
    'pdf-merged-table-structured-evidence.png'
  )
})

async function expectLinuxScreenshot(
  locator: Locator,
  snapshotName: string
): Promise<void> {
  if (process.platform !== 'linux') {
    return
  }
  await expect(locator).toHaveScreenshot(snapshotName)
}

function expectPixelClose(actual: number, expected: number): void {
  // The preview's one-pixel border participates in getBoundingClientRect(),
  // while absolutely positioned locator percentages use its padding box.
  expect(Math.abs(actual - expected)).toBeLessThanOrEqual(1.5)
}

async function routeTableEvidence(
  page: import('@playwright/test').Page,
  response: ReturnType<typeof makeMergedDocxVisualFixture>['tableEvidence']
) {
  if (!response) {
    throw new Error('table visual fixture must provide table evidence')
  }
  await page.route('**/api/tasks/**/tables/**/evidence**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response)
    })
  })
}

function previewSvg(
  width: number,
  height: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  rotation: number
): string {
  const targetWidth = x1 - x0
  const targetHeight = y1 - y0
  return `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
         viewBox="0 0 ${width} ${height}">
      <rect width="${width}" height="${height}" fill="#ffffff"/>
      <rect x="8" y="8" width="${width - 16}" height="${height - 16}"
            rx="4" fill="#fafafa" stroke="#d0d5dd"/>
      <rect x="18" y="19" width="${Math.min(width * 0.46, 126)}" height="7"
            rx="2" fill="#475467"/>
      <rect x="18" y="29" width="${Math.min(width * 0.27, 74)}" height="3"
            rx="1.5" fill="#98a2b3"/>
      <line x1="18" y1="36" x2="${width - 18}" y2="36" stroke="#e4e7ec"/>
      <rect x="${x0}" y="${y0}" width="${targetWidth}" height="${targetHeight}"
            rx="2" fill="#fff4e5" stroke="#f79009" stroke-width="1"/>
      <rect x="${x0 + 5}" y="${y0 + 8}" width="${Math.max(8, targetWidth - 18)}"
            height="4" rx="2" fill="#b54708"/>
      <rect x="${x0 + 5}" y="${y0 + 16}" width="${Math.max(8, targetWidth * 0.62)}"
            height="3" rx="1.5" fill="#dc6803"/>
      <circle cx="${width - 22}" cy="${height - 20}" r="4"
              fill="${rotation === 0 || rotation === 180 ? '#98a2b3' : '#667085'}"/>
    </svg>
  `
}
