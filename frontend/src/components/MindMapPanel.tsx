import { useCallback, useEffect, useMemo, useState } from "react";
import type { ChangeEvent } from "react";
import {
  Background,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type OnConnect,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  FileText,
  GitFork,
  Network,
  Plus,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";

import { api } from "../api";
import type {
  KnowledgeBase,
  KnowledgeMindMap,
  MindMapGraph,
  MindMapNode,
} from "../api";

import "@xyflow/react/dist/style.css";

type FlowNodeData = { label: string; kind: MindMapNode["kind"] };
type FlowNode = Node<FlowNodeData>;

type MindMapPanelProps = {
  knowledgeBase: KnowledgeBase | null;
  onNotice: (notice: { kind: "error" | "success"; text: string }) => void;
};

const DEFAULT_NODE_POSITION = { x: 120, y: 120 };

/** 将持久化图结构转换为画布节点；样式由节点类型统一控制。 */
function toFlowNodes(graph: MindMapGraph): FlowNode[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    data: { label: node.label, kind: node.kind },
    position: node.position,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    style: nodeStyle(node.kind),
  }));
}

function toFlowEdges(graph: MindMapGraph): Edge[] {
  return graph.edges.map((edge) => ({
    ...edge,
    type: "smoothstep",
  }));
}

function nodeStyle(kind: MindMapNode["kind"]) {
  const base = {
    border: "1px solid #cfdcd5",
    borderRadius: 6,
    boxShadow: "0 2px 7px rgba(18, 45, 32, 0.08)",
    color: "#24372d",
    fontSize: 12,
    fontWeight: 650,
    maxWidth: 240,
    padding: "9px 11px",
  };
  if (kind === "root") {
    return {
      ...base,
      background: "#176343",
      borderColor: "#176343",
      color: "#ffffff",
      fontSize: 13,
    };
  }
  if (kind === "document") {
    return { ...base, background: "#eaf5ee", borderColor: "#b9d9c4" };
  }
  if (kind === "topic") {
    return { ...base, background: "#edf4fa", borderColor: "#bfd5e5" };
  }
  if (kind === "note") {
    return { ...base, background: "#fdf5df", borderColor: "#ead6a4" };
  }
  if (kind === "manual") {
    return { ...base, background: "#edf4fa", borderColor: "#bfd5e5" };
  }
  return { ...base, background: "#ffffff" };
}

function serializeGraph(nodes: FlowNode[], edges: Edge[]): MindMapGraph {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      label: node.data.label.trim() || "未命名节点",
      kind: node.data.kind,
      position: { x: node.position.x, y: node.position.y },
    })),
    edges: edges
      .filter((edge) => edge.source && edge.target)
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
      })),
  };
}

