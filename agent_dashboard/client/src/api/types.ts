export type TaskStatus = 'PENDING' | 'IN_PROGRESS' | 'DONE' | 'FAILED' | 'BLOCKED' | 'ABORTED' | 'AWAITING_REVIEW' | 'WAITING_FOR_HUMAN' | 'SKIPPED_UPSTREAM_FAILED' | 'UNKNOWN'
export type WorkflowPhase = 'EMPTY' | 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'DESIGN' | 'DEVELOPMENT' | 'TEST_READY' | 'TESTING' | 'BLOCKED' | 'PAUSED' | 'ROLLBACK' | 'UNKNOWN'

export interface SessionEvent { ts: string; agent_id: string; level: 'debug' | 'info' | 'warn' | 'error'; message: string; step_type: string; run_id: string; data?: Record<string, unknown>; seq?: string }
export interface SessionInfo { session_id: string; event_count: number }
export interface TaskSessionEvents { req_id: string; task: string; events: SessionEvent[]; sessions: SessionInfo[] }
export interface Task {
  id: string; name: string; status: TaskStatus; raw_status?: string; assigned_agent: string; depends_on: string[]; last_updated: string
  deployed_version?: string; health_check_url?: string; error_log_url?: string; screenshot_url?: string; git_commit?: string; error_message?: string
  type?: 'design' | 'backend' | 'frontend' | 'test' | string
}
export interface Workflow {
  id: string; title: string; phase: WorkflowPhase; raw_phase?: string; created_at: string; tasks: Record<string, Task>
  artifacts: { api_spec?: string; test_report?: string }
}
export interface CapabilitiesSnapshot {
  features: Record<string, boolean>; permissions: string[]; mode: 'local' | 'trusted-proxy'
  actor: { subject: string; display_name: string }; reasons: Record<string, string>; request_id: string
}

export interface RunSummary { run_id: string; status: string; started_at?: string; finished_at?: string }
export interface RunCreationResult { run: RunSummary; workspace: Record<string, unknown>; request_id: string }
export interface AttemptWorkspaceBinding {
  binding_id: string; req_id: string; run_id: string; task_id: string; attempt_id: string
  workspace_id: string; binding_type: 'RUN_SHARED' | 'ISOLATED'; write_scope: string[]
  base_commit_sha?: string | null; writable: boolean; bound_at: string
}
export interface WorkspaceTreeEntry { name: string; path: string; type: 'file' | 'directory' | 'symlink'; size: number }
export interface WorkspaceFile {
  workspace_id: string; binding_id: string; path: string; size: number; content: string | null
  sha256: string | null; encoding?: string | null; binary: boolean; writable: boolean; reason?: string | null
}
export interface ProjectGroup {
  group_id: string; name: string; description: string; status: 'ACTIVE' | 'ARCHIVED'
  default_workspace_id: string | null; virtual?: boolean; revision: number
}
export interface ProjectWorkspace {
  workspace_id: string; group_id: string; name: string; source_type: 'LOCAL_PATH' | 'GIT_CLONE'
  root_ref: string; access: 'READ_ONLY' | 'READ_WRITE'; status: string; revision: number
}
export interface ProjectGroupMember { subject_id: string; role: 'OWNER' | 'MAINTAINER' | 'DEVELOPER' | 'VIEWER'; revision: number }
export interface WorkspacePreflight { workspace_id: string; exists: boolean; readable: boolean; writable: boolean; git: boolean; head_sha?: string; branch?: string; dirty?: boolean }
export interface EventEnvelope {
  event_id: string; sequence: number; type: string; occurred_at: string
  subject: { group_id?: string; req_id?: string; run_id?: string; task_id?: string; attempt_id?: string }
  actor: { type: string; id: string }; data: Record<string, unknown>
}
export interface WorkspaceAction {
  action_id: string; label: string; description: string; timeout_seconds: number
}
export interface WorkspaceActionResult {
  action_id: string; exit_code: number; stdout: string; stderr: string; event_id: string
}
