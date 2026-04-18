import { TaskStatus, WorkflowPhase, STATUS_CONFIG, PHASE_CONFIG } from '@/lib/mockData';
import { cn } from '@/lib/utils';

interface TaskStatusBadgeProps {
  status: TaskStatus;
  className?: string;
}

export function TaskStatusBadge({ status, className }: TaskStatusBadgeProps) {
  const config = STATUS_CONFIG[status];
  return (
    <span className={cn('status-badge', config.bgColor, config.color, className)}>
      <span
        className={cn(
          'status-dot',
          config.dotColor,
          status === 'IN_PROGRESS' && 'pulse-dot'
        )}
      />
      {config.label}
    </span>
  );
}

interface PhaseBadgeProps {
  phase: WorkflowPhase;
  className?: string;
}

export function PhaseBadge({ phase, className }: PhaseBadgeProps) {
  const config = PHASE_CONFIG[phase];
  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-1 rounded text-xs font-semibold tracking-wide font-mono',
        config.bgColor,
        config.color,
        className
      )}
    >
      {config.label}
    </span>
  );
}