export function MindMapPanel({ knowledgeBase, onNotice }: MindMapPanelProps) {
  const [maps, setMaps] = useState<KnowledgeMindMap[]>([]);
  const [selectedMapId, setSelectedMapId] = useState<string | null>(null);
  const [nodes, setNodes] = useState<FlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [title, setTitle] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<
    FlowNode,
    Edge
  > | null>(null);
  const [fitRevision, setFitRevision] = useState(0);

  const selectedMap = useMemo(
    () => maps.find((item) => item.id === selectedMapId) ?? null,
    [maps, selectedMapId],
  );
  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );
  const canGenerate = knowledgeBase?.indexStatus === "ready";

  const loadMaps = useCallback(
    async (knowledgeBaseId: string) => {
      setIsLoading(true);
      try {
        const response = await api.listMindMaps(knowledgeBaseId);
        setMaps(response.items);
        setSelectedMapId((current) => current ?? response.items[0]?.id ?? null);
      } catch (error) {
        onNotice({
          kind: "error",
          text: getErrorMessage(error, "无法读取知识导图。"),
        });
      } finally {
        setIsLoading(false);
      }
    },
    [onNotice],
  );

  useEffect(() => {
    setMaps([]);
    setSelectedMapId(null);
    setNodes([]);
    setEdges([]);
    setTitle("");
    setSelectedNodeId(null);
    setIsDirty(false);
    if (knowledgeBase) void loadMaps(knowledgeBase.id);
  }, [knowledgeBase, loadMaps]);

  useEffect(() => {
    if (!selectedMap) {
      setNodes([]);
      setEdges([]);
      setTitle("");
      setSelectedNodeId(null);
      setIsDirty(false);
      return;
    }
    setNodes(toFlowNodes(selectedMap.graph));
    setEdges(toFlowEdges(selectedMap.graph));
    setTitle(selectedMap.title);
    setSelectedNodeId(null);
    setIsDirty(false);
    // 数据来自异步接口，画布初始化完成后才有节点尺寸，需在切图时重新定位一次。
    setFitRevision((current) => current + 1);
  }, [selectedMap]);

  useEffect(() => {
    if (!flowInstance || !selectedMap) return;
    const animationFrame = window.requestAnimationFrame(() => {
      flowInstance.fitView({ maxZoom: 1.1, minZoom: 0.2, padding: 0.2 });
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [fitRevision, flowInstance, selectedMap]);

  const handleNodesChange = useCallback(
    (changes: Parameters<typeof applyNodeChanges<FlowNode>>[0]) => {
      setNodes((current) => applyNodeChanges(changes, current));
    },
    [],
  );
  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof applyEdgeChanges>[0]) => {
      setEdges((current) => applyEdgeChanges(changes, current));
    },
    [],
  );
  const handleConnect: OnConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target) return;
    setEdges((current) =>
      addEdge(
        {
          ...connection,
          id: `edge:${crypto.randomUUID()}`,
          type: "smoothstep",
        },
        current,
      ),
    );
    setIsDirty(true);
  }, []);
  const handleNodeClick: NodeMouseHandler<FlowNode> = useCallback((_, node) => {
    setSelectedNodeId(node.id);
  }, []);

  async function handleGenerate() {
    if (!knowledgeBase) return;
    if (!canGenerate) {
      onNotice({
        kind: "error",
        text: "请等待知识库索引完成后再生成语义导图。",
      });
      return;
    }
    setIsGenerating(true);
    try {
      const generated = await api.generateMindMap(knowledgeBase.id);
      setMaps((current) => [generated, ...current]);
      setSelectedMapId(generated.id);
      onNotice({
        kind: "success",
        text: "已根据已索引文档和活跃笔记生成导图。",
      });
    } catch (error) {
      onNotice({
        kind: "error",
        text: getErrorMessage(error, "生成知识导图失败。"),
      });
    } finally {
      setIsGenerating(false);
    }
  }

  function handleAddNode() {
    const id = `manual:${crypto.randomUUID()}`;
    const offset = nodes.length * 18;
    setNodes((current) => [
      ...current,
      {
        id,
        data: { label: "新建观点", kind: "manual" },
        position: {
          x: DEFAULT_NODE_POSITION.x + offset,
          y: DEFAULT_NODE_POSITION.y + offset,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        style: nodeStyle("manual"),
      },
    ]);
    setSelectedNodeId(id);
    setIsDirty(true);
  }

  function handleSelectedNodeLabel(event: ChangeEvent<HTMLInputElement>) {
    if (!selectedNodeId) return;
    const label = event.target.value.slice(0, 500);
    setNodes((current) =>
      current.map((node) =>
        node.id === selectedNodeId
          ? { ...node, data: { ...node.data, label } }
          : node,
      ),
    );
    setIsDirty(true);
  }

  function handleDeleteSelected() {
    if (!selectedNodeId) return;
    setNodes((current) => current.filter((node) => node.id !== selectedNodeId));
    setEdges((current) =>
      current.filter(
        (edge) =>
          edge.source !== selectedNodeId && edge.target !== selectedNodeId,
      ),
    );
    setSelectedNodeId(null);
    setIsDirty(true);
  }

  async function handleSave() {
    if (!selectedMap) return;
    const normalizedTitle = title.trim();
    if (!normalizedTitle) {
      onNotice({ kind: "error", text: "请填写导图名称后再保存。" });
      return;
    }
    setIsSaving(true);
    try {
      const saved = await api.updateMindMap(selectedMap.id, {
        title: normalizedTitle,
        graph: serializeGraph(nodes, edges),
        version: selectedMap.version,
      });
      setMaps((current) =>
        current.map((item) => (item.id === saved.id ? saved : item)),
      );
      setIsDirty(false);
      onNotice({ kind: "success", text: "导图修改已保存。" });
    } catch (error) {
      onNotice({
        kind: "error",
        text: getErrorMessage(error, "保存失败，请重新载入导图后再试。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  if (!knowledgeBase) {
    return (
      <MindMapEmpty
        icon={<Network size={28} />}
        text="选择知识库后即可生成知识导图。"
      />
    );
  }

  if (!isLoading && maps.length === 0) {
    return (
      <section className="mind-map-empty" aria-label="知识导图">
        <div className="mind-map-empty-icon">
          <GitFork size={28} />
        </div>
        <p className="eyebrow">Knowledge map</p>
        <h2>从资料结构到可编辑导图</h2>
        <p>
          {canGenerate
            ? "由模型从已索引文档和活跃笔记中提炼主题与要点，之后可按你的理解自由调整。"
            : "知识库正在构建索引，完成后才能生成可靠的语义导图。"}
        </p>
        <button
          className="primary-command"
          disabled={!canGenerate || isGenerating}
          onClick={() => void handleGenerate()}
          type="button"
        >
          <Sparkles size={16} />
          {isGenerating ? "正在生成" : "生成知识导图"}
        </button>
      </section>
    );
  }

  return (
    <section className="mind-map-panel" aria-label="可编辑知识导图">
      <header className="mind-map-toolbar">
        <div className="mind-map-title-field">
          <label htmlFor="mind-map-title">导图名称</label>
          <input
            id="mind-map-title"
            maxLength={240}
            onChange={(event) => {
              setTitle(event.target.value);
              setIsDirty(true);
            }}
            value={title}
          />
        </div>
        <div className="mind-map-actions">
          <label className="mind-map-select" title="切换已生成的导图">
            <span className="sr-only">选择导图</span>
            <select
              onChange={(event) => setSelectedMapId(event.target.value)}
              value={selectedMapId ?? ""}
            >
              {maps.map((map) => (
                <option key={map.id} value={map.id}>
                  {map.title}
                </option>
              ))}
            </select>
          </label>
          <button
            className="icon-button subtle"
            onClick={handleAddNode}
            title="新增节点"
            type="button"
          >
            <Plus size={17} />
          </button>
          <button
            className="secondary-command"
            disabled={!canGenerate || isGenerating}
            onClick={() => void handleGenerate()}
            type="button"
          >
            <Sparkles size={15} />
            生成新图
          </button>
          <button
            className="primary-command"
            disabled={!isDirty || isSaving}
            onClick={() => void handleSave()}
            type="button"
          >
            <Save size={15} />
            {isSaving ? "保存中" : "保存"}
          </button>
        </div>
      </header>
      <div className="mind-map-body">
        <div className="mind-map-canvas">
          <ReactFlow<FlowNode>
            edges={edges}
            fitView
            fitViewOptions={{ maxZoom: 1.1, minZoom: 0.2, padding: 0.2 }}
            minZoom={0.2}
            nodes={nodes}
            onConnect={handleConnect}
            onEdgesChange={handleEdgesChange}
            onEdgesDelete={() => setIsDirty(true)}
            onInit={setFlowInstance}
            onNodeClick={handleNodeClick}
            onNodeDragStop={() => setIsDirty(true)}
            onNodesChange={handleNodesChange}
            onNodesDelete={() => setIsDirty(true)}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#d8e4dc" gap={18} size={1} />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={(node) =>
                node.data.kind === "root" ? "#176343" : "#a8c9b3"
              }
              pannable
              zoomable
            />
          </ReactFlow>
        </div>
        <aside className="mind-map-inspector" aria-label="节点编辑器">
          <div className="mind-map-inspector-heading">
            <FileText size={16} />
            <div>
              <p className="eyebrow">Node editor</p>
              <h3>节点内容</h3>
            </div>
          </div>
          {selectedNode ? (
            <>
              <label htmlFor="mind-map-node-label">显示文本</label>
              <input
                id="mind-map-node-label"
                maxLength={500}
                onChange={handleSelectedNodeLabel}
                value={selectedNode.data.label}
              />
              <p className="mind-map-node-kind">
                {nodeKindLabel(selectedNode.data.kind)}
              </p>
              <button
                className="danger-command"
                onClick={handleDeleteSelected}
                type="button"
              >
                <Trash2 size={15} />
                删除节点
              </button>
            </>
          ) : (
            <p className="mind-map-inspector-empty">
              点击画布节点后可修改文本；拖动节点或从节点边缘拖出即可调整结构。
            </p>
          )}
        </aside>
      </div>
    </section>
  );
}

function MindMapEmpty({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <section className="mind-map-empty" aria-label="知识导图">
      <div className="mind-map-empty-icon">{icon}</div>
      <h2>知识导图</h2>
      <p>{text}</p>
    </section>
  );
}

function nodeKindLabel(kind: MindMapNode["kind"]) {
  const labels: Record<string, string> = {
    root: "知识库根节点",
    document: "导入文档",
    concept: "文档片段",
    note: "手工笔记",
    manual: "手工节点",
  };
  return labels[kind] ?? "节点";
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}
