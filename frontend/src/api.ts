export type KnowledgeBase = {
  id: string;
  name: string;
  description: string | null;
  status: string;
  embeddingRevision: number;
  indexStatus: "ready" | "stale" | "building";
  graphRevision: number;
  graphStatus: "ready" | "stale" | "building";
  createdAt: string;
  updatedAt: string;
};

export type Note = {
  id: string;
  knowledgeBaseId: string;
  title: string;
  content: string;
  version: number;
  status: string;
  createdAt: string;
  updatedAt: string;
};

export type KnowledgeDocument = {
  id: string;
  knowledgeBaseId: string;
  title: string;
  sourceType: "plain_text" | "markdown" | "pdf" | "docx" | "webpage";
  sourceUrl: string | null;
  sourceValidationState:
    | "not_applicable"
    | "pending"
    | "valid"
    | "unavailable"
    | "unchecked"
    | string;
  sourceIsApproved: boolean;
  sourceValidatedAt: string | null;
  sourceValidationStatusCode: number | null;
  sourceRedirectUrl: string | null;
  sourceContentType: string | null;
  sourceValidationErrorCode: string | null;
  sourceTrustLevel: "verified" | "standard" | "unverified" | string;
  effectiveAt: string | null;
  expiresAt: string | null;
  conflictState: "none" | "conflicted" | string;
  supersedesDocumentId: string | null;
  governanceVersion: number;
  status: "queued" | "processing" | "indexed" | "failed" | "archived";
  createdAt: string;
  updatedAt: string;
};

export type KnowledgeDocumentDetail = KnowledgeDocument & {
  rawContent: string;
};

