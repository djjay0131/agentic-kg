'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import ForceGraph2D, { ForceGraphMethods, NodeObject, LinkObject } from 'react-force-graph-2d';
import { GraphNode, GraphLink } from '@/lib/api';

interface GraphViewProps {
  nodes: GraphNode[];
  links: GraphLink[];
  onNodeClick?: (node: GraphNode) => void;
  onNodeHover?: (node: GraphNode | null) => void;
  width?: number;
  height?: number;
}

// Color mapping for node types
const NODE_COLORS: Record<string, string> = {
  problem: '#3b82f6', // blue
  paper: '#10b981',   // green
  domain: '#8b5cf6',  // purple
};

const NODE_SIZES: Record<string, number> = {
  problem: 8,
  paper: 6,
  domain: 10,
};

interface GraphNodeExtended extends NodeObject {
  id: string;
  label: string;
  type: string;
  properties: Record<string, unknown>;
}

interface GraphLinkExtended extends LinkObject {
  type: string;
  properties?: Record<string, unknown>;
}

export default function GraphView({
  nodes,
  links,
  onNodeClick,
  onNodeHover,
  width,
  height,
}: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<ForceGraphMethods>();
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [hoveredNode, setHoveredNode] = useState<GraphNodeExtended | null>(null);

  // Update dimensions on resize
  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        setDimensions({
          width: width || containerRef.current.clientWidth,
          height: height || containerRef.current.clientHeight,
        });
      }
    };

    updateDimensions();
    window.addEventListener('resize', updateDimensions);
    return () => window.removeEventListener('resize', updateDimensions);
  }, [width, height]);

  // Zoom to fit on data change
  useEffect(() => {
    if (fgRef.current && nodes.length > 0) {
      setTimeout(() => {
        fgRef.current?.zoomToFit(400, 50);
      }, 500);
    }
  }, [nodes]);

  // Transform data for force graph
  const graphData = {
    nodes: nodes.map((n) => ({
      id: n.id,
      label: n.label,
      type: n.type,
      properties: n.properties,
    })),
    links: links.map((l) => ({
      source: l.source,
      target: l.target,
      type: l.type,
      properties: l.properties,
    })),
  };

  const handleNodeClick = useCallback(
    (node: NodeObject) => {
      if (onNodeClick) {
        onNodeClick(node as unknown as GraphNode);
      }
    },
    [onNodeClick]
  );

  const handleNodeHover = useCallback(
    (node: NodeObject | null) => {
      setHoveredNode(node as GraphNodeExtended | null);
      if (onNodeHover) {
        onNodeHover(node as unknown as GraphNode | null);
      }
    },
    [onNodeHover]
  );

  const nodeCanvasObject = useCallback(
    (node: NodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const gNode = node as GraphNodeExtended;
      const label = gNode.label || '';
      const size = NODE_SIZES[gNode.type] || 6;
      const color = NODE_COLORS[gNode.type] || '#666';
      const x = node.x || 0;
      const y = node.y || 0;

      // Draw node circle
      ctx.beginPath();
      ctx.arc(x, y, size, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();

      // Draw border
      ctx.strokeStyle = hoveredNode?.id === node.id ? '#fff' : 'rgba(255,255,255,0.3)';
      ctx.lineWidth = hoveredNode?.id === node.id ? 2 : 1;
      ctx.stroke();

      // Draw label if zoomed in enough
      if (globalScale > 0.8) {
        const fontSize = Math.max(10 / globalScale, 3);
        ctx.font = `${fontSize}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = '#e5e7eb';

        // Truncate label if needed
        const maxLen = 30;
        const displayLabel = label.length > maxLen ? label.substring(0, maxLen) + '...' : label;
        ctx.fillText(displayLabel, x, y + size + 2);
      }
    },
    [hoveredNode]
  );

  const linkCanvasObject = useCallback(
    (link: LinkObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const gLink = link as GraphLinkExtended;
      const source = link.source as NodeObject;
      const target = link.target as NodeObject;

      if (!source || !target || source.x === undefined || target.x === undefined) return;

      const sx = source.x;
      const sy = source.y || 0;
      const tx = target.x;
      const ty = target.y || 0;

      // Draw link line
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(tx, ty);

      // Color based on relation type
      let strokeColor = 'rgba(100, 100, 100, 0.5)';
      if (gLink.type === 'EXTRACTED_FROM') strokeColor = 'rgba(16, 185, 129, 0.5)';
      else if (gLink.type === 'IN_DOMAIN') strokeColor = 'rgba(139, 92, 246, 0.3)';
      else if (gLink.type === 'SIMILAR_TO') strokeColor = 'rgba(59, 130, 246, 0.5)';

      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = 1;
      ctx.stroke();

      // Draw arrow
      const angle = Math.atan2(ty - sy, tx - sx);
      const arrowLen = 6;
      const arrowX = tx - (NODE_SIZES[(target as GraphNodeExtended).type] || 6) * Math.cos(angle);
      const arrowY = ty - (NODE_SIZES[(target as GraphNodeExtended).type] || 6) * Math.sin(angle);

      ctx.beginPath();
      ctx.moveTo(arrowX, arrowY);
      ctx.lineTo(
        arrowX - arrowLen * Math.cos(angle - Math.PI / 6),
        arrowY - arrowLen * Math.sin(angle - Math.PI / 6)
      );
      ctx.lineTo(
        arrowX - arrowLen * Math.cos(angle + Math.PI / 6),
        arrowY - arrowLen * Math.sin(angle + Math.PI / 6)
      );
      ctx.closePath();
      ctx.fillStyle = strokeColor;
      ctx.fill();

      // Draw label if zoomed in
      if (globalScale > 1.5 && gLink.type) {
        const midX = (sx + tx) / 2;
        const midY = (sy + ty) / 2;
        const fontSize = Math.max(8 / globalScale, 2);
        ctx.font = `${fontSize}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#9ca3af';
        ctx.fillText(gLink.type.replace(/_/g, ' '), midX, midY);
      }
    },
    []
  );

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        No graph data available. Extract some papers to populate the knowledge graph.
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full h-full bg-gray-900 rounded-lg overflow-hidden relative">
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        nodeCanvasObject={nodeCanvasObject}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        nodeRelSize={6}
        linkDirectionalArrowLength={0}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        warmupTicks={50}
        cooldownTicks={100}
        backgroundColor="#111827"
      />

      {/* Hover tooltip */}
      {hoveredNode && (
        <div className="absolute top-4 left-4 bg-gray-800 border border-gray-700 rounded-lg p-3 max-w-xs shadow-lg">
          <div className="flex items-center gap-2 mb-2">
            <div
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: NODE_COLORS[hoveredNode.type] }}
            />
            <span className="text-xs text-gray-400 uppercase">{hoveredNode.type}</span>
          </div>
          <p className="text-sm text-white">{hoveredNode.label}</p>
          {hoveredNode.properties.domain && (
            <p className="text-xs text-gray-400 mt-1">
              Domain: {hoveredNode.properties.domain as string}
            </p>
          )}
          {hoveredNode.properties.status && (
            <p className="text-xs text-gray-400">
              Status: {hoveredNode.properties.status as string}
            </p>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-4 right-4 bg-gray-800 border border-gray-700 rounded-lg p-3">
        <div className="text-xs text-gray-400 mb-2">Legend</div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.problem }} />
            <span className="text-xs text-gray-300">Problem</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.paper }} />
            <span className="text-xs text-gray-300">Paper</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: NODE_COLORS.domain }} />
            <span className="text-xs text-gray-300">Domain</span>
          </div>
        </div>
      </div>
    </div>
  );
}
