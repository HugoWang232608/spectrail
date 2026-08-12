import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadExport, getAgentTrace } from './client'


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

describe('generation-bound Agent trace reads', () => {
  it('validates the trace response generation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        schema_version: 'agent_trace_snapshot_v1',
        task_id: 'task agent',
        run_generation: 4,
        events: [],
        attempts: [],
        final_state: {}
      }),
      {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'X-Spectrail-Run-Generation': '4'
        }
      }
    ))
    vi.stubGlobal('fetch', fetchMock)

    const trace = await getAgentTrace('task agent', 4)

    expect(trace.run_generation).toBe(4)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/tasks/task%20agent/agent/trace?expected_run_generation=4'
    )
  })
})
