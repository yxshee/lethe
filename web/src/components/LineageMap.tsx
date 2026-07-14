import type { GraphResponse, KnowledgeNode, LineageEdge, ObjectKind } from "../api/types";

interface PositionedNode {
  node: KnowledgeNode;
  x: number;
  y: number;
}

const kindOrder: ObjectKind[] = ["source", "chunk", "embedding", "cache", "summary", "memory"];

function shortRef(value: string, limit = 22): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit - 7)}…${value.slice(-6)}`;
}

function nodesFromGraph(graph: GraphResponse | null): KnowledgeNode[] {
  if (!graph) return [];
  return [...graph.nodes].sort((left, right) => {
    const byKind = kindOrder.indexOf(left.kind) - kindOrder.indexOf(right.kind);
    return byKind || left.version_id.localeCompare(right.version_id);
  });
}

function edgesFromGraph(graph: GraphResponse | null): LineageEdge[] {
  return graph?.edges ?? [];
}

function layout(nodes: KnowledgeNode[]): PositionedNode[] {
  const levels = new Map<number, KnowledgeNode[]>();
  for (const node of nodes) {
    const level = node.kind === "source" ? 0 : node.kind === "chunk" ? 1 : 2;
    levels.set(level, [...(levels.get(level) ?? []), node]);
  }

  const xByLevel = [104, 364, 652];
  return [...levels.entries()].flatMap(([level, levelNodes]) => {
    const gap = 300 / (levelNodes.length + 1);
    return levelNodes.map((node, index) => ({
      node,
      x: xByLevel[level],
      y: 26 + gap * (index + 1),
    }));
  });
}

function stateLabel(node: KnowledgeNode): string {
  if (node.lifecycle_state === "tombstoned") return "tombstone";
  return node.lifecycle_state;
}

export function LineageMap({ graph }: { graph: GraphResponse | null }) {
  const nodes = nodesFromGraph(graph);
  const edges = edgesFromGraph(graph);
  const positioned = layout(nodes);
  const positions = new Map(positioned.map(({ node, x, y }) => [node.version_id, { x, y }]));

  if (!nodes.length) {
    return (
      <div className="lineage-empty" data-testid="lineage-empty">
        <svg viewBox="0 0 780 280" aria-hidden="true">
          <path d="M128 140H352M428 140H652" />
          <circle cx="92" cy="140" r="28" />
          <circle cx="390" cy="140" r="28" />
          <circle cx="688" cy="140" r="28" />
        </svg>
        <p>Lineage waits for a signed event or graph refresh.</p>
        <span>No payload content is rendered here—only opaque versions and lifecycle state.</span>
      </div>
    );
  }

  return (
    <>
      <div className="lineage-canvas">
        <svg viewBox="0 0 780 352" role="img" aria-labelledby="lineage-title lineage-description">
          <title id="lineage-title">Declared immutable lineage</title>
          <desc id="lineage-description">Version nodes joined from sources to five derivative classes.</desc>
          <g className="lineage-edges">
            {edges.map((edge) => {
              const from = positions.get(edge.parent_version_id);
              const to = positions.get(edge.child_version_id);
              if (!from || !to) return null;
              const mid = from.x + (to.x - from.x) / 2;
              return (
                <path
                  key={`${edge.parent_version_id}:${edge.child_version_id}`}
                  d={`M${from.x + 72},${from.y} C${mid},${from.y} ${mid},${to.y} ${to.x - 72},${to.y}`}
                />
              );
            })}
          </g>
          {positioned.map(({ node, x, y }) => (
            <g
              className={`lineage-node lineage-node--${node.lifecycle_state}`}
              key={node.version_id}
              transform={`translate(${x - 72} ${y - 28})`}
            >
              <rect width="144" height="56" rx="2" />
              <text className="lineage-kind" x="10" y="18">
                {node.kind}
              </text>
              <text className="lineage-version" x="10" y="37">
                {shortRef(node.version_id, 20)}
              </text>
              <circle cx="132" cy="12" r="4" />
            </g>
          ))}
        </svg>
      </div>

      <div className="table-wrap lineage-table-wrap">
        <table>
          <caption className="visually-hidden">Declared lineage objects</caption>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Version</th>
              <th>State</th>
              <th>Parents</th>
              <th>Policy</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.version_id}>
                <td><span className="kind-mark">{node.kind}</span></td>
                <td title={node.version_id}>{shortRef(node.version_id)}</td>
                <td><span className={`state-text state-text--${node.lifecycle_state}`}>{stateLabel(node)}</span></td>
                <td>{edges.filter((edge) => edge.child_version_id === node.version_id).length}</td>
                <td>—</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
