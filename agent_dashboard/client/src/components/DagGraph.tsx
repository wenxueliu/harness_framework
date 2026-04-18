import { useMemo } from 'react';
import { Task, STATUS_CONFIG, TASK_TYPE_ICON } from '@/lib/mockData';
import { cn } from '@/lib/utils';

interface DagGraphProps {
  tasks: Record<string, Task>;
  onTaskClick?: (task: Task) => void;
}

interface NodePosition {
  x: number;
  y: number;
  task: Task;
}

// Topological sort to determine column (level) for each node
function computeLevels(tasks: Record<string, Task>): Record<string, number> {
  const levels: Record<string, number> = {};
  const visited = new Set<string>();

  function dfs(id: string): number {
    if (visited.has(id)) return levels[id] ?? 0;
    visited.add(id);
    const task = tasks[id];
    if (!task || task.depends_on.length === 0) {
      levels[id] = 0;
      return 0;
    }
    const maxDep = Math.max(...task.depends_on.map((dep) => dfs(dep)));
    levels[id] = maxDep + 1;
    return levels[id];
  }

  Object.keys(tasks).forEach((id) => dfs(id));
  return levels;
}

export function DagGraph({ tasks, onTaskClick }: DagGraphProps) {
  const NODE_W = 160;
  const NODE_H = 64;
  const COL_GAP = 80;
  const ROW_GAP = 24;
  const PAD_X = 24;
  const PAD_Y = 24;

  const { positions, svgWidth, svgHeight } = useMemo(() => {
    const levels = computeLevels(tasks);
    const maxLevel = Math.max(...Object.values(levels));

    // Group tasks by level
    const byLevel: Record<number, string[]> = {};
    Object.entries(levels).forEach(([id, lvl]) => {
      if (!byLevel[lvl]) byLevel[lvl] = [];
      byLevel[lvl].push(id);
    });

    // Sort within each level by type order
    const typeOrder = ['design', 'backend', 'frontend', 'test'];
    Object.values(byLevel).forEach((ids) => {
      ids.sort((a, b) => {
        const ta = typeOrder.indexOf(tasks[a]?.type ?? '');
        const tb = typeOrder.indexOf(tasks[b]?.type ?? '');
        return ta - tb;
      });
    });

    const positions: Record<string, NodePosition> = {};
    for (let lvl = 0; lvl <= maxLevel; lvl++) {
      const ids = byLevel[lvl] ?? [];
      ids.forEach((id, i) => {
        positions[id] = {
          x: PAD_X + lvl * (NODE_W + COL_GAP),
          y: PAD_Y + i * (NODE_H + ROW_GAP),
          task: tasks[id],
        };
      });
    }

    const maxRows = Math.max(...Object.values(byLevel).map((ids) => ids.length));
    const svgWidth = PAD_X * 2 + (maxLevel + 1) * (NODE_W + COL_GAP) - COL_GAP;
    const svgHeight = PAD_Y * 2 + maxRows * (NODE_H + ROW_GAP) - ROW_GAP;

    return { positions, svgWidth, svgHeight };
  }, [tasks]);

  // Build edges
  const edges = useMemo(() => {
    const result: { from: string; to: string }[] = [];
    Object.entries(tasks).forEach(([id, task]) => {
      task.depends_on.forEach((dep) => {
        if (positions[dep] && positions[id]) {
          result.push({ from: dep, to: id });
        }
      });
    });
    return result;
  }, [tasks, positions]);

  const getNodeBorderColor = (status: Task['status']) => {
    const map: Record<string, string> = {
      DONE: '#34d399',
      IN_PROGRESS: '#60a5fa',
      PENDING: '#fbbf24',
      FAILED: '#f87171',
      BLOCKED: '#f87171',
    };
    return map[status] ?? '#4b5563';
  };

  return (
    <div className="overflow-x-auto overflow-y-auto">
      <svg
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="block"
      >
        {/* Edges */}
        {edges.map(({ from, to }) => {
          const src = positions[from];
          const dst = positions[to];
          if (!src || !dst) return null;
          const x1 = src.x + NODE_W;
          const y1 = src.y + NODE_H / 2;
          const x2 = dst.x;
          const y2 = dst.y + NODE_H / 2;
          const mx = (x1 + x2) / 2;
          const isDone = src.task.status === 'DONE';
          return (
            <path
              key={`${from}-${to}`}
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
              className={isDone ? 'dag-connector-active' : 'dag-connector'}
              strokeOpacity={isDone ? 0.8 : 0.4}
            />
          );
        })}

        {/* Nodes */}
        {Object.entries(positions).map(([id, pos]) => {
          const { task } = pos;
          const borderColor = getNodeBorderColor(task.status);
          const icon = TASK_TYPE_ICON[task.type ?? ''] ?? '◇';
          const isActive = task.status === 'IN_PROGRESS';
          const isFailed = task.status === 'FAILED' || task.status === 'BLOCKED';

          return (
            <g
              key={id}
              transform={`translate(${pos.x}, ${pos.y})`}
              onClick={() => onTaskClick?.(task)}
              className="cursor-pointer"
            >
              {/* Node background */}
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={6}
                fill="oklch(0.16 0.01 264)"
                stroke={borderColor}
                strokeWidth={isActive ? 1.5 : 1}
                strokeOpacity={isActive ? 1 : 0.6}
              />
              {/* Active glow */}
              {isActive && (
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill="none"
                  stroke={borderColor}
                  strokeWidth={4}
                  strokeOpacity={0.15}
                />
              )}
              {/* Type icon */}
              <text
                x={14}
                y={NODE_H / 2 + 1}
                dominantBaseline="middle"
                fontSize={14}
                fill={borderColor}
                opacity={0.9}
              >
                {icon}
              </text>
              {/* Task name */}
              <text
                x={32}
                y={NODE_H / 2 - 8}
                dominantBaseline="middle"
                fontSize={11}
                fontFamily="'Space Grotesk', sans-serif"
                fontWeight={500}
                fill="oklch(0.88 0.005 264)"
              >
                {task.name.length > 12 ? task.name.slice(0, 12) + '…' : task.name}
              </text>
              {/* Agent ID */}
              <text
                x={32}
                y={NODE_H / 2 + 8}
                dominantBaseline="middle"
                fontSize={9}
                fontFamily="'JetBrains Mono', monospace"
                fill="oklch(0.5 0.01 264)"
              >
                {task.assigned_agent.length > 20
                  ? task.assigned_agent.slice(0, 20) + '…'
                  : task.assigned_agent}
              </text>
              {/* Status dot */}
              <circle
                cx={NODE_W - 12}
                cy={NODE_H / 2}
                r={4}
                fill={borderColor}
                opacity={0.9}
              />
              {/* Failed indicator */}
              {isFailed && (
                <text
                  x={NODE_W - 12}
                  y={NODE_H / 2 - 12}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={10}
                  fill="#f87171"
                >
                  ✕
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
