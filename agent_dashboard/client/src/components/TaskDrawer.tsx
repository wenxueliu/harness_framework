import { Task } from '@/lib/mockData';
import { TaskStatusBadge } from './StatusBadge';
import { X, ExternalLink, GitCommit, Server, FileText, Camera } from 'lucide-react';
import { cn } from '@/lib/utils';

interface TaskDrawerProps {
  task: Task | null;
  onClose: () => void;
}

function MetaRow({
  icon,
  label,
  value,
  href,
  mono,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  href?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-border/50 last:border-0">
      <span className="text-muted-foreground mt-0.5 flex-shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-muted-foreground mb-0.5">{label}</div>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              'text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1 truncate transition-colors',
              mono && 'font-mono text-xs'
            )}
          >
            <span className="truncate">{value}</span>
            <ExternalLink size={10} className="flex-shrink-0" />
          </a>
        ) : (
          <span className={cn('text-sm text-foreground', mono && 'font-mono text-xs')}>
            {value}
          </span>
        )}
      </div>
    </div>
  );
}

export function TaskDrawer({ task, onClose }: TaskDrawerProps) {
  if (!task) return null;

  const lastUpdated = new Date(task.last_updated).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className="flex flex-col h-full bg-[oklch(0.135_0.009_264)] border-l border-border">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="flex-1 min-w-0 pr-3">
          <div className="text-xs text-muted-foreground font-mono mb-1">{task.id}</div>
          <h3 className="font-display font-semibold text-sm text-foreground leading-tight">
            {task.name}
          </h3>
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-accent flex-shrink-0"
        >
          <X size={14} />
        </button>
      </div>

      {/* Status */}
      <div className="px-4 py-3 border-b border-border">
        <TaskStatusBadge status={task.status} />
      </div>

      {/* Meta info */}
      <div className="flex-1 overflow-y-auto px-4 py-2">
        <MetaRow
          icon={<Server size={13} />}
          label="执行 Agent"
          value={task.assigned_agent}
          mono
        />
        <MetaRow
          icon={<FileText size={13} />}
          label="最后更新"
          value={lastUpdated}
        />
        {task.git_commit && (
          <MetaRow
            icon={<GitCommit size={13} />}
            label="Git Commit"
            value={task.git_commit}
            mono
          />
        )}
        {task.deployed_version && (
          <MetaRow
            icon={<Server size={13} />}
            label="部署版本"
            value={task.deployed_version}
            mono
          />
        )}
        {task.health_check_url && (
          <MetaRow
            icon={<Server size={13} />}
            label="健康检查端点"
            value={task.health_check_url}
            href={task.health_check_url}
            mono
          />
        )}
        {task.error_log_url && (
          <MetaRow
            icon={<FileText size={13} />}
            label="错误日志"
            value="查看错误日志 →"
            href={task.error_log_url}
          />
        )}
        {task.screenshot_url && (
          <MetaRow
            icon={<Camera size={13} />}
            label="失败截图"
            value="查看截图 →"
            href={task.screenshot_url}
          />
        )}

        {/* Dependencies */}
        {task.depends_on.length > 0 && (
          <div className="mt-3">
            <div className="text-xs text-muted-foreground mb-2">前置依赖</div>
            <div className="flex flex-wrap gap-1.5">
              {task.depends_on.map((dep) => (
                <span
                  key={dep}
                  className="font-mono text-xs px-2 py-0.5 rounded bg-accent text-accent-foreground border border-border/50"
                >
                  {dep}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
