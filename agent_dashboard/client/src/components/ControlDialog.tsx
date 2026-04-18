import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

type ControlSignal = 'PAUSE' | 'RESUME' | 'ABORT' | 'RETRY';

interface ControlDialogProps {
  open: boolean;
  signal: ControlSignal | null;
  reqId: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const SIGNAL_CONFIG: Record<
  ControlSignal,
  { title: string; description: string; confirmLabel: string; confirmClass: string }
> = {
  PAUSE: {
    title: '暂停需求执行',
    description:
      '所有 Agent 将在完成当前原子操作后暂停，任务状态保持不变。可通过 RESUME 指令恢复执行。',
    confirmLabel: '确认暂停',
    confirmClass: 'bg-amber-500 hover:bg-amber-600 text-white',
  },
  RESUME: {
    title: '恢复需求执行',
    description: '已暂停的 Agent 将重新开始轮询并继续执行剩余任务。',
    confirmLabel: '确认恢复',
    confirmClass: 'bg-blue-500 hover:bg-blue-600 text-white',
  },
  ABORT: {
    title: '中止并回退需求',
    description:
      '⚠️ 危险操作：所有 Agent 将执行 cleanup，删除已部署的 K8s 资源并注销 Consul 服务。数据库变更将执行 Migration Down 回滚。此操作不可撤销。',
    confirmLabel: '确认中止',
    confirmClass: 'bg-red-600 hover:bg-red-700 text-white',
  },
  RETRY: {
    title: '重试失败任务',
    description: '将所有 FAILED 状态的任务重置为 PENDING，Agent 将重新尝试执行。',
    confirmLabel: '确认重试',
    confirmClass: 'bg-emerald-600 hover:bg-emerald-700 text-white',
  },
};

export function ControlDialog({
  open,
  signal,
  reqId,
  onConfirm,
  onCancel,
}: ControlDialogProps) {
  if (!signal) return null;
  const config = SIGNAL_CONFIG[signal];

  return (
    <AlertDialog open={open}>
      <AlertDialogContent className="bg-[oklch(0.16_0.01_264)] border border-[oklch(0.28_0.01_264)] text-foreground max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="font-display text-base text-foreground">
            {config.title}
          </AlertDialogTitle>
          <AlertDialogDescription className="text-sm text-muted-foreground leading-relaxed">
            <span className="font-mono text-xs text-blue-400 block mb-2">{reqId}</span>
            {config.description}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel
            onClick={onCancel}
            className="bg-transparent border-border text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            取消
          </AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} className={config.confirmClass}>
            {config.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