export type KnowledgeTag = {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  name: string;
  description: string | null;
  state: "active" | "archived" | string;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type KnowledgeTagAssignment = {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  tagId: string;
  tagName: string;
  assetType: "document" | "note";
  assetId: string;
  state: "pending" | "approved" | "rejected";
  source: "manual" | "rule_match" | string;
  confidence: number | null;
  reviewerId: string | null;
  reviewedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type IngestionJob = {
  id: string;
  documentId: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  attempts: number;
  errorCode: string | null;
  errorMessage: string | null;
  configSnapshot: Record<string, string>;
  createdAt: string;
  updatedAt: string;
};

export type CreateDocumentResult = {
  document: KnowledgeDocument;
  ingestionJob: IngestionJob;
};

export type Evidence = {
  sourceType: "note" | "document_chunk";
  sourceId: string;
  title: string;
  content: string;
  score: number;
  locator: string;
  sourceUrl?: string | null;
  sourceValidationState?: string;
  sourceIsApproved?: boolean;
  sourceTrustLevel?: string;
  governanceAvailability?: string;
  conflictState?: string;
};

export type RetrievalDiagnostics = {
  keywordCandidates: number;
  semanticCandidates: number;
  fusedCandidates: number;
  entityRetrievalEnabled: boolean;
  entityMatchedEntities: number;
  entityCandidates: number;
  entityCoveredDocuments: number;
  dualRouteFusedCandidates: number;
  tagRetrievalEnabled: boolean;
  tagMatchedTags: number;
  tagCandidates: number;
  tagCoveredAssets: number;
  tagRouteFusedCandidates: number;
  metadataBoostedCandidates: number;
  rerankCandidates: number;
  finalCandidates: number;
  dynamicTopKEnabled: boolean;
  dynamicTopKProfile: string;
  dynamicTopKMinimum: number;
  dynamicTopKSelected: number;
  dynamicTopKSourceCoverage: number;
  dynamicTopKBudgetTokens: number;
  dynamicTopKEstimatedTokens: number;
  dynamicTopKStopReason: string;
  dynamicTopKBoundaryScoreGap: number | null;
  governanceExcludedSuperseded: number;
  governanceExcludedFutureEffective: number;
  governanceExpiredCandidates: number;
  governanceConflictedCandidates: number;
  governanceTrustAdjustedCandidates: number;
  queryRewriteMs: number;
  queryVariantCount: number;
  querySubqueryCount: number;
  querySynonymCount: number;
  queryFanoutCandidates: number;
  hybridRetrievalMs: number;
  rerankMs: number;
  contextExpanded: number;
  contextCharacters: number;
  contextExpansionMs: number;
  graphMode: "local" | "multi_hop" | "global" | string;
  graphMatchedEntities: number;
  graphExpandedEntities: number;
  graphCandidates: number;
  graphCoveredDocuments: number;
  matchedCommunities: number;
  communitySummaryCandidates: number;
  communityExpandedChunks: number;
  communityCoveredDocuments: number;
  totalMs: number;
};

export type Citation = {
  citationIndex: number;
  sourceType: "note" | "document_chunk";
  sourceId: string;
  title: string;
  content: string;
  locator: string;
  score: number;
  sourceUrl?: string | null;
  sourceValidationState?: string;
  sourceIsApproved?: boolean;
};

export type Conversation = {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  title: string;
  state: "active";
  createdAt: string;
  updatedAt: string;
};

export type ConversationMessage = {
  id: string;
  workspaceId: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  state: "streaming" | "completed" | "failed";
  citations: Citation[];
  providerName: string | null;
  modelName: string | null;
  createdAt: string;
  updatedAt: string;
};

export type FeedbackSentiment = "helpful" | "unhelpful";

export type FeedbackReason =
  | "incorrect_answer"
  | "missing_evidence"
  | "irrelevant_evidence"
  | "citation_problem"
  | "outdated_information"
  | "other";

export type AnswerFeedback = {
  id: string;
  assistantMessageId: string;
  agentRunId: string;
  sentiment: FeedbackSentiment;
  reasonCode: FeedbackReason | null;
  stageEventIds: string[];
  createdAt: string;
  updatedAt: string;
};

export type FeedbackTriage = {
  id: string;
  feedbackId: string;
  category: string;
  state: "open" | "in_review" | "resolved" | "dismissed";
  resolutionTarget:
    "knowledge_draft" | "evaluation_case" | "product_bug" | null;
  reviewerId: string | null;
  reviewedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type FeedbackKnowledgeDraft = {
  id: string;
  feedbackTriageId: string;
  knowledgeBaseId: string;
  title: string;
  content: string;
  state: "pending" | "approved" | "rejected";
  reviewerId: string | null;
  reviewedAt: string | null;
  createdNoteId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type FeedbackEvaluationCase = {
  id: string;
  feedbackTriageId: string;
  knowledgeBaseId: string;
  query: string;
  expectedSourceTitles: string[];
  requiredKeywords: string[];
  limit: number;
  state: "pending" | "approved" | "rejected";
  reviewerId: string | null;
  reviewedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AnswerTrace = {
  step:
    | "routing"
    | "rewrite"
    | "retrieve"
    | "rerank"
    | "parent_context"
    | "diagnostics"
    | "grounding"
    | "community"
    | string;
  label: string;
  detail: string;
};

export type ProviderStatus = {
  provider: string;
  model: string;
  configured: boolean;
  developmentOnly: boolean;
};

export type RuntimeConfiguration = {
  llm: ProviderStatus;
  embedding: ProviderStatus;
  retrievalMode: string;
  workspaceAuthEnabled: boolean;
  productionReady: boolean;
  warnings: string[];
};

/** 由服务端部署并启用的解析、切分能力；浏览器只能读取目录和选择已启用项。 */
export type ExtensionDescriptor = {
  name: string;
  version: string;
  kind: "parser" | "chunker";
  sourceTypes: string[];
};

export type ExtensionCatalog = {
  parsers: ExtensionDescriptor[];
  chunkers: ExtensionDescriptor[];
};

export type WorkspaceModelConfiguration = {
  llmProvider: string;
  llmModel: string;
  llmBaseUrl: string;
  hasLlmApiKey: boolean;
  embeddingProvider: string;
  embeddingModel: string;
  embeddingBaseUrl: string;
  hasEmbeddingApiKey: boolean;
  embeddingDimensions: number;
  embeddingRevision: number;
  useQueryRewrite: boolean;
  useQueryRouter: boolean;
  useReranker: boolean;
  rerankerProvider: string;
  rerankerModel: string;
  rerankerBaseUrl: string;
  hasRerankerApiKey: boolean;
  canSaveSecrets: boolean;
};

export type UpdateWorkspaceModelConfiguration = {
  llmProvider: string;
  llmModel: string;
  llmBaseUrl: string;
  llmApiKey?: string;
  clearLlmApiKey: boolean;
  embeddingProvider: string;
  embeddingModel: string;
  embeddingBaseUrl: string;
  embeddingApiKey?: string;
  clearEmbeddingApiKey: boolean;
  embeddingDimensions: number;
  useQueryRewrite: boolean;
  useQueryRouter: boolean;
  useReranker: boolean;
  rerankerProvider: string;
  rerankerModel: string;
  rerankerBaseUrl: string;
  rerankerApiKey?: string;
  clearRerankerApiKey: boolean;
};

export type ModelConnectionKind = "llm" | "embedding" | "reranker";

export type TestModelConnectionPayload = {
  provider: string;
  model: string;
  baseUrl: string;
  apiKey?: string;
};

export type ModelConnectionTest = {
  provider: string;
  model: string;
  latencyMs: number;
  message: string;
};

export type ChangeProposal = {
  id: string;
  agentRunId: string;
  knowledgeBaseId: string;
  action: string;
  payload: Record<string, string>;
  rationale: string;
  state: "pending" | "approved" | "rejected" | "expired";
  riskLevel: "low" | "medium" | "high" | string;
  requiredRole: "viewer" | "editor" | "approver" | "owner" | string;
  evidenceSnapshot: ProposalEvidence[];
  expiresAt: string | null;
  createdAt: string;
  updatedAt: string;
};

/** 审批快照只包含来源定位信息，正文仍由证据面板按需读取。 */
export type ProposalEvidence = {
  sourceType?: string;
  sourceId?: string;
  title?: string;
  locator?: string;
  sourceUrl?: string | null;
  score?: number;
};

export type AgentRuntimeEvent = {
  event: string;
  runId?: string;
  threadId?: string | null;
  node?: string;
  tool?: string;
  sequence?: number;
  evidenceCount?: number;
  resultCount?: number;
};

export type AgentRuntimeToolCall = {
  id: string;
  node: string;
  toolName: string;
  state: string;
  requiresApproval: boolean;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AgentRuntimeRun = {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  state: string;
  policyVersion: string;
  threadId: string | null;
  currentNode: string;
  outputJson: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
  toolCalls: AgentRuntimeToolCall[];
};

export type MindMapNode = {
  id: string;
  label: string;
  kind: "root" | "document" | "concept" | "note" | "manual" | string;
  position: { x: number; y: number };
};

export type MindMapEdge = {
  id: string;
  source: string;
  target: string;
};

export type MindMapGraph = {
  nodes: MindMapNode[];
  edges: MindMapEdge[];
};

export type KnowledgeMindMap = {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  title: string;
  graph: MindMapGraph;
  version: number;
  createdAt: string;
  updatedAt: string;
};

type ApiErrorBody = { error?: { message?: string; code?: string } };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.error?.message ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function upload<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(path, { method: "POST", body: form });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.error?.message ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

type StreamHandlers = {
  onStarted: (assistantMessageId: string) => void;
  onCitation: (citation: Citation) => void;
  onTrace: (trace: AnswerTrace) => void;
  onDelta: (text: string) => void;
  onCompleted: (message: ConversationMessage) => void;
};

type RuntimeStreamHandlers = {
  onEvent: (event: AgentRuntimeEvent) => void;
  onCompleted: (run: AgentRuntimeRun) => void;
};

function parseSseEvent(block: string): { event: string; data: string } | null {
  const event = block
    .split("\n")
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const data = block
    .split("\n")
    .find((line) => line.startsWith("data:"))
    ?.slice("data:".length)
    .trim();
  return event && data ? { event, data } : null;
}

async function streamConversationMessage(
  conversationId: string,
  content: string,
  explainRetrieval: boolean,
  handlers: StreamHandlers,
) {
  const response = await fetch(
    `/api/v1/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, explainRetrieval }),
    },
  );
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.error?.message ?? `请求失败 (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseSseEvent(block);
      if (!event) continue;
      const payload = JSON.parse(event.data) as Record<string, unknown>;
      if (event.event === "started") {
        handlers.onStarted(String(payload.assistantMessageId));
      } else if (event.event === "trace") {
        handlers.onTrace(payload as unknown as AnswerTrace);
      } else if (event.event === "citation") {
        handlers.onCitation(payload as unknown as Citation);
      } else if (event.event === "delta") {
        handlers.onDelta(String(payload.text ?? ""));
      } else if (event.event === "completed") {
        handlers.onCompleted(payload as unknown as ConversationMessage);
      } else if (event.event === "error") {
        throw new Error(String(payload.message ?? "问答生成失败。"));
      }
    }
    if (done) break;
  }
}

async function streamAgentResearch(
  knowledgeBaseId: string,
  query: string,
  agenticMode: "auto" | "force" | "off",
  handlers: RuntimeStreamHandlers,
) {
  const response = await fetch("/api/v1/agent/runs/research/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ knowledgeBaseId, query, agenticMode }),
  });
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(body.error?.message ?? `璇锋眰澶辫触 (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseSseEvent(block);
      if (!event) continue;
      const payload = JSON.parse(event.data) as Record<string, unknown>;
      if (event.event === "completed") {
        handlers.onCompleted(payload as unknown as AgentRuntimeRun);
      } else if (event.event === "error") {
        throw new Error(String(payload.message ?? "Agent Runtime 执行失败。"));
      } else {
        handlers.onEvent({
          event: event.event,
          ...payload,
        } as AgentRuntimeEvent);
      }
    }
    if (done) break;
  }
}

export const api = {
  listKnowledgeBases: () =>
    request<{ items: KnowledgeBase[] }>("/api/v1/knowledge-bases"),
  createKnowledgeBase: (name: string, description: string) =>
    request<KnowledgeBase>("/api/v1/knowledge-bases", {
      method: "POST",
      body: JSON.stringify({ name, description: description || null }),
    }),
  updateKnowledgeBase: (
    knowledgeBaseId: string,
    payload: { name?: string; description?: string | null },
  ) =>
    request<KnowledgeBase>(`/api/v1/knowledge-bases/${knowledgeBaseId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  archiveKnowledgeBase: (knowledgeBaseId: string) =>
    request<KnowledgeBase>(`/api/v1/knowledge-bases/${knowledgeBaseId}`, {
      method: "DELETE",
    }),
  listKnowledgeTags: (knowledgeBaseId: string) =>
    request<{ items: KnowledgeTag[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/tags`,
    ),
  createKnowledgeTag: (
    knowledgeBaseId: string,
    payload: { name: string; description?: string },
  ) =>
    request<KnowledgeTag>(`/api/v1/knowledge-bases/${knowledgeBaseId}/tags`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  archiveKnowledgeTag: (tagId: string, version: number) =>
    request<KnowledgeTag>(`/api/v1/knowledge-tags/${tagId}`, {
      method: "DELETE",
      body: JSON.stringify({ version }),
    }),
  listKnowledgeTagAssignments: (knowledgeBaseId: string, state?: string) =>
    request<{ items: KnowledgeTagAssignment[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/tag-assignments${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  reviewKnowledgeTagAssignment: (
    assignmentId: string,
    decision: "approved" | "rejected",
  ) =>
    request<KnowledgeTagAssignment>(
      `/api/v1/tag-assignments/${assignmentId}/review`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
  listNotes: (knowledgeBaseId: string) =>
    request<{ items: Note[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/notes`,
    ),
  createNote: (knowledgeBaseId: string, title: string, content: string) =>
    request<Note>(`/api/v1/knowledge-bases/${knowledgeBaseId}/notes`, {
      method: "POST",
      body: JSON.stringify({ title, content }),
    }),
  updateNote: (
    noteId: string,
    title: string,
    content: string,
    version: number,
  ) =>
    request<Note>(`/api/v1/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify({ title, content, version }),
    }),
  listDocuments: (knowledgeBaseId: string) =>
    request<{ items: KnowledgeDocument[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/documents`,
    ),
  getDocument: (documentId: string) =>
    request<KnowledgeDocumentDetail>(`/api/v1/documents/${documentId}`),
  uploadDocument: (knowledgeBaseId: string, file: File, chunker?: string) => {
    const form = new FormData();
    form.set("knowledge_base_id", knowledgeBaseId);
    form.set("file", file);
    if (chunker) form.set("chunker", chunker);
    return upload<CreateDocumentResult>("/api/v1/documents/upload", form);
  },
  importUrlDocument: (
    knowledgeBaseId: string,
    url: string,
    title?: string,
    chunker?: string,
  ) =>
    request<CreateDocumentResult>("/api/v1/documents/url", {
      method: "POST",
      body: JSON.stringify({
        knowledgeBaseId,
        url,
        title: title || null,
        chunker: chunker || "structured",
      }),
    }),
  getIngestionJob: (jobId: string) =>
    request<IngestionJob>(`/api/v1/ingestion-jobs/${jobId}`),
  retryDocumentIngestion: (documentId: string) =>
    request<IngestionJob>(`/api/v1/documents/${documentId}/retry`, {
      method: "POST",
    }),
  revalidateDocumentSource: (documentId: string) =>
    request<KnowledgeDocument>(
      `/api/v1/documents/${documentId}/source-validation`,
      {
        method: "POST",
      },
    ),
  archiveDocument: (documentId: string) =>
    request<KnowledgeDocument>(`/api/v1/documents/${documentId}`, {
      method: "DELETE",
    }),
  updateDocumentGovernance: (
    documentId: string,
    payload: {
      sourceTrustLevel: "verified" | "standard" | "unverified";
      effectiveAt?: string | null;
      expiresAt?: string | null;
      conflictState: "none" | "conflicted";
      supersedesDocumentId?: string | null;
      governanceVersion: number;
    },
  ) =>
    request<KnowledgeDocument>(`/api/v1/documents/${documentId}/governance`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  rechunkKnowledgeBase: (knowledgeBaseId: string) =>
    request<{ documentCount: number; state: "building" }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/documents/rechunk`,
      { method: "POST" },
    ),
  search: (knowledgeBaseId: string, query: string) =>
    request<{
      retriever: string;
      cacheBackend: string;
      embeddingCacheHit: boolean;
      rewrittenQuery: string;
      queryRewriter: string;
      queryRewriteCacheHit: boolean;
      queryRewriteFallback: boolean;
      reranker: string | null;
      rerankerCacheHit: boolean;
      rerankerFallback: boolean;
      diagnostics: RetrievalDiagnostics;
      evidences: Evidence[];
    }>("/api/v1/retrieval/search", {
      method: "POST",
      body: JSON.stringify({ knowledgeBaseId, query }),
    }),
  listConversations: (knowledgeBaseId: string) =>
    request<{ items: Conversation[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/conversations`,
    ),
  createConversation: (knowledgeBaseId: string, title: string) =>
    request<Conversation>("/api/v1/conversations", {
      method: "POST",
      body: JSON.stringify({ knowledgeBaseId, title }),
    }),
  updateConversation: (conversationId: string, title: string) =>
    request<Conversation>(`/api/v1/conversations/${conversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  archiveConversation: (conversationId: string) =>
    request<Conversation>(`/api/v1/conversations/${conversationId}`, {
      method: "DELETE",
    }),
  listConversationMessages: (conversationId: string) =>
    request<{ items: ConversationMessage[] }>(
      `/api/v1/conversations/${conversationId}/messages`,
    ),
  submitAnswerFeedback: (
    assistantMessageId: string,
    payload: { sentiment: FeedbackSentiment; reasonCode?: FeedbackReason },
  ) =>
    request<{ feedback: AnswerFeedback }>(
      `/api/v1/conversation-messages/${assistantMessageId}/feedback`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  listFeedbackTriage: (
    knowledgeBaseId: string,
    state?: FeedbackTriage["state"],
  ) =>
    request<{ items: FeedbackTriage[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/feedback-triage${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  reviewFeedbackTriage: (
    triageId: string,
    payload: {
      state: FeedbackTriage["state"];
      resolutionTarget?: FeedbackTriage["resolutionTarget"];
    },
  ) =>
    request<FeedbackTriage>(`/api/v1/feedback-triage/${triageId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listFeedbackKnowledgeDrafts: (knowledgeBaseId: string, state?: string) =>
    request<{ items: FeedbackKnowledgeDraft[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/feedback-knowledge-drafts${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  createFeedbackKnowledgeDraft: (payload: {
    feedbackTriageId: string;
    title: string;
    content: string;
  }) =>
    request<FeedbackKnowledgeDraft>("/api/v1/feedback-knowledge-drafts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  reviewFeedbackKnowledgeDraft: (
    draftId: string,
    decision: "approved" | "rejected",
  ) =>
    request<FeedbackKnowledgeDraft>(
      `/api/v1/feedback-knowledge-drafts/${draftId}/review`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
  listFeedbackEvaluationCases: (knowledgeBaseId: string, state?: string) =>
    request<{ items: FeedbackEvaluationCase[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/feedback-evaluation-cases${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  createFeedbackEvaluationCase: (payload: {
    feedbackTriageId: string;
    query: string;
    expectedSourceTitles: string[];
    requiredKeywords: string[];
    limit: number;
  }) =>
    request<FeedbackEvaluationCase>("/api/v1/feedback-evaluation-cases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  reviewFeedbackEvaluationCase: (
    caseId: string,
    decision: "approved" | "rejected",
  ) =>
    request<FeedbackEvaluationCase>(
      `/api/v1/feedback-evaluation-cases/${caseId}/review`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
  streamConversationMessage,
  streamAgentResearch,
  getRuntimeExtensions: () =>
    request<ExtensionCatalog>("/api/v1/runtime/extensions"),
  getRuntimeConfiguration: () =>
    request<RuntimeConfiguration>("/api/v1/runtime/configuration"),
  getModelConfiguration: () =>
    request<WorkspaceModelConfiguration>("/api/v1/runtime/model-configuration"),
  updateModelConfiguration: (payload: UpdateWorkspaceModelConfiguration) =>
    request<WorkspaceModelConfiguration>(
      "/api/v1/runtime/model-configuration",
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  testModelConnection: (
    kind: ModelConnectionKind,
    payload: TestModelConnectionPayload,
  ) =>
    request<ModelConnectionTest>(
      `/api/v1/runtime/model-configuration/test/${kind}`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  rebuildEmbeddings: (knowledgeBaseId: string) =>
    request<{
      documentCount: number;
      chunkCount: number;
      embeddingRevision: number;
      indexStatus: "ready" | "stale" | "building";
    }>(`/api/v1/knowledge-bases/${knowledgeBaseId}/embeddings/rebuild`, {
      method: "POST",
    }),
  rebuildGraph: (knowledgeBaseId: string) =>
    request<{
      state: "ready" | "stale" | "building";
      documentCount: number;
      entityCount: number;
      relationCount: number;
      communityCount: number;
      graphRevision: number;
      extractorProvider: string;
      summaryProvider: string;
      summaryFallback: number;
    }>(`/api/v1/knowledge-bases/${knowledgeBaseId}/graph/rebuild`, {
      method: "POST",
    }),
  getGraphStatus: (knowledgeBaseId: string) =>
    request<{
      state: "ready" | "stale" | "building";
      documentCount: number;
      entityCount: number;
      relationCount: number;
      communityCount: number;
      graphRevision: number;
      extractorProvider: string;
      summaryProvider: string;
      summaryFallback: number;
    }>(`/api/v1/knowledge-bases/${knowledgeBaseId}/graph/status`),
  listMindMaps: (knowledgeBaseId: string) =>
    request<{ items: KnowledgeMindMap[] }>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/mind-maps`,
    ),
  generateMindMap: (knowledgeBaseId: string) =>
    request<KnowledgeMindMap>(
      `/api/v1/knowledge-bases/${knowledgeBaseId}/mind-maps/generate`,
      { method: "POST" },
    ),
  updateMindMap: (
    mindMapId: string,
    payload: { title: string; graph: MindMapGraph; version: number },
  ) =>
    request<KnowledgeMindMap>(`/api/v1/mind-maps/${mindMapId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  createProposal: (
    knowledgeBaseId: string,
    title: string,
    content: string,
    rationale: string,
    evidenceSnapshot: ProposalEvidence[] = [],
  ) =>
    request<{ proposal: ChangeProposal }>("/api/v1/agent/note-proposals", {
      method: "POST",
      body: JSON.stringify({
        knowledgeBaseId,
        title,
        content,
        rationale,
        evidenceSnapshot,
      }),
    }),
  approveProposal: (proposalId: string) =>
    request<ChangeProposal>(`/api/v1/change-proposals/${proposalId}/approve`, {
      method: "POST",
    }),
  rejectProposal: (proposalId: string) =>
    request<ChangeProposal>(`/api/v1/change-proposals/${proposalId}/reject`, {
      method: "POST",
    }),
};
