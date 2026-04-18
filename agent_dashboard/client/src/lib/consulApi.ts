/**
 * consulApi.ts — 直连本地 Consul HTTP API（dev mode）
 *
 * 适配 stage-bridge 后端的 KV 路径约定：
 *   workflows/<req_id>/dependencies            ← DAG 定义
 *   workflows/<req_id>/title                   ← 需求标题
 *   workflows/<req_id>/control                 ← PAUSE / RESUME / ABORT
 *   workflows/<req_id>/tasks/<name>/<field>    ← 任务状态与产物
 *
 * Consul dev mode 已通过 start_consul_dev.sh 配置 CORS，浏览器可直连。
 */

import type { Task, TaskStatus, Workflow, WorkflowPhase } from "./mockData";

const CONSUL_BASE =
  (import.meta.env.VITE_CONSUL_URL as string) || "http://127.0.0.1:8500";

interface ConsulKVItem {
  Key: string;
  Value: string | null;
  ModifyIndex: number;
}

async function consulKvList(prefix: string): Promise<ConsulKVItem[]> {
  const url = `${CONSUL_BASE}/v1/kv/${prefix}?recurse=true`;
  const resp = await fetch(url);
  if (resp.status === 404) return [];
  if (!resp.ok) throw new Error(`Consul KV list failed: ${resp.status}`);
  return resp.json();
}

async function consulKvPut(key: string, value: string): Promise<void> {
  const url = `${CONSUL_BASE}/v1/kv/${key}`;
  const resp = await fetch(url, {
    method: "PUT",
    body: value,
  });
  if (!resp.ok) throw new Error(`Consul KV put failed: ${resp.status}`);
}

async function consulKvDelete(key: string): Promise<void> {
  const url = `${CONSUL_BASE}/v1/kv/${key}`;
  const resp = await fetch(url, { method: "DELETE" });
  if (!resp.ok && resp.status !== 404) {
    throw new Error(`Consul KV delete failed: ${resp.status}`);
  }
}

function decodeBase64(s: string | null): string {
  if (!s) return "";
  try {
    return decodeURIComponent(escape(atob(s)));
  } catch {
    return atob(s);
  }
}

/**
 * 将 Consul KV 列表解析为 Workflow[] 结构。
 */
export async function fetchWorkflowsFromConsul(): Promise<Workflow[]> {
  const items = await consulKvList("workflows");
  if (items.length === 0) return [];

  // 按 req_id 聚合
  type Bucket = {
    req_id: string;
    title?: string;
    control?: string;
    created_at?: string;
    dependencies?: Record<string, { type?: string; depends_on?: string[]; service_name?: string | null; description?: string }>;
    tasks: Record<string, Record<string, string>>;
    feedback: Record<string, Record<string, string>>;
    context: Record<string, string>;
  };
  const buckets = new Map<string, Bucket>();

  for (const it of items) {
    const parts = it.Key.split("/");
    if (parts.length < 2 || parts[0] !== "workflows") continue;
    const reqId = parts[1];
    if (!buckets.has(reqId)) {
      buckets.set(reqId, {
        req_id: reqId,
        tasks: {},
        feedback: {},
        context: {},
      });
    }
    const b = buckets.get(reqId)!;
    const value = decodeBase64(it.Value);

    if (parts.length === 3) {
      const field = parts[2];
      if (field === "title") b.title = value;
      else if (field === "control") b.control = value;
      else if (field === "created_at") b.created_at = value;
      else if (field === "dependencies") {
        try {
          b.dependencies = JSON.parse(value);
        } catch {}
      }
    } else if (parts.length >= 5 && parts[2] === "tasks") {
      const name = parts[3];
      const field = parts[4];
      if (!b.tasks[name]) b.tasks[name] = {};
      b.tasks[name][field] = value;
    } else if (parts.length >= 5 && parts[2] === "feedback") {
      const svc = parts[3];
      if (!b.feedback[svc]) b.feedback[svc] = {};
      b.feedback[svc][parts[4]] = value;
    } else if (parts.length >= 3 && parts[2] === "context") {
      const k = parts.slice(3).join("/");
      b.context[k] = value;
    }
  }

  // 转换为前端 Workflow 类型
  const result: Workflow[] = [];
  for (const b of Array.from(buckets.values())) {
    const tasks: Record<string, Task> = {};
    for (const [name, fieldsRaw] of Object.entries(b.tasks)) {
      const fields = fieldsRaw as Record<string, string>;
      const status = (fields.status as TaskStatus) || "PENDING";
      const depsArr =
        b.dependencies?.[name]?.depends_on ?? [];
      tasks[name] = {
        id: name,
        name: fields.description || name,
        status: normalizeStatus(status),
        assigned_agent: fields.assigned_agent || "",
        depends_on: depsArr,
        last_updated: fields.last_updated || fields.activated_at || fields.created_at || "",
        deployed_version: fields.deployed_version,
        health_check_url: fields.health_check_url,
        error_log_url: fields.error_log_url,
        git_commit: fields.commit,
        type: (b.dependencies?.[name]?.type ?? fields.type ?? "backend") as Task["type"],
      };
    }

    result.push({
      id: b.req_id,
      title: b.title || b.req_id,
      phase: derivePhase(tasks, b.control),
      created_at: b.created_at || "",
      tasks,
      artifacts: {
        api_spec: b.context["api_spec_url"],
        test_report: b.context["test_report_url"],
      },
    });
  }

  result.sort((a, b) => a.id.localeCompare(b.id));
  return result;
}

