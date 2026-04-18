// Mock data simulating Consul KV API responses
// In production, replace fetchWorkflows() with real Consul HTTP API calls:
// GET /v1/kv/workflows/?recurse=true&token=<ACL_TOKEN>

export type TaskStatus = 'PENDING' | 'IN_PROGRESS' | 'DONE' | 'FAILED' | 'BLOCKED';
export type WorkflowPhase =
  | 'DESIGN'
  | 'DEVELOPMENT'
  | 'TEST_READY'
  | 'TESTING'
  | 'DONE'
  | 'BLOCKED'
  | 'PAUSED'
  | 'ROLLBACK';

export interface Task {
  id: string;
  name: string;
  status: TaskStatus;
  assigned_agent: string;
  depends_on: string[];
  last_updated: string;
  deployed_version?: string;
  health_check_url?: string;
  error_log_url?: string;
  screenshot_url?: string;
  git_commit?: string;
  type?: 'design' | 'backend' | 'frontend' | 'test';
}

export interface Workflow {
  id: string;
  title: string;
  phase: WorkflowPhase;
  created_at: string;
  tasks: Record<string, Task>;
  artifacts: {
    api_spec?: string;
    test_report?: string;
  };
}

export const MOCK_WORKFLOWS: Workflow[] = [
  {
    id: 'REQ-2026-001',
    title: '用户订单中心 v2.0',
    phase: 'DEVELOPMENT',
    created_at: '2026-04-17T09:00:00Z',
    artifacts: {
      api_spec: 'https://git.example.com/specs/order-v2/api.yaml',
    },
    tasks: {
      'design-api-doc': {
        id: 'design-api-doc',
        name: 'API 契约设计',
        status: 'DONE',
        assigned_agent: 'agent-design-01',
        depends_on: [],
        last_updated: '2026-04-17T10:30:00Z',
        git_commit: 'a1b2c3d',
        type: 'design',
      },
      'backend-user-service': {
        id: 'backend-user-service',
        name: '用户服务',
        status: 'DONE',
        assigned_agent: 'agent-backend-user-01',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-17T14:20:00Z',
        deployed_version: 'v2.1.0',
        health_check_url: 'http://user-svc.staging:8080/health',
        git_commit: 'b2c3d4e',
        type: 'backend',
      },
      'backend-order-service': {
        id: 'backend-order-service',
        name: '订单服务',
        status: 'IN_PROGRESS',
        assigned_agent: 'agent-backend-order-01',
        depends_on: ['design-api-doc', 'backend-user-service'],
        last_updated: '2026-04-18T08:45:00Z',
        type: 'backend',
      },
      'frontend-checkout': {
        id: 'frontend-checkout',
        name: '结算页面',
        status: 'IN_PROGRESS',
        assigned_agent: 'agent-frontend-01',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-18T09:10:00Z',
        type: 'frontend',
      },
      'test-e2e': {
        id: 'test-e2e',
        name: 'E2E 集成测试',
        status: 'PENDING',
        assigned_agent: 'agent-test-01',
        depends_on: ['backend-user-service', 'backend-order-service', 'frontend-checkout'],
        last_updated: '2026-04-17T09:00:00Z',
        type: 'test',
      },
    },
  },
  {
    id: 'REQ-2026-002',
    title: '支付网关集成',
    phase: 'TESTING',
    created_at: '2026-04-15T08:00:00Z',
    artifacts: {
      api_spec: 'https://git.example.com/specs/payment/api.yaml',
      test_report: 'https://reports.example.com/REQ-2026-002/e2e-report.html',
    },
    tasks: {
      'design-api-doc': {
        id: 'design-api-doc',
        name: 'API 契约设计',
        status: 'DONE',
        assigned_agent: 'agent-design-01',
        depends_on: [],
        last_updated: '2026-04-15T11:00:00Z',
        git_commit: 'c3d4e5f',
        type: 'design',
      },
      'backend-payment-service': {
        id: 'backend-payment-service',
        name: '支付服务',
        status: 'DONE',
        assigned_agent: 'agent-backend-payment-01',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-16T16:00:00Z',
        deployed_version: 'v1.0.3',
        health_check_url: 'http://payment-svc.staging:8080/health',
        git_commit: 'd4e5f6g',
        type: 'backend',
      },
      'frontend-payment': {
        id: 'frontend-payment',
        name: '支付页面',
        status: 'DONE',
        assigned_agent: 'agent-frontend-02',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-16T17:30:00Z',
        git_commit: 'e5f6g7h',
        type: 'frontend',
      },
      'test-e2e': {
        id: 'test-e2e',
        name: 'E2E 集成测试',
        status: 'IN_PROGRESS',
        assigned_agent: 'agent-test-01',
        depends_on: ['backend-payment-service', 'frontend-payment'],
        last_updated: '2026-04-18T09:30:00Z',
        type: 'test',
      },
    },
  },
  {
    id: 'REQ-2026-003',
    title: '消息通知中心',
    phase: 'BLOCKED',
    created_at: '2026-04-16T10:00:00Z',
    artifacts: {},
    tasks: {
      'design-api-doc': {
        id: 'design-api-doc',
        name: 'API 契约设计',
        status: 'DONE',
        assigned_agent: 'agent-design-01',
        depends_on: [],
        last_updated: '2026-04-16T14:00:00Z',
        git_commit: 'f6g7h8i',
        type: 'design',
      },
      'backend-notification-service': {
        id: 'backend-notification-service',
        name: '通知服务',
        status: 'FAILED',
        assigned_agent: 'agent-backend-notif-01',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-17T11:00:00Z',
        error_log_url: 'https://logs.example.com/REQ-2026-003/backend-error.log',
        type: 'backend',
      },
      'frontend-notification': {
        id: 'frontend-notification',
        name: '通知组件',
        status: 'PENDING',
        assigned_agent: 'agent-frontend-03',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-16T10:00:00Z',
        type: 'frontend',
      },
      'test-e2e': {
        id: 'test-e2e',
        name: 'E2E 集成测试',
        status: 'PENDING',
        assigned_agent: 'agent-test-01',
        depends_on: ['backend-notification-service', 'frontend-notification'],
        last_updated: '2026-04-16T10:00:00Z',
        type: 'test',
      },
    },
  },
  {
    id: 'REQ-2026-004',
    title: '数据报表模块',
    phase: 'DONE',
    created_at: '2026-04-10T09:00:00Z',
    artifacts: {
      api_spec: 'https://git.example.com/specs/report/api.yaml',
      test_report: 'https://reports.example.com/REQ-2026-004/e2e-report.html',
    },
    tasks: {
      'design-api-doc': {
        id: 'design-api-doc',
        name: 'API 契约设计',
        status: 'DONE',
        assigned_agent: 'agent-design-01',
        depends_on: [],
        last_updated: '2026-04-10T12:00:00Z',
        type: 'design',
      },
      'backend-report-service': {
        id: 'backend-report-service',
        name: '报表服务',
        status: 'DONE',
        assigned_agent: 'agent-backend-report-01',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-12T15:00:00Z',
        deployed_version: 'v1.2.0',
        type: 'backend',
      },
      'frontend-report': {
        id: 'frontend-report',
        name: '报表页面',
        status: 'DONE',
        assigned_agent: 'agent-frontend-04',
        depends_on: ['design-api-doc'],
        last_updated: '2026-04-12T16:00:00Z',
        type: 'frontend',
      },
      'test-e2e': {
        id: 'test-e2e',
        name: 'E2E 集成测试',
        status: 'DONE',
        assigned_agent: 'agent-test-01',
        depends_on: ['backend-report-service', 'frontend-report'],
        last_updated: '2026-04-13T10:00:00Z',
        type: 'test',
      },
    },
  },
];

