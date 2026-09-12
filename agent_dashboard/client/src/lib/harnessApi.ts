/**
 * Harness Framework WebAPI client.
 *
 * The dashboard talks to the daemon on port 8080 instead of reading Consul
 * directly. This keeps the UI working with Consul, --local, and --local-file.
 */
import type {
  Task,
  TaskSessionEvents,
  TaskStatus,
  Workflow,
  WorkflowPhase,
} from "./mockData";

const HARNESS_API =
  (import.meta.env.VITE_HARNESS_API as string) || "http://127.0.0.1:8080";

interface WorkflowSummary {
  req_id: string;
  title?: string;
  control?: string;
}

interface WorkflowListResponse {
  workflows: WorkflowSummary[];
}

interface TaskDefinition {
  type?: string;
  depends_on?: string[];
}

interface WorkflowDetail {
  req_id: string;
  control?: string;
  dependencies?: Record<string, TaskDefinition>;
  tasks?: Record<string, Record<string, string>>;
  context?: Record<string, string>;
}

export interface HumanMessage {
  message_id: string;
  message: string;
  actor: string;
  mode: "queue" | "interrupt";
  status: "PENDING" | "PROCESSING" | "APPLIED" | "INTERRUPTED" | "FAILED";
  created_at: string;
  started_at?: string;
  finished_at?: string;
  response?: string;
  error?: string;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${HARNESS_API}${path}`);
  if (!response.ok) {
    throw new Error(`Harness API ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function normalizeStatus(raw: string): TaskStatus {
  if (["PENDING", "IN_PROGRESS", "DONE", "FAILED", "BLOCKED"].includes(raw)) {
    return raw as TaskStatus;
  }
  if (raw === "ABORTED") return "FAILED";
  if (["AWAITING_REVIEW", "WAITING_FOR_HUMAN"].includes(raw)) return "BLOCKED";
  return "PENDING";
}

function derivePhase(
  tasks: Record<string, Task>,
  control: string | undefined,
): WorkflowPhase {
  if (control === "PAUSE") return "PAUSED";
  if (control === "ABORT") return "ROLLBACK";

  const values = Object.values(tasks);
  if (values.length === 0) return "DESIGN";
  if (values.every((task) => task.status === "DONE")) return "DONE";
  if (values.some((task) => task.status === "FAILED")) return "BLOCKED";

  const testTask = values.find(
    (task) => task.id.startsWith("test") || task.type === "test",
  );
  if (testTask?.status === "IN_PROGRESS") return "TESTING";

  const designTask = values.find(
    (task) => task.id.startsWith("design") || task.type === "design",
  );
  if (designTask && designTask.status !== "DONE") return "DESIGN";

  const buildTasks = values.filter(
    (task) => task.type === "backend" || task.type === "frontend",
  );
  if (buildTasks.length > 0 && buildTasks.every((task) => task.status === "DONE")) {
    return "TEST_READY";
  }
  return "DEVELOPMENT";
}

function toWorkflow(summary: WorkflowSummary, detail: WorkflowDetail): Workflow {
  const definitions = detail.dependencies ?? {};
  const taskRecords = detail.tasks ?? {};
  const tasks: Record<string, Task> = {};

  for (const [name, fields] of Object.entries(taskRecords)) {
    const definition = definitions[name] ?? {};
    tasks[name] = {
      id: name,
      name: fields.description || name,
      status: normalizeStatus(fields.status || "PENDING"),
      assigned_agent: fields.assigned_agent || "",
      depends_on: definition.depends_on ?? [],
      last_updated:
        fields.completed_at || fields.last_updated || fields.activated_at || fields.created_at || "",
      deployed_version: fields.deployed_version,
      health_check_url: fields.health_check_url,
      error_log_url: fields.error_log_url,
      git_commit: fields.commit,
      type: (definition.type || fields.type || "backend") as Task["type"],
    };
  }

  const createdAt = Object.values(taskRecords)
    .map((fields) => fields.created_at)
    .filter(Boolean)
    .sort()[0] || "";
  const control = detail.control || summary.control;
  const context = detail.context ?? {};

  return {
    id: detail.req_id,
    title: summary.title || detail.req_id,
    phase: derivePhase(tasks, control),
    created_at: createdAt,
    tasks,
    artifacts: {
      api_spec: context.api_spec_url,
      test_report: context.test_report_url,
    },
  };
}

export async function fetchWorkflowsFromHarness(): Promise<Workflow[]> {
  const listing = await getJson<WorkflowListResponse>("/api/workflows");
  const workflows = await Promise.all(
    listing.workflows.map(async (summary) => {
      const detail = await getJson<WorkflowDetail>(
        `/api/workflow/${encodeURIComponent(summary.req_id)}`,
      );
      return toWorkflow(summary, detail);
    }),
  );
  return workflows.sort((a, b) => a.id.localeCompare(b.id));
}

export async function sendControlSignalToHarness(
  reqId: string,
  signal: "PAUSE" | "RESUME" | "ABORT" | "RETRY",
  taskName?: string,
): Promise<void> {
  const response = await fetch(
    `${HARNESS_API}/api/workflow/${encodeURIComponent(reqId)}/control`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: signal, task_name: taskName || "" }),
    },
  );
  if (!response.ok) {
    throw new Error(`Harness control API failed: ${response.status}`);
  }
}

export async function pingHarness(): Promise<boolean> {
  try {
    const response = await fetch(`${HARNESS_API}/api/health`);
    return response.ok;
  } catch {
    return false;
  }
}

export async function fetchTaskSessionEvents(
  reqId: string,
  taskName: string,
): Promise<TaskSessionEvents> {
  const response = await fetch(
    `${HARNESS_API}/api/sessions/${encodeURIComponent(reqId)}/${encodeURIComponent(taskName)}?limit=200`,
  );
  if (response.status === 404) {
    return { req_id: reqId, task: taskName, events: [], sessions: [] };
  }
  if (!response.ok) {
    throw new Error(`Sessions API failed: ${response.status}`);
  }
  return response.json() as Promise<TaskSessionEvents>;
}

export async function fetchTaskMessages(
  reqId: string,
  taskName: string,
): Promise<HumanMessage[]> {
  const response = await fetch(
    `${HARNESS_API}/api/workflow/${encodeURIComponent(reqId)}/task/${encodeURIComponent(taskName)}/messages`,
  );
  if (!response.ok) {
    throw new Error(`Task messages API failed: ${response.status}`);
  }
  const payload = await response.json() as { messages: HumanMessage[] };
  return payload.messages;
}

export async function sendTaskMessage(
  reqId: string,
  taskName: string,
  input: { message: string; actor: string; mode: "queue" | "interrupt" },
): Promise<HumanMessage> {
  const response = await fetch(
    `${HARNESS_API}/api/workflow/${encodeURIComponent(reqId)}/task/${encodeURIComponent(taskName)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(payload.error || `Send task message failed: ${response.status}`);
  }
  const payload = await response.json() as { message: HumanMessage };
  return payload.message;
}