function normalizeStatus(raw: string): TaskStatus {
  const known: TaskStatus[] = [
    "PENDING",
    "IN_PROGRESS",
    "DONE",
    "FAILED",
    "BLOCKED",
  ];
  return known.includes(raw as TaskStatus) ? (raw as TaskStatus) : "PENDING";
}

function derivePhase(
  tasks: Record<string, Task>,
  control: string | undefined
): WorkflowPhase {
  if (control === "PAUSE") return "PAUSED";
  if (control === "ABORT") return "ROLLBACK";

  const arr = Object.values(tasks);
  if (arr.length === 0) return "DESIGN";

  const allDone = arr.every((t) => t.status === "DONE");
  if (allDone) return "DONE";

  const anyFailed = arr.some((t) => t.status === "FAILED");
  if (anyFailed) return "BLOCKED";

  const testTask = arr.find(
    (t) => t.id.startsWith("test") || t.type === "test"
  );
  if (testTask?.status === "IN_PROGRESS") return "TESTING";

  const designTask = arr.find(
    (t) => t.id.startsWith("design") || t.type === "design"
  );
  if (designTask?.status !== "DONE") return "DESIGN";

  const buildDone = arr
    .filter((t) => t.type === "backend" || t.type === "frontend")
    .every((t) => t.status === "DONE");
  if (buildDone) return "TEST_READY";

  return "DEVELOPMENT";
}

/**
 * 写入控制信号到 Consul。
 */
export async function sendControlSignalToConsul(
  reqId: string,
  signal: "PAUSE" | "RESUME" | "ABORT" | "RETRY",
  taskName?: string
): Promise<void> {
  const ctlPath = `workflows/${reqId}/control`;
  if (signal === "RESUME") {
    await consulKvDelete(ctlPath);
    return;
  }
  if (signal === "RETRY") {
    if (!taskName) throw new Error("RETRY requires taskName");
    await consulKvPut(
      `workflows/${reqId}/tasks/${taskName}/status`,
      "PENDING"
    );
    await consulKvDelete(`workflows/${reqId}/tasks/${taskName}/error_message`);
    return;
  }
  await consulKvPut(ctlPath, signal);
}

/**
 * 检测 Consul 是否可达，用于看板启动时的连接探测。
 */
export async function pingConsul(): Promise<boolean> {
  try {
    const resp = await fetch(`${CONSUL_BASE}/v1/status/leader`);
    return resp.ok;
  } catch {
    return false;
  }
}
