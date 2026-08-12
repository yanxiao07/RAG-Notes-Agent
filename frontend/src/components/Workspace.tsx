import { useDeferredValue, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  Bot,
  Archive,
  BookOpenText,
  Check,
  ChevronRight,
  ExternalLink,
  FileText,
  FileType2,
  FolderPlus,
  LibraryBig,
  ListFilter,
  MessageSquarePlus,
  Network,
  NotebookPen,
  PanelRightClose,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Save,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  ThumbsDown,
  ThumbsUp,
  Upload,
  X,
} from "lucide-react";

import type {
  AgentRuntimeEvent,
  AgentRuntimeRun,
  AnswerFeedback,
  AnswerTrace,
  ChangeProposal,
  Citation,
  Conversation,
  ConversationMessage,
  Evidence,
  ExtensionCatalog,
  FeedbackEvaluationCase,
  FeedbackKnowledgeDraft,
  FeedbackReason,
  FeedbackSentiment,
  FeedbackTriage,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeTag,
  KnowledgeTagAssignment,
  ModelConnectionKind,
  ModelConnectionTest,
  Note,
  RetrievalDiagnostics,
  RuntimeConfiguration,
  WorkspaceModelConfiguration,
} from "../api";
import { MarkdownContent, markdownToPlainText } from "./MarkdownContent";

export type WorkspaceTab =
  "assistant" | "research" | "notes" | "mindMap" | "settings";

type GlobalRailProps = {
  activeTab: WorkspaceTab;
  onNavigate: (tab: WorkspaceTab) => void;
  onReview: () => void;
};

export function GlobalRail({
  activeTab,
  onNavigate,
  onReview,
}: GlobalRailProps) {
  return (
    <aside className="global-rail" aria-label="工作区导航">
      <button
        className="product-mark"
        onClick={() => onNavigate("research")}
        title="知识工作台"
        type="button"
      >
        <BookOpenText size={21} aria-hidden="true" />
      </button>
      <nav className="rail-nav">
        <button
          aria-label="问答助手"
          className={
            activeTab === "assistant" ? "rail-action active" : "rail-action"
          }
          onClick={() => onNavigate("assistant")}
          title="问答助手"
          type="button"
        >
          <Bot size={19} />
        </button>
        <button
          aria-label="研究检索"
          className={
            activeTab === "research" ? "rail-action active" : "rail-action"
          }
          onClick={() => onNavigate("research")}
          title="研究检索"
          type="button"
        >
          <Search size={19} />
        </button>
        <button
          aria-label="笔记资料"
          className={
            activeTab === "notes" ? "rail-action active" : "rail-action"
          }
          onClick={() => onNavigate("notes")}
          title="笔记资料"
          type="button"
        >
          <NotebookPen size={19} />
        </button>
        <button
          aria-label="知识导图"
          className={
            activeTab === "mindMap" ? "rail-action active" : "rail-action"
          }
          onClick={() => onNavigate("mindMap")}
          title="知识导图"
          type="button"
        >
          <Network size={19} />
        </button>
        <button
          aria-label="运行设置"
          className={
            activeTab === "settings" ? "rail-action active" : "rail-action"
          }
          onClick={() => onNavigate("settings")}
          title="运行设置"
          type="button"
        >
          <Settings size={19} />
        </button>
        <button
          aria-label="审批队列"
          className="rail-action"
          onClick={onReview}
          title="审批队列"
          type="button"
        >
          <ShieldCheck size={19} />
        </button>
      </nav>
      <div className="rail-footer" title="本地工作区">
        <span>LN</span>
      </div>
    </aside>
  );
}

type LibraryPanelProps = {
  knowledgeBases: KnowledgeBase[];
  selectedId: string | null;
  isLoading: boolean;
  onCreate: () => void;
  onArchive: (knowledgeBase: KnowledgeBase) => void;
  onRename: (knowledgeBase: KnowledgeBase) => void;
  onSelect: (id: string) => void;
};

