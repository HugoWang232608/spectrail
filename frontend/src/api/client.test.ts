import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadExport } from './client'


afterEach(() => {
  vi.unstubAllGlobals()
})


describe('generation-bound export downloads', () => {
  it('returns a Blob only after validating the response generation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      new Blob(['reqir'], { type: 'application/json' }),
      {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'X-Spectrail-Run-Generation': '3'
        }
      }
    ))
    vi.stubGlobal('fetch', fetchMock)

    const blob = await downloadExport('task-1', 'reqir.json', 3)

    expect(await blob.text()).toBe('reqir')
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/tasks/task-1/exports/reqir.json?expected_run_generation=3'
    )
  })

  it('preserves RUN_GENERATION_CHANGED as a structured client error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        detail: {
          code: 'RUN_GENERATION_CHANGED',
          message: 'expected task run generation 3, found 4'
        }
      }),
      {
        status: 409,
        headers: { 'Content-Type': 'application/json' }
      }
    )))

    await expect(
      downloadExport('task-1', 'requirements.xlsx', 3)
    ).rejects.toEqual({
      code: 'RUN_GENERATION_CHANGED',
      message: 'expected task run generation 3, found 4'
    })
  })
})
