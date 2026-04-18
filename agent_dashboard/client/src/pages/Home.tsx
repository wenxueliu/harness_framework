/*
 * Design: Dark Tech Dashboard — Mobile-First Responsive
 * Mobile: Bottom tab nav + drawer sheet for requirement list + full-width DAG + card-based task list
 * Tablet: Collapsible sidebar + main content
 * Desktop: Full three-column layout (sidebar + main + stats panel)
 */

import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  Workflow,
  Task,
  fetchWorkflows as fetchWorkflowsMock,
  sendControlSignal as sendControlSignalMock,
  PHASE_CONFIG,
  TASK_TYPE_ICON,
} from '@/lib/mockData';
import {
  fetchWorkflowsFromConsul,
  sendControlSignalToConsul,
  pingConsul,
} from '@/lib/consulApi';
import { DagGraph } from '@/components/DagGraph';
import { TaskStatusBadge, PhaseBadge } from '@/components/StatusBadge';
import { ControlDialog } from '@/components/ControlDialog';
import { TaskDrawer } from '@/components/TaskDrawer';
import {
  Pause,
  Play,
  XCircle,
  RotateCcw,
  RefreshCw,
  ExternalLink,
  Activity,
  CheckCircle2,
  AlertCircle,
  Clock,
  ChevronRight,
  Menu,
  X,
  LayoutList,
  GitBranch,
  BarChart3,
} from 'lucide-react';
import { cn } from '@/lib/utils';

type ControlSignal = 'PAUSE' | 'RESUME' | 'ABORT' | 'RETRY';
type MobileTab = 'dag' | 'tasks' | 'stats';