// Simulate Consul API fetch with a delay
export async function fetchWorkflows(): Promise<Workflow[]> {
  await new Promise((r) => setTimeout(r, 600));
  return MOCK_WORKFLOWS;
}

export async function sendControlSignal(
  reqId: string,
  signal: 'PAUSE' | 'RESUME' | 'ABORT' | 'RETRY'
): Promise<void> {
  // In production: PUT /v1/kv/workflows/<reqId>/control with value=signal
  await new Promise((r) => setTimeout(r, 400));
  console.log(`[Consul KV] workflows/${reqId}/control = ${signal}`);
}

export const STATUS_CONFIG: Record<
  TaskStatus,
  { label: string; color: string; dotColor: string; bgColor: string }
> = {
  PENDING: {
    label: 'PENDING',
    color: 'text-amber-400',
    dotColor: 'bg-amber-400',
    bgColor: 'bg-amber-400/10 border border-amber-400/20',
  },
  IN_PROGRESS: {
    label: 'IN PROGRESS',
    color: 'text-blue-400',
    dotColor: 'bg-blue-400',
    bgColor: 'bg-blue-400/10 border border-blue-400/20',
  },
  DONE: {
    label: 'DONE',
    color: 'text-emerald-400',
    dotColor: 'bg-emerald-400',
    bgColor: 'bg-emerald-400/10 border border-emerald-400/20',
  },
  FAILED: {
    label: 'FAILED',
    color: 'text-red-400',
    dotColor: 'bg-red-400',
    bgColor: 'bg-red-400/10 border border-red-400/20',
  },
  BLOCKED: {
    label: 'BLOCKED',
    color: 'text-red-400',
    dotColor: 'bg-red-400',
    bgColor: 'bg-red-400/10 border border-red-400/20',
  },
};

export const PHASE_CONFIG: Record<
  WorkflowPhase,
  { label: string; color: string; bgColor: string }
> = {
  DESIGN: {
    label: 'DESIGN',
    color: 'text-slate-300',
    bgColor: 'bg-slate-500/15 border border-slate-500/30',
  },
  DEVELOPMENT: {
    label: 'DEVELOPMENT',
    color: 'text-blue-400',
    bgColor: 'bg-blue-400/10 border border-blue-400/20',
  },
  TEST_READY: {
    label: 'TEST READY',
    color: 'text-violet-400',
    bgColor: 'bg-violet-400/10 border border-violet-400/20',
  },
  TESTING: {
    label: 'TESTING',
    color: 'text-violet-400',
    bgColor: 'bg-violet-400/10 border border-violet-400/20',
  },
  DONE: {
    label: 'DONE',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-400/10 border border-emerald-400/20',
  },
  BLOCKED: {
    label: 'BLOCKED',
    color: 'text-red-400',
    bgColor: 'bg-red-400/10 border border-red-400/20',
  },
  PAUSED: {
    label: 'PAUSED',
    color: 'text-slate-400',
    bgColor: 'bg-slate-400/10 border border-slate-400/20',
  },
  ROLLBACK: {
    label: 'ROLLBACK',
    color: 'text-amber-400',
    bgColor: 'bg-amber-400/10 border border-amber-400/20',
  },
};

export const TASK_TYPE_ICON: Record<string, string> = {
  design: '✦',
  backend: '⬡',
  frontend: '◈',
  test: '◎',
  '': '◇',
};

export const TASK_TYPE_ORDER = ['design', 'backend', 'frontend', 'test'] as const;
