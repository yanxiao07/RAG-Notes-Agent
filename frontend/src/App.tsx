import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";
import { AlertCircle, X } from "lucide-react";

import { api } from "./api";
import type {
  AgentRuntimeEvent,
  AgentRuntimeRun,
  AnswerFeedback,
  ChangeProposal,
  AnswerTrace,
  Citation,
  Conversation,
  ConversationMessage,
  Evidence,
  ExtensionCatalog,
  FeedbackEvaluationCase,
  FeedbackKnowledgeDraft,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  KnowledgeTag,
  KnowledgeTagAssignment,
  ModelConnectionKind,
  ModelConnectionTest,
  Note,
  FeedbackReason,
  FeedbackSentiment,
  FeedbackTriage,
  RetrievalDiagnostics,
  RuntimeConfiguration,
  UpdateWorkspaceModelConfiguration,
  WorkspaceModelConfiguration,
} from "./api";
import { MarkdownContent } from "./components/MarkdownContent";
import { Modal } from "./components/Modal";
import {
  AssistantPanel,
  GlobalRail,
  Inspector,
  LibraryPanel,
  NotesPanel,
  ResearchPanel,
  SettingsPanel,
  WorkspaceHeader,
} from "./components/Workspace";
import type { WorkspaceTab } from "./components/Workspace";

// 导图库体积较大，仅在用户进入导图工作区时按需加载，避免影响问答首屏。
const MindMapPanel = lazy(async () => {
  const module = await import("./components/MindMapPanel");
  return { default: module.MindMapPanel };
});

type Notice = { kind: "error" | "success"; text: string } | null;
type ModalView =
  | "knowledge-base"
  | "note"
  | "upload"
  | "conversation-rename"
  | "conversation-archive"
  | "document-archive"
  | "document-reader"
  | null;