// ─── Progress Bar ───────────────────────────────────────────────────────────
function ProgressBar({ tasks }: { tasks: Record<string, Task> }) {
  const all = Object.values(tasks);
  const done = all.filter((t) => t.status === 'DONE').length;
  const failed = all.filter((t) => t.status === 'FAILED' || t.status === 'BLOCKED').length;
  const inProgress = all.filter((t) => t.status === 'IN_PROGRESS').length;
  const pct = Math.round((done / all.length) * 100);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground font-mono">{done}/{all.length} 任务完成</span>
        <span className="text-muted-foreground font-mono">{pct}%</span>
      </div>
      <div className="h-1.5 bg-[oklch(0.22_0.01_264)] rounded-full overflow-hidden">
        <div
          className="h-full bg-emerald-500 rounded-full transition-all duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex gap-3 text-xs text-muted-foreground">
        {inProgress > 0 && (
          <span className="flex items-center gap-1 text-blue-400">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 pulse-dot" />
            {inProgress} 进行中
          </span>
        )}
        {failed > 0 && (
          <span className="flex items-center gap-1 text-red-400">
            <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
            {failed} 失败
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Summary Card ────────────────────────────────────────────────────────────
function SummaryCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
      <div className={cn('p-2 rounded-md flex-shrink-0', color)}>{icon}</div>
      <div>
        <div className="text-2xl font-display font-bold text-foreground leading-none">{value}</div>
        <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
      </div>
    </div>
  );
}

// ─── Mobile Requirement Sheet ────────────────────────────────────────────────
function RequirementSheet({
  open,
  onClose,
  workflows,
  selectedId,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  workflows: Workflow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/60 z-40 md:hidden"
          onClick={onClose}
        />
      )}
      {/* Sheet */}
      <div
        className={cn(
          'fixed top-0 left-0 h-full w-72 bg-sidebar border-r border-border z-50 flex flex-col transition-transform duration-300 ease-in-out md:hidden',
          open ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-sm font-display font-semibold text-foreground">需求列表</span>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground p-1 rounded hover:bg-accent transition-colors"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {workflows.map((wf) => {
            const phaseConf = PHASE_CONFIG[wf.phase];
            const isSelected = wf.id === selectedId;
            const taskList = Object.values(wf.tasks);
            const doneCount = taskList.filter((t) => t.status === 'DONE').length;
            const hasFailure = taskList.some(
              (t) => t.status === 'FAILED' || t.status === 'BLOCKED'
            );

            return (
              <button
                key={wf.id}
                onClick={() => {
                  onSelect(wf.id);
                  onClose();
                }}
                className={cn(
                  'w-full text-left px-4 py-3 transition-colors border-r-2',
                  isSelected
                    ? 'bg-blue-500/10 border-blue-400'
                    : 'hover:bg-accent border-transparent'
                )}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={cn('font-mono text-xs font-medium', isSelected ? 'text-blue-400' : 'text-muted-foreground')}>
                    {wf.id}
                  </span>
                  {hasFailure && <AlertCircle size={11} className="text-red-400" />}
                </div>
                <div className={cn('text-xs font-medium leading-tight mb-1.5 truncate', isSelected ? 'text-foreground' : 'text-foreground/70')}>
                  {wf.title}
                </div>
                <div className="flex items-center justify-between">
                  <span className={cn('text-xs font-mono px-1.5 py-0.5 rounded', phaseConf.bgColor, phaseConf.color)}>
                    {phaseConf.label}
                  </span>
                  <span className="text-xs text-muted-foreground font-mono">
                    {doneCount}/{taskList.length}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

// ─── Mobile Task Card ─────────────────────────────────────────────────────────
function TaskCard({ task, onClick }: { task: Task; onClick: () => void }) {
  const icon = TASK_TYPE_ICON[task.type ?? ''] ?? '◇';
  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-card border border-border rounded-lg p-3 hover:border-blue-500/40 transition-colors active:bg-accent"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-muted-foreground text-sm flex-shrink-0">{icon}</span>
          <div className="min-w-0">
            <div className="text-xs font-medium text-foreground truncate">{task.name}</div>
            <div className="font-mono text-xs text-muted-foreground truncate">{task.assigned_agent}</div>
          </div>
        </div>
        <TaskStatusBadge status={task.status} className="flex-shrink-0" />
      </div>
      {task.depends_on.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {task.depends_on.map((dep) => (
            <span key={dep} className="font-mono text-xs px-1.5 py-0.5 rounded bg-accent text-muted-foreground border border-border/50">
              {dep}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────
export default function Home() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [pendingSignal, setPendingSignal] = useState<ControlSignal | null>(null);
  const [pendingTaskName, setPendingTaskName] = useState<string | null>(null);
  const [dataSource, setDataSource] = useState<'consul' | 'mock'>('mock');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [mobileTab, setMobileTab] = useState<MobileTab>('dag');
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      let data: Workflow[] = [];
      let usedConsul = false;
      try {
        const consulOk = await pingConsul();
        if (consulOk) {
          data = await fetchWorkflowsFromConsul();
          usedConsul = true;
        }
      } catch (e) {
        console.warn('Consul fetch failed, falling back to mock', e);
      }
      if (!usedConsul || data.length === 0) {
        data = await fetchWorkflowsMock();
      }
      setDataSource(usedConsul ? 'consul' : 'mock');
      setWorkflows(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedId]);

  useEffect(() => {
    load();
    const timer = setInterval(() => load(true), 30000);
    return () => clearInterval(timer);
  }, []);

  const selectedWorkflow = workflows.find((w) => w.id === selectedId) ?? null;

  const handleControl = (signal: ControlSignal, taskName?: string) => {
    setPendingSignal(signal);
    setPendingTaskName(taskName ?? null);
    setDialogOpen(true);
  };

  const handleConfirm = async () => {
    if (!pendingSignal || !selectedId) return;
    setDialogOpen(false);
    try {
      if (dataSource === 'consul') {
        await sendControlSignalToConsul(
          selectedId,
          pendingSignal,
          pendingTaskName ?? undefined,
        );
      } else {
        await sendControlSignalMock(selectedId, pendingSignal);
      }
      const labels: Record<ControlSignal, string> = {
        PAUSE: '暂停指令已发送',
        RESUME: '恢复指令已发送',
        ABORT: '中止指令已发送',
        RETRY: '重试指令已发送',
      };
      toast.success(labels[pendingSignal], {
        description: `workflows/${selectedId}/control = ${pendingSignal}`,
      });
    } catch {
      toast.error('指令发送失败，请检查 Consul 连接');
    }
    setPendingSignal(null);
    setPendingTaskName(null);
  };

  const handleTaskClick = (task: Task) => {
    setSelectedTask(task);
    setTaskDrawerOpen(true);
  };

  // Summary stats
  const totalDone = workflows.filter((w) => w.phase === 'DONE').length;
  const totalInProgress = workflows.filter((w) => w.phase === 'DEVELOPMENT' || w.phase === 'TESTING').length;
  const totalBlocked = workflows.filter((w) => w.phase === 'BLOCKED' || w.phase === 'PAUSED').length;
  const totalAgents = new Set(
    workflows.flatMap((w) => Object.values(w.tasks).map((t) => t.assigned_agent))
  ).size;

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-3">
          <div className="w-8 h-8 border-2 border-blue-400 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm text-muted-foreground font-mono">连接 Consul KV...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* ── Top Header ── */}
      <header className="h-12 border-b border-border flex items-center px-3 gap-3 flex-shrink-0 sticky top-0 z-30 bg-background/95 backdrop-blur-sm">
        {/* Mobile: hamburger */}
        <button
          onClick={() => setSheetOpen(true)}
          className="md:hidden text-muted-foreground hover:text-foreground p-1.5 rounded hover:bg-accent transition-colors"
        >
          <Menu size={16} />
        </button>

        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded bg-blue-500/20 border border-blue-500/40 flex items-center justify-center">
            <Activity size={11} className="text-blue-400" />
          </div>
          <span className="font-display font-semibold text-sm text-foreground">
            Agent Dashboard
          </span>
        </div>

        <div className="hidden sm:flex items-center gap-1.5 ml-1">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full pulse-dot',
              dataSource === 'consul' ? 'bg-emerald-400' : 'bg-amber-400',
            )}
          />
          <span className="text-xs text-muted-foreground font-mono">
            {dataSource === 'consul' ? 'Consul · 已连接' : 'Mock · 演示数据'}
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-accent"
          >
            <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} />
            <span className="hidden sm:inline">刷新</span>
          </button>
          <span className="text-xs text-muted-foreground font-mono hidden sm:inline">
            {new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
      </header>

      {/* ── Mobile Requirement Sheet ── */}
      <RequirementSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        workflows={workflows}
        selectedId={selectedId}
        onSelect={(id) => {
          setSelectedId(id);
          setSelectedTask(null);
          setMobileTab('dag');
        }}
      />

      {/* ── Body ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Desktop sidebar */}
        <aside className="hidden md:flex w-56 border-r border-border flex-col flex-shrink-0 overflow-hidden">
          <div className="px-3 py-2.5 border-b border-border">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">需求列表</span>
            <span className="ml-2 text-xs font-mono text-muted-foreground">{workflows.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            {workflows.map((wf) => {
              const phaseConf = PHASE_CONFIG[wf.phase];
              const isSelected = wf.id === selectedId;
              const taskList = Object.values(wf.tasks);
              const doneCount = taskList.filter((t) => t.status === 'DONE').length;
              const hasFailure = taskList.some((t) => t.status === 'FAILED' || t.status === 'BLOCKED');

              return (
                <button
                  key={wf.id}
                  onClick={() => { setSelectedId(wf.id); setSelectedTask(null); }}
                  className={cn(
                    'w-full text-left px-3 py-2.5 transition-colors border-r-2',
                    isSelected ? 'bg-blue-500/10 border-blue-400' : 'hover:bg-accent border-transparent'
                  )}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className={cn('font-mono text-xs font-medium', isSelected ? 'text-blue-400' : 'text-muted-foreground')}>
                      {wf.id}
                    </span>
                    {hasFailure && <AlertCircle size={10} className="text-red-400" />}
                  </div>
                  <div className={cn('text-xs font-medium leading-tight mb-1.5 truncate', isSelected ? 'text-foreground' : 'text-foreground/70')}>
                    {wf.title}
                  </div>
                  <div className="flex items-center justify-between">
                    <span className={cn('text-xs font-mono px-1.5 py-0.5 rounded', phaseConf.bgColor, phaseConf.color)}>
                      {phaseConf.label}
                    </span>
                    <span className="text-xs text-muted-foreground font-mono">{doneCount}/{taskList.length}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {/* ── Main Content ── */}
        <main className="flex-1 flex flex-col overflow-hidden min-w-0">
          {selectedWorkflow ? (
            <>
              {/* Workflow header */}
              <div className="border-b border-border px-3 sm:px-5 py-2.5 flex-shrink-0">
                {/* Title row */}
                <div className="flex items-start gap-2 mb-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      <span className="font-mono text-xs text-muted-foreground">{selectedWorkflow.id}</span>
                      <ChevronRight size={12} className="text-border hidden sm:block" />
                      <PhaseBadge phase={selectedWorkflow.phase} />
                    </div>
                    <h1 className="font-display font-semibold text-sm sm:text-base text-foreground truncate">
                      {selectedWorkflow.title}
                    </h1>
                  </div>
                </div>

                {/* Control buttons — scrollable on mobile */}
                <div className="flex items-center gap-2 overflow-x-auto pb-0.5 scrollbar-hide">
                  {selectedWorkflow.phase === 'BLOCKED' && (
                    <button onClick={() => handleControl('RETRY')}
                      className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition-colors whitespace-nowrap">
                      <RotateCcw size={11} />重试
                    </button>
                  )}
                  {selectedWorkflow.phase === 'PAUSED' ? (
                    <button onClick={() => handleControl('RESUME')}
                      className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-400 hover:bg-blue-500/20 transition-colors whitespace-nowrap">
                      <Play size={11} />恢复
                    </button>
                  ) : (
                    selectedWorkflow.phase !== 'DONE' && (
                      <button onClick={() => handleControl('PAUSE')}
                        className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 hover:bg-amber-500/20 transition-colors whitespace-nowrap">
                        <Pause size={11} />暂停
                      </button>
                    )
                  )}
                  {selectedWorkflow.phase !== 'DONE' && (
                    <button onClick={() => handleControl('ABORT')}
                      className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors whitespace-nowrap">
                      <XCircle size={11} />中止
                    </button>
                  )}
                  {selectedWorkflow.artifacts.api_spec && (
                    <a href={selectedWorkflow.artifacts.api_spec} target="_blank" rel="noopener noreferrer"
                      className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-accent border border-border text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap">
                      <ExternalLink size={11} />API 文档
                    </a>
                  )}
                  {selectedWorkflow.artifacts.test_report && (
                    <a href={selectedWorkflow.artifacts.test_report} target="_blank" rel="noopener noreferrer"
                      className="flex-shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-violet-500/10 border border-violet-500/30 text-violet-400 hover:bg-violet-500/20 transition-colors whitespace-nowrap">
                      <ExternalLink size={11} />测试报告
                    </a>
                  )}
                </div>
              </div>

              {/* ── Mobile Tab Bar ── */}
              <div className="md:hidden flex border-b border-border flex-shrink-0">
                {([
                  { key: 'dag', label: 'DAG 图', icon: <GitBranch size={13} /> },
                  { key: 'tasks', label: '任务列表', icon: <LayoutList size={13} /> },
                  { key: 'stats', label: '概览', icon: <BarChart3 size={13} /> },
                ] as { key: MobileTab; label: string; icon: React.ReactNode }[]).map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setMobileTab(tab.key)}
                    className={cn(
                      'flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors border-b-2',
                      mobileTab === tab.key
                        ? 'text-blue-400 border-blue-400'
                        : 'text-muted-foreground border-transparent hover:text-foreground'
                    )}
                  >
                    {tab.icon}
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* ── Scrollable Content ── */}
              <div className="flex-1 overflow-y-auto">
                <div className="flex h-full">
                  {/* Desktop: left main area */}
                  <div className="flex-1 overflow-y-auto min-w-0">

                    {/* Progress bar — always visible */}
                    <div className={cn('p-3 sm:p-5', 'md:block', mobileTab !== 'dag' && mobileTab !== 'tasks' ? 'hidden md:block' : '')}>
                      <div className="bg-card border border-border rounded-lg p-4">
                        <ProgressBar tasks={selectedWorkflow.tasks} />
                      </div>
                    </div>

                    {/* DAG section */}
                    <div className={cn(
                      'px-3 sm:px-5 pb-3 sm:pb-5',
                      mobileTab === 'dag' ? 'block' : 'hidden md:block'
                    )}>
                      <div className="flex items-center gap-2 mb-3">
                        <h2 className="font-display font-semibold text-sm text-foreground">任务依赖拓扑</h2>
                        <div className="hidden sm:flex items-center gap-3 ml-auto text-xs text-muted-foreground">
                          {Object.entries(TASK_TYPE_ICON).map(([type, icon]) => (
                            <span key={type} className="flex items-center gap-1">
                              <span>{icon}</span>
                              <span className="capitalize">{type}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                      <div className="bg-card border border-border rounded-lg p-3 sm:p-4 overflow-x-auto">
                        <DagGraph tasks={selectedWorkflow.tasks} onTaskClick={handleTaskClick} />
                      </div>
                      <p className="text-xs text-muted-foreground mt-2 text-center md:hidden">
                        左右滑动查看完整图表 · 点击节点查看详情
                      </p>
                    </div>

                    {/* Task list section */}
                    <div className={cn(
                      'px-3 sm:px-5 pb-3 sm:pb-5',
                      mobileTab === 'tasks' ? 'block' : 'hidden md:block'
                    )}>
                      <h2 className="font-display font-semibold text-sm text-foreground mb-3">任务列表</h2>

                      {/* Mobile: card layout */}
                      <div className="md:hidden space-y-2">
                        {Object.values(selectedWorkflow.tasks).map((task) => (
                          <TaskCard key={task.id} task={task} onClick={() => handleTaskClick(task)} />
                        ))}
                      </div>

                      {/* Desktop: table layout */}
                      <div className="hidden md:block bg-card border border-border rounded-lg overflow-hidden">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-border">
                              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">任务</th>
                              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground">状态</th>
                              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden lg:table-cell">执行 Agent</th>
                              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden xl:table-cell">版本 / Commit</th>
                              <th className="text-left px-4 py-2.5 text-xs font-medium text-muted-foreground hidden xl:table-cell">更新时间</th>
                              <th className="px-4 py-2.5 w-8" />
                            </tr>
                          </thead>
                          <tbody>
                            {Object.values(selectedWorkflow.tasks).map((task) => {
                              const isSelected = selectedTask?.id === task.id;
                              const icon = TASK_TYPE_ICON[task.type ?? ''] ?? '◇';
                              return (
                                <tr
                                  key={task.id}
                                  onClick={() => setSelectedTask(isSelected ? null : task)}
                                  className={cn(
                                    'border-b border-border/50 last:border-0 cursor-pointer transition-colors',
                                    isSelected ? 'bg-blue-500/5' : 'hover:bg-accent/50'
                                  )}
                                >
                                  <td className="px-4 py-3">
                                    <div className="flex items-center gap-2">
                                      <span className="text-muted-foreground text-xs">{icon}</span>
                                      <div>
                                        <div className="font-medium text-xs text-foreground">{task.name}</div>
                                        <div className="font-mono text-xs text-muted-foreground">{task.id}</div>
                                      </div>
                                    </div>
                                  </td>
                                  <td className="px-4 py-3"><TaskStatusBadge status={task.status} /></td>
                                  <td className="px-4 py-3 hidden lg:table-cell">
                                    <span className="font-mono text-xs text-muted-foreground">{task.assigned_agent}</span>
                                  </td>
                                  <td className="px-4 py-3 hidden xl:table-cell">
                                    <span className="font-mono text-xs text-muted-foreground">
                                      {task.deployed_version ?? (task.git_commit ? `#${task.git_commit}` : '—')}
                                    </span>
                                  </td>
                                  <td className="px-4 py-3 hidden xl:table-cell">
                                    <span className="font-mono text-xs text-muted-foreground">
                                      {new Date(task.last_updated).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                                    </span>
                                  </td>
                                  <td className="px-4 py-3">
                                    {(task.error_log_url || task.screenshot_url) && (
                                      <AlertCircle size={12} className="text-red-400" />
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Stats section — mobile only tab */}
                    <div className={cn(
                      'px-3 pb-6',
                      mobileTab === 'stats' ? 'block md:hidden' : 'hidden'
                    )}>
                      <h2 className="font-display font-semibold text-sm text-foreground mb-3">全局概览</h2>
                      <div className="grid grid-cols-2 gap-3">
                        <SummaryCard icon={<CheckCircle2 size={14} className="text-emerald-400" />} label="已完成需求" value={totalDone} color="bg-emerald-400/10" />
                        <SummaryCard icon={<Activity size={14} className="text-blue-400" />} label="进行中需求" value={totalInProgress} color="bg-blue-400/10" />
                        <SummaryCard icon={<AlertCircle size={14} className="text-red-400" />} label="阻塞需求" value={totalBlocked} color="bg-red-400/10" />
                        <SummaryCard icon={<Clock size={14} className="text-violet-400" />} label="活跃 Agent" value={totalAgents} color="bg-violet-400/10" />
                      </div>
                    </div>
                  </div>

                  {/* Desktop: right task detail drawer */}
                  {selectedTask && (
                    <div className="hidden md:flex w-64 flex-shrink-0 overflow-hidden border-l border-border">
                      <TaskDrawer task={selectedTask} onClose={() => setSelectedTask(null)} />
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
              请从左侧选择一个需求
            </div>
          )}
        </main>

        {/* Desktop: right stats panel */}
        <aside className="hidden xl:flex w-48 border-l border-border flex-col flex-shrink-0 overflow-y-auto p-3 gap-3">
          <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">全局概览</div>
          <SummaryCard icon={<CheckCircle2 size={14} className="text-emerald-400" />} label="已完成需求" value={totalDone} color="bg-emerald-400/10" />
          <SummaryCard icon={<Activity size={14} className="text-blue-400" />} label="进行中需求" value={totalInProgress} color="bg-blue-400/10" />
          <SummaryCard icon={<AlertCircle size={14} className="text-red-400" />} label="阻塞需求" value={totalBlocked} color="bg-red-400/10" />
          <SummaryCard icon={<Clock size={14} className="text-violet-400" />} label="活跃 Agent" value={totalAgents} color="bg-violet-400/10" />
        </aside>
      </div>

      {/* ── Mobile Task Detail Bottom Sheet ── */}
      <>
        {taskDrawerOpen && selectedTask && (
          <div
            className="fixed inset-0 bg-black/60 z-40 md:hidden"
            onClick={() => setTaskDrawerOpen(false)}
          />
        )}
        <div
          className={cn(
            'fixed bottom-0 left-0 right-0 z-50 bg-[oklch(0.135_0.009_264)] border-t border-border rounded-t-2xl transition-transform duration-300 ease-in-out md:hidden',
            taskDrawerOpen && selectedTask ? 'translate-y-0' : 'translate-y-full'
          )}
          style={{ maxHeight: '75vh' }}
        >
          {/* Drag handle */}
          <div className="flex justify-center pt-3 pb-1">
            <div className="w-10 h-1 rounded-full bg-border" />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 'calc(75vh - 32px)' }}>
            <TaskDrawer
              task={selectedTask}
              onClose={() => setTaskDrawerOpen(false)}
            />
          </div>
        </div>
      </>

      {/* Control dialog */}
      <ControlDialog
        open={dialogOpen}
        signal={pendingSignal}
        reqId={selectedId ?? ''}
        onConfirm={handleConfirm}
        onCancel={() => {
          setDialogOpen(false);
          setPendingSignal(null);
          setPendingTaskName(null);
        }}
      />
    </div>
  );
}