export function LibraryPanel({
  knowledgeBases,
  selectedId,
  isLoading,
  onCreate,
  onArchive,
  onRename,
  onSelect,
}: LibraryPanelProps) {
  const filter = useStateWithDeferredValue("");
  const visibleKnowledgeBases = useMemo(
    () =>
      knowledgeBases.filter((knowledgeBase) =>
        knowledgeBase.name
          .toLocaleLowerCase()
          .includes(filter.deferred.toLocaleLowerCase()),
      ),
    [filter.deferred, knowledgeBases],
  );

  return (
    <aside className="library-panel" aria-label="知识库列表">
      <header className="library-header">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>知识库</h1>
        </div>
        <button
          aria-label="新建知识库"
          className="icon-button subtle"
          onClick={onCreate}
          title="新建知识库"
          type="button"
        >
          <Plus size={18} />
        </button>
      </header>
      <label className="library-filter">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="筛选知识库"
          onChange={(event) => filter.setValue(event.target.value)}
          placeholder="筛选知识库"
          value={filter.value}
        />
      </label>
      <div className="library-list">
        <div className="list-label">
          <span>全部知识库</span>
          <span>{knowledgeBases.length}</span>
        </div>
        {isLoading && <p className="loading-copy">正在读取工作区...</p>}
        {!isLoading && visibleKnowledgeBases.length === 0 && (
          <p className="empty-copy">没有匹配的知识库。</p>
        )}
        {visibleKnowledgeBases.map((knowledgeBase) => (
          <div
            className={
              knowledgeBase.id === selectedId
                ? "library-item-row selected"
                : "library-item-row"
            }
            key={knowledgeBase.id}
          >
            <button
              className="library-item"
              onClick={() => onSelect(knowledgeBase.id)}
              type="button"
            >
              <LibraryBig size={16} aria-hidden="true" />
              <span>{knowledgeBase.name}</span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>
            <div className="library-item-actions">
              <button
                aria-label={`重命名 ${knowledgeBase.name}`}
                className="icon-button subtle"
                onClick={() => onRename(knowledgeBase)}
                title="重命名知识库"
                type="button"
              >
                <Pencil size={14} />
              </button>
              <button
                aria-label={`归档 ${knowledgeBase.name}`}
                className="icon-button subtle danger"
                onClick={() => onArchive(knowledgeBase)}
                title="归档知识库"
                type="button"
              >
                <Archive size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>
      <button className="create-library" onClick={onCreate} type="button">
        <FolderPlus size={16} />
        新建知识库
      </button>
    </aside>
  );
}

function useStateWithDeferredValue(initialValue: string) {
  const [value, setValue] = useState(initialValue);
  // 输入筛选不应让大型知识库列表在每个按键时阻塞界面。
  const deferred = useDeferredValue(value);
  return { deferred, setValue, value };
}

type WorkspaceHeaderProps = {
  activeTab: WorkspaceTab;
  knowledgeBase: KnowledgeBase | null;
  noteCount: number;
  onCreateNote: () => void;
  onSelectTab: (tab: WorkspaceTab) => void;
};

export function WorkspaceHeader({
  activeTab,
  knowledgeBase,
  noteCount,
  onCreateNote,
  onSelectTab,
}: WorkspaceHeaderProps) {
  return (
    <header className="workspace-header">
      <div className="breadcrumb">
        <span>知识库</span>
        <ChevronRight size={14} aria-hidden="true" />
        <strong>{knowledgeBase?.name ?? "未选择知识库"}</strong>
      </div>
      <div className="header-actions">
        <div className="segmented-tabs" role="tablist" aria-label="工作视图">
          <button
            aria-selected={activeTab === "assistant"}
            onClick={() => onSelectTab("assistant")}
            role="tab"
            type="button"
          >
            问答
          </button>
          <button
            aria-selected={activeTab === "research"}
            onClick={() => onSelectTab("research")}
            role="tab"
            type="button"
          >
            检索
          </button>
          <button
            aria-selected={activeTab === "notes"}
            onClick={() => onSelectTab("notes")}
            role="tab"
            type="button"
          >
            笔记 <span>{noteCount}</span>
          </button>
          <button
            aria-selected={activeTab === "mindMap"}
            onClick={() => onSelectTab("mindMap")}
            role="tab"
            type="button"
          >
            导图
          </button>
          <button
            aria-selected={activeTab === "settings"}
            onClick={() => onSelectTab("settings")}
            role="tab"
            type="button"
          >
            设置
          </button>
        </div>
        <button
          className="primary-command"
          disabled={!knowledgeBase}
          onClick={onCreateNote}
          type="button"
        >
          <Plus size={16} />
          新建笔记
        </button>
      </div>
    </header>
  );
}

export function SettingsPanel({
  configuration,
  connectionResults,
  extensionCatalog,
  modelConfiguration,
  isSavingConfiguration,
  isTestingConnection,
  isRebuilding,
  isRebuildingGraph,
  isRechunking,
  isLoadingTags,
  isLoadingFeedbackGovernance,
  isSavingTag,
  knowledgeBaseName,
  knowledgeTags,
  feedbackTriages,
  feedbackKnowledgeDrafts,
  feedbackEvaluationCases,
  onSaveConfiguration,
  onTestConnection,
  onArchiveTag,
  onCreateTag,
  onRebuildEmbeddings,
  onRebuildGraph,
  onRechunkDocuments,
  onReviewTagAssignment,
  onResolveFeedbackTriage,
  onCreateFeedbackKnowledgeDraft,
  onCreateFeedbackEvaluationCase,
  onReviewFeedbackKnowledgeDraft,
  onReviewFeedbackEvaluationCase,
  pendingTagAssignments,
  reviewingTagAssignmentId,
  processingFeedbackGovernanceId,
}: {
  configuration: RuntimeConfiguration | null;
  connectionResults: Record<ModelConnectionKind, ModelConnectionTest | null>;
  extensionCatalog: ExtensionCatalog | null;
  modelConfiguration: WorkspaceModelConfiguration | null;
  isSavingConfiguration: boolean;
  isTestingConnection: Record<ModelConnectionKind, boolean>;
  isRebuilding: boolean;
  isRebuildingGraph: boolean;
  isRechunking: boolean;
  isLoadingTags: boolean;
  isLoadingFeedbackGovernance: boolean;
  isSavingTag: boolean;
  knowledgeBaseName: string | null;
  knowledgeTags: KnowledgeTag[];
  feedbackTriages: FeedbackTriage[];
  feedbackKnowledgeDrafts: FeedbackKnowledgeDraft[];
  feedbackEvaluationCases: FeedbackEvaluationCase[];
  onSaveConfiguration: (event: FormEvent<HTMLFormElement>) => void;
  onTestConnection: (kind: ModelConnectionKind, form: HTMLFormElement) => void;
  onArchiveTag: (tag: KnowledgeTag) => void;
  onCreateTag: (event: FormEvent<HTMLFormElement>) => void;
  onRebuildEmbeddings: () => void;
  onRebuildGraph: () => void;
  onRechunkDocuments: () => void;
  onReviewTagAssignment: (
    assignment: KnowledgeTagAssignment,
    decision: "approved" | "rejected",
  ) => void;
  onResolveFeedbackTriage: (
    triage: FeedbackTriage,
    target: "knowledge_draft" | "evaluation_case" | "product_bug",
  ) => void;
  onCreateFeedbackKnowledgeDraft: (payload: {
    feedbackTriageId: string;
    title: string;
    content: string;
  }) => void;
  onCreateFeedbackEvaluationCase: (payload: {
    feedbackTriageId: string;
    query: string;
    expectedSourceTitles: string[];
    requiredKeywords: string[];
    limit: number;
  }) => void;
  onReviewFeedbackKnowledgeDraft: (
    draft: FeedbackKnowledgeDraft,
    decision: "approved" | "rejected",
  ) => void;
  onReviewFeedbackEvaluationCase: (
    evaluationCase: FeedbackEvaluationCase,
    decision: "approved" | "rejected",
  ) => void;
  pendingTagAssignments: KnowledgeTagAssignment[];
  reviewingTagAssignmentId: string | null;
  processingFeedbackGovernanceId: string | null;
}) {
  if (!configuration || !modelConfiguration) {
    return <section className="settings-panel">正在读取运行配置...</section>;
  }
  return (
    <section className="settings-panel" aria-label="运行设置">
      <div className="settings-heading">
        <div>
          <p className="eyebrow">
            <Settings size={14} aria-hidden="true" />
            Runtime configuration
          </p>
          <h2>运行设置</h2>
          <p>
            模型密钥由服务端环境变量或密钥管理系统托管，不会在浏览器中暴露。
          </p>
        </div>
        <span
          className={
            configuration.productionReady
              ? "runtime-state ready"
              : "runtime-state local"
          }
        >
          {configuration.productionReady ? "生产就绪" : "本地开发"}
        </span>
      </div>
      <div className="settings-grid">
        <RuntimeCard label="问答模型" status={configuration.llm} />
        <RuntimeCard label="语义嵌入" status={configuration.embedding} />
      </div>
      <form className="model-settings-form" onSubmit={onSaveConfiguration}>
        <div className="model-settings-heading">
          <div>
            <h3>模型连接</h3>
            <p>密钥只在提交时发送，保存后不会再次显示。</p>
          </div>
          <button
            className="primary-command"
            disabled={isSavingConfiguration}
            title="保存模型配置"
            type="submit"
          >
            <Save size={16} />
            {isSavingConfiguration ? "保存中" : "保存配置"}
          </button>
        </div>
        <div className="model-settings-grid">
          <section className="model-config-group" aria-label="问答模型配置">
            <h4>问答模型</h4>
            <label>
              Provider
              <select
                defaultValue={modelConfiguration.llmProvider}
                name="llmProvider"
              >
                <option value="openai_compatible">OpenAI Compatible</option>
                <option value="evidence_synthesis">本地证据摘要</option>
              </select>
            </label>
            <label>
              模型
              <input
                defaultValue={modelConfiguration.llmModel}
                name="llmModel"
              />
            </label>
            <label>
              网关地址
              <input
                defaultValue={modelConfiguration.llmBaseUrl}
                name="llmBaseUrl"
                type="url"
              />
            </label>
            <label>
              API Key
              <input
                autoComplete="new-password"
                name="llmApiKey"
                placeholder={
                  modelConfiguration.hasLlmApiKey
                    ? "已配置，留空保持不变"
                    : "输入密钥"
                }
                type="password"
              />
            </label>
            {modelConfiguration.hasLlmApiKey && (
              <label className="model-secret-control">
                <input name="clearLlmApiKey" type="checkbox" />
                移除已保存的 Key
              </label>
            )}
            <label className="model-secret-control">
              <input
                defaultChecked={modelConfiguration.useQueryRewrite}
                name="useQueryRewrite"
                type="checkbox"
              />
              启用 Query Rewrite
            </label>
            <label className="model-secret-control">
              <input
                defaultChecked={modelConfiguration.useQueryRouter}
                name="useQueryRouter"
                type="checkbox"
              />
              启用智能路由（LLM Router）
            </label>
            <ConnectionTestAction
              isTesting={isTestingConnection.llm}
              kind="llm"
              onTestConnection={onTestConnection}
              result={connectionResults.llm}
            />
          </section>
          <section className="model-config-group" aria-label="语义嵌入配置">
            <h4>语义嵌入</h4>
            <label>
              Provider
              <select
                defaultValue={modelConfiguration.embeddingProvider}
                name="embeddingProvider"
              >
                <option value="openai_compatible">OpenAI Compatible</option>
                <option value="hashing">本地 Hashing</option>
              </select>
            </label>
            <label>
              模型
              <input
                defaultValue={modelConfiguration.embeddingModel}
                name="embeddingModel"
              />
            </label>
            <label>
              网关地址
              <input
                defaultValue={modelConfiguration.embeddingBaseUrl}
                name="embeddingBaseUrl"
                type="url"
              />
            </label>
            <label>
              API Key
              <input
                autoComplete="new-password"
                name="embeddingApiKey"
                placeholder={
                  modelConfiguration.hasEmbeddingApiKey
                    ? "已配置，留空保持不变"
                    : "输入密钥"
                }
                type="password"
              />
            </label>
            {modelConfiguration.hasEmbeddingApiKey && (
              <label className="model-secret-control">
                <input name="clearEmbeddingApiKey" type="checkbox" />
                移除已保存的 Key
              </label>
            )}
            <label>
              向量维度
              <input
                defaultValue={modelConfiguration.embeddingDimensions}
                min="8"
                name="embeddingDimensions"
                type="number"
              />
            </label>
            <label className="model-secret-control">
              <input
                defaultChecked={modelConfiguration.useReranker}
                name="useReranker"
                type="checkbox"
              />
              启用候选重排
            </label>
            <ConnectionTestAction
              isTesting={isTestingConnection.embedding}
              kind="embedding"
              onTestConnection={onTestConnection}
              result={connectionResults.embedding}
            />
          </section>
          <section className="model-config-group" aria-label="候选重排配置">
            <h4>候选重排</h4>
            <label>
              Provider
              <select
                defaultValue={modelConfiguration.rerankerProvider}
                name="rerankerProvider"
              >
                <option value="rule">本地规则回退</option>
                <option value="dashscope_compatible">
                  DashScope Compatible
                </option>
              </select>
            </label>
            <label>
              模型
              <input
                defaultValue={modelConfiguration.rerankerModel}
                name="rerankerModel"
              />
            </label>
            <label>
              网关地址
              <input
                defaultValue={modelConfiguration.rerankerBaseUrl}
                name="rerankerBaseUrl"
                type="url"
              />
            </label>
            <label>
              API Key
              <input
                autoComplete="new-password"
                name="rerankerApiKey"
                placeholder={
                  modelConfiguration.hasRerankerApiKey
                    ? "已配置，留空保持不变"
                    : "输入密钥"
                }
                type="password"
              />
            </label>
            {modelConfiguration.hasRerankerApiKey && (
              <label className="model-secret-control">
                <input name="clearRerankerApiKey" type="checkbox" />
                移除已保存的 Key
              </label>
            )}
            <ConnectionTestAction
              isTesting={isTestingConnection.reranker}
              kind="reranker"
              onTestConnection={onTestConnection}
              result={connectionResults.reranker}
            />
          </section>
        </div>
      </form>
      {!modelConfiguration.canSaveSecrets && (
        <p className="settings-security-warning">
          当前服务未配置密钥加密主密钥，API Key
          只能用于临时连通性测试，不能持久化保存。
        </p>
      )}
      <ExtensionCatalogPanel catalog={extensionCatalog} />
      <div className="settings-actions">
        <div>
          <strong>重建当前知识库索引</strong>
          <span>{knowledgeBaseName ?? "选择知识库后可执行"}</span>
        </div>
        <div className="settings-index-actions">
          <button
            className="secondary-command"
            disabled={!knowledgeBaseName || isRechunking}
            onClick={onRechunkDocuments}
            type="button"
          >
            {isRechunking ? "正在重建切分" : "重建切分与索引"}
          </button>
          <button
            className="secondary-command"
            disabled={!knowledgeBaseName || isRebuilding || isRechunking}
            onClick={onRebuildEmbeddings}
            type="button"
          >
            {isRebuilding ? "正在重建" : "仅重建 Embedding"}
          </button>
          <button
            className="secondary-command"
            disabled={
              !knowledgeBaseName ||
              isRebuildingGraph ||
              isRebuilding ||
              isRechunking
            }
            onClick={onRebuildGraph}
            type="button"
          >
            {isRebuildingGraph ? "正在重建图谱" : "重建实体与社区图谱"}
          </button>
        </div>
      </div>
      <TagGovernancePanel
        isLoading={isLoadingTags}
        isSaving={isSavingTag}
        knowledgeBaseName={knowledgeBaseName}
        onArchiveTag={onArchiveTag}
        onCreateTag={onCreateTag}
        onReview={onReviewTagAssignment}
        pendingAssignments={pendingTagAssignments}
        reviewingAssignmentId={reviewingTagAssignmentId}
        tags={knowledgeTags}
      />
      <FeedbackGovernancePanel
        evaluationCases={feedbackEvaluationCases}
        isLoading={isLoadingFeedbackGovernance}
        knowledgeDrafts={feedbackKnowledgeDrafts}
        onCreateEvaluationCase={onCreateFeedbackEvaluationCase}
        onCreateKnowledgeDraft={onCreateFeedbackKnowledgeDraft}
        onResolveTriage={onResolveFeedbackTriage}
        onReviewEvaluationCase={onReviewFeedbackEvaluationCase}
        onReviewKnowledgeDraft={onReviewFeedbackKnowledgeDraft}
        processingId={processingFeedbackGovernanceId}
        triages={feedbackTriages}
      />
      {configuration.warnings.length > 0 && (
        <ul className="runtime-warnings">
          {configuration.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ExtensionCatalogPanel({
  catalog,
}: {
  catalog: ExtensionCatalog | null;
}) {
  if (!catalog) {
    return (
      <section className="extension-catalog" aria-label="已部署扩展">
        <p className="extension-catalog-loading">正在读取已部署扩展...</p>
      </section>
    );
  }

  const groups: Array<{
    label: string;
    description: string;
    items: ExtensionCatalog["parsers"];
  }> = [
    {
      label: "解析器",
      description: "决定不同来源如何转为可检索文本",
      items: catalog.parsers,
    },
    {
      label: "切分器",
      description: "导入时可选择，选择结果会写入任务配置快照",
      items: catalog.chunkers,
    },
  ];

  return (
    <section className="extension-catalog" aria-label="已部署扩展">
      <div className="extension-catalog-heading">
        <div>
          <h3>已部署扩展</h3>
          <p>
            扩展由部署方发布和审核；此处仅展示已启用能力，不支持在线安装或执行第三方代码。
          </p>
        </div>
        <span className="extension-catalog-count">
          {catalog.parsers.length + catalog.chunkers.length} 项
        </span>
      </div>
      <div className="extension-catalog-grid">
        {groups.map((group) => (
          <div className="extension-catalog-group" key={group.label}>
            <div className="extension-catalog-group-heading">
              <strong>{group.label}</strong>
              <span>{group.description}</span>
            </div>
            {group.items.length > 0 ? (
              <ul className="extension-list">
                {group.items.map((item) => (
                  <li
                    className="extension-row"
                    key={`${item.kind}-${item.name}`}
                  >
                    <div>
                      <strong>{item.name}</strong>
                      <span>v{item.version}</span>
                    </div>
                    <p>{item.sourceTypes.join(", ") || "通用"}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="extension-catalog-empty">
                当前没有已启用的{group.label}。
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function TagGovernancePanel({
  isLoading,
  isSaving,
  knowledgeBaseName,
  onArchiveTag,
  onCreateTag,
  onReview,
  pendingAssignments,
  reviewingAssignmentId,
  tags,
}: {
  isLoading: boolean;
  isSaving: boolean;
  knowledgeBaseName: string | null;
  onArchiveTag: (tag: KnowledgeTag) => void;
  onCreateTag: (event: FormEvent<HTMLFormElement>) => void;
  onReview: (
    assignment: KnowledgeTagAssignment,
    decision: "approved" | "rejected",
  ) => void;
  pendingAssignments: KnowledgeTagAssignment[];
  reviewingAssignmentId: string | null;
  tags: KnowledgeTag[];
}) {
  return (
    <section className="tag-governance-panel" aria-label="知识库标签治理">
      <div className="tag-governance-heading">
        <div>
          <h3>知识库标签治理</h3>
          <p>仅批准的标签可作为定向召回补充，通用检索始终保留。</p>
        </div>
        <span className="tag-governance-count">{tags.length} 个词条</span>
      </div>
      <form className="tag-create-form" onSubmit={onCreateTag}>
        <input
          aria-label="标签名称"
          disabled={!knowledgeBaseName || isSaving}
          maxLength={120}
          name="tagName"
          placeholder="添加受控标签"
          required
        />
        <input
          aria-label="标签说明"
          disabled={!knowledgeBaseName || isSaving}
          maxLength={500}
          name="tagDescription"
          placeholder="可选说明"
        />
        <button
          className="secondary-command"
          disabled={!knowledgeBaseName || isSaving}
          type="submit"
        >
          <Plus size={15} />
          添加
        </button>
      </form>
      {isLoading ? (
        <p className="tag-governance-empty">正在读取标签治理数据...</p>
      ) : (
        <>
          <div className="tag-token-list" aria-label="受控标签列表">
            {tags.length > 0 ? (
              tags.map((tag) => (
                <span className="tag-token" key={tag.id}>
                  <span title={tag.description ?? tag.name}>{tag.name}</span>
                  <button
                    aria-label={`归档标签 ${tag.name}`}
                    className="tag-token-remove"
                    disabled={isSaving}
                    onClick={() => onArchiveTag(tag)}
                    title="归档标签"
                    type="button"
                  >
                    <X size={13} />
                  </button>
                </span>
              ))
            ) : (
              <p className="tag-governance-empty">
                当前知识库尚未建立受控标签。
              </p>
            )}
          </div>
          <div className="tag-review-heading">
            <strong>待审核建议</strong>
            <span>{pendingAssignments.length} 条</span>
          </div>
          <div className="tag-review-list">
            {pendingAssignments.length > 0 ? (
              pendingAssignments.map((assignment) => {
                const isReviewing = reviewingAssignmentId === assignment.id;
                return (
                  <div className="tag-review-item" key={assignment.id}>
                    <div>
                      <strong>{assignment.tagName}</strong>
                      <span>
                        {assignment.assetType === "document" ? "文档" : "笔记"}{" "}
                        ·{" "}
                        {assignment.source === "rule_match"
                          ? "规则建议"
                          : "手动建议"}
                      </span>
                    </div>
                    <div className="tag-review-actions">
                      <button
                        aria-label={`批准 ${assignment.tagName}`}
                        className="icon-button approve"
                        disabled={isReviewing}
                        onClick={() => onReview(assignment, "approved")}
                        title="批准"
                        type="button"
                      >
                        <Check size={15} />
                      </button>
                      <button
                        aria-label={`拒绝 ${assignment.tagName}`}
                        className="icon-button subtle"
                        disabled={isReviewing}
                        onClick={() => onReview(assignment, "rejected")}
                        title="拒绝"
                        type="button"
                      >
                        <X size={15} />
                      </button>
                    </div>
                  </div>
                );
              })
            ) : (
              <p className="tag-governance-empty">没有待审核的标签建议。</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function FeedbackGovernancePanel({
  evaluationCases,
  isLoading,
  knowledgeDrafts,
  onCreateEvaluationCase,
  onCreateKnowledgeDraft,
  onResolveTriage,
  onReviewEvaluationCase,
  onReviewKnowledgeDraft,
  processingId,
  triages,
}: {
  evaluationCases: FeedbackEvaluationCase[];
  isLoading: boolean;
  knowledgeDrafts: FeedbackKnowledgeDraft[];
  onCreateEvaluationCase: (payload: {
    feedbackTriageId: string;
    query: string;
    expectedSourceTitles: string[];
    requiredKeywords: string[];
    limit: number;
  }) => void;
  onCreateKnowledgeDraft: (payload: {
    feedbackTriageId: string;
    title: string;
    content: string;
  }) => void;
  onResolveTriage: (
    triage: FeedbackTriage,
    target: "knowledge_draft" | "evaluation_case" | "product_bug",
  ) => void;
  onReviewEvaluationCase: (
    evaluationCase: FeedbackEvaluationCase,
    decision: "approved" | "rejected",
  ) => void;
  onReviewKnowledgeDraft: (
    draft: FeedbackKnowledgeDraft,
    decision: "approved" | "rejected",
  ) => void;
  processingId: string | null;
  triages: FeedbackTriage[];
}) {
  const actionableTriages = triages.filter(
    (triage) =>
      triage.state === "open" ||
      (triage.state === "resolved" &&
        triage.resolutionTarget !== "product_bug" &&
        !knowledgeDrafts.some(
          (draft) => draft.feedbackTriageId === triage.id,
        ) &&
        !evaluationCases.some(
          (evaluationCase) => evaluationCase.feedbackTriageId === triage.id,
        )),
  );
  const pendingDrafts = knowledgeDrafts.filter(
    (draft) => draft.state === "pending",
  );
  const pendingCases = evaluationCases.filter(
    (evaluationCase) => evaluationCase.state === "pending",
  );

  return (
    <section
      className="feedback-governance-panel"
      aria-label="回答反馈质量治理"
    >
      <div className="feedback-governance-heading">
        <div>
          <h3>回答反馈质量治理</h3>
          <p>
            仅展示分类与审核状态；原问题、回答正文和提示词不会进入治理队列。
          </p>
        </div>
        <span className="tag-governance-count">
          {actionableTriages.length} 待处理
        </span>
      </div>
      {isLoading ? (
        <p className="tag-governance-empty">正在读取反馈治理数据...</p>
      ) : (
        <>
          <div className="feedback-triage-list">
            {actionableTriages.length > 0 ? (
              actionableTriages.map((triage) => {
                const isProcessing = processingId === triage.id;
                return (
                  <article className="feedback-triage-item" key={triage.id}>
                    <div className="feedback-triage-meta">
                      <strong>{formatFeedbackCategory(triage.category)}</strong>
                      <span>{formatCompactDate(triage.createdAt)}</span>
                    </div>
                    {triage.state === "open" ? (
                      <div className="feedback-target-actions">
                        <button
                          className="secondary-command"
                          disabled={isProcessing}
                          onClick={() =>
                            onResolveTriage(triage, "knowledge_draft")
                          }
                          type="button"
                        >
                          知识草稿
                        </button>
                        <button
                          className="secondary-command"
                          disabled={isProcessing}
                          onClick={() =>
                            onResolveTriage(triage, "evaluation_case")
                          }
                          type="button"
                        >
                          回归评测
                        </button>
                        <button
                          className="secondary-command"
                          disabled={isProcessing}
                          onClick={() => onResolveTriage(triage, "product_bug")}
                          type="button"
                        >
                          产品缺陷
                        </button>
                      </div>
                    ) : triage.resolutionTarget === "knowledge_draft" ? (
                      <FeedbackKnowledgeDraftForm
                        disabled={isProcessing}
                        onSubmit={onCreateKnowledgeDraft}
                        triageId={triage.id}
                      />
                    ) : (
                      <FeedbackEvaluationCaseForm
                        disabled={isProcessing}
                        onSubmit={onCreateEvaluationCase}
                        triageId={triage.id}
                      />
                    )}
                  </article>
                );
              })
            ) : (
              <p className="tag-governance-empty">没有需要创建草稿的分诊项。</p>
            )}
          </div>
          <div className="feedback-review-heading">
            <strong>待审核知识草稿</strong>
            <span>{pendingDrafts.length} 条</span>
          </div>
          <div className="feedback-review-list">
            {pendingDrafts.length > 0 ? (
              pendingDrafts.map((draft) => (
                <details className="feedback-review-item" key={draft.id}>
                  <summary>
                    <span>{draft.title}</span>
                    <span>待审核</span>
                  </summary>
                  <div className="feedback-draft-preview">
                    <MarkdownContent content={draft.content} />
                  </div>
                  <ReviewActions
                    disabled={processingId === draft.id}
                    onReview={(decision) =>
                      onReviewKnowledgeDraft(draft, decision)
                    }
                  />
                </details>
              ))
            ) : (
              <p className="tag-governance-empty">没有待审核知识草稿。</p>
            )}
          </div>
          <div className="feedback-review-heading">
            <strong>待审核回归评测</strong>
            <span>{pendingCases.length} 条</span>
          </div>
          <div className="feedback-review-list">
            {pendingCases.length > 0 ? (
              pendingCases.map((evaluationCase) => (
                <details
                  className="feedback-review-item"
                  key={evaluationCase.id}
                >
                  <summary>
                    <span>评测用例</span>
                    <span>待审核</span>
                  </summary>
                  <dl className="feedback-case-preview">
                    <dt>问题</dt>
                    <dd>{evaluationCase.query}</dd>
                    <dt>预期来源</dt>
                    <dd>{evaluationCase.expectedSourceTitles.join("、")}</dd>
                    <dt>必备关键词</dt>
                    <dd>
                      {evaluationCase.requiredKeywords.join("、") || "未设置"}
                    </dd>
                  </dl>
                  <ReviewActions
                    disabled={processingId === evaluationCase.id}
                    onReview={(decision) =>
                      onReviewEvaluationCase(evaluationCase, decision)
                    }
                  />
                </details>
              ))
            ) : (
              <p className="tag-governance-empty">没有待审核回归评测用例。</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function FeedbackKnowledgeDraftForm({
  disabled,
  onSubmit,
  triageId,
}: {
  disabled: boolean;
  onSubmit: (payload: {
    feedbackTriageId: string;
    title: string;
    content: string;
  }) => void;
  triageId: string;
}) {
  return (
    <form
      className="feedback-draft-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const title = String(form.get("feedbackDraftTitle") ?? "").trim();
        const content = String(form.get("feedbackDraftContent") ?? "").trim();
        if (title && content)
          onSubmit({ feedbackTriageId: triageId, title, content });
      }}
    >
      <input
        aria-label="知识草稿标题"
        disabled={disabled}
        maxLength={240}
        name="feedbackDraftTitle"
        placeholder="知识草稿标题"
        required
      />
      <textarea
        aria-label="知识草稿正文"
        disabled={disabled}
        maxLength={100000}
        name="feedbackDraftContent"
        placeholder="仅录入经人工复核的知识内容"
        required
      />
      <button className="primary-command" disabled={disabled} type="submit">
        创建待审核草稿
      </button>
    </form>
  );
}

function FeedbackEvaluationCaseForm({
  disabled,
  onSubmit,
  triageId,
}: {
  disabled: boolean;
  onSubmit: (payload: {
    feedbackTriageId: string;
    query: string;
    expectedSourceTitles: string[];
    requiredKeywords: string[];
    limit: number;
  }) => void;
  triageId: string;
}) {
  return (
    <form
      className="feedback-draft-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const query = String(form.get("feedbackCaseQuery") ?? "").trim();
        const expectedSourceTitles = splitFeedbackValues(
          String(form.get("feedbackCaseSources") ?? ""),
        );
        if (!query || expectedSourceTitles.length === 0) return;
        onSubmit({
          feedbackTriageId: triageId,
          query,
          expectedSourceTitles,
          requiredKeywords: splitFeedbackValues(
            String(form.get("feedbackCaseKeywords") ?? ""),
          ),
          limit: Number(form.get("feedbackCaseLimit") ?? 5),
        });
      }}
    >
      <input
        aria-label="回归评测问题"
        disabled={disabled}
        maxLength={2000}
        name="feedbackCaseQuery"
        placeholder="复现问题"
        required
      />
      <input
        aria-label="预期来源标题"
        disabled={disabled}
        name="feedbackCaseSources"
        placeholder="预期来源标题，多个用逗号分隔"
        required
      />
      <input
        aria-label="必备关键词"
        disabled={disabled}
        name="feedbackCaseKeywords"
        placeholder="可选关键词，多个用逗号分隔"
      />
      <label className="feedback-limit-field">
        Top K
        <input
          defaultValue={5}
          disabled={disabled}
          max={20}
          min={1}
          name="feedbackCaseLimit"
          type="number"
        />
      </label>
      <button className="primary-command" disabled={disabled} type="submit">
        创建待审核用例
      </button>
    </form>
  );
}

function ReviewActions({
  disabled,
  onReview,
}: {
  disabled: boolean;
  onReview: (decision: "approved" | "rejected") => void;
}) {
  return (
    <div className="feedback-review-actions">
      <button
        className="secondary-command"
        disabled={disabled}
        onClick={() => onReview("approved")}
        type="button"
      >
        <Check size={15} />
        批准
      </button>
      <button
        className="secondary-command"
        disabled={disabled}
        onClick={() => onReview("rejected")}
        type="button"
      >
        <X size={15} />
        拒绝
      </button>
    </div>
  );
}

function splitFeedbackValues(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatFeedbackCategory(category: string) {
  const labels: Record<string, string> = {
    generation_grounding: "回答可信度",
    knowledge_stale_or_conflict: "知识时效与冲突",
    product_or_bug: "产品或缺陷",
    rerank_error: "证据相关性",
    retrieval_miss: "检索缺失",
  };
  return labels[category] ?? "待复核反馈";
}

function ConnectionTestAction({
  isTesting,
  kind,
  onTestConnection,
  result,
}: {
  isTesting: boolean;
  kind: ModelConnectionKind;
  onTestConnection: (kind: ModelConnectionKind, form: HTMLFormElement) => void;
  result: ModelConnectionTest | null;
}) {
  const labelByKind: Record<ModelConnectionKind, string> = {
    llm: "测试 LLM 连通性",
    embedding: "测试 Embedding 连通性",
    reranker: "测试 Reranker 连通性",
  };
  const label = labelByKind[kind];
  return (
    <div className="connection-test-action">
      <button
        className="secondary-command"
        disabled={isTesting}
        onClick={(event) => {
          const form = event.currentTarget.form;
          if (form) onTestConnection(kind, form);
        }}
        type="button"
      >
        {isTesting ? "测试中" : label}
      </button>
      {result && (
        <span className="connection-test-result">
          {result.message}
          {result.latencyMs > 0 ? ` ${result.latencyMs} ms` : ""}
        </span>
      )}
    </div>
  );
}

function RuntimeCard({
  label,
  status,
}: {
  label: string;
  status: RuntimeConfiguration["llm"];
}) {
  return (
    <dl className="runtime-card">
      <dt>{label}</dt>
      <dd>{status.provider}</dd>
      <dt>模型</dt>
      <dd>{status.model}</dd>
      <dt>状态</dt>
      <dd>{status.configured ? "已配置" : "待配置"}</dd>
    </dl>
  );
}

type AssistantPanelProps = {
  answerTraces: Record<string, AnswerTrace[]>;
  feedbackByMessage: Record<string, AnswerFeedback>;
  conversations: Conversation[];
  disabled: boolean;
  explainRetrieval: boolean;
  isAnswering: boolean;
  messages: ConversationMessage[];
  onAsk: (event: FormEvent<HTMLFormElement>) => void;
  onExplainRetrievalChange: (enabled: boolean) => void;
  onFeedback: (
    messageId: string,
    sentiment: FeedbackSentiment,
    reasonCode?: FeedbackReason,
  ) => void;
  onNewConversation: () => void;
  onRenameConversation: (conversation: Conversation) => void;
  onArchiveConversation: (conversation: Conversation) => void;
  onSelectCitation: (citation: Citation) => void;
  onSelectConversation: (conversationId: string) => void;
  selectedConversationId: string | null;
  submittingFeedbackId: string | null;
};

export function AssistantPanel({
  answerTraces,
  feedbackByMessage,
  conversations,
  disabled,
  explainRetrieval,
  isAnswering,
  messages,
  onAsk,
  onExplainRetrievalChange,
  onFeedback,
  onNewConversation,
  onRenameConversation,
  onArchiveConversation,
  onSelectCitation,
  onSelectConversation,
  selectedConversationId,
  submittingFeedbackId,
}: AssistantPanelProps) {
  return (
    <section className="assistant-panel" aria-label="问答助手">
      <div className="assistant-heading">
        <div>
          <p className="eyebrow">
            <Bot size={14} aria-hidden="true" />
            Grounded answer
          </p>
          <h2>问答助手</h2>
          <p>每次回答仅基于当前知识库检索到的证据。</p>
        </div>
        <button
          aria-label="新建问答"
          className="icon-button subtle"
          disabled={disabled || isAnswering}
          onClick={onNewConversation}
          title="新建问答"
          type="button"
        >
          <MessageSquarePlus size={18} />
        </button>
      </div>
      <label className="explain-retrieval-toggle">
        <input
          checked={explainRetrieval}
          disabled={disabled || isAnswering}
          onChange={(event) => onExplainRetrievalChange(event.target.checked)}
          type="checkbox"
        />
        <span>显示检索过程</span>
      </label>
      {conversations.length > 0 && (
        <section className="conversation-history" aria-label="历史问答">
          <div className="conversation-history-heading">
            <span>历史问答</span>
            <span>{conversations.length} 个会话</span>
          </div>
          <div className="conversation-history-list">
            {conversations.map((conversation) => (
              <div
                className={
                  conversation.id === selectedConversationId
                    ? "conversation-history-item selected"
                    : "conversation-history-item"
                }
                key={conversation.id}
              >
                <button
                  aria-pressed={conversation.id === selectedConversationId}
                  className="conversation-history-select"
                  onClick={() => onSelectConversation(conversation.id)}
                  type="button"
                >
                  <strong>{conversation.title}</strong>
                  <small>
                    {formatConversationDate(conversation.updatedAt)}
                  </small>
                </button>
                <div className="conversation-history-actions">
                  <button
                    aria-label={`重命名 ${conversation.title}`}
                    className="icon-button subtle"
                    disabled={isAnswering}
                    onClick={() => onRenameConversation(conversation)}
                    title="重命名问答"
                    type="button"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    aria-label={`归档 ${conversation.title}`}
                    className="icon-button subtle danger"
                    disabled={isAnswering}
                    onClick={() => onArchiveConversation(conversation)}
                    title="归档问答"
                    type="button"
                  >
                    <Archive size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      <div className="conversation-thread" aria-live="polite">
        {messages.length === 0 ? (
          <div className="assistant-empty">
            <Bot size={30} aria-hidden="true" />
            <h3>从资料出发</h3>
            <p>提出问题后，回答会附带可回查的来源定位。</p>
          </div>
        ) : (
          messages.map((message) => (
            <article
              className={
                message.role === "user"
                  ? "chat-message user"
                  : "chat-message assistant"
              }
              key={message.id}
            >
              <div className="chat-message-meta">
                {message.role === "user" ? "你" : "Agent"}
                {message.role === "assistant" && message.providerName && (
                  <span>{message.providerName}</span>
                )}
              </div>
              <MarkdownContent
                className="chat-message-content"
                content={message.content || "正在生成回答..."}
              />
              {message.role === "assistant" &&
                answerTraces[message.id]?.length && (
                  <details className="answer-trace" open>
                    <summary>检索过程</summary>
                    <ol>
                      {answerTraces[message.id].map((trace, index) => (
                        <li key={`${message.id}-${trace.step}-${index}`}>
                          <strong>{trace.label}</strong>
                          <span>{trace.detail}</span>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              {message.role === "assistant" && message.citations.length > 0 && (
                <div className="message-citations" aria-label="回答引用">
                  {message.citations.map((citation) => (
                    <button
                      key={`${message.id}-${citation.citationIndex}`}
                      onClick={() => onSelectCitation(citation)}
                      title={`查看来源：${citation.title}`}
                      type="button"
                    >
                      <span>[{citation.citationIndex}]</span>
                      {citation.title}
                      {citation.sourceValidationState === "unavailable" && (
                        <small
                          className="citation-source-warning"
                          title="网页来源当前不可用；回答引用的是已入库的历史证据。"
                        >
                          来源不可用
                        </small>
                      )}
                    </button>
                  ))}
                </div>
              )}
              {message.role === "assistant" &&
                message.state === "completed" && (
                  <FeedbackActions
                    feedback={feedbackByMessage[message.id]}
                    isSubmitting={submittingFeedbackId === message.id}
                    messageId={message.id}
                    onFeedback={onFeedback}
                  />
                )}
              {message.state === "failed" && <small>生成失败</small>}
            </article>
          ))
        )}
      </div>
      <form className="assistant-composer" onSubmit={onAsk}>
        <textarea
          aria-label="向问答助手提问"
          disabled={disabled || isAnswering}
          name="question"
          placeholder="向当前知识库提问"
          rows={2}
        />
        <button
          aria-label="发送问题"
          disabled={disabled || isAnswering}
          title="发送问题"
          type="submit"
        >
          <Send size={17} />
        </button>
      </form>
    </section>
  );
}

function FeedbackActions({
  feedback,
  isSubmitting,
  messageId,
  onFeedback,
}: {
  feedback: AnswerFeedback | undefined;
  isSubmitting: boolean;
  messageId: string;
  onFeedback: (
    messageId: string,
    sentiment: FeedbackSentiment,
    reasonCode?: FeedbackReason,
  ) => void;
}) {
  const [showReasons, setShowReasons] = useState(false);
  const selectedSentiment = feedback?.sentiment;
  const reasons: Array<{ value: FeedbackReason; label: string }> = [
    { value: "incorrect_answer", label: "回答不正确" },
    { value: "missing_evidence", label: "缺少证据" },
    { value: "irrelevant_evidence", label: "引用不相关" },
    { value: "citation_problem", label: "引用有问题" },
    { value: "outdated_information", label: "信息已过期" },
    { value: "other", label: "其他问题" },
  ];
  return (
    <div className="answer-feedback" aria-label="回答反馈">
      <div className="answer-feedback-actions">
        <button
          aria-label="回答有帮助"
          className={
            selectedSentiment === "helpful"
              ? "feedback-button selected"
              : "feedback-button"
          }
          disabled={isSubmitting}
          onClick={() => {
            setShowReasons(false);
            onFeedback(messageId, "helpful");
          }}
          title="回答有帮助"
          type="button"
        >
          <ThumbsUp size={14} />
        </button>
        <button
          aria-label="回答无帮助"
          className={
            selectedSentiment === "unhelpful"
              ? "feedback-button selected"
              : "feedback-button"
          }
          disabled={isSubmitting}
          onClick={() => setShowReasons((value) => !value)}
          title="回答无帮助"
          type="button"
        >
          <ThumbsDown size={14} />
        </button>
      </div>
      {showReasons && (
        <div className="feedback-reasons" role="group" aria-label="无帮助原因">
          {reasons.map((reason) => (
            <button
              className={
                feedback?.reasonCode === reason.value ? "selected" : ""
              }
              disabled={isSubmitting}
              key={reason.value}
              onClick={() => {
                setShowReasons(false);
                onFeedback(messageId, "unhelpful", reason.value);
              }}
              type="button"
            >
              {reason.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type ResearchPanelProps = {
  isRuntimeRunning: boolean;
  disabled: boolean;
  isSaving: boolean;
  lastQuery: string;
  retrieverName: string | null;
  resultCount: number;
  retrievalDiagnostics: RetrievalDiagnostics | null;
  selectedEvidence: Evidence | null;
  evidences: Evidence[];
  runtimeEvents: AgentRuntimeEvent[];
  runtimeRun: AgentRuntimeRun | null;
  onSearch: (event: FormEvent<HTMLFormElement>) => void;
  onRunAgent: (query: string, agenticMode: "auto" | "force" | "off") => void;
  onSelectEvidence: (evidence: Evidence) => void;
};

export function ResearchPanel({
  isRuntimeRunning,
  disabled,
  isSaving,
  lastQuery,
  retrieverName,
  resultCount,
  retrievalDiagnostics,
  selectedEvidence,
  evidences,
  runtimeEvents,
  runtimeRun,
  onSearch,
  onSelectEvidence,
  onRunAgent,
}: ResearchPanelProps) {
  function handleResearchSubmit(event: FormEvent<HTMLFormElement>) {
    const form = new FormData(event.currentTarget);
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const action =
      submitter instanceof HTMLButtonElement ? submitter.value : "search";
    if (action === "agent") {
      event.preventDefault();
      const query = String(form.get("query") ?? "").trim();
      const agenticMode = String(form.get("agenticMode") ?? "auto") as
        "auto" | "force" | "off";
      if (query) onRunAgent(query, agenticMode);
      return;
    }
    onSearch(event);
  }
  return (
    <section className="research-panel" aria-label="证据检索">
      <div className="research-intro">
        <div>
          <p className="eyebrow">
            <Sparkles size={14} aria-hidden="true" />
            Evidence retrieval
          </p>
          <h2>从可追溯资料中开始研究</h2>
          <p>结果保留原始来源和定位信息，后续 Agent 操作必须基于已选证据。</p>
        </div>
        <div className="method-note">
          <ListFilter size={15} aria-hidden="true" />
          <span>{retrieverName ?? "尚未检索"}</span>
        </div>
      </div>
      <div className="rag-pipeline" aria-label="RAG 检索链路">
        <div className="rag-pipeline-heading">
          <span>RAG 检索链路</span>
          <span>
            {retrievalDiagnostics
              ? `${retrievalDiagnostics.totalMs.toFixed(0)} ms`
              : "等待检索"}
          </span>
        </div>
        <ol>
          <li>
            <span className="rag-pipeline-index">01</span>
            <strong>查询理解</strong>
            <small>
              {retrievalDiagnostics
                ? `${retrievalDiagnostics.queryRewriteMs.toFixed(0)} ms · ${retrievalDiagnostics.queryVariantCount} 路 Query`
                : "改写与规范化"}
            </small>
          </li>
          <li>
            <span className="rag-pipeline-index">02</span>
            <strong>混合召回</strong>
            <small>
              {retrievalDiagnostics
                ? `${retrievalDiagnostics.entityRetrievalEnabled ? `实体 ${retrievalDiagnostics.entityCandidates}（命中 ${retrievalDiagnostics.entityMatchedEntities}） · ` : ""}RRF ${retrievalDiagnostics.dualRouteFusedCandidates} · 词 ${retrievalDiagnostics.keywordCandidates} / 向 ${retrievalDiagnostics.semanticCandidates}${retrievalDiagnostics.graphMode !== "local" ? ` · 图谱 ${retrievalDiagnostics.graphMatchedEntities} 实体` : ""}${retrievalDiagnostics.matchedCommunities ? ` · 社区 ${retrievalDiagnostics.matchedCommunities} / 覆盖 ${retrievalDiagnostics.communityCoveredDocuments} 篇` : ""}`
                : "关键词 + 向量 + RRF"}
            </small>
          </li>
          <li>
            <span className="rag-pipeline-index">03</span>
            <strong>相关性排序</strong>
            <small>
              {retrievalDiagnostics
                ? retrievalDiagnostics.dynamicTopKEnabled
                  ? `${retrievalDiagnostics.rerankCandidates} → K${retrievalDiagnostics.dynamicTopKSelected}（最少 ${retrievalDiagnostics.dynamicTopKMinimum}，来源 ${retrievalDiagnostics.dynamicTopKSourceCoverage}，${dynamicTopKStopLabel(retrievalDiagnostics.dynamicTopKStopReason)}）`
                  : `${retrievalDiagnostics.rerankCandidates} → ${retrievalDiagnostics.finalCandidates}`
                : "噪声过滤与重排"}
            </small>
          </li>
          <li>
            <span className="rag-pipeline-index">04</span>
            <strong>证据生成</strong>
            <small>
              {retrievalDiagnostics
                ? `${retrievalDiagnostics.contextExpanded} 个上下文 · ${retrievalDiagnostics.hybridRetrievalMs.toFixed(0)} ms`
                : "回答附带可追溯引用"}
            </small>
          </li>
        </ol>
      </div>
      <form className="research-composer" onSubmit={handleResearchSubmit}>
        <Search size={20} aria-hidden="true" />
        <input
          aria-label="检索知识库"
          disabled={disabled}
          name="query"
          placeholder="提出一个关于当前知识库的问题"
        />
        <button
          disabled={disabled || isSaving}
          name="action"
          type="submit"
          value="search"
        >
          检索证据
        </button>
        <button
          className="research-agent-button"
          disabled={disabled || isRuntimeRunning}
          name="action"
          type="submit"
          value="agent"
        >
          <Bot size={15} aria-hidden="true" />
          {isRuntimeRunning ? "Agent 运行中" : "运行 Agent"}
        </button>
        <select
          aria-label="Agent 检索模式"
          className="agentic-mode-select"
          defaultValue="auto"
          disabled={disabled || isRuntimeRunning}
          name="agenticMode"
          title="Agent 检索模式"
        >
          <option value="auto">自动判断</option>
          <option value="force">有限再检索</option>
          <option value="off">单步检索</option>
        </select>
      </form>
      <AgentRuntimeTimeline events={runtimeEvents} run={runtimeRun} />
      <div className="result-toolbar">
        <div>
          <strong>{lastQuery ? `“${lastQuery}”` : "等待查询"}</strong>
          <span>
            {lastQuery ? `${resultCount} 条可用证据` : "选择知识库后即可开始"}
          </span>
        </div>
        {lastQuery && <span className="result-label">按相关度排序</span>}
      </div>
      <div className="evidence-results">
        {evidences.map((evidence, index) => (
          <button
            aria-pressed={selectedEvidence?.locator === evidence.locator}
            className={
              selectedEvidence?.locator === evidence.locator
                ? "evidence-row selected"
                : "evidence-row"
            }
            key={evidence.locator}
            onClick={() => onSelectEvidence(evidence)}
            type="button"
          >
            <span className="rank">{String(index + 1).padStart(2, "0")}</span>
            <span className="evidence-row-body">
              <span className="evidence-row-meta">
                <span>
                  {evidence.sourceType === "note" ? "笔记" : "文档块"}
                </span>
                <span>{Math.round(evidence.score * 100)}% 匹配</span>
              </span>
              <strong>{evidence.title}</strong>
              <span>{markdownToPlainText(evidence.content)}</span>
            </span>
            <ChevronRight size={17} aria-hidden="true" />
          </button>
        ))}
        {lastQuery && evidences.length === 0 && (
          <p className="empty-copy search-empty">
            没有命中可作为证据的内容。请调整问题或补充资料。
          </p>
        )}
      </div>
    </section>
  );
}

function dynamicTopKStopLabel(reason: string) {
  const labels: Record<string, string> = {
    candidates_exhausted: "候选已用完",
    context_budget_reached: "达到上下文预算",
    maximum_candidates_reached: "达到候选上限",
    score_gap_reached: "分数断崖停止",
  };
  return labels[reason] ?? "自适应选择";
}

function AgentRuntimeTimeline({
  events,
  run,
}: {
  events: AgentRuntimeEvent[];
  run: AgentRuntimeRun | null;
}) {
  if (events.length === 0 && run === null) return null;
  return (
    <section
      className="agent-runtime-timeline"
      aria-label="Agent Runtime 运行轨迹"
    >
      <header>
        <div>
          <span className="eyebrow">Agent Runtime</span>
          <strong>{run ? `运行 ${run.state}` : "正在编排 RAG 工具"}</strong>
        </div>
        {run && <code>{run.currentNode}</code>}
      </header>
      <ol>
        {events.map((event, index) => (
          <li
            key={`${event.event}-${event.node ?? event.tool ?? index}-${index}`}
          >
            <span className="runtime-event-dot" />
            <div>
              <strong>{runtimeEventLabel(event)}</strong>
              <small>{runtimeEventDetail(event)}</small>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function runtimeEventLabel(event: AgentRuntimeEvent): string {
  if (event.event === "started") return "运行已创建";
  if (event.event === "resumed") return "从快照恢复";
  if (event.event === "node")
    return event.node === "route" ? "路由判断" : "进入节点";
  if (event.event === "tool_started") return "调用知识检索";
  if (event.event === "tool_completed") return "检索工具完成";
  if (event.event === "checkpoint") return "状态快照已保存";
  if (event.event === "finished") return "运行完成";
  return event.event;
}

function runtimeEventDetail(event: AgentRuntimeEvent): string {
  if (event.event === "tool_completed") {
    return `${event.resultCount ?? event.evidenceCount ?? 0} 条结果进入后续链路`;
  }
  if (event.event === "checkpoint") {
    return `第 ${event.sequence ?? 0} 个可恢复快照`;
  }
  if (event.node) return `节点：${event.node}`;
  if (event.tool) return `工具：${event.tool}`;
  return "已记录运行状态";
}

type NotesPanelProps = {
  documents: KnowledgeDocument[];
  notes: Note[];
  retryingDocumentId: string | null;
  revalidatingDocumentId: string | null;
  onCreate: () => void;
  onEdit: (note: Note) => void;
  onRetryDocument: (document: KnowledgeDocument) => void;
  onRevalidateDocument: (document: KnowledgeDocument) => void;
  onArchiveDocument: (document: KnowledgeDocument) => void;
  onReadDocument: (document: KnowledgeDocument) => void;
  onUpload: () => void;
};

export function NotesPanel({
  documents,
  notes,
  retryingDocumentId,
  revalidatingDocumentId,
  onCreate,
  onEdit,
  onRetryDocument,
  onRevalidateDocument,
  onArchiveDocument,
  onReadDocument,
  onUpload,
}: NotesPanelProps) {
  return (
    <section className="notes-panel" aria-label="资料库">
      <div className="table-header">
        <div>
          <p className="eyebrow">Library materials</p>
          <h2>资料</h2>
        </div>
        <div className="materials-actions">
          <button
            className="secondary-command"
            onClick={onUpload}
            type="button"
          >
            <Upload size={16} />
            导入文档
          </button>
          <button
            className="secondary-command"
            onClick={onCreate}
            type="button"
          >
            <Plus size={16} />
            新建笔记
          </button>
        </div>
      </div>
      <section className="material-section" aria-label="已导入文档">
        <div className="material-section-heading">
          <span>已导入文档</span>
          <span>{documents.length}</span>
        </div>
        {documents.length === 0 ? (
          <div className="documents-empty">
            <FileType2 size={22} aria-hidden="true" />
            <p>
              导入 TXT、Markdown、PDF 或 DOCX 后，系统会解析并建立可检索证据。
            </p>
          </div>
        ) : (
          <div
            className="documents-table"
            role="table"
            aria-label="已导入文档列表"
          >
            <div className="documents-table-head" role="row">
              <span>文件</span>
              <span>类型</span>
              <span>入库状态</span>
              <span>更新</span>
              <span aria-label="操作" />
            </div>
            {documents.map((document) => (
              <article
                className="documents-table-row"
                key={document.id}
                role="row"
              >
                <strong>
                  <FileText size={15} aria-hidden="true" />
                  {document.title}
                  {document.sourceUrl &&
                    (document.sourceValidationState === "unavailable" ? (
                      <span
                        aria-label="来源当前不可用"
                        className="document-source-unavailable"
                        title="来源当前不可用，仍可阅读已入库的历史内容"
                      >
                        来源不可用
                      </span>
                    ) : (
                      <a
                        className="document-source-link"
                        href={document.sourceRedirectUrl ?? document.sourceUrl}
                        rel="noreferrer"
                        target="_blank"
                        title="打开已验证的来源"
                      >
                        <ExternalLink size={12} aria-hidden="true" />
                      </a>
                    ))}
                </strong>
                <span>
                  {document.sourceType === "webpage"
                    ? "网页"
                    : document.sourceType}
                  {document.sourceType === "webpage" && (
                    <small
                      className={`source-validation-state ${document.sourceValidationState}`}
                      title={sourceValidationTitle(document)}
                    >
                      {sourceValidationLabel(document)}
                    </small>
                  )}
                </span>
                <span className={`document-status ${document.status}`}>
                  {documentStatusLabel(document.status)}
                </span>
                <time>{formatCompactDate(document.updatedAt)}</time>
                <div className="document-actions">
                  <button
                    className="read-document-button"
                    onClick={() => onReadDocument(document)}
                    type="button"
                  >
                    阅读
                  </button>
                  {document.status === "failed" && (
                    <button
                      className="retry-document-button"
                      disabled={retryingDocumentId === document.id}
                      onClick={() => onRetryDocument(document)}
                      type="button"
                    >
                      {retryingDocumentId === document.id ? "重试中" : "重试"}
                    </button>
                  )}
                  {document.sourceType === "webpage" && (
                    <button
                      aria-label={`复核来源 ${document.title}`}
                      className="icon-button subtle"
                      disabled={
                        document.status !== "indexed" ||
                        revalidatingDocumentId === document.id
                      }
                      onClick={() => onRevalidateDocument(document)}
                      title={
                        document.status !== "indexed"
                          ? "文档入库完成后可复核来源"
                          : "重新校验网页来源"
                      }
                      type="button"
                    >
                      <RefreshCw
                        className={
                          revalidatingDocumentId === document.id
                            ? "is-spinning"
                            : undefined
                        }
                        size={14}
                      />
                    </button>
                  )}
                  <button
                    aria-label={`删除文档 ${document.title}`}
                    className="icon-button subtle danger"
                    disabled={document.status === "processing"}
                    onClick={() => onArchiveDocument(document)}
                    title={
                      document.status === "processing"
                        ? "处理中不可删除"
                        : "删除已导入文档"
                    }
                    type="button"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      <section className="material-section" aria-label="笔记资料">
        <div className="material-section-heading">
          <span>手工笔记</span>
          <span>{notes.length}</span>
        </div>
        {notes.length === 0 ? (
          <div className="notes-empty">
            <FileText size={26} aria-hidden="true" />
            <p>还没有笔记。将研究结论、来源和判断写入这里，供后续检索使用。</p>
          </div>
        ) : (
          <div className="notes-table" role="table" aria-label="笔记列表">
            <div className="notes-table-head" role="row">
              <span>标题</span>
              <span>内容摘要</span>
              <span>版本</span>
              <span>更新</span>
              <span aria-label="操作" />
            </div>
            {notes.map((note) => (
              <article className="notes-table-row" key={note.id} role="row">
                <strong>{note.title}</strong>
                <p>{note.content || "空笔记"}</p>
                <span>v{note.version}</span>
                <time>{formatCompactDate(note.updatedAt)}</time>
                <button
                  aria-label={`编辑笔记 ${note.title}`}
                  className="icon-button subtle"
                  onClick={() => onEdit(note)}
                  title="编辑笔记"
                  type="button"
                >
                  <Pencil size={14} />
                </button>
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}

function documentStatusLabel(status: KnowledgeDocument["status"]) {
  const labels: Record<KnowledgeDocument["status"], string> = {
    failed: "失败",
    indexed: "已入库",
    processing: "处理中",
    queued: "排队中",
    archived: "已归档",
  };
  return labels[status];
}

function sourceValidationLabel(document: KnowledgeDocument) {
  const labels: Record<string, string> = {
    pending: "来源复核中",
    valid: document.sourceIsApproved ? "来源已验证" : "链接可用",
    unavailable: "来源不可用",
    unchecked: "未校验",
  };
  return labels[document.sourceValidationState] ?? "来源待确认";
}

function sourceValidationTitle(document: KnowledgeDocument) {
  if (document.sourceValidationState === "valid") {
    const status = document.sourceValidationStatusCode
      ? `HTTP ${document.sourceValidationStatusCode}`
      : "已完成连通性校验";
    return document.sourceIsApproved ? `${status}，命中受信任域名策略` : status;
  }
  if (document.sourceValidationErrorCode) {
    return `校验状态：${document.sourceValidationErrorCode}`;
  }
  return sourceValidationLabel(document);
}

type InspectorProps = {
  evidence: Evidence | null;
  isSaving: boolean;
  proposal: ChangeProposal | null;
  onClose: () => void;
  onCreateProposal: () => void;
  onDecision: (decision: "approve" | "reject") => void;
};

export function Inspector({
  evidence,
  isSaving,
  proposal,
  onClose,
  onCreateProposal,
  onDecision,
}: InspectorProps) {
  return (
    <aside className="inspector" id="review-panel" aria-label="证据检查器">
      <header className="inspector-header">
        <div>
          <p className="eyebrow">Inspector</p>
          <h2>证据检查</h2>
        </div>
        <button
          aria-label="清除当前证据"
          className="icon-button subtle"
          disabled={!evidence}
          onClick={onClose}
          title="清除当前证据"
          type="button"
        >
          <PanelRightClose size={17} />
        </button>
      </header>
      {evidence ? (
        <div className="inspector-content">
          <div className="evidence-status">
            <span>
              {evidence.sourceType === "note" ? "笔记证据" : "文档证据"}
            </span>
            <strong>{Math.round(evidence.score * 100)}%</strong>
          </div>
          {evidence.sourceUrl && (
            <p
              className={`evidence-source-state ${evidence.sourceValidationState ?? "not_applicable"}`}
            >
              {evidence.sourceValidationState === "unavailable"
                ? "网页来源当前不可用，以下内容为已入库的历史证据。"
                : evidence.sourceIsApproved
                  ? "网页来源已验证，且命中受信任域名策略。"
                  : "网页来源已记录，当前引用仅使用已入库的证据内容。"}
            </p>
          )}
          <h3>{evidence.title}</h3>
          <MarkdownContent
            className="evidence-content"
            content={evidence.content}
          />
          <div className="locator">
            <span>来源定位</span>
            <code>{evidence.locator}</code>
          </div>
          <div className="inspector-divider" />
          <div className="agent-review">
            <div className="agent-review-heading">
              <Bot size={17} />
              <div>
                <h3>Agent 变更提议</h3>
                <p>所有写入必须先经确认。</p>
              </div>
            </div>
            {!proposal && (
              <button
                className="secondary-command full"
                disabled={isSaving}
                onClick={onCreateProposal}
                type="button"
              >
                <Sparkles size={16} />
                基于此证据创建提议
              </button>
            )}
            {proposal && (
              <ProposalCard
                isSaving={isSaving}
                proposal={proposal}
                onDecision={onDecision}
              />
            )}
          </div>
        </div>
      ) : (
        <div className="inspector-empty">
          <ShieldCheck size={28} aria-hidden="true" />
          <h3>选择一条证据</h3>
          <p>从检索结果选择内容后，可查看来源定位并发起受控的 Agent 变更。</p>
        </div>
      )}
    </aside>
  );
}

function ProposalCard({
  isSaving,
  proposal,
  onDecision,
}: {
  isSaving: boolean;
  proposal: ChangeProposal;
  onDecision: (decision: "approve" | "reject") => void;
}) {
  const expiresAt = proposal.expiresAt ? new Date(proposal.expiresAt) : null;
  const isExpired =
    proposal.state === "expired" ||
    (expiresAt !== null && expiresAt.getTime() <= Date.now());
  const stateLabel = isExpired
    ? "已过期"
    : proposal.state === "pending"
      ? "待审批"
      : proposal.state === "approved"
        ? "已批准"
        : "已拒绝";
  const riskLabel =
    proposal.riskLevel === "high"
      ? "高风险"
      : proposal.riskLevel === "medium"
        ? "中风险"
        : proposal.riskLevel === "low"
          ? "低风险"
          : proposal.riskLevel;
  const roleLabel: Record<string, string> = {
    viewer: "查看者",
    editor: "编辑者",
    approver: "审批者",
    owner: "所有者",
  };
  return (
    <div className="proposal-card">
      <div className="proposal-state">
        <span>{stateLabel}</span>
        <time>{formatCompactDate(proposal.updatedAt)}</time>
      </div>
      <div className="proposal-governance" aria-label="提议治理信息">
        <span className={`proposal-risk ${proposal.riskLevel}`}>
          风险：{riskLabel}
        </span>
        <span>
          所需角色：{roleLabel[proposal.requiredRole] ?? proposal.requiredRole}
        </span>
        {expiresAt && (
          <time className={isExpired ? "expired" : ""}>
            {isExpired
              ? "已过期"
              : `有效至 ${formatDateTime(proposal.expiresAt)}`}
          </time>
        )}
      </div>
      <MarkdownContent
        className="proposal-rationale"
        content={proposal.rationale}
      />
      {proposal.evidenceSnapshot.length > 0 && (
        <div className="proposal-evidence">
          <span className="proposal-section-label">依据快照</span>
          {proposal.evidenceSnapshot.map((item, index) => (
            <div
              className="proposal-evidence-item"
              key={`${item.sourceId ?? "evidence"}-${index}`}
            >
              <strong>{item.title ?? "未命名来源"}</strong>
              <code>{item.locator ?? item.sourceId ?? "未提供定位"}</code>
              {typeof item.score === "number" && (
                <span>{Math.round(item.score * 100)}% 匹配</span>
              )}
            </div>
          ))}
        </div>
      )}
      {proposal.state === "pending" && !isExpired && (
        <div className="proposal-actions">
          <button
            className="approve-button"
            disabled={isSaving}
            onClick={() => onDecision("approve")}
            type="button"
          >
            <Check size={15} />
            批准
          </button>
          <button
            className="reject-button"
            disabled={isSaving}
            onClick={() => onDecision("reject")}
            type="button"
          >
            <X size={15} />
            拒绝
          </button>
        </div>
      )}
    </div>
  );
}

function formatDateTime(value: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatCompactDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function formatConversationDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