export function App() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<
    string | null
  >(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [answerTraces, setAnswerTraces] = useState<
    Record<string, AnswerTrace[]>
  >({});
  const [feedbackByMessage, setFeedbackByMessage] = useState<
    Record<string, AnswerFeedback>
  >({});
  const [submittingFeedbackId, setSubmittingFeedbackId] = useState<
    string | null
  >(null);
  const [explainRetrieval, setExplainRetrieval] = useState(false);
  const [runtimeConfiguration, setRuntimeConfiguration] =
    useState<RuntimeConfiguration | null>(null);
  const [extensionCatalog, setExtensionCatalog] =
    useState<ExtensionCatalog | null>(null);
  const [modelConfiguration, setModelConfiguration] =
    useState<WorkspaceModelConfiguration | null>(null);
  const [knowledgeTags, setKnowledgeTags] = useState<KnowledgeTag[]>([]);
  const [tagAssignments, setTagAssignments] = useState<
    KnowledgeTagAssignment[]
  >([]);
  const [isLoadingTags, setIsLoadingTags] = useState(false);
  const [isSavingTag, setIsSavingTag] = useState(false);
  const [reviewingTagAssignmentId, setReviewingTagAssignmentId] = useState<
    string | null
  >(null);
  const [feedbackTriages, setFeedbackTriages] = useState<FeedbackTriage[]>([]);
  const [feedbackKnowledgeDrafts, setFeedbackKnowledgeDrafts] = useState<
    FeedbackKnowledgeDraft[]
  >([]);
  const [feedbackEvaluationCases, setFeedbackEvaluationCases] = useState<
    FeedbackEvaluationCase[]
  >([]);
  const [isLoadingFeedbackGovernance, setIsLoadingFeedbackGovernance] =
    useState(false);
  const [processingFeedbackGovernanceId, setProcessingFeedbackGovernanceId] =
    useState<string | null>(null);
  const [evidences, setEvidences] = useState<Evidence[]>([]);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(
    null,
  );
  const [proposal, setProposal] = useState<ChangeProposal | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("assistant");
  const [modalView, setModalView] = useState<ModalView>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isAnswering, setIsAnswering] = useState(false);
  const [isRebuildingEmbeddings, setIsRebuildingEmbeddings] = useState(false);
  const [isRebuildingGraph, setIsRebuildingGraph] = useState(false);
  const [isRechunkingDocuments, setIsRechunkingDocuments] = useState(false);
  const [retryingDocumentId, setRetryingDocumentId] = useState<string | null>(
    null,
  );
  const [revalidatingDocumentId, setRevalidatingDocumentId] = useState<
    string | null
  >(null);
  const [isSavingConfiguration, setIsSavingConfiguration] = useState(false);
  const [isTestingConnection, setIsTestingConnection] = useState<
    Record<ModelConnectionKind, boolean>
  >({ llm: false, embedding: false, reranker: false });
  const [connectionResults, setConnectionResults] = useState<
    Record<ModelConnectionKind, ModelConnectionTest | null>
  >({ llm: null, embedding: null, reranker: null });
  const [lastQuery, setLastQuery] = useState("");
  const [retrieverName, setRetrieverName] = useState<string | null>(null);
  const [retrievalDiagnostics, setRetrievalDiagnostics] =
    useState<RetrievalDiagnostics | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<AgentRuntimeEvent[]>([]);
  const [runtimeRun, setRuntimeRun] = useState<AgentRuntimeRun | null>(null);
  const [isRuntimeRunning, setIsRuntimeRunning] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [renameTarget, setRenameTarget] = useState<KnowledgeBase | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<KnowledgeBase | null>(
    null,
  );
  const [editingNote, setEditingNote] = useState<Note | null>(null);
  const [documentTarget, setDocumentTarget] =
    useState<KnowledgeDocument | null>(null);
  const [documentDetail, setDocumentDetail] =
    useState<KnowledgeDocumentDetail | null>(null);
  // 阅读请求可能在用户快速切换文档后乱序返回，用序号丢弃过期响应。
  const documentReadRequestRef = useRef(0);
  const [conversationTarget, setConversationTarget] =
    useState<Conversation | null>(null);

  const loadKnowledgeBases = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await api.listKnowledgeBases();
      setKnowledgeBases(data.items);
      setSelectedId((current) => current ?? data.items[0]?.id ?? null);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法连接后端服务。"),
      });
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadNotes = useCallback(async (knowledgeBaseId: string) => {
    try {
      const data = await api.listNotes(knowledgeBaseId);
      setNotes(data.items);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取笔记。"),
      });
    }
  }, []);

  const loadDocuments = useCallback(async (knowledgeBaseId: string) => {
    try {
      const data = await api.listDocuments(knowledgeBaseId);
      setDocuments(data.items);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取已导入文档。"),
      });
    }
  }, []);

  const loadConversations = useCallback(async (knowledgeBaseId: string) => {
    try {
      const data = await api.listConversations(knowledgeBaseId);
      setConversations(data.items);
      setSelectedConversationId(
        (current) => current ?? data.items[0]?.id ?? null,
      );
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取问答会话。"),
      });
    }
  }, []);

  const loadMessages = useCallback(async (conversationId: string) => {
    try {
      const data = await api.listConversationMessages(conversationId);
      setMessages(data.items);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取问答记录。"),
      });
    }
  }, []);

  const loadRuntimeConfiguration = useCallback(async () => {
    try {
      setRuntimeConfiguration(await api.getRuntimeConfiguration());
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取运行配置。"),
      });
    }
  }, []);

  const loadModelConfiguration = useCallback(async () => {
    try {
      setModelConfiguration(await api.getModelConfiguration());
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取模型配置。"),
      });
    }
  }, []);

  // 目录仅暴露部署端已允许的扩展元数据，不包含安装入口或敏感运行配置。
  const loadExtensionCatalog = useCallback(async () => {
    try {
      setExtensionCatalog(await api.getRuntimeExtensions());
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取已部署扩展目录。"),
      });
    }
  }, []);

  const loadTagGovernance = useCallback(async (knowledgeBaseId: string) => {
    setIsLoadingTags(true);
    try {
      const [tags, assignments] = await Promise.all([
        api.listKnowledgeTags(knowledgeBaseId),
        api.listKnowledgeTagAssignments(knowledgeBaseId),
      ]);
      setKnowledgeTags(tags.items);
      setTagAssignments(assignments.items);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "无法读取标签治理数据。"),
      });
    } finally {
      setIsLoadingTags(false);
    }
  }, []);

  const loadFeedbackGovernance = useCallback(
    async (knowledgeBaseId: string) => {
      setIsLoadingFeedbackGovernance(true);
      try {
        const [triage, drafts, cases] = await Promise.all([
          api.listFeedbackTriage(knowledgeBaseId),
          api.listFeedbackKnowledgeDrafts(knowledgeBaseId),
          api.listFeedbackEvaluationCases(knowledgeBaseId),
        ]);
        setFeedbackTriages(triage.items);
        setFeedbackKnowledgeDrafts(drafts.items);
        setFeedbackEvaluationCases(cases.items);
      } catch (error) {
        setNotice({
          kind: "error",
          text: getErrorMessage(error, "无法读取反馈质量治理数据。"),
        });
      } finally {
        setIsLoadingFeedbackGovernance(false);
      }
    },
    [],
  );

  useEffect(() => {
    void loadKnowledgeBases();
  }, [loadKnowledgeBases]);

  useEffect(() => {
    // 切换知识库时重置仅属于前一个库的证据与审批上下文，防止跨库误写。
    setEvidences([]);
    setSelectedEvidence(null);
    setProposal(null);
    setLastQuery("");
    setRetrieverName(null);
    setRetrievalDiagnostics(null);
    setRuntimeEvents([]);
    setRuntimeRun(null);
    setConversations([]);
    setSelectedConversationId(null);
    setMessages([]);
    setAnswerTraces({});
    setFeedbackByMessage({});
    setFeedbackTriages([]);
    setFeedbackKnowledgeDrafts([]);
    setFeedbackEvaluationCases([]);
    if (selectedId) {
      void Promise.all([
        loadNotes(selectedId),
        loadDocuments(selectedId),
        loadConversations(selectedId),
      ]);
    } else {
      setNotes([]);
      setDocuments([]);
      setKnowledgeTags([]);
      setTagAssignments([]);
    }
  }, [loadConversations, loadDocuments, loadNotes, selectedId]);

  useEffect(() => {
    if (selectedConversationId && !isAnswering) {
      void loadMessages(selectedConversationId);
    }
  }, [isAnswering, loadMessages, selectedConversationId]);

  useEffect(() => {
    if (activeTab === "settings") {
      const requests: Promise<void>[] = [];
      if (runtimeConfiguration === null)
        requests.push(loadRuntimeConfiguration());
      if (modelConfiguration === null) requests.push(loadModelConfiguration());
      if (extensionCatalog === null) requests.push(loadExtensionCatalog());
      if (selectedId) requests.push(loadTagGovernance(selectedId));
      if (requests.length > 0) void Promise.all(requests);
    }
  }, [
    activeTab,
    extensionCatalog,
    loadExtensionCatalog,
    loadModelConfiguration,
    loadRuntimeConfiguration,
    modelConfiguration,
    runtimeConfiguration,
    selectedId,
    loadTagGovernance,
  ]);

  useEffect(() => {
    if (activeTab === "settings" && selectedId) {
      void loadFeedbackGovernance(selectedId);
    }
  }, [activeTab, loadFeedbackGovernance, selectedId]);

  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.id === selectedId) ?? null,
    [knowledgeBases, selectedId],
  );

  async function handleCreateKnowledgeBase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const name = String(form.get("knowledgeBaseName") ?? "").trim();
    const description = String(
      form.get("knowledgeBaseDescription") ?? "",
    ).trim();
    if (!name) return;

    setIsSaving(true);
    try {
      const created = await api.createKnowledgeBase(name, description);
      setKnowledgeBases((items) => [created, ...items]);
      setSelectedId(created.id);
      setModalView(null);
      formElement.reset();
      setNotice({
        kind: "success",
        text: "知识库已创建，可以开始导入资料或记录笔记。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "创建知识库失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRenameKnowledgeBase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!renameTarget) return;
    const form = new FormData(event.currentTarget);
    const name = String(form.get("knowledgeBaseName") ?? "").trim();
    const description = String(
      form.get("knowledgeBaseDescription") ?? "",
    ).trim();
    if (!name) return;

    setIsSaving(true);
    try {
      const updated = await api.updateKnowledgeBase(renameTarget.id, {
        name,
        description: description || null,
      });
      setKnowledgeBases((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setRenameTarget(null);
      setNotice({ kind: "success", text: "知识库信息已更新。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "更新知识库失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleArchiveKnowledgeBase() {
    if (!archiveTarget) return;
    setIsSaving(true);
    try {
      await api.archiveKnowledgeBase(archiveTarget.id);
      setKnowledgeBases((items) => {
        const remaining = items.filter((item) => item.id !== archiveTarget.id);
        setSelectedId((current) =>
          current === archiveTarget.id ? (remaining[0]?.id ?? null) : current,
        );
        return remaining;
      });
      setArchiveTarget(null);
      setNotice({
        kind: "success",
        text: "知识库已归档，不会再参与检索或问答。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "归档知识库失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSaveModelConfiguration(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    // 异步请求返回后 SyntheticEvent 的 currentTarget 可能已失效，先保存表单引用。
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    if (!modelConfiguration) {
      setNotice({ kind: "error", text: "模型配置尚未加载完成，请稍后重试。" });
      return;
    }
    const llmApiKey = String(form.get("llmApiKey") ?? "").trim();
    const embeddingApiKey = String(form.get("embeddingApiKey") ?? "").trim();
    const rerankerApiKey = String(form.get("rerankerApiKey") ?? "").trim();
    const llmProvider = String(form.get("llmProvider") ?? "").trim();
    const llmModel = String(form.get("llmModel") ?? "").trim();
    const llmBaseUrl = String(form.get("llmBaseUrl") ?? "").trim();
    const embeddingProvider = String(form.get("embeddingProvider") ?? "").trim();
    const embeddingModel = String(form.get("embeddingModel") ?? "").trim();
    const embeddingBaseUrl = String(form.get("embeddingBaseUrl") ?? "").trim();
    const embeddingDimensions = Number(form.get("embeddingDimensions"));
    const rerankerProvider = String(form.get("rerankerProvider") ?? "rule").trim();
    const rerankerModel = String(form.get("rerankerModel") ?? "").trim();
    const rerankerBaseUrl = String(form.get("rerankerBaseUrl") ?? "").trim();
    const useQueryRewrite = form.get("useQueryRewrite") === "on";
    const useQueryRouter = form.get("useQueryRouter") === "on";
    const useReranker = form.get("useReranker") === "on";
    const clearLlmApiKey = form.get("clearLlmApiKey") === "on";
    const clearEmbeddingApiKey = form.get("clearEmbeddingApiKey") === "on";
    const clearRerankerApiKey = form.get("clearRerankerApiKey") === "on";
    const payload: UpdateWorkspaceModelConfiguration = {};

    // 表单由三个独立模型组组成，只提交用户实际改动的组，避免空配置影响其他 Provider。
    const llmChanged =
      llmProvider !== modelConfiguration.llmProvider ||
      llmModel !== modelConfiguration.llmModel ||
      llmBaseUrl !== modelConfiguration.llmBaseUrl ||
      Boolean(llmApiKey) ||
      clearLlmApiKey;
    if (llmChanged) {
      Object.assign(payload, { llmProvider, llmModel, llmBaseUrl });
      if (llmApiKey) payload.llmApiKey = llmApiKey;
      if (clearLlmApiKey) payload.clearLlmApiKey = true;
    }
    if (useQueryRewrite !== modelConfiguration.useQueryRewrite) {
      payload.useQueryRewrite = useQueryRewrite;
    }
    if (useQueryRouter !== modelConfiguration.useQueryRouter) {
      payload.useQueryRouter = useQueryRouter;
    }

    const embeddingChanged =
      embeddingProvider !== modelConfiguration.embeddingProvider ||
      embeddingModel !== modelConfiguration.embeddingModel ||
      embeddingBaseUrl !== modelConfiguration.embeddingBaseUrl ||
      embeddingDimensions !== modelConfiguration.embeddingDimensions ||
      Boolean(embeddingApiKey) ||
      clearEmbeddingApiKey;
    if (embeddingChanged) {
      Object.assign(payload, {
        embeddingProvider,
        embeddingModel,
        embeddingBaseUrl,
        embeddingDimensions,
      });
      if (embeddingApiKey) payload.embeddingApiKey = embeddingApiKey;
      if (clearEmbeddingApiKey) payload.clearEmbeddingApiKey = true;
    }

    const rerankerChanged =
      rerankerProvider !== modelConfiguration.rerankerProvider ||
      rerankerModel !== modelConfiguration.rerankerModel ||
      rerankerBaseUrl !== modelConfiguration.rerankerBaseUrl ||
      useReranker !== modelConfiguration.useReranker ||
      Boolean(rerankerApiKey) ||
      clearRerankerApiKey;
    if (rerankerChanged) {
      Object.assign(payload, { rerankerProvider, rerankerModel, rerankerBaseUrl });
      if (useReranker !== modelConfiguration.useReranker) {
        payload.useReranker = useReranker;
      }
      if (rerankerApiKey) payload.rerankerApiKey = rerankerApiKey;
      if (clearRerankerApiKey) payload.clearRerankerApiKey = true;
    }
    if (Object.keys(payload).length === 0) {
      setNotice({ kind: "error", text: "请先填写或修改至少一项模型配置。" });
      return;
    }

    setIsSavingConfiguration(true);
    try {
      const updated = await api.updateModelConfiguration(payload);
      setModelConfiguration(updated);
      setKnowledgeBases((items) =>
        items.map((item) =>
          item.indexStatus === "ready" &&
          item.embeddingRevision !== updated.embeddingRevision
            ? { ...item, indexStatus: "stale" }
            : item,
        ),
      );
      await loadRuntimeConfiguration();
      formElement.reset();
      setNotice({ kind: "success", text: "模型配置已安全保存。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "保存模型配置失败。"),
      });
    } finally {
      setIsSavingConfiguration(false);
    }
  }

  async function handleTestModelConnection(
    kind: ModelConnectionKind,
    formElement: HTMLFormElement,
  ) {
    const form = new FormData(formElement);
    const apiKeyName = `${kind}ApiKey`;
    const apiKey = String(form.get(apiKeyName) ?? "").trim();
    const prefix = kind;
    const payload = {
      provider: String(form.get(`${prefix}Provider`) ?? "").trim(),
      model: String(form.get(`${prefix}Model`) ?? "").trim(),
      baseUrl: String(form.get(`${prefix}BaseUrl`) ?? "").trim(),
      ...(apiKey ? { apiKey } : {}),
    };
    setIsTestingConnection((current) => ({ ...current, [kind]: true }));
    setConnectionResults((current) => ({ ...current, [kind]: null }));
    try {
      const result = await api.testModelConnection(kind, payload);
      setConnectionResults((current) => ({ ...current, [kind]: result }));
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "模型连通测试失败。"),
      });
    } finally {
      setIsTestingConnection((current) => ({ ...current, [kind]: false }));
    }
  }

  async function handleCreateKnowledgeTag(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const name = String(form.get("tagName") ?? "").trim();
    const description = String(form.get("tagDescription") ?? "").trim();
    if (!name) return;
    setIsSavingTag(true);
    try {
      const tag = await api.createKnowledgeTag(selectedId, {
        name,
        ...(description ? { description } : {}),
      });
      setKnowledgeTags((items) =>
        [...items, tag].toSorted((left, right) =>
          left.name.localeCompare(right.name),
        ),
      );
      formElement.reset();
      setNotice({
        kind: "success",
        text: "受控标签已创建，自动建议仍需审批后才会参与检索。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "创建标签失败。"),
      });
    } finally {
      setIsSavingTag(false);
    }
  }

  async function handleArchiveKnowledgeTag(tag: KnowledgeTag) {
    setIsSavingTag(true);
    try {
      await api.archiveKnowledgeTag(tag.id, tag.version);
      setKnowledgeTags((items) => items.filter((item) => item.id !== tag.id));
      setNotice({ kind: "success", text: "标签已归档，历史审核记录会保留。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "归档标签失败，请刷新后重试。"),
      });
    } finally {
      setIsSavingTag(false);
    }
  }

  async function handleReviewKnowledgeTagAssignment(
    assignment: KnowledgeTagAssignment,
    decision: "approved" | "rejected",
  ) {
    setReviewingTagAssignmentId(assignment.id);
    try {
      const reviewed = await api.reviewKnowledgeTagAssignment(
        assignment.id,
        decision,
      );
      setTagAssignments((items) =>
        items.map((item) => (item.id === reviewed.id ? reviewed : item)),
      );
      setNotice({
        kind: "success",
        text: decision === "approved" ? "标签建议已批准。" : "标签建议已拒绝。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "处理标签建议失败。"),
      });
    } finally {
      setReviewingTagAssignmentId(null);
    }
  }

  async function handleResolveFeedbackTriage(
    triage: FeedbackTriage,
    resolutionTarget: "knowledge_draft" | "evaluation_case" | "product_bug",
  ) {
    setProcessingFeedbackGovernanceId(triage.id);
    try {
      const updated = await api.reviewFeedbackTriage(triage.id, {
        state: "resolved",
        resolutionTarget,
      });
      setFeedbackTriages((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setNotice({
        kind: "success",
        text: "反馈分诊已完成，等待创建受控草稿。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "更新反馈分诊失败。"),
      });
    } finally {
      setProcessingFeedbackGovernanceId(null);
    }
  }

  async function handleCreateFeedbackKnowledgeDraft(payload: {
    feedbackTriageId: string;
    title: string;
    content: string;
  }) {
    setProcessingFeedbackGovernanceId(payload.feedbackTriageId);
    try {
      const draft = await api.createFeedbackKnowledgeDraft(payload);
      setFeedbackKnowledgeDrafts((items) => [draft, ...items]);
      setNotice({
        kind: "success",
        text: "知识草稿已创建，批准前不会参与检索。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "创建知识草稿失败。"),
      });
    } finally {
      setProcessingFeedbackGovernanceId(null);
    }
  }

  async function handleCreateFeedbackEvaluationCase(payload: {
    feedbackTriageId: string;
    query: string;
    expectedSourceTitles: string[];
    requiredKeywords: string[];
    limit: number;
  }) {
    setProcessingFeedbackGovernanceId(payload.feedbackTriageId);
    try {
      const evaluationCase = await api.createFeedbackEvaluationCase(payload);
      setFeedbackEvaluationCases((items) => [evaluationCase, ...items]);
      setNotice({
        kind: "success",
        text: "回归评测草稿已创建，等待审核后纳入受控集合。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "创建回归评测草稿失败。"),
      });
    } finally {
      setProcessingFeedbackGovernanceId(null);
    }
  }

  async function handleReviewFeedbackKnowledgeDraft(
    draft: FeedbackKnowledgeDraft,
    decision: "approved" | "rejected",
  ) {
    setProcessingFeedbackGovernanceId(draft.id);
    try {
      const updated = await api.reviewFeedbackKnowledgeDraft(
        draft.id,
        decision,
      );
      setFeedbackKnowledgeDrafts((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      if (decision === "approved" && selectedId) await loadNotes(selectedId);
      setNotice({
        kind: "success",
        text:
          decision === "approved"
            ? "知识草稿已批准并进入笔记知识层。"
            : "知识草稿已拒绝。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "处理知识草稿失败。"),
      });
    } finally {
      setProcessingFeedbackGovernanceId(null);
    }
  }

  async function handleReviewFeedbackEvaluationCase(
    evaluationCase: FeedbackEvaluationCase,
    decision: "approved" | "rejected",
  ) {
    setProcessingFeedbackGovernanceId(evaluationCase.id);
    try {
      const updated = await api.reviewFeedbackEvaluationCase(
        evaluationCase.id,
        decision,
      );
      setFeedbackEvaluationCases((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setNotice({
        kind: "success",
        text:
          decision === "approved"
            ? "回归评测用例已批准并进入受控集合。"
            : "回归评测用例已拒绝。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "处理回归评测用例失败。"),
      });
    } finally {
      setProcessingFeedbackGovernanceId(null);
    }
  }

  async function handleCreateNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const title = String(form.get("noteTitle") ?? "").trim();
    const content = String(form.get("noteContent") ?? "").trim();
    if (!title) return;

    setIsSaving(true);
    try {
      const created = await api.createNote(selectedId, title, content);
      setNotes((items) => [created, ...items]);
      setModalView(null);
      formElement.reset();
      setNotice({
        kind: "success",
        text: "笔记已保存，并会参与后续证据检索。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "保存笔记失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleUpdateNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingNote) return;
    const form = new FormData(event.currentTarget);
    const title = String(form.get("noteTitle") ?? "").trim();
    const content = String(form.get("noteContent") ?? "").trim();
    if (!title) return;

    setIsSaving(true);
    try {
      const updated = await api.updateNote(
        editingNote.id,
        title,
        content,
        editingNote.version,
      );
      setNotes((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEditingNote(null);
      setNotice({ kind: "success", text: "笔记已更新并重新建立语义索引。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "更新笔记失败，请刷新后重试。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    const query = String(
      new FormData(event.currentTarget).get("query") ?? "",
    ).trim();
    if (!query) return;

    if (new FormData(event.currentTarget).get("action") === "agent") {
      await handleRunAgent(query, "auto");
      return;
    }

    setIsSaving(true);
    try {
      const data = await api.search(selectedId, query);
      setEvidences(data.evidences);
      setSelectedEvidence(data.evidences[0] ?? null);
      setProposal(null);
      setLastQuery(query);
      setRetrieverName(data.retriever);
      setRetrievalDiagnostics(data.diagnostics);
      setNotice(
        data.evidences.length
          ? null
          : { kind: "error", text: "没有找到可支撑该问题的证据。" },
      );
    } catch (error) {
      setNotice({ kind: "error", text: getErrorMessage(error, "检索失败。") });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRunAgent(
    query: string,
    agenticMode: "auto" | "force" | "off",
  ) {
    if (!selectedId || isRuntimeRunning) return;
    setIsRuntimeRunning(true);
    setRuntimeEvents([]);
    setRuntimeRun(null);
    try {
      await api.streamAgentResearch(selectedId, query, agenticMode, {
        onEvent: (event) => setRuntimeEvents((items) => [...items, event]),
        onCompleted: (run) => setRuntimeRun(run),
      });
      setNotice({
        kind: "success",
        text: "Agent Runtime 已完成，可查看运行轨迹。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "Agent Runtime 执行失败。"),
      });
    } finally {
      setIsRuntimeRunning(false);
    }
  }

  async function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId || isAnswering) return;
    const formElement = event.currentTarget;
    const content = String(
      new FormData(formElement).get("question") ?? "",
    ).trim();
    if (!content) return;

    setIsAnswering(true);
    const pendingId = `pending-${Date.now()}`;
    let assistantMessageId = pendingId;
    const timestamp = new Date().toISOString();
    try {
      let conversationId = selectedConversationId;
      if (!conversationId) {
        const created = await api.createConversation(
          selectedId,
          content.slice(0, 40),
        );
        conversationId = created.id;
        setConversations((items) => [created, ...items]);
        setSelectedConversationId(created.id);
      }
      const activeConversationId = conversationId;
      setMessages((items) => [
        ...items,
        {
          id: `user-${pendingId}`,
          workspaceId: "",
          conversationId: activeConversationId,
          role: "user",
          content,
          state: "completed",
          citations: [],
          providerName: null,
          modelName: null,
          createdAt: timestamp,
          updatedAt: timestamp,
        },
        {
          id: pendingId,
          workspaceId: "",
          conversationId: activeConversationId,
          role: "assistant",
          content: "",
          state: "streaming",
          citations: [],
          providerName: null,
          modelName: null,
          createdAt: timestamp,
          updatedAt: timestamp,
        },
      ]);
      formElement.reset();
      await api.streamConversationMessage(
        activeConversationId,
        content,
        explainRetrieval,
        {
          onStarted: (messageId) => {
            assistantMessageId = messageId;
            setMessages((items) =>
              items.map((message) =>
                message.id === pendingId
                  ? { ...message, id: assistantMessageId }
                  : message,
              ),
            );
          },
          onTrace: (trace) => {
            setAnswerTraces((items) => ({
              ...items,
              [assistantMessageId]: [
                ...(items[assistantMessageId] ?? []),
                trace,
              ],
            }));
          },
          onCitation: (citation) => {
            setMessages((items) =>
              items.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, citations: [...message.citations, citation] }
                  : message,
              ),
            );
          },
          onDelta: (text) => {
            setMessages((items) =>
              items.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, content: `${message.content}${text}` }
                  : message,
              ),
            );
          },
          onCompleted: (message) => {
            setMessages((items) =>
              items.map((item) =>
                item.id === assistantMessageId ? message : item,
              ),
            );
            setConversations((items) =>
              items.map((item) =>
                item.id === activeConversationId
                  ? { ...item, updatedAt: message.updatedAt }
                  : item,
              ),
            );
          },
        },
      );
    } catch (error) {
      setMessages((items) =>
        items.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: "问答生成失败，请稍后重试。",
                state: "failed",
              }
            : message,
        ),
      );
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "问答生成失败。"),
      });
    } finally {
      setIsAnswering(false);
    }
  }

  async function handleAnswerFeedback(
    assistantMessageId: string,
    sentiment: FeedbackSentiment,
    reasonCode?: FeedbackReason,
  ) {
    setSubmittingFeedbackId(assistantMessageId);
    try {
      const result = await api.submitAnswerFeedback(assistantMessageId, {
        sentiment,
        ...(reasonCode ? { reasonCode } : {}),
      });
      setFeedbackByMessage((items) => ({
        ...items,
        [assistantMessageId]: result.feedback,
      }));
      setNotice({
        kind: "success",
        text:
          sentiment === "helpful" ? "反馈已记录。" : "反馈已进入待分诊队列。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "提交回答反馈失败。"),
      });
    } finally {
      setSubmittingFeedbackId(null);
    }
  }

  function handleNewConversation() {
    if (isAnswering) return;
    setSelectedConversationId(null);
    setMessages([]);
  }

  function handleSelectConversation(conversationId: string) {
    if (isAnswering) return;
    setSelectedConversationId(conversationId);
  }

  function handleRenameConversation(conversation: Conversation) {
    if (isAnswering) return;
    setConversationTarget(conversation);
    setModalView("conversation-rename");
  }

  function handleArchiveConversation(conversation: Conversation) {
    if (isAnswering) return;
    setConversationTarget(conversation);
    setModalView("conversation-archive");
  }

  async function handleUpdateConversation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conversationTarget) return;
    const title = String(
      new FormData(event.currentTarget).get("conversationTitle") ?? "",
    ).trim();
    if (!title) return;
    setIsSaving(true);
    try {
      const updated = await api.updateConversation(
        conversationTarget.id,
        title,
      );
      setConversations((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setConversationTarget(null);
      setModalView(null);
      setNotice({ kind: "success", text: "问答标题已更新。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "更新问答标题失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleArchiveConversationConfirm() {
    if (!conversationTarget) return;
    setIsSaving(true);
    try {
      await api.archiveConversation(conversationTarget.id);
      setConversations((items) => {
        const remaining = items.filter(
          (item) => item.id !== conversationTarget.id,
        );
        if (selectedConversationId === conversationTarget.id) {
          setSelectedConversationId(remaining[0]?.id ?? null);
          setMessages([]);
        }
        return remaining;
      });
      setConversationTarget(null);
      setModalView(null);
      setNotice({ kind: "success", text: "问答已归档。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "归档问答失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  function handleSelectCitation(citation: Citation) {
    setSelectedEvidence({
      sourceType: citation.sourceType,
      sourceId: citation.sourceId,
      title: citation.title,
      content: citation.content,
      score: citation.score,
      locator: citation.locator,
      sourceUrl: citation.sourceUrl,
      sourceValidationState: citation.sourceValidationState,
      sourceIsApproved: citation.sourceIsApproved,
      sourceTrustLevel: citation.sourceTrustLevel,
      governanceAvailability: citation.governanceAvailability,
      conflictState: citation.conflictState,
    });
  }

  async function handleRebuildEmbeddings() {
    if (!selectedId || isRebuildingEmbeddings) return;
    setIsRebuildingEmbeddings(true);
    try {
      const result = await api.rebuildEmbeddings(selectedId);
      setKnowledgeBases((items) =>
        items.map((item) =>
          item.id === selectedId
            ? {
                ...item,
                embeddingRevision: result.embeddingRevision,
                indexStatus: result.indexStatus,
              }
            : item,
        ),
      );
      setNotice({
        kind: "success",
        text: `已重建 ${result.documentCount} 个文档、${result.chunkCount} 个切块的 Embedding。`,
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "重建 Embedding 失败。"),
      });
    } finally {
      setIsRebuildingEmbeddings(false);
    }
  }

  async function handleRebuildGraph() {
    if (!selectedId || isRebuildingGraph) return;
    setIsRebuildingGraph(true);
    try {
      const result = await api.rebuildGraph(selectedId);
      setKnowledgeBases((items) =>
        items.map((item) =>
          item.id === selectedId
            ? {
                ...item,
                graphRevision: result.graphRevision,
                graphStatus: result.state,
              }
            : item,
        ),
      );
      setNotice({
        kind: "success",
        text: `图谱重建已开始：${result.documentCount} 个文档，当前状态为${result.state}。`,
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "重建图谱失败。"),
      });
    } finally {
      setIsRebuildingGraph(false);
    }
  }

  async function handleRechunkDocuments() {
    if (!selectedId || isRechunkingDocuments) return;
    setIsRechunkingDocuments(true);
    try {
      const result = await api.rechunkKnowledgeBase(selectedId);
      setKnowledgeBases((items) =>
        items.map((item) =>
          item.id === selectedId
            ? { ...item, indexStatus: result.state }
            : item,
        ),
      );
      void loadDocuments(selectedId);
      setNotice({
        kind: "success",
        text: `已开始重建 ${result.documentCount} 个文档的结构化切分与索引。完成前该知识库不可检索。`,
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "重建文档切分失败。"),
      });
    } finally {
      setIsRechunkingDocuments(false);
    }
  }

  async function handleUploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) return;
    const formElement = event.currentTarget;
    const formData = new FormData(formElement);
    const url = String(formData.get("url") ?? "").trim();
    const chunker = String(formData.get("chunker") ?? "structured").trim();
    const file = formData.get("file");
    if (!url && (!(file instanceof File) || file.size === 0)) {
      setNotice({ kind: "error", text: "请选择一个非空文件。" });
      return;
    }

    setIsSaving(true);
    try {
      const title = String(formData.get("title") ?? "").trim();
      const result = url
        ? await api.importUrlDocument(
            selectedId,
            url,
            title || undefined,
            chunker,
          )
        : await api.uploadDocument(selectedId, file as File, chunker);
      setDocuments((items) => [result.document, ...items]);
      setModalView(null);
      formElement.reset();
      setNotice({
        kind: "success",
        text: "文件已接收，正在解析并建立检索索引。",
      });
      await waitForIngestion(result.ingestionJob.id);
      await loadDocuments(selectedId);
      setNotice({ kind: "success", text: "文件已完成解析并可用于证据检索。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "导入文件失败。"),
      });
      await loadDocuments(selectedId);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRetryDocument(document: KnowledgeDocument) {
    if (retryingDocumentId) return;
    setRetryingDocumentId(document.id);
    try {
      const job = await api.retryDocumentIngestion(document.id);
      setDocuments((items) =>
        items.map((item) =>
          item.id === document.id ? { ...item, status: "queued" } : item,
        ),
      );
      await waitForIngestion(job.id);
      if (selectedId) await loadDocuments(selectedId);
      setNotice({ kind: "success", text: "文档已重新完成解析和索引。" });
    } catch (error) {
      if (selectedId) await loadDocuments(selectedId);
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "文档重试失败。"),
      });
    } finally {
      setRetryingDocumentId(null);
    }
  }

  async function handleRevalidateDocumentSource(document: KnowledgeDocument) {
    if (!selectedId || revalidatingDocumentId) return;
    setRevalidatingDocumentId(document.id);
    try {
      const pending = await api.revalidateDocumentSource(document.id);
      setDocuments((items) =>
        items.map((item) => (item.id === pending.id ? pending : item)),
      );
      setNotice({ kind: "success", text: "已提交网页来源复核请求。" });
      // 来源校验在后台执行；延迟刷新一次即可拿到大多数短请求的最终状态，避免轮询占用接口。
      window.setTimeout(() => void loadDocuments(selectedId), 1_200);
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "网页来源复核失败。"),
      });
    } finally {
      setRevalidatingDocumentId(null);
    }
  }

  async function handleArchiveDocument() {
    if (!documentTarget) return;
    setIsSaving(true);
    try {
      await api.archiveDocument(documentTarget.id);
      setDocuments((items) =>
        items.filter((document) => document.id !== documentTarget.id),
      );
      setDocumentTarget(null);
      setModalView(null);
      setNotice({
        kind: "success",
        text: "文档已从当前知识库和检索索引中移除，可重新导入同一文件。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "删除文档失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleUpdateDocumentGovernance(
    document: KnowledgeDocument,
    payload: {
      sourceTrustLevel: "verified" | "standard" | "unverified";
      effectiveAt: string | null;
      expiresAt: string | null;
      conflictState: "none" | "conflicted";
      supersedesDocumentId: string | null;
    },
  ) {
    if (isSaving) return;
    setIsSaving(true);
    try {
      const updated = await api.updateDocumentGovernance(document.id, {
        ...payload,
        governanceVersion: document.governanceVersion,
      });
      setDocuments((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      setDocumentTarget(updated);
      setDocumentDetail((current) =>
        current && current.id === updated.id
          ? { ...current, ...updated }
          : current,
      );
      setNotice({ kind: "success", text: "资料治理已更新。" });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "资料治理更新失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function waitForIngestion(jobId: string) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const job = await api.getIngestionJob(jobId);
      if (job.state === "succeeded") return;
      if (job.state === "failed" || job.state === "cancelled") {
        throw new Error(job.errorMessage ?? "文件入库未完成。");
      }
      await delay(400);
    }
    throw new Error("文件仍在入库队列中，请稍后刷新资料列表。");
  }

  async function handleCreateProposal() {
    if (!selectedId || !selectedEvidence) return;
    setIsSaving(true);
    try {
      const data = await api.createProposal(
        selectedId,
        `整理：${selectedEvidence.title}`,
        selectedEvidence.content,
        `依据已选证据 ${selectedEvidence.locator} 生成，等待确认后写入知识库。`,
        [
          {
            sourceType: selectedEvidence.sourceType,
            sourceId: selectedEvidence.sourceId,
            title: selectedEvidence.title,
            locator: selectedEvidence.locator,
            sourceUrl: selectedEvidence.sourceUrl,
            score: selectedEvidence.score,
          },
        ],
      );
      setProposal(data.proposal);
      setNotice({
        kind: "success",
        text: "Agent 已创建变更提议，知识库尚未发生写入。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "创建提议失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleProposalDecision(decision: "approve" | "reject") {
    if (!proposal) return;
    setIsSaving(true);
    try {
      const updated =
        decision === "approve"
          ? await api.approveProposal(proposal.id)
          : await api.rejectProposal(proposal.id);
      setProposal(updated);
      if (decision === "approve" && selectedId) await loadNotes(selectedId);
      setNotice({
        kind: "success",
        text:
          decision === "approve"
            ? "提议已批准，笔记已写入知识库。"
            : "提议已拒绝。",
      });
    } catch (error) {
      setNotice({
        kind: "error",
        text: getErrorMessage(error, "处理提议失败。"),
      });
    } finally {
      setIsSaving(false);
    }
  }

  function handleNavigate(tab: WorkspaceTab) {
    setActiveTab(tab);
  }

  function focusReview() {
    document
      .getElementById("review-panel")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <main className="app-shell">
      <GlobalRail
        activeTab={activeTab}
        onNavigate={handleNavigate}
        onReview={focusReview}
      />
      <LibraryPanel
        isLoading={isLoading}
        knowledgeBases={knowledgeBases}
        onArchive={setArchiveTarget}
        onCreate={() => setModalView("knowledge-base")}
        onRename={setRenameTarget}
        onSelect={setSelectedId}
        selectedId={selectedId}
      />
      <section className="workspace-frame">
        <WorkspaceHeader
          activeTab={activeTab}
          knowledgeBase={selectedKnowledgeBase}
          noteCount={notes.length}
          onCreateNote={() => setModalView("note")}
          onSelectTab={setActiveTab}
        />
        {notice && (
          <NoticeBanner notice={notice} onClose={() => setNotice(null)} />
        )}
        <div
          className={
            activeTab === "mindMap"
              ? "workspace-grid mind-map-workspace"
              : "workspace-grid"
          }
        >
          <div className="workspace-canvas">
            {activeTab === "assistant" ? (
              <AssistantPanel
                answerTraces={answerTraces}
                feedbackByMessage={feedbackByMessage}
                conversations={conversations}
                disabled={!selectedKnowledgeBase}
                explainRetrieval={explainRetrieval}
                isAnswering={isAnswering}
                messages={messages}
                onAsk={handleAsk}
                onExplainRetrievalChange={setExplainRetrieval}
                onFeedback={(messageId, sentiment, reasonCode) =>
                  void handleAnswerFeedback(messageId, sentiment, reasonCode)
                }
                onNewConversation={handleNewConversation}
                onRenameConversation={handleRenameConversation}
                onArchiveConversation={handleArchiveConversation}
                onSelectCitation={handleSelectCitation}
                onSelectConversation={handleSelectConversation}
                selectedConversationId={selectedConversationId}
                submittingFeedbackId={submittingFeedbackId}
              />
            ) : activeTab === "research" ? (
              <ResearchPanel
                disabled={!selectedKnowledgeBase}
                evidences={evidences}
                isRuntimeRunning={isRuntimeRunning}
                isSaving={isSaving}
                lastQuery={lastQuery}
                onSearch={handleSearch}
                onSelectEvidence={setSelectedEvidence}
                resultCount={evidences.length}
                retrievalDiagnostics={retrievalDiagnostics}
                retrieverName={retrieverName}
                runtimeEvents={runtimeEvents}
                runtimeRun={runtimeRun}
                selectedEvidence={selectedEvidence}
                onRunAgent={(query, agenticMode) =>
                  void handleRunAgent(query, agenticMode)
                }
              />
            ) : activeTab === "notes" ? (
              <NotesPanel
                documents={documents}
                notes={notes}
                revalidatingDocumentId={revalidatingDocumentId}
                onCreate={() => setModalView("note")}
                onEdit={setEditingNote}
                onRetryDocument={(document) =>
                  void handleRetryDocument(document)
                }
                onRevalidateDocument={(document) =>
                  void handleRevalidateDocumentSource(document)
                }
                onArchiveDocument={(document) => {
                  setDocumentTarget(document);
                  setModalView("document-archive");
                }}
                onReadDocument={(document) => {
                  const requestId = ++documentReadRequestRef.current;
                  setDocumentTarget(document);
                  setDocumentDetail(null);
                  setModalView("document-reader");
                  void api
                    .getDocument(document.id)
                    .then((detail) => {
                      if (requestId === documentReadRequestRef.current) {
                        setDocumentDetail(detail);
                      }
                    })
                    .catch((error) => {
                      if (requestId !== documentReadRequestRef.current) return;
                      setNotice({
                        kind: "error",
                        text: getErrorMessage(error, "读取文档失败。"),
                      });
                      setDocumentTarget(null);
                      setModalView(null);
                    });
                }}
                onUpload={() => {
                  if (extensionCatalog === null) void loadExtensionCatalog();
                  setModalView("upload");
                }}
                retryingDocumentId={retryingDocumentId}
              />
            ) : activeTab === "mindMap" ? (
              <Suspense fallback={<MindMapLoading />}>
                <MindMapPanel
                  knowledgeBase={selectedKnowledgeBase}
                  onNotice={setNotice}
                />
              </Suspense>
            ) : (
              <SettingsPanel
                configuration={runtimeConfiguration}
                connectionResults={connectionResults}
                extensionCatalog={extensionCatalog}
                isSavingConfiguration={isSavingConfiguration}
                isTestingConnection={isTestingConnection}
                isRebuilding={isRebuildingEmbeddings}
                isRebuildingGraph={isRebuildingGraph}
                isRechunking={isRechunkingDocuments}
                isLoadingTags={isLoadingTags}
                isLoadingFeedbackGovernance={isLoadingFeedbackGovernance}
                isSavingTag={isSavingTag}
                knowledgeBaseName={selectedKnowledgeBase?.name ?? null}
                knowledgeTags={knowledgeTags}
                modelConfiguration={modelConfiguration}
                feedbackTriages={feedbackTriages}
                feedbackKnowledgeDrafts={feedbackKnowledgeDrafts}
                feedbackEvaluationCases={feedbackEvaluationCases}
                pendingTagAssignments={tagAssignments.filter(
                  (assignment) => assignment.state === "pending",
                )}
                reviewingTagAssignmentId={reviewingTagAssignmentId}
                processingFeedbackGovernanceId={processingFeedbackGovernanceId}
                onArchiveTag={(tag) => void handleArchiveKnowledgeTag(tag)}
                onCreateTag={handleCreateKnowledgeTag}
                onRebuildEmbeddings={() => void handleRebuildEmbeddings()}
                onRebuildGraph={() => void handleRebuildGraph()}
                onRechunkDocuments={() => void handleRechunkDocuments()}
                onReviewTagAssignment={(assignment, decision) =>
                  void handleReviewKnowledgeTagAssignment(assignment, decision)
                }
                onResolveFeedbackTriage={(triage, target) =>
                  void handleResolveFeedbackTriage(triage, target)
                }
                onCreateFeedbackKnowledgeDraft={(payload) =>
                  void handleCreateFeedbackKnowledgeDraft(payload)
                }
                onCreateFeedbackEvaluationCase={(payload) =>
                  void handleCreateFeedbackEvaluationCase(payload)
                }
                onReviewFeedbackKnowledgeDraft={(draft, decision) =>
                  void handleReviewFeedbackKnowledgeDraft(draft, decision)
                }
                onReviewFeedbackEvaluationCase={(evaluationCase, decision) =>
                  void handleReviewFeedbackEvaluationCase(
                    evaluationCase,
                    decision,
                  )
                }
                onSaveConfiguration={handleSaveModelConfiguration}
                onTestConnection={handleTestModelConnection}
              />
            )}
          </div>
          {activeTab !== "mindMap" && (
            <Inspector
              evidence={selectedEvidence}
              isSaving={isSaving}
              onClose={() => setSelectedEvidence(null)}
              onCreateProposal={() => void handleCreateProposal()}
              onDecision={(decision) => void handleProposalDecision(decision)}
              proposal={proposal}
            />
          )}
        </div>
      </section>
      {modalView === "knowledge-base" && (
        <KnowledgeBaseModal
          isSaving={isSaving}
          onClose={() => setModalView(null)}
          onSubmit={handleCreateKnowledgeBase}
        />
      )}
      {modalView === "note" && (
        <NoteModal
          disabled={!selectedKnowledgeBase}
          isSaving={isSaving}
          knowledgeBaseName={selectedKnowledgeBase?.name ?? ""}
          onClose={() => setModalView(null)}
          onSubmit={handleCreateNote}
        />
      )}
      {editingNote && (
        <NoteModal
          isSaving={isSaving}
          key={editingNote.id}
          knowledgeBaseName={selectedKnowledgeBase?.name ?? ""}
          note={editingNote}
          onClose={() => setEditingNote(null)}
          onSubmit={handleUpdateNote}
        />
      )}
      {modalView === "upload" && (
        <UploadModal
          disabled={!selectedKnowledgeBase}
          isSaving={isSaving}
          knowledgeBaseName={selectedKnowledgeBase?.name ?? ""}
          chunkers={extensionCatalog?.chunkers ?? []}
          onClose={() => setModalView(null)}
          onSubmit={handleUploadDocument}
        />
      )}
      {renameTarget && (
        <RenameKnowledgeBaseModal
          isSaving={isSaving}
          knowledgeBase={renameTarget}
          onClose={() => setRenameTarget(null)}
          onSubmit={handleRenameKnowledgeBase}
        />
      )}
      {archiveTarget && (
        <ArchiveKnowledgeBaseModal
          isSaving={isSaving}
          knowledgeBase={archiveTarget}
          onClose={() => setArchiveTarget(null)}
          onConfirm={() => void handleArchiveKnowledgeBase()}
        />
      )}
      {modalView === "conversation-rename" && conversationTarget && (
        <RenameConversationModal
          conversation={conversationTarget}
          isSaving={isSaving}
          onClose={() => {
            setConversationTarget(null);
            setModalView(null);
          }}
          onSubmit={handleUpdateConversation}
        />
      )}
      {modalView === "conversation-archive" && conversationTarget && (
        <ArchiveConversationModal
          conversation={conversationTarget}
          isSaving={isSaving}
          onClose={() => {
            setConversationTarget(null);
            setModalView(null);
          }}
          onConfirm={() => void handleArchiveConversationConfirm()}
        />
      )}
      {modalView === "document-archive" && documentTarget && (
        <ArchiveDocumentModal
          document={documentTarget}
          isSaving={isSaving}
          onClose={() => {
            setDocumentTarget(null);
            setModalView(null);
          }}
          onConfirm={() => void handleArchiveDocument()}
        />
      )}
      {modalView === "document-reader" && documentTarget && (
        <DocumentReaderModal
          document={documentTarget}
          detail={documentDetail}
          documents={documents}
          isSaving={isSaving}
          onClose={() => {
            documentReadRequestRef.current += 1;
            setDocumentTarget(null);
            setModalView(null);
            setDocumentDetail(null);
          }}
          onUpdateGovernance={handleUpdateDocumentGovernance}
        />
      )}
    </main>
  );
}

function MindMapLoading() {
  return <div className="mind-map-loading">正在加载导图画布...</div>;
}

function NoticeBanner({
  notice,
  onClose,
}: {
  notice: Exclude<Notice, null>;
  onClose: () => void;
}) {
  return (
    <div className={`notice ${notice.kind}`} role="status">
      <AlertCircle size={16} aria-hidden="true" />
      <span>{notice.text}</span>
      <button
        aria-label="关闭提示"
        onClick={onClose}
        title="关闭"
        type="button"
      >
        <X size={16} />
      </button>
    </div>
  );
}

function KnowledgeBaseModal({
  isSaving,
  onClose,
  onSubmit,
}: {
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal onClose={onClose} title="新建知识库">
      <form className="modal-form" onSubmit={onSubmit}>
        <label htmlFor="knowledgeBaseName">名称</label>
        <input
          autoFocus
          id="knowledgeBaseName"
          name="knowledgeBaseName"
          placeholder="例如：产品研究"
          required
        />
        <label htmlFor="knowledgeBaseDescription">说明</label>
        <textarea
          id="knowledgeBaseDescription"
          name="knowledgeBaseDescription"
          placeholder="说明资料边界、研究主题或协作目标（可选）"
          rows={4}
        />
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button className="primary-command" disabled={isSaving} type="submit">
            创建知识库
          </button>
        </div>
      </form>
    </Modal>
  );
}

function RenameKnowledgeBaseModal({
  isSaving,
  knowledgeBase,
  onClose,
  onSubmit,
}: {
  isSaving: boolean;
  knowledgeBase: KnowledgeBase;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal onClose={onClose} title="编辑知识库">
      <form className="modal-form" onSubmit={onSubmit}>
        <label htmlFor="renameKnowledgeBaseName">名称</label>
        <input
          autoFocus
          defaultValue={knowledgeBase.name}
          id="renameKnowledgeBaseName"
          name="knowledgeBaseName"
          required
        />
        <label htmlFor="renameKnowledgeBaseDescription">说明</label>
        <textarea
          defaultValue={knowledgeBase.description ?? ""}
          id="renameKnowledgeBaseDescription"
          name="knowledgeBaseDescription"
          rows={4}
        />
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button className="primary-command" disabled={isSaving} type="submit">
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ArchiveKnowledgeBaseModal({
  isSaving,
  knowledgeBase,
  onClose,
  onConfirm,
}: {
  isSaving: boolean;
  knowledgeBase: KnowledgeBase;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal onClose={onClose} title="归档知识库">
      <div className="modal-form">
        <p className="form-context">
          将“{knowledgeBase.name}
          ”归档后，它不再出现在工作区，也不会参与后续检索和问答。
        </p>
        <p className="form-context">历史资料和审计记录会保留，以便追溯。</p>
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="reject-button"
            disabled={isSaving}
            onClick={onConfirm}
            type="button"
          >
            归档
          </button>
        </div>
      </div>
    </Modal>
  );
}

function RenameConversationModal({
  conversation,
  isSaving,
  onClose,
  onSubmit,
}: {
  conversation: Conversation;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal onClose={onClose} title="重命名问答">
      <form className="modal-form" onSubmit={onSubmit}>
        <label htmlFor="conversationTitle">标题</label>
        <input
          autoFocus
          defaultValue={conversation.title}
          id="conversationTitle"
          name="conversationTitle"
          maxLength={240}
          required
        />
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button className="primary-command" disabled={isSaving} type="submit">
            保存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ArchiveConversationModal({
  conversation,
  isSaving,
  onClose,
  onConfirm,
}: {
  conversation: Conversation;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal onClose={onClose} title="归档问答">
      <div className="modal-form">
        <p className="form-context">
          将“{conversation.title}
          ”归档后，它会从历史列表中移除，问答消息和引用仍会保留。
        </p>
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="reject-button"
            disabled={isSaving}
            onClick={onConfirm}
            type="button"
          >
            归档
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ArchiveDocumentModal({
  document,
  isSaving,
  onClose,
  onConfirm,
}: {
  document: KnowledgeDocument;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal onClose={onClose} title="删除已导入文档">
      <div className="modal-form">
        <p className="form-context">
          将“{document.title}”从当前知识库和检索索引中移除。
        </p>
        <p className="form-context">
          原始内容会保留在归档记录中，同一文件之后可以重新导入。
        </p>
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="reject-button"
            disabled={isSaving}
            onClick={onConfirm}
            type="button"
          >
            删除并归档
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DocumentReaderModal({
  document,
  detail,
  documents,
  isSaving,
  onClose,
  onUpdateGovernance,
}: {
  document: KnowledgeDocument;
  detail: KnowledgeDocumentDetail | null;
  documents: KnowledgeDocument[];
  isSaving: boolean;
  onClose: () => void;
  onUpdateGovernance: (
    document: KnowledgeDocument,
    payload: {
      sourceTrustLevel: "verified" | "standard" | "unverified";
      effectiveAt: string | null;
      expiresAt: string | null;
      conflictState: "none" | "conflicted";
      supersedesDocumentId: string | null;
    },
  ) => void;
}) {
  const [isGovernanceOpen, setIsGovernanceOpen] = useState(false);
  const [sourceTrustLevel, setSourceTrustLevel] = useState(
    normalizeTrustLevel(document.sourceTrustLevel),
  );
  const [effectiveAt, setEffectiveAt] = useState(
    toDateTimeLocalValue(document.effectiveAt),
  );
  const [expiresAt, setExpiresAt] = useState(
    toDateTimeLocalValue(document.expiresAt),
  );
  const [conflictState, setConflictState] = useState(
    normalizeConflictState(document.conflictState),
  );
  const [supersedesDocumentId, setSupersedesDocumentId] = useState(
    document.supersedesDocumentId ?? "",
  );

  useEffect(() => {
    setIsGovernanceOpen(false);
    setSourceTrustLevel(normalizeTrustLevel(document.sourceTrustLevel));
    setEffectiveAt(toDateTimeLocalValue(document.effectiveAt));
    setExpiresAt(toDateTimeLocalValue(document.expiresAt));
    setConflictState(normalizeConflictState(document.conflictState));
    setSupersedesDocumentId(document.supersedesDocumentId ?? "");
  }, [document.id]);

  const eligiblePredecessors = documents.filter(
    (candidate) =>
      candidate.id !== document.id &&
      candidate.knowledgeBaseId === document.knowledgeBaseId &&
      candidate.status !== "archived",
  );

  function handleGovernanceSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onUpdateGovernance(document, {
      sourceTrustLevel,
      effectiveAt: toIsoTimestamp(effectiveAt),
      expiresAt: toIsoTimestamp(expiresAt),
      conflictState,
      supersedesDocumentId: supersedesDocumentId || null,
    });
  }

  return (
    <Modal
      className="reader-modal"
      onClose={onClose}
      title={`阅读：${document.title}`}
    >
      <div className="document-reader">
        <div className="document-reader-meta">
          <span className="document-reader-type">
            {documentReaderSourceLabel(document.sourceType)}
          </span>
          <span className="document-reader-status">
            {document.status === "indexed"
              ? "已入库"
              : documentReaderStatusLabel(document.status)}
          </span>
          {document.webContentState === "changed" && (
            <span className="document-reader-change-warning">
              网页正文已变化，当前显示的是已入库历史版本
            </span>
          )}
          {document.sourceUrl &&
            (document.sourceValidationState === "unavailable" ? (
              <span className="document-reader-source unavailable">
                原始来源当前不可用
              </span>
            ) : (
              <a
                className="document-reader-source"
                href={document.sourceRedirectUrl ?? document.sourceUrl}
                rel="noreferrer"
                target="_blank"
              >
                打开来源
              </a>
            ))}
        </div>
        <section className="document-governance" aria-label="资料治理">
          <div className="document-governance-summary">
            <div>
              <strong>资料治理</strong>
              <span>
                {documentTrustLabel(document.sourceTrustLevel)} ·
                {document.effectiveAt ? " 已设生效时间" : " 即刻生效"} ·
                {document.conflictState === "conflicted"
                  ? " 存在冲突"
                  : " 无冲突"}
              </span>
            </div>
            <button
              aria-expanded={isGovernanceOpen}
              className="secondary-command governance-toggle"
              onClick={() => setIsGovernanceOpen((open) => !open)}
              type="button"
            >
              {isGovernanceOpen ? "收起" : "编辑"}
            </button>
          </div>
          {isGovernanceOpen && (
            <form
              className="document-governance-form"
              onSubmit={handleGovernanceSubmit}
            >
              <p>
                用于调整检索证据优先级；过期或冲突资料仍保留为可追溯历史证据。
              </p>
              <div className="document-governance-fields">
                <label>
                  来源可信度
                  <select
                    disabled={isSaving}
                    onChange={(event) =>
                      setSourceTrustLevel(
                        event.target.value as
                          "verified" | "standard" | "unverified",
                      )
                    }
                    value={sourceTrustLevel}
                  >
                    <option value="verified">已核验</option>
                    <option value="standard">常规</option>
                    <option value="unverified">未核验</option>
                  </select>
                </label>
                <label>
                  冲突状态
                  <select
                    disabled={isSaving}
                    onChange={(event) =>
                      setConflictState(
                        event.target.value as "none" | "conflicted",
                      )
                    }
                    value={conflictState}
                  >
                    <option value="none">无冲突</option>
                    <option value="conflicted">存在冲突</option>
                  </select>
                </label>
                <label>
                  生效时间
                  <input
                    disabled={isSaving}
                    onChange={(event) => setEffectiveAt(event.target.value)}
                    type="datetime-local"
                    value={effectiveAt}
                  />
                </label>
                <label>
                  到期时间
                  <input
                    disabled={isSaving}
                    onChange={(event) => setExpiresAt(event.target.value)}
                    type="datetime-local"
                    value={expiresAt}
                  />
                </label>
                <label className="document-governance-replacement">
                  替代哪份资料
                  <select
                    disabled={isSaving}
                    onChange={(event) =>
                      setSupersedesDocumentId(event.target.value)
                    }
                    value={supersedesDocumentId}
                  >
                    <option value="">不替代其他资料</option>
                    {eligiblePredecessors.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.title}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="document-governance-actions">
                <span>版本 {document.governanceVersion}</span>
                <button
                  className="primary-command"
                  disabled={isSaving}
                  type="submit"
                >
                  {isSaving ? "保存中..." : "保存治理设置"}
                </button>
              </div>
            </form>
          )}
        </section>
        {detail ? (
          <MarkdownContent
            className="document-reader-content"
            content={detail.rawContent}
          />
        ) : (
          <div className="document-reader-loading">正在读取文档内容…</div>
        )}
      </div>
    </Modal>
  );
}

function normalizeTrustLevel(
  value: KnowledgeDocument["sourceTrustLevel"],
): "verified" | "standard" | "unverified" {
  return value === "verified" || value === "unverified" ? value : "standard";
}

function normalizeConflictState(
  value: KnowledgeDocument["conflictState"],
): "none" | "conflicted" {
  return value === "conflicted" ? "conflicted" : "none";
}

function toDateTimeLocalValue(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const timezoneOffset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - timezoneOffset).toISOString().slice(0, 16);
}

function toIsoTimestamp(value: string) {
  return value ? new Date(value).toISOString() : null;
}

function documentTrustLabel(value: KnowledgeDocument["sourceTrustLevel"]) {
  const labels: Record<string, string> = {
    verified: "已核验来源",
    standard: "常规来源",
    unverified: "未核验来源",
  };
  return labels[value] ?? "常规来源";
}

function documentReaderSourceLabel(
  sourceType: KnowledgeDocument["sourceType"],
) {
  const labels: Record<KnowledgeDocument["sourceType"], string> = {
    plain_text: "TXT",
    markdown: "Markdown",
    pdf: "PDF",
    docx: "DOCX",
    webpage: "网页",
  };
  return labels[sourceType];
}

function documentReaderStatusLabel(status: KnowledgeDocument["status"]) {
  const labels: Record<KnowledgeDocument["status"], string> = {
    failed: "失败",
    indexed: "已入库",
    processing: "处理中",
    queued: "排队中",
    archived: "已归档",
  };
  return labels[status];
}

function NoteModal({
  disabled,
  isSaving,
  knowledgeBaseName,
  note,
  onClose,
  onSubmit,
}: {
  disabled?: boolean;
  isSaving: boolean;
  knowledgeBaseName: string;
  note?: Note;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal onClose={onClose} title={note ? "编辑笔记" : "新建笔记"}>
      <form className="modal-form" onSubmit={onSubmit}>
        <p className="form-context">写入：{knowledgeBaseName}</p>
        <label htmlFor="noteTitle">标题</label>
        <input
          autoFocus
          disabled={disabled}
          id="noteTitle"
          name="noteTitle"
          defaultValue={note?.title}
          placeholder="记录一个可检索的结论"
          required
        />
        <label htmlFor="noteContent">内容</label>
        <textarea
          disabled={disabled}
          id="noteContent"
          name="noteContent"
          defaultValue={note?.content}
          placeholder="写下事实、判断、来源与待验证的问题..."
          rows={10}
        />
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="primary-command"
            disabled={disabled || isSaving}
            type="submit"
          >
            {note ? "保存修改" : "保存笔记"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function UploadModal({
  chunkers,
  disabled,
  isSaving,
  knowledgeBaseName,
  onClose,
  onSubmit,
}: {
  chunkers: ExtensionCatalog["chunkers"];
  disabled: boolean;
  isSaving: boolean;
  knowledgeBaseName: string;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal onClose={onClose} title="导入资料">
      <form className="modal-form" onSubmit={onSubmit}>
        <p className="form-context">导入到：{knowledgeBaseName}</p>
        <p className="form-section-label">导入方式</p>
        <label htmlFor="uploadUrl">网页 URL（可选）</label>
        <input
          disabled={disabled || isSaving}
          id="uploadUrl"
          name="url"
          placeholder="https://example.com/article"
          type="url"
        />
        <label htmlFor="uploadTitle">网页标题（可选）</label>
        <input
          disabled={disabled || isSaving}
          id="uploadTitle"
          name="title"
          placeholder="默认使用网页标题"
          type="text"
        />
        <label htmlFor="uploadFile">选择文件</label>
        <input
          accept=".txt,.md,.markdown,.pdf,.docx"
          disabled={disabled || isSaving}
          id="uploadFile"
          name="file"
          type="file"
        />
        <label htmlFor="uploadChunker">文本切分策略</label>
        <select
          defaultValue={chunkers[0]?.name ?? "structured"}
          disabled={disabled || isSaving || chunkers.length === 0}
          id="uploadChunker"
          name="chunker"
        >
          {chunkers.map((chunker) => (
            <option key={chunker.name} value={chunker.name}>
              {chunker.name} (v{chunker.version})
            </option>
          ))}
        </select>
        {chunkers.length === 0 && (
          <p className="upload-hint">
            当前未读取到切分策略目录。请检查服务端扩展部署后重试。
          </p>
        )}
        <p className="upload-hint">
          支持网页 URL、TXT、Markdown、PDF、DOCX，单文件最大 25
          MB。导入后将自动抓取或解析、切块并建立索引。
        </p>
        <div className="modal-actions">
          <button className="secondary-command" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="primary-command"
            disabled={disabled || isSaving || chunkers.length === 0}
            type="submit"
          >
            开始导入
          </button>
        </div>
      </form>
    </Modal>
  );
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) =>
    window.setTimeout(resolve, milliseconds),
  );
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
