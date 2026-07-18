// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  BlocksResponse,
  ReqIRPackage,
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

describe('App legacy Evidence recovery', () => {
  it('clears stale review state, reruns the task, and loads rebuilt Evidence', async () => {
    const task = completedTask()
    const pendingRun = deferred<TaskRunResponse>()
    api.getTask.mockResolvedValue(task)
    api.getReqIR
      .mockResolvedValueOnce(reqirPackage('req_old', 'Old parser evidence'))
      .mockResolvedValueOnce(reqirPackage('req_new', 'Rebuilt evidence'))
    api.getBlocks
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
      .mockResolvedValueOnce({
        task_id: 'task-1',
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
        manifest: task.manifest!
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
      .mockResolvedValueOnce(reqirPackage('req_new', 'Rebuilt evidence'))
    api.getBlocks
      .mockRejectedValueOnce({
        code: 'EVIDENCE_LEGACY_CONTINUATION_REBUILD_REQUIRED',
        message: 'legacy Evidence must be rebuilt'
      })
      .mockResolvedValueOnce({
        task_id: 'task-1',
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

  it('reports a lost run response as a notice after loading changed trusted Evidence', async () => {
    const completed = completedTask()
    const rebuiltFingerprint = 'b'.repeat(64)
    api.getTask
      .mockResolvedValueOnce(completed)
      .mockResolvedValueOnce(completed)
    api.getReqIR
      .mockResolvedValueOnce(
        reqirPackage('req_old', 'Evidence generation A')
      )
      .mockResolvedValueOnce(
        reqirPackage(
          'req_new',
          'Evidence generation B',
          rebuiltFingerprint
        )
      )
    api.getBlocks
      .mockResolvedValueOnce(
        blocksResponse(EVIDENCE_FINGERPRINT, 'Evidence generation A')
      )
      .mockResolvedValueOnce(
        blocksResponse(rebuiltFingerprint, 'Evidence generation B')
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
      rebuiltFingerprint
    )
  })
})

function completedTask(): TaskStatusResponse {
  return taskWithStatus('task-1', 'completed')
}

function taskWithStatus(
  taskId: string,
  status: string
): TaskStatusResponse {
  return {
    task_id: taskId,
    status,
    task: {
      task_id: taskId,
      goal: 'Review requirements',
      model_mode: 'mock',
      status,
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
  evidenceFingerprint = EVIDENCE_FINGERPRINT
): ReqIRPackage {
  return {
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

function blocksResponse(
  evidenceFingerprint: string,
  text: string
): BlocksResponse {
  return {
    task_id: 'task-1',
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

function completedRun(
  counts: Record<string, number> = {}
): TaskRunResponse {
  const task = completedTask()
  return {
    task_id: task.task_id,
    status: 'completed',
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
