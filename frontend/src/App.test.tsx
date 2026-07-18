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
})

afterEach(() => {
  cleanup()
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
})

function completedTask(): TaskStatusResponse {
  return {
    task_id: 'task-1',
    status: 'completed',
    task: {
      task_id: 'task-1',
      goal: 'Review requirements',
      model_mode: 'mock',
      status: 'completed',
      created_at: '2026-07-18T00:00:00Z',
      updated_at: '2026-07-18T00:00:01Z',
      input_document: 'input/original.pdf',
      original_filename: 'requirements.pdf',
      output_dir: 'outputs/tasks/task-1',
      pipeline_config: {}
    },
    manifest: {
      task_id: 'task-1',
      status: 'completed',
      input_document: 'input/original.pdf',
      output_dir: 'outputs/tasks/task-1',
      model_mode: 'mock',
      started_at: '2026-07-18T00:00:00Z',
      completed_at: '2026-07-18T00:00:01Z',
      counts: {},
      outputs: {},
      error: null,
      warning_codes: [],
      zero_result_reason: null
    }
  }
}

function reqirPackage(id: string, statement: string): ReqIRPackage {
  return {
    metadata: {
      evidence_fingerprint: EVIDENCE_FINGERPRINT
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}
