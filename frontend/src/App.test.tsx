// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  BlocksResponse,
  ReqIRResponse,
  TaskRunResponse,
  TaskStatusResponse
} from './api/types'
import App from './App'

const api = vi.hoisted(() => ({
  createTask: vi.fn(),
  getBlocks: vi.fn(),
  getReqIR: vi.fn(),
  getTask: vi.fn(),
  reviewRequirement: vi.fn(),
  runTask: vi.fn(),
  uploadDocument: vi.fn()
}))

vi.mock('./api/client', () => ({
  API_BASE_URL: '/api',
  getExportUrl: (taskId: string, filename: string) => (
    `/api/tasks/${taskId}/exports/${filename}`
  ),
  ...api
}))

const EVIDENCE_FINGERPRINT = 'a'.repeat(64)

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('App pipeline run reconciliation', () => {
  it('clears stale review state, reruns the task, and loads rebuilt Evidence', async () => {
    const task = completedTask()
    const pendingRun = deferred<TaskRunResponse>()
    api.getTask
      .mockResolvedValueOnce(task)
      .mockResolvedValueOnce(completedTask(2))
    api.getReqIR
      .mockResolvedValueOnce(reqirPackage('req_old', 'Old parser evidence'))
      .mockResolvedValueOnce(
        reqirPackage('req_new', 'Rebuilt evidence', undefined, 2)
      )
    api.getBlocks
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
      .mockResolvedValueOnce({
        task_id: 'task-1',
        run_generation: 2,
        evidence_fingerprint: EVIDENCE_FINGERPRINT,
        items: [{
          block_id: 'blk_001',
          document_id: 'doc_001',
          type: 'paragraph',
          text: 'Rebuilt evidence',
          section_path: [],
          order: 1,
          metadata: {}
        }]
      } satisfies BlocksResponse)
    api.runTask.mockReturnValueOnce(pendingRun.promise)

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))

    expect(await screen.findByRole('button', {
      name: 'Rerun task'
    })).toBeTruthy()
    expect(screen.getAllByText('Old parser evidence').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Rerun task' }))

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining('deletes existing review decisions')
    )
    expect(api.runTask).toHaveBeenCalledWith('task-1')
    expect(await screen.findByText('No requirements')).toBeTruthy()
    expect(screen.getByText('No source')).toBeTruthy()
    expect(api.getReqIR).toHaveBeenCalledTimes(1)

    await act(async () => {
      pendingRun.resolve({
        task_id: 'task-1',
        status: 'completed',
        run_generation: 2,
        manifest: {
          ...task.manifest!,
          run_generation: 2
        }
      })
    })

    await waitFor(() => {
      expect(api.getReqIR).toHaveBeenCalledTimes(2)
      expect(api.getBlocks).toHaveBeenCalledTimes(2)
    })
    expect(screen.getAllByText('Rebuilt evidence').length).toBeGreaterThan(0)
    expect(screen.queryByText('Old parser evidence')).toBeNull()
  })

  it('disables rerun while another task load is pending', async () => {
    const pendingLoad = deferred<TaskStatusResponse>()
    api.getTask
      .mockResolvedValueOnce(completedTask())
      .mockReturnValueOnce(pendingLoad.promise)
    api.getReqIR.mockResolvedValueOnce(
      reqirPackage('req_old', 'Old parser evidence')
    )
    api.getBlocks.mockRejectedValueOnce({
      code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
      message: 'legacy Evidence must be rebuilt'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    expect(await screen.findByRole('button', {
      name: 'Rerun task'
    })).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-2' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    await waitFor(() => expect(api.getTask).toHaveBeenCalledTimes(2))

    const rerun = screen.getByRole('button', { name: 'Rerun task' })
    expect(rerun.hasAttribute('disabled')).toBe(true)
    fireEvent.click(rerun)
    expect(api.runTask).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()

    await act(async () => {
      pendingLoad.resolve(taskWithStatus('task-2', 'uploaded'))
    })
    await waitFor(() => {
      expect(screen.getByText('task-2')).toBeTruthy()
    })
  })

  it('refreshes the failed task snapshot while preserving the pipeline error', async () => {
    const completed = completedTask()
    const failed = taskWithStatus('task-1', 'failed')
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(failed)
    api.getReqIR.mockResolvedValueOnce(
      reqirPackage('req_old', 'Old parser evidence')
    )
    api.getBlocks.mockRejectedValueOnce({
      code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
      message: 'legacy Evidence must be rebuilt'
    })
    api.runTask.mockRejectedValueOnce({
      code: 'PIPELINE_FAILED',
      message: 'parser rerun failed'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    fireEvent.click(await screen.findByRole('button', {
      name: 'Rerun task'
    }))

    const error = await screen.findByRole('alert')
    expect(error.textContent).toContain('PIPELINE_FAILED')
    expect(error.textContent).toContain('parser rerun failed')
    expect(api.getTask).toHaveBeenCalledTimes(2)
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', {
      name: 'Download reqir.json'
    }).hasAttribute('disabled')).toBe(true)
    expect(screen.queryByRole('link', {
      name: 'Download reqir.json'
    })).toBeNull()
    expect(screen.getByText('No requirements')).toBeTruthy()
    expect(screen.getByText('No source')).toBeTruthy()
  })

  it('restores readable Evidence when the run request fails before reaching the backend', async () => {
    const completed = completedTask()
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(completed)
    api.getReqIR
      .mockResolvedValueOnce(reqirPackage('req_old', 'Existing evidence'))
      .mockResolvedValueOnce(reqirPackage('req_old', 'Existing evidence'))
    api.getBlocks
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
    api.runTask.mockRejectedValueOnce({
      code: 'NETWORK_ERROR',
      message: 'request did not reach the backend'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    fireEvent.click(await screen.findByRole('button', {
      name: 'Rerun task'
    }))

    expect(await screen.findByText('NETWORK_ERROR')).toBeTruthy()
    expect(screen.getByText('request did not reach the backend')).toBeTruthy()
    await waitFor(() => expect(api.getReqIR).toHaveBeenCalledTimes(2))
    expect(screen.getAllByText('Existing evidence').length).toBeGreaterThan(0)
    expect(screen.queryByText('No requirements')).toBeNull()
    expect(screen.getByRole('link', {
      name: 'Download reqir.json'
    })).toBeTruthy()
  })

  it('uses the successful run response when the follow-up task snapshot fails', async () => {
    const completed = completedTask()
    const runResult = completedRun({
      validated_requirements: 2
    })
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockRejectedValueOnce(new Error('task snapshot unavailable'))
    api.getReqIR
      .mockResolvedValueOnce(reqirPackage('req_old', 'Old parser evidence'))
      .mockResolvedValueOnce(
        reqirPackage('req_new', 'Rebuilt evidence', undefined, 2)
      )
    api.getBlocks
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
      .mockResolvedValueOnce({
        task_id: 'task-1',
        run_generation: 2,
        evidence_fingerprint: EVIDENCE_FINGERPRINT,
        items: [{
          block_id: 'blk_001',
          document_id: 'doc_001',
          type: 'paragraph',
          text: 'Rebuilt evidence',
          section_path: [],
          order: 1,
          metadata: {}
        }]
      } satisfies BlocksResponse)
    api.runTask.mockResolvedValueOnce(runResult)

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    fireEvent.click(await screen.findByRole('button', {
      name: 'Rerun task'
    }))

    await waitFor(() => expect(api.getReqIR).toHaveBeenCalledTimes(2))
    expect(screen.getAllByText('Rebuilt evidence').length).toBeGreaterThan(0)
    expect(screen.queryByText('Old parser evidence')).toBeNull()
    expect(metricValue('Validated Requirements')).toBe('2')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('reconciles an ordinary pipeline rerun failure without retaining stale Evidence', async () => {
    const completed = completedTask()
    const failed = taskWithStatus('task-1', 'failed')
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(failed)
    api.getReqIR.mockResolvedValueOnce(
      reqirPackage('req_old', 'Previously exported evidence')
    )
    api.getBlocks.mockResolvedValueOnce(
      blocksResponse(EVIDENCE_FINGERPRINT, 'Previously exported evidence')
    )
    api.runTask.mockRejectedValueOnce({
      code: 'PIPELINE_FAILED',
      message: 'ordinary rerun failed'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))

    expect(
      (await screen.findAllByText('Previously exported evidence')).length
    ).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining('Running the pipeline again deletes')
    )
    const error = await screen.findByRole('alert')
    expect(error.textContent).toContain('PIPELINE_FAILED')
    expect(error.textContent).toContain('ordinary rerun failed')
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0)
    expect(screen.queryByText('Previously exported evidence')).toBeNull()
    expect(screen.getByText('No requirements')).toBeTruthy()
    expect(screen.getByText('No source')).toBeTruthy()
    expect(screen.getByRole('button', {
      name: 'Download reqir.json'
    }).hasAttribute('disabled')).toBe(true)
  })

  it('reports a lost run response as a notice when the generation advances with identical Evidence', async () => {
    const completed = completedTask(1)
    const rerunCompleted = completedTask(2)
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(rerunCompleted)
    api.getReqIR
      .mockResolvedValueOnce(
        reqirPackage('req_old', 'Evidence generation A')
      )
      .mockResolvedValueOnce(
        reqirPackage('req_new', 'Evidence generation B', undefined, 2)
      )
    api.getBlocks
      .mockResolvedValueOnce(
        blocksResponse(EVIDENCE_FINGERPRINT, 'Evidence generation A')
      )
      .mockResolvedValueOnce(
        blocksResponse(EVIDENCE_FINGERPRINT, 'Evidence generation B', 2)
      )
    api.runTask.mockRejectedValueOnce({
      code: 'NETWORK_ERROR',
      message: 'run response was interrupted'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    expect(
      (await screen.findAllByText('Evidence generation A')).length
    ).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(await screen.findByText('RUN_RESPONSE_LOST')).toBeTruthy()
    expect(screen.getByText(
      /Run response was lost, but the task completed successfully/
    )).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText('NETWORK_ERROR')).toBeNull()
    expect(screen.getAllByText('Evidence generation B').length).toBeGreaterThan(0)
    expect(screen.queryByText('Evidence generation A')).toBeNull()
    expect(api.getBlocks).toHaveBeenLastCalledWith(
      'task-1',
      EVIDENCE_FINGERPRINT,
      2
    )
  })

  it('rejects ReqIR from a newer task generation during snapshot loading', async () => {
    api.getTask.mockResolvedValueOnce(completedTask(1))
    api.getReqIR.mockResolvedValueOnce(
      reqirPackage('req_newer', 'Newer generation evidence', undefined, 2)
    )

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('RUN_GENERATION_CHANGED')
    expect(api.getReqIR).toHaveBeenCalledWith('task-1', 1)
    expect(api.getBlocks).not.toHaveBeenCalled()
    expect(screen.getByText('No requirements')).toBeTruthy()
    expect(screen.queryByText('Newer generation evidence')).toBeNull()
  })

  it('reports a lost first-run response as a notice after trusted Evidence loads', async () => {
    const uploaded = taskWithStatus('task-1', 'uploaded', 0)
    const completed = completedTask(1)
    api.getTask
      .mockResolvedValueOnce(uploaded)
      .mockResolvedValueOnce(completed)
    api.getReqIR.mockResolvedValueOnce(
      reqirPackage('req_first', 'First-run evidence')
    )
    api.getBlocks.mockResolvedValueOnce(
      blocksResponse(EVIDENCE_FINGERPRINT, 'First-run evidence')
    )
    api.runTask.mockRejectedValueOnce({
      code: 'NETWORK_ERROR',
      message: 'first run response was interrupted'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    await waitFor(() => expect(api.getTask).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(confirm).not.toHaveBeenCalled()
    expect(await screen.findByText('RUN_RESPONSE_LOST')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getAllByText('First-run evidence').length).toBeGreaterThan(0)
  })

  it('keeps the network error when the run generation does not advance', async () => {
    const completed = completedTask(1)
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(completed)
    api.getReqIR
      .mockResolvedValueOnce(reqirPackage('req_old', 'Unchanged evidence'))
      .mockResolvedValueOnce(reqirPackage('req_old', 'Unchanged evidence'))
    api.getBlocks
      .mockResolvedValueOnce(
        blocksResponse(EVIDENCE_FINGERPRINT, 'Unchanged evidence')
      )
      .mockResolvedValueOnce(
        blocksResponse(EVIDENCE_FINGERPRINT, 'Unchanged evidence')
      )
    api.runTask.mockRejectedValueOnce({
      code: 'NETWORK_ERROR',
      message: 'request did not reach the backend'
    })

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    expect(
      (await screen.findAllByText('Unchanged evidence')).length
    ).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(await screen.findByText('NETWORK_ERROR')).toBeTruthy()
    expect(screen.queryByText('RUN_RESPONSE_LOST')).toBeNull()
    expect(screen.getAllByText('Unchanged evidence').length).toBeGreaterThan(0)
  })

  it('requires confirmation for a status-unavailable task', async () => {
    api.getTask.mockResolvedValueOnce(
      taskWithStatus('task-1', 'status_unavailable', 1)
    )
    vi.mocked(confirm).mockReturnValueOnce(false)

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    await waitFor(() => expect(api.getTask).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(confirm).toHaveBeenCalledWith(
      expect.stringContaining('deletes existing review decisions')
    )
    expect(api.runTask).not.toHaveBeenCalled()
  })

  it('leaves review state unchanged when a destructive rerun is cancelled', async () => {
    const completed = completedTask(1)
    api.getTask.mockResolvedValueOnce(completed)
    api.getReqIR.mockResolvedValueOnce(
      reqirPackageWithSecondRequirement()
    )
    api.getBlocks.mockResolvedValueOnce(
      blocksResponse(
        EVIDENCE_FINGERPRINT,
        'Evidence kept after cancellation'
      )
    )

    render(<App />)
    fireEvent.change(screen.getByLabelText('Task ID'), {
      target: { value: 'task-1' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Load' }))
    expect(
      (await screen.findAllByText('Evidence kept after cancellation')).length
    ).toBeGreaterThan(0)
    const selectedRow = screen.getByText('req_selected').closest('tr')
    expect(selectedRow).not.toBeNull()
    fireEvent.click(selectedRow!)
    expect(selectedRow?.classList.contains('selected-row')).toBe(true)
    const uploadInput = document.querySelector<HTMLInputElement>(
      'input[type="file"]'
    )
    expect(uploadInput).not.toBeNull()
    fireEvent.change(uploadInput!, {
      target: {
        files: [new File(['unsafe'], 'unsupported.txt')]
      }
    })
    expect(await screen.findByText('INVALID_DOCUMENT')).toBeTruthy()
    vi.mocked(confirm).mockReturnValueOnce(false)

    fireEvent.click(screen.getByRole('button', { name: 'Run Pipeline' }))

    expect(api.runTask).not.toHaveBeenCalled()
    expect(api.getReqIR).toHaveBeenCalledTimes(1)
    expect(api.getBlocks).toHaveBeenCalledTimes(1)
    expect(
      screen.getAllByText('Evidence kept after cancellation').length
    ).toBeGreaterThan(0)
    expect(selectedRow?.classList.contains('selected-row')).toBe(true)
    expect(screen.getByText('INVALID_DOCUMENT')).toBeTruthy()
    expect(screen.queryByText('No requirements')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain(
      'only .md, .markdown, .docx, and text-based .pdf files are supported'
    )
    expect(screen.queryByRole('status')).toBeNull()
  })
})

function completedTask(runGeneration = 1): TaskStatusResponse {
  return taskWithStatus('task-1', 'completed', runGeneration)
}

function taskWithStatus(
  taskId: string,
  status: string,
  runGeneration = 1
): TaskStatusResponse {
  return {
    task_id: taskId,
    status,
    run_generation: runGeneration,
    task: {
      task_id: taskId,
      goal: 'Review requirements',
      model_mode: 'mock',
      status,
      run_generation: runGeneration,
      created_at: '2026-07-18T00:00:00Z',
      updated_at: '2026-07-18T00:00:01Z',
      input_document: 'input/original.pdf',
      original_filename: 'requirements.pdf',
      output_dir: `outputs/tasks/${taskId}`,
      pipeline_config: {}
    },
    manifest: {
      task_id: taskId,
      status,
      run_generation: runGeneration,
      input_document: 'input/original.pdf',
      output_dir: `outputs/tasks/${taskId}`,
      model_mode: 'mock',
      started_at: '2026-07-18T00:00:00Z',
      completed_at: '2026-07-18T00:00:01Z',
      counts: {},
      outputs: {},
      error: status === 'failed' ? 'parser rerun failed' : null,
      warning_codes: [],
      zero_result_reason: null
    }
  }
}

function reqirPackage(
  id: string,
  statement: string,
  evidenceFingerprint = EVIDENCE_FINGERPRINT,
  runGeneration = 1
): ReqIRResponse {
  return {
    run_generation: runGeneration,
    package: {
      metadata: {
        evidence_fingerprint: evidenceFingerprint
      },
      items: [{
        id,
        version: 1,
        title: statement,
        type: 'functional',
        ears_pattern: 'ubiquitous',
        statement,
        priority: 'must',
        verification_method: 'inspection',
        sources: [{
          document_id: 'doc_001',
          block_id: 'blk_001',
          quote: statement,
          match_status: 'PASS_EXACT',
          locator_status: 'PASS_DERIVED',
          capability_results: []
        }],
        confidence: 1,
        review_status: 'pending',
        possible_duplicate_ids: [],
        derived_from: [],
        tags: [],
        review_log: [],
        metadata: {}
      }]
    }
  }
}

function blocksResponse(
  evidenceFingerprint: string,
  text: string,
  runGeneration = 1
): BlocksResponse {
  return {
    task_id: 'task-1',
    run_generation: runGeneration,
    evidence_fingerprint: evidenceFingerprint,
    items: [{
      block_id: 'blk_001',
      document_id: 'doc_001',
      type: 'paragraph',
      text,
      section_path: [],
      order: 1,
      metadata: {}
    }]
  }
}

function reqirPackageWithSecondRequirement(): ReqIRResponse {
  const response = reqirPackage(
    'req_kept',
    'Evidence kept after cancellation'
  )
  const packagePayload = response.package
  const first = packagePayload.items[0]
  packagePayload.items.push({
    ...first,
    id: 'req_selected',
    title: 'Selected requirement remains selected',
    statement: 'Selected requirement remains selected',
    sources: [{
      ...first.sources[0],
      quote: 'Selected requirement remains selected'
    }]
  })
  return response
}

function completedRun(
  counts: Record<string, number> = {},
  runGeneration = 2
): TaskRunResponse {
  const task = completedTask(runGeneration)
  return {
    task_id: task.task_id,
    status: 'completed',
    run_generation: runGeneration,
    manifest: {
      ...task.manifest!,
      started_at: '2026-07-18T00:01:00Z',
      completed_at: '2026-07-18T00:01:01Z',
      counts
    }
  }
}

function metricValue(label: string): string | null | undefined {
  const metricLabel = screen.getByText(label, { selector: '.metric span' })
  return metricLabel.parentElement?.querySelector('strong')?.textContent
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}
