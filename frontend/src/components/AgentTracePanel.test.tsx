// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { AgentTraceSnapshot, TaskStatusResponse } from '../api/types'
import AgentTracePanel from './AgentTracePanel'

afterEach(cleanup)

describe('AgentTracePanel', () => {
  it('renders immutable events and attempts as read-only details', () => {
    render(
      <AgentTracePanel
        task={agentTask()}
        trace={traceSnapshot()}
        error={null}
        loading={false}
      />
    )

    expect(screen.getByRole('heading', { name: 'Agent Trace' })).toBeTruthy()
    expect(screen.getByText('tool_result')).toBeTruthy()
    expect(screen.getByText('Attempt 1')).toBeTruthy()
    expect(screen.getByText('run_requirement_extraction')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()

    fireEvent.click(screen.getByText('Payload'))
    expect(screen.getByText(/validated_requirements/)).toBeTruthy()
  })

  it('explains that fixed orchestration has no Agent trace', () => {
    const task = agentTask()
    task.manifest!.orchestration = { mode: 'fixed' }

    render(
      <AgentTracePanel
        task={task}
        trace={null}
        error={null}
        loading={false}
      />
    )

    expect(screen.getByText('Fixed orchestration has no Agent trace.')).toBeTruthy()
  })
})

function agentTask(): TaskStatusResponse {
  return {
    task_id: 'task-1',
    status: 'completed',
    run_generation: 1,
    task: {
      task_id: 'task-1',
      goal: 'extract_requirements',
      model_mode: 'mock',
      status: 'completed',
      run_generation: 1,
      created_at: '2026-08-12T00:00:00Z',
      updated_at: '2026-08-12T00:00:01Z',
      input_document: 'input/original.md',
      original_filename: 'sample.md',
      output_dir: 'outputs/tasks/task-1',
      pipeline_config: { orchestration_mode: 'agent' }
    },
    manifest: {
      task_id: 'task-1',
      run_generation: 1,
      status: 'completed',
      input_document: 'input/original.md',
      output_dir: 'outputs/tasks/task-1',
      model_mode: 'mock',
      started_at: '2026-08-12T00:00:00Z',
      completed_at: '2026-08-12T00:00:01Z',
      counts: {},
      outputs: {},
      error: null,
      warning_codes: [],
      zero_result_reason: null,
      orchestration: { mode: 'agent', outcome: 'completed' }
    }
  }
}

function traceSnapshot(): AgentTraceSnapshot {
  return {
    schema_version: 'agent_trace_snapshot_v1',
    task_id: 'task-1',
    run_generation: 1,
    events: [{
      schema_version: 'agent_trace_event_v1',
      sequence: 1,
      run_generation: 1,
      event_type: 'tool_result',
      step: 1,
      planner_request_fingerprint: null,
      tool: 'run_requirement_extraction',
      payload: { validated_requirements: 14 },
      created_at: '2026-08-12T00:00:00Z'
    }],
    attempts: [{
      schema_version: 'agent_attempt_summary_v1',
      run_generation: 1,
      attempt: 1,
      arguments: { chunking_mode: 'auto' },
      pipeline_status: 'completed',
      warning_codes: [],
      counts: { validated_requirements: 14 },
      error_code: null,
      started_at: '2026-08-12T00:00:00Z',
      completed_at: '2026-08-12T00:00:01Z'
    }],
    final_state: {
      schema_version: 'agent_final_state_v1',
      task_id: 'task-1',
      run_generation: 1,
      outcome: 'completed',
      steps_used: 2,
      planner_calls: 2,
      tool_invocations: 2,
      pipeline_attempts: 1,
      final_pipeline_status: 'completed',
      reason: 'Completed.'
    }
  }
}
