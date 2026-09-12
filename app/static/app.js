const state = {
  view: sessionStorage.getItem("my-agent-view") || "chat",
  mode: sessionStorage.getItem("my-agent-mode") || "chat",
  histories: { chat: readHistory("chat"), agent: readHistory("agent") },
  sessionIds: { chat: null, agent: null },
  workspaces: [],
  sessions: [],
  activeWorkspaceId: sessionStorage.getItem("my-agent-workspace"),
  busy: false,
  latestReview: readJson("my-agent-latest-review", null),
  gitFileCount: 0,
  estate: { watchlists: [], sources: [], jobs: [], busy: false },
};

const messages = document.querySelector("#messages");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const composer = document.querySelector("#composer");
const modeNote = document.querySelector("#mode-note");
const workspaceSelect = document.querySelector("#workspace-select");
const runPanel = document.querySelector("#run-panel");
const runEvents = document.querySelector("#run-events");
const planSteps = document.querySelector("#plan-steps");
const reviewResult = document.querySelector("#review-result");

document.querySelectorAll(".mode-tab").forEach((button) => {
  button.addEventListener("click", () => switchMode(button.dataset.mode));
});
document.querySelectorAll(".bottom-nav button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.target));
});
composer.addEventListener("submit", (event) => {
  event.preventDefault();
  submitMessage();
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});
input.addEventListener("input", resizeInput);
workspaceSelect.addEventListener("change", () => selectWorkspace(workspaceSelect.value));
document.querySelector("#workspace-form").addEventListener("submit", createWorkspace);
document.querySelector("#new-chat").addEventListener("click", () => createNewSession("chat"));
document.querySelector("#new-agent").addEventListener("click", () => createNewSession("agent"));
document.querySelector("#refresh-git").addEventListener("click", refreshGit);
document.querySelector("#commit-form").addEventListener("submit", commitChanges);
document.querySelector("#rename-workspace").addEventListener("click", renameWorkspace);
document.querySelector("#delete-workspace").addEventListener("click", deleteWorkspace);
document.querySelectorAll("[data-estate-tab]").forEach((button) => {
  button.addEventListener("click", () => switchEstatePane(button.dataset.estateTab));
});
document.querySelector("#estate-analysis-form").addEventListener("submit", submitEstateAnalysis);
document.querySelector("#estate-watchlist").addEventListener("change", applySelectedWatchlist);
document.querySelector("#watchlist-form").addEventListener("submit", saveWatchlist);
document.querySelector("#watchlist-reset").addEventListener("click", resetWatchlistForm);
document.querySelector("#estate-job-form").addEventListener("submit", createEstateJob);
document.querySelector("#refresh-sources").addEventListener("click", loadEstateSources);
document.querySelector("#refresh-jobs").addEventListener("click", loadEstateJobs);

function switchView(view) {
  const allowed = ["chat", "projects", "real-estate", "running", "review", "usage"];
  if (!allowed.includes(view)) view = "chat";
  state.view = view;
  sessionStorage.setItem("my-agent-view", view);
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("active", section.dataset.view === view);
  });
  document.querySelectorAll(".bottom-nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === view);
  });
  composer.classList.toggle("hidden", view !== "chat");
  if (view === "review") {
    document.querySelector('.bottom-nav [data-target="review"]').classList.remove("has-update");
  }
  if (view === "projects") refreshGit();
  if (view === "real-estate") loadEstateDashboard();
  if (view === "usage") checkUsage();
}

function switchMode(mode) {
  if (mode === "real-estate") {
    switchView("real-estate");
    return;
  }
  if (state.busy || !["chat", "agent"].includes(mode)) return;
  state.mode = mode;
  sessionStorage.setItem("my-agent-mode", mode);
  document.querySelectorAll(".mode-tab").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  replaceChildren(
    modeNote,
    element("span", { className: `note-icon ${mode === "agent" ? "agent" : ""}` }, mode === "agent" ? "A" : "C"),
    element(
      "div",
      {},
      element("strong", {}, mode === "agent" ? "Gemini Architect" : "대화 전용 · Gemini"),
      element("p", {}, mode === "agent" ? "Coding 작업을 Reviewer가 독립 검토합니다." : "로컬 PC에는 접근하지 않습니다."),
    ),
  );
  input.placeholder = mode === "chat" ? "메시지를 입력하세요" : "PC에서 수행할 작업을 요청하세요";
  renderMessages();
}

async function submitMessage() {
  const text = input.value.trim();
  if (!text || state.busy) return;
  if (state.mode === "agent" && !state.activeWorkspaceId) {
    showError("먼저 Projects에서 workspace를 등록하세요.");
    return;
  }
  const historyBefore = [...state.histories[state.mode]];
  appendHistory({ role: "user", content: text });
  input.value = "";
  resizeInput();
  setBusy(true);

  try {
    const sessionId = await ensureSession(text);
    if (state.mode === "chat") {
      await sendChat(text, historyBefore, sessionId);
    } else {
      await sendAgent(text, historyBefore, sessionId);
    }
    await loadSessions();
  } catch (error) {
    appendHistory({ role: "assistant", content: `오류: ${error.message}`, error: true });
  } finally {
    setBusy(false);
    input.focus();
  }
}

async function ensureSession(firstMessage) {
  if (state.sessionIds[state.mode]) return state.sessionIds[state.mode];
  // General Chat does not use local files or tools, so it must remain usable
  // before a project workspace is configured. In that case sessionStorage is
  // the cache/persistence layer until the user selects a workspace.
  if (!state.activeWorkspaceId && state.mode === "chat") return null;
  const session = await requestJson("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      name: firstMessage.replace(/\s+/g, " ").slice(0, 48),
      workspace_id: state.activeWorkspaceId,
      mode: state.mode,
    }),
  });
  state.sessionIds[state.mode] = session.id;
  sessionStorage.setItem(sessionIdKey(state.mode), session.id);
  return session.id;
}

async function sendChat(message, history, sessionId) {
  const payload = await requestJson("/api/chat", {
    method: "POST",
    body: JSON.stringify({
      message,
      history: apiHistory(history),
      session_id: sessionId,
      workspace_id: state.activeWorkspaceId,
    }),
  });
  appendHistory({ role: "assistant", content: payload.answer });
}

async function sendAgent(message, history, sessionId) {
  resetRun();
  setPhase("request", "done");
  setPhase("plan", "running");
  setRunTitle("Agent 작업 중", "running");
  switchView("running");
  const response = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history: apiHistory(history),
      session_id: sessionId,
      workspace_id: state.activeWorkspaceId,
    }),
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Agent 연결에 실패했습니다.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalReceived = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const line = block.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "final") {
        finalReceived = true;
        appendHistory({ role: "assistant", content: event.answer });
        setPhase("result", "done");
      } else if (event.type === "error") {
        setRunTitle("Agent 작업 실패", "failed");
        throw new Error(event.message);
      } else {
        renderRunEvent(event);
      }
    }
    if (done) break;
  }
  if (!finalReceived) throw new Error("Agent 응답이 예기치 않게 종료되었습니다.");
  setRunTitle("Agent 작업 완료", "done");
  await refreshGit();
}

function resetRun() {
  planSteps.replaceChildren();
  runEvents.replaceChildren();
  document.querySelectorAll("#phase-flow [data-phase]").forEach((item) => {
    item.className = item.dataset.phase === "request" ? "done" : "";
  });
}

function renderRunEvent(event) {
  if (event.type === "plan") {
    setPhase("plan", "done");
    renderPlan(event.steps || []);
    return;
  }
  if (event.type === "plan_step") {
    const item = [...planSteps.children].find((child) => child.dataset.stepId === event.id);
    if (item) item.className = `plan-${event.status}`;
    return;
  }
  if (event.type === "phase") {
    setPhase(event.name, event.status);
    return;
  }
  if (event.type === "review") {
    state.latestReview = event.review;
    sessionStorage.setItem("my-agent-latest-review", JSON.stringify(event.review));
    renderReview();
    document.querySelector('.bottom-nav [data-target="review"]').classList.add("has-update");
    return;
  }
  const labels = { running: "실행 중", completed: "완료", failed: "실패" };
  let text = event.message || "";
  if (event.type === "agent") text = `${event.name} 선택`;
  if (event.type === "tool") text = `${event.name}() · ${labels[event.status] || event.status}`;
  if (!text) return;
  const item = element("li", { className: event.type === "tool" ? `tool-${event.status}` : "" }, text);
  runEvents.append(item);
  item.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPlan(steps) {
  planSteps.replaceChildren();
  steps.forEach((step) => {
    planSteps.append(
      element("li", { className: `plan-${step.status || "pending"}`, dataset: { stepId: String(step.id) } }, step.title),
    );
  });
}

function setPhase(name, status) {
  const item = [...document.querySelectorAll("#phase-flow [data-phase]")]
    .find((candidate) => candidate.dataset.phase === name);
  if (item) item.className = status;
}

function setRunTitle(text, status) {
  const markerClass = status === "running" ? "spinner" : status === "done" ? "done-mark" : status === "failed" ? "failed-mark" : "idle-mark";
  replaceChildren(runPanel.querySelector(".run-title"), element("span", { className: markerClass }, status === "done" ? "✓" : status === "failed" ? "!" : ""), element("strong", {}, text));
}

async function loadWorkspaces() {
  state.workspaces = await requestJson("/api/workspaces");
  if (!state.workspaces.some((item) => item.id === state.activeWorkspaceId)) {
    state.activeWorkspaceId = state.workspaces[0]?.id || null;
  }
  workspaceSelect.replaceChildren();
  if (!state.workspaces.length) {
    workspaceSelect.append(element("option", { value: "" }, "Workspace 없음"));
  } else {
    state.workspaces.forEach((workspace) => {
      const option = element("option", { value: workspace.id }, workspace.name);
      option.selected = workspace.id === state.activeWorkspaceId;
      workspaceSelect.append(option);
    });
  }
  if (state.activeWorkspaceId) {
    sessionStorage.setItem("my-agent-workspace", state.activeWorkspaceId);
    state.histories.chat = readJson(historyKey("chat"), state.histories.chat);
    state.histories.agent = readJson(historyKey("agent"), state.histories.agent);
    state.sessionIds.chat = sessionStorage.getItem(sessionIdKey("chat"));
    state.sessionIds.agent = sessionStorage.getItem(sessionIdKey("agent"));
  }
  renderActiveWorkspace();
  await loadSessions();
}

async function selectWorkspace(workspaceId) {
  if (!workspaceId || workspaceId === state.activeWorkspaceId || state.busy) return;
  state.activeWorkspaceId = workspaceId;
  sessionStorage.setItem("my-agent-workspace", workspaceId);
  state.sessionIds = {
    chat: sessionStorage.getItem(sessionIdKey("chat")),
    agent: sessionStorage.getItem(sessionIdKey("agent")),
  };
  state.histories = {
    chat: readJson(historyKey("chat"), []),
    agent: readJson(historyKey("agent"), []),
  };
  state.latestReview = null;
  renderReview();
  renderMessages();
  renderActiveWorkspace();
  await loadSessions();
  await refreshGit();
}

function renderActiveWorkspace() {
  const workspace = activeWorkspace();
  document.querySelector("#active-workspace-name").textContent = workspace?.name || "선택된 프로젝트 없음";
  document.querySelector("#active-workspace-root").textContent = workspace?.root_path || "";
}

async function createWorkspace(event) {
  event.preventDefault();
  try {
    const workspace = await requestJson("/api/workspaces", {
      method: "POST",
      body: JSON.stringify({
        name: document.querySelector("#workspace-name").value.trim(),
        root_path: document.querySelector("#workspace-path").value.trim(),
      }),
    });
    event.target.reset();
    state.activeWorkspaceId = workspace.id;
    await loadWorkspaces();
  } catch (error) {
    showError(error.message);
  }
}

async function renameWorkspace() {
  const workspace = activeWorkspace();
  if (!workspace) return;
  const name = window.prompt("새 프로젝트 이름", workspace.name);
  if (!name?.trim()) return;
  try {
    await requestJson(`/api/workspaces/${encodeURIComponent(workspace.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim() }),
    });
    await loadWorkspaces();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteWorkspace() {
  const workspace = activeWorkspace();
  if (!workspace || !window.confirm(`"${workspace.name}" 등록을 해제할까요? 폴더 자체는 삭제되지 않습니다.`)) return;
  try {
    await requestJson(`/api/workspaces/${encodeURIComponent(workspace.id)}`, { method: "DELETE" });
    state.activeWorkspaceId = null;
    await loadWorkspaces();
  } catch (error) {
    showError(error.message);
  }
}

async function loadSessions() {
  const list = document.querySelector("#session-list");
  if (!state.activeWorkspaceId) {
    state.sessions = [];
    replaceChildren(list, element("p", { className: "empty-inline" }, "Workspace를 먼저 등록하세요."));
    return;
  }
  state.sessions = await requestJson(`/api/sessions?workspace_id=${encodeURIComponent(state.activeWorkspaceId)}`);
  for (const mode of ["chat", "agent"]) {
    const selected = state.sessionIds[mode];
    if (selected && !state.sessions.some((item) => item.id === selected && item.mode === mode)) {
      state.sessionIds[mode] = null;
      sessionStorage.removeItem(sessionIdKey(mode));
    }
  }
  list.replaceChildren();
  if (!state.sessions.length) {
    list.append(element("p", { className: "empty-inline" }, "저장된 세션이 없습니다."));
    return;
  }
  state.sessions.forEach((session) => {
    const open = element("button", { type: "button", className: "session-open" });
    open.append(element("strong", {}, session.name), element("span", {}, `${session.mode} · ${formatTime(session.updated_at)}`));
    open.addEventListener("click", () => openSession(session.id));
    const rename = element("button", { type: "button", title: "이름 변경" }, "✎");
    rename.addEventListener("click", () => renameSession(session));
    const remove = element("button", { type: "button", title: "삭제", className: "danger-link" }, "×");
    remove.addEventListener("click", () => removeSession(session));
    list.append(element("article", { className: "session-row" }, open, element("div", { className: "session-actions" }, rename, remove)));
  });
}

async function createNewSession(mode) {
  if (!state.activeWorkspaceId) return showError("Workspace를 먼저 등록하세요.");
  try {
    const session = await requestJson("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ name: "New session", workspace_id: state.activeWorkspaceId, mode }),
    });
    state.sessionIds[mode] = session.id;
    sessionStorage.setItem(sessionIdKey(mode), session.id);
    state.histories[mode] = [];
    persistHistory(mode);
    switchMode(mode);
    switchView("chat");
    await loadSessions();
  } catch (error) {
    showError(error.message);
  }
}

async function openSession(sessionId) {
  try {
    const session = await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (session.workspace_id !== state.activeWorkspaceId) return;
    state.sessionIds[session.mode] = session.id;
    sessionStorage.setItem(sessionIdKey(session.mode), session.id);
    state.histories[session.mode] = session.messages.map(({ role, content }) => ({ role, content }));
    const reviewedMessage = [...session.messages].reverse().find((item) => item.metadata?.review);
    state.latestReview = reviewedMessage?.metadata?.review || null;
    renderReview();
    persistHistory(session.mode);
    switchMode(session.mode);
    switchView("chat");
  } catch (error) {
    showError(error.message);
  }
}

async function renameSession(session) {
  const name = window.prompt("새 세션 이름", session.name);
  if (!name?.trim()) return;
  try {
    await requestJson(`/api/sessions/${encodeURIComponent(session.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim() }),
    });
    await loadSessions();
  } catch (error) {
    showError(error.message);
  }
}

async function removeSession(session) {
  if (!window.confirm(`"${session.name}" 세션을 삭제할까요?`)) return;
  try {
    await requestJson(`/api/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
    if (state.sessionIds[session.mode] === session.id) {
      state.sessionIds[session.mode] = null;
      sessionStorage.removeItem(sessionIdKey(session.mode));
      state.histories[session.mode] = [];
      persistHistory(session.mode);
      renderMessages();
    }
    await loadSessions();
  } catch (error) {
    showError(error.message);
  }
}

async function refreshGit() {
  const summary = document.querySelector("#git-summary");
  const stat = document.querySelector("#git-stat");
  const files = document.querySelector("#diff-files");
  if (!state.activeWorkspaceId) {
    summary.textContent = "Workspace를 먼저 선택하세요.";
    stat.textContent = "";
    files.replaceChildren();
    return;
  }
  summary.textContent = "변경사항을 확인하는 중…";
  try {
    const report = await requestJson(`/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/git/diff`);
    state.gitFileCount = report.files.length;
    summary.textContent = report.clean
      ? "Working tree clean"
      : `${report.files.length} files changed · +${report.additions} / -${report.deletions}`;
    stat.textContent = report.stat || "";
    files.replaceChildren();
    report.files.forEach((file) => files.append(createDiffDetails(file)));
  } catch (error) {
    state.gitFileCount = 0;
    summary.textContent = error.message;
    stat.textContent = "";
    files.replaceChildren();
  }
}

function createDiffDetails(file) {
  const details = element("details", { className: "diff-file" });
  const summary = element("summary", {}, element("code", {}, file.path), element("span", {}, file.status.trim() || "M"));
  const content = element("pre", { className: "diff-content" }, "펼치면 diff를 불러옵니다.");
  details.append(summary, content);
  details.addEventListener("toggle", async () => {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = "true";
    content.textContent = "불러오는 중…";
    try {
      const url = `/api/workspaces/${encodeURIComponent(state.activeWorkspaceId)}/git/diff?file=${encodeURIComponent(file.path)}`;
      const report = await requestJson(url);
      content.textContent = report.diff || "Git diff가 없습니다. 새 파일은 commit 전까지 내용이 표시되지 않을 수 있습니다.";
    } catch (error) {
      content.textContent = error.message;
    }
  });
  return details;
}

async function commitChanges(event) {
  event.preventDefault();
  const workspace = activeWorkspace();
  const field = document.querySelector("#commit-message");
  const message = field.value.trim();
  if (!workspace || !message) return;
  const confirmed = window.confirm(`"${workspace.name}"의 안전한 변경 파일 ${state.gitFileCount}개를 commit할까요?\n\n${message}`);
  if (!confirmed) return;
  try {
    const result = await requestJson(`/api/workspaces/${encodeURIComponent(workspace.id)}/git/commit`, {
      method: "POST",
      body: JSON.stringify({ message, confirmed: true }),
    });
    field.value = "";
    window.alert(`Commit 완료: ${result.commit}`);
    await refreshGit();
  } catch (error) {
    showError(error.message);
  }
}

function renderReview() {
  const review = state.latestReview;
  if (!review) {
    reviewResult.className = "review-result card empty-card";
    reviewResult.textContent = "아직 Reviewer 결과가 없습니다.";
    return;
  }
  const status = review.status || ({ approve: "pass", revise: "warning", conflict: "conflict" }[review.verdict] || "warning");
  reviewResult.className = `review-result card review-${status}`;
  reviewResult.replaceChildren();
  const confidence = Math.round(Number(review.confidence || 0) * 100);
  reviewResult.append(
    element("div", { className: "review-head" },
      element("span", { className: `review-badge ${status}` }, status.toUpperCase()),
      element("span", { className: "confidence" }, `Confidence ${confidence}%`),
    ),
    element("p", { className: "review-summary" }, review.summary || "요약 없음"),
  );
  const issueList = element("div", { className: "issue-list" });
  const issues = Array.isArray(review.issues) ? review.issues : [];
  if (!issues.length) {
    issueList.append(element("p", { className: "empty-inline" }, "발견된 issue가 없습니다."));
  } else {
    issues.forEach((raw) => {
      const issue = typeof raw === "string" ? { severity: "medium", message: raw } : raw;
      const location = [issue.file, issue.line ? `:${issue.line}` : ""].filter(Boolean).join("");
      issueList.append(
        element("article", { className: `issue severity-${issue.severity || "medium"}` },
          element("div", {}, element("span", { className: "severity" }, issue.severity || "medium"), location ? element("code", {}, location) : null),
          element("p", {}, issue.message || ""),
        ),
      );
    });
  }
  reviewResult.append(issueList);
}

function appendHistory(item) {
  state.histories[state.mode].push(item);
  state.histories[state.mode] = state.histories[state.mode].slice(-40);
  persistHistory(state.mode);
  renderMessages();
}

function renderMessages() {
  messages.replaceChildren();
  const history = state.histories[state.mode];
  if (!history.length) {
    messages.append(
      element("div", { className: "empty-state" },
        element("strong", {}, state.mode === "chat" ? "무엇이든 물어보세요." : "PC 작업을 요청하세요."),
        element("span", {}, state.mode === "chat" ? "대화는 서버 세션과 브라우저 cache에 저장됩니다." : "Manager가 계획을 세우고 진행 상황을 전달합니다."),
      ),
    );
    return;
  }
  history.forEach((item) => {
    messages.append(
      element("article", { className: `message ${item.role}${item.error ? " error" : ""}` },
        element("span", { className: "message-label" }, item.role === "user" ? "You" : "Assistant"),
        element("div", { className: "message-body" }, item.content),
      ),
    );
  });
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
}

function setBusy(busy) {
  state.busy = busy;
  input.disabled = busy;
  sendButton.disabled = busy;
  sendButton.textContent = busy ? "처리 중" : "보내기";
  workspaceSelect.disabled = busy;
  document.querySelectorAll(".mode-tab").forEach((button) => { button.disabled = busy; });
}

function apiHistory(history) {
  return history.slice(-40).map(({ role, content }) => ({ role, content }));
}

function persistHistory(mode) {
  sessionStorage.setItem(historyKey(mode), JSON.stringify(state.histories[mode]));
}

function readHistory(mode) {
  return readJson(`my-agent-${mode}-history`, []);
}

function historyKey(mode) {
  return state.activeWorkspaceId ? `my-agent-${state.activeWorkspaceId}-${mode}-history` : `my-agent-${mode}-history`;
}

function sessionIdKey(mode) {
  return `my-agent-${state.activeWorkspaceId}-${mode}-session`;
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

async function checkHealth() {
  const indicator = document.querySelector("#health");
  try {
    const data = await requestJson("/api/health");
    const ready = data.chat_ready && data.agent_ready && data.workspace_configured;
    indicator.classList.toggle("warning", !ready);
    indicator.classList.remove("offline");
    if (!data.chat_ready || !data.agent_ready) indicator.querySelector("span").textContent = "API 키 필요";
    else if (!data.workspace_configured) indicator.querySelector("span").textContent = "Workspace 필요";
    else indicator.querySelector("span").textContent = "서버 연결됨";
    indicator.title = Object.entries(data.models || {}).map(([role, model]) => `${role}: ${model}`).join("\n");
  } catch {
    indicator.classList.add("offline");
    indicator.querySelector("span").textContent = "연결 안 됨";
  }
}

async function checkUsage() {
  const detail = document.querySelector("#usage-detail");
  try {
    const data = await requestJson("/api/usage");
    document.querySelector("#usage").textContent = `$${Number(data.estimated_cost_usd || 0).toFixed(4)}`;
    detail.replaceChildren(
      metric("Requests", data.requests),
      metric("Input tokens", number(data.input_tokens)),
      metric("Output tokens", number(data.output_tokens)),
      metric("Est. cost", `$${Number(data.estimated_cost_usd || 0).toFixed(6)}`),
    );
    Object.entries(data.by_role || {}).forEach(([role, item]) => {
      detail.append(metric(role, `${item.requests} · $${Number(item.estimated_cost_usd || 0).toFixed(5)}`));
    });
  } catch {
    document.querySelector("#usage").textContent = "$-.----";
    detail.replaceChildren(element("p", { className: "empty-inline" }, "Usage를 불러오지 못했습니다."));
  }
}

function metric(label, value) {
  return element("article", { className: "metric card" }, element("span", {}, label), element("strong", {}, String(value)));
}

async function requestJson(url, options = {}) {
  const headers = { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) };
  const response = await fetch(url, { ...options, headers });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `요청 실패 (${response.status})`);
  return payload;
}

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attributes).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node[key] = value;
  });
  children.flat().filter((child) => child !== null && child !== undefined).forEach((child) => {
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}

function replaceChildren(parent, ...children) {
  parent.replaceChildren(...children.filter(Boolean));
}

function readJson(key, fallback) {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(key) || "null");
    return parsed ?? fallback;
  } catch {
    return fallback;
  }
}

function activeWorkspace() {
  return state.workspaces.find((item) => item.id === state.activeWorkspaceId);
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function number(value) {
  return Number(value || 0).toLocaleString("ko-KR");
}

function showError(message) {
  window.alert(`오류: ${message}`);
}

function switchEstatePane(name) {
  const allowed = ["analysis", "watchlists", "sources"];
  const selected = allowed.includes(name) ? name : "analysis";
  document.querySelectorAll("[data-estate-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.estateTab === selected);
  });
  document.querySelectorAll("[data-estate-pane]").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.estatePane === selected);
  });
  if (selected === "sources") Promise.all([loadEstateSources(), loadEstateJobs()]).catch((error) => showError(error.message));
}

async function loadEstateDashboard() {
  try {
    await Promise.all([loadEstateWatchlists(), loadEstateSources(), loadEstateJobs()]);
    const selected = document.querySelector("#estate-watchlist").value;
    const query = selected ? `?watchlist_id=${encodeURIComponent(selected)}` : "";
    const latest = await requestJson(`/api/real-estate/reports/latest${query}`);
    if (latest?.report) renderEstateReport(latest.report);
  } catch (error) {
    document.querySelector("#estate-progress").textContent = `데이터를 불러오지 못했습니다: ${error.message}`;
  }
}

async function loadEstateWatchlists() {
  state.estate.watchlists = await requestJson("/api/real-estate/watchlists");
  const picker = document.querySelector("#estate-watchlist");
  const selected = picker.value;
  picker.replaceChildren(element("option", { value: "" }, "직접 입력"));
  state.estate.watchlists.forEach((item) => picker.append(element("option", { value: item.id }, item.name)));
  if (state.estate.watchlists.some((item) => item.id === selected)) picker.value = selected;
  renderWatchlists();
}

function renderWatchlists() {
  const list = document.querySelector("#watchlist-list");
  list.replaceChildren();
  if (!state.estate.watchlists.length) {
    list.append(element("p", { className: "empty-inline" }, "등록된 watchlist가 없습니다."));
    return;
  }
  state.estate.watchlists.forEach((item) => {
    const edit = element("button", { type: "button" }, "수정");
    edit.addEventListener("click", () => editWatchlist(item));
    const use = element("button", { type: "button" }, "분석에 사용");
    use.addEventListener("click", () => {
      document.querySelector("#estate-watchlist").value = item.id;
      applySelectedWatchlist();
      switchEstatePane("analysis");
    });
    const remove = element("button", { type: "button", className: "danger-link" }, "삭제");
    remove.addEventListener("click", () => deleteWatchlist(item));
    list.append(element("article", { className: "card" },
      element("h3", {}, item.name),
      element("div", { className: "source-meta" },
        element("span", {}, `지역 ${joinOrDash(item.regions)}`),
        element("span", {}, `비교 ${joinOrDash(item.comparison_regions)}`),
        element("span", {}, `단지 ${joinOrDash(item.complexes)}`),
      ),
      element("div", { className: "card-actions" }, use, edit, remove),
    ));
  });
}

function applySelectedWatchlist() {
  const item = state.estate.watchlists.find((watchlist) => watchlist.id === document.querySelector("#estate-watchlist").value);
  if (!item) return;
  document.querySelector("#estate-region").value = item.regions?.[0] || "";
  document.querySelector("#estate-comparisons").value = (item.comparison_regions || []).join(", ");
}

function editWatchlist(item) {
  document.querySelector("#watchlist-id").value = item.id;
  document.querySelector("#watchlist-name").value = item.name;
  document.querySelector("#watchlist-regions").value = (item.regions || []).join(", ");
  document.querySelector("#watchlist-comparisons").value = (item.comparison_regions || []).join(", ");
  document.querySelector("#watchlist-complexes").value = (item.complexes || []).join(", ");
  document.querySelector("#watchlist-district-codes").value = Object.entries(item.district_codes || {}).map(([name, code]) => `${name}=${code}`).join(", ");
  switchEstatePane("watchlists");
  document.querySelector("#watchlist-name").focus();
}

function resetWatchlistForm() {
  document.querySelector("#watchlist-form").reset();
  document.querySelector("#watchlist-id").value = "";
}

async function saveWatchlist(event) {
  event.preventDefault();
  const id = document.querySelector("#watchlist-id").value;
  const payload = {
    name: document.querySelector("#watchlist-name").value.trim(),
    regions: commaList(document.querySelector("#watchlist-regions").value),
    comparison_regions: commaList(document.querySelector("#watchlist-comparisons").value),
    complexes: commaList(document.querySelector("#watchlist-complexes").value),
    property_types: ["apartment"],
    district_codes: districtCodes(document.querySelector("#watchlist-district-codes").value),
    profile: {},
  };
  try {
    await requestJson(id ? `/api/real-estate/watchlists/${encodeURIComponent(id)}` : "/api/real-estate/watchlists", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    resetWatchlistForm();
    await loadEstateWatchlists();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteWatchlist(item) {
  if (!window.confirm(`"${item.name}" watchlist를 삭제할까요?`)) return;
  try {
    await requestJson(`/api/real-estate/watchlists/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    await loadEstateWatchlists();
  } catch (error) {
    showError(error.message);
  }
}

async function loadEstateSources() {
  state.estate.sources = await requestJson("/api/real-estate/sources");
  const list = document.querySelector("#estate-source-list");
  const picker = document.querySelector("#estate-job-source");
  const selected = picker.value;
  list.replaceChildren();
  picker.replaceChildren();
  state.estate.sources.forEach((item) => {
    picker.append(element("option", { value: item.id }, item.name));
    const status = item.status || "error";
    list.append(element("article", { className: "card" },
      element("div", { className: "review-head" }, element("h3", {}, item.name), element("span", { className: `source-status ${status}` }, status)),
      element("div", { className: "source-meta" },
        element("span", {}, `신뢰도 ${item.reliability_level}`),
        element("span", {}, `최근 성공 ${item.last_success_at ? formatTime(item.last_success_at) : "없음"}`),
        item.configuration_env ? element("span", {}, `설정: ${item.configuration_env}`) : null,
      ),
      item.last_error ? element("p", { className: "source-error" }, item.last_error) : null,
    ));
  });
  if (state.estate.sources.some((item) => item.id === selected)) picker.value = selected;
}

async function createEstateJob(event) {
  event.preventDefault();
  const sourceId = document.querySelector("#estate-job-source").value;
  const sourceUrl = document.querySelector("#estate-job-url").value.trim();
  const statblId = document.querySelector("#estate-job-statbl-id").value.trim();
  const payload = {
    source_id: sourceId,
    period_start: document.querySelector("#estate-job-start").value,
    period_end: document.querySelector("#estate-job-end").value,
    region: document.querySelector("#estate-job-region").value.trim(),
    district_code: document.querySelector("#estate-job-district-code").value.trim() || null,
    source_url: sourceUrl || null,
    parameters: statblId ? { statbl_id: statblId, cycle: "MM" } : {},
  };
  try {
    await requestJson("/api/real-estate/jobs", { method: "POST", body: JSON.stringify(payload) });
    await loadEstateJobs();
  } catch (error) {
    showError(error.message);
  }
}

async function loadEstateJobs() {
  state.estate.jobs = await requestJson("/api/real-estate/jobs");
  const list = document.querySelector("#estate-job-list");
  list.replaceChildren();
  if (!state.estate.jobs.length) {
    list.append(element("p", { className: "empty-inline" }, "수집 작업이 없습니다."));
    return;
  }
  state.estate.jobs.slice(0, 30).forEach((job) => {
    list.append(element("article", { className: "card" },
      element("div", { className: "review-head" },
        element("h3", {}, state.estate.sources.find((source) => source.id === job.source_id)?.name || job.source_id),
        element("span", { className: `source-status ${job.status}` }, job.status),
      ),
      element("div", { className: "source-meta" },
        element("span", {}, job.region),
        element("span", {}, `${job.period_start} ~ ${job.period_end}`),
        element("span", {}, `시도 ${job.attempts}/${job.max_attempts}`),
      ),
      job.last_error ? element("p", { className: "source-error" }, job.last_error) : null,
    ));
  });
}

async function submitEstateAnalysis(event) {
  event.preventDefault();
  if (state.estate.busy) return;
  state.estate.busy = true;
  const button = document.querySelector("#estate-analyze");
  button.disabled = true;
  const progress = document.querySelector("#estate-progress");
  progress.replaceChildren(element("p", { className: "running" }, "분석 요청을 준비하고 있습니다…"));
  document.querySelector("#estate-report").replaceChildren();
  try {
    const response = await fetch("/api/real-estate/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: document.querySelector("#estate-question").value.trim(),
        analysis_mode: document.querySelector("#estate-mode").value,
        region: document.querySelector("#estate-region").value.trim(),
        comparison_regions: commaList(document.querySelector("#estate-comparisons").value),
        period_start: document.querySelector("#estate-period-start").value || null,
        period_end: document.querySelector("#estate-period-end").value || null,
        watchlist_id: document.querySelector("#estate-watchlist").value || null,
      }),
    });
    if (!response.ok || !response.body) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "분석 연결에 실패했습니다.");
    }
    await readEstateStream(response, progress);
  } catch (error) {
    progress.append(element("p", { className: "source-error" }, error.message));
  } finally {
    state.estate.busy = false;
    button.disabled = false;
  }
}

async function readEstateStream(response, progress) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalReceived = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const line = block.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "real_estate_progress") {
        progress.append(element("p", { className: "done" }, `✓ ${event.message}`));
      } else if (event.type === "real_estate_final") {
        finalReceived = true;
        renderEstateReport(event.result.report);
      } else if (event.type === "error") {
        throw new Error(event.message || "분석에 실패했습니다.");
      }
    }
    if (done) break;
  }
  if (!finalReceived) throw new Error("분석 응답이 예기치 않게 종료되었습니다.");
}

function renderEstateReport(report) {
  const root = document.querySelector("#estate-report");
  const primary = report.metrics?.primary || {};
  const latest = primary.latest || {};
  root.replaceChildren(
    element("article", { className: "card estate-conclusion" },
      element("span", { className: `source-status ${report.data_status === "ready" ? "ready" : "warning"}` }, report.data_status || "unknown"),
      element("strong", {}, report.one_line_conclusion || "결론을 생성할 근거가 부족합니다."),
      element("div", { className: "estate-meta" }, element("span", {}, `데이터 기준일 ${report.as_of_date || "미확인"}`), element("span", {}, `지역 ${report.region || "-"}`)),
    ),
    estateMetrics(latest, primary),
    estateListCard("확인된 사실", report.confirmed_facts, "fact"),
    estateListCard("사실로부터의 추론", report.inferences, "inference"),
    estateListCard("상승 근거", report.upside_reasons, "upside"),
    estateListCard("하락·반대 근거", report.downside_reasons, "downside"),
    estateScenarioCard(report.scenarios),
    estateListCard("판단을 바꿀 핵심 지표", report.key_indicators, "indicator"),
    estateListCard("부족하거나 확인되지 않은 정보", report.missing_information, "missing"),
    estateSourcesCard(report.sources),
  );
}

function estateMetrics(latest, primary) {
  const values = [
    ["표본", latest.sample_count ?? primary.sample_count ?? "-"],
    ["3개월 중앙값/㎡", latest.rolling_3m_median_price_per_sqm_krw ? `${number(latest.rolling_3m_median_price_per_sqm_krw)}원` : "-"],
    ["거래량 전월비", percent(latest.volume_mom_pct)],
    ["6개월 변화", percent(latest.price_change_6m_pct)],
  ];
  return element("article", { className: "card" }, element("h3", {}, "주요 지표"), element("div", { className: "indicator-grid usage-grid" }, ...values.map(([label, value]) => metric(label, value))));
}

function estateListCard(title, items, label) {
  const safeItems = Array.isArray(items) ? items : [];
  return element("article", { className: "card" }, element("h3", {}, title), safeItems.length
    ? element("ul", { className: "estate-card-list" }, ...safeItems.map((item) => element("li", {}, element("span", { className: "claim-label" }, label), " ", String(item))))
    : element("p", { className: "empty-inline" }, "확인된 항목이 없습니다."));
}

function estateScenarioCard(items) {
  const scenarios = Array.isArray(items) ? items : [];
  return element("article", { className: "card" }, element("h3", {}, "조건별 시나리오"), element("div", { className: "scenario-grid" },
    ...scenarios.map((item) => element("section", { className: `scenario ${item.name || "base"}` },
      element("strong", {}, ({ bull: "강세", base: "기준", bear: "약세" })[item.name] || item.name || "시나리오"),
      element("p", {}, item.description || ""),
      element("p", {}, `조건: ${joinOrDash(item.conditions)}`),
    )),
  ));
}

function estateSourcesCard(items) {
  const sources = Array.isArray(items) ? items : [];
  const links = sources.map((item) => {
    const url = safeHttpUrl(item.url);
    const node = element(url ? "a" : "div", { className: "report-source" }, element("strong", {}, item.name || item.source_id), element("span", {}, `신뢰도 ${item.reliability_level || "-"} · ${item.as_of_date || "기준일 미확인"}`));
    if (url) {
      node.href = url;
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
    return node;
  });
  return element("article", { className: "card" }, element("h3", {}, "출처"), ...links, sources.length ? null : element("p", { className: "empty-inline" }, "표시할 공식 출처가 없습니다."));
}

function commaList(value) {
  return [...new Set(String(value || "").split(",").map((item) => item.trim()).filter(Boolean))];
}

function districtCodes(value) {
  const result = {};
  commaList(value).forEach((item) => {
    const [name, code] = item.split("=").map((part) => part.trim());
    if (name && /^\d{5}$/.test(code || "")) result[name] = code;
  });
  return result;
}

function joinOrDash(items) {
  return Array.isArray(items) && items.length ? items.join(", ") : "-";
}

function percent(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "-";
}

function safeHttpUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function setDefaultEstateDates() {
  const now = new Date();
  const end = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const startDate = new Date(now.getFullYear() - 1, now.getMonth(), 1);
  const start = `${startDate.getFullYear()}-${String(startDate.getMonth() + 1).padStart(2, "0")}`;
  ["#estate-period-end", "#estate-job-end"].forEach((selector) => { document.querySelector(selector).value = end; });
  ["#estate-period-start", "#estate-job-start"].forEach((selector) => { document.querySelector(selector).value = start; });
}

switchMode(state.mode);
switchView(state.view);
renderReview();
setDefaultEstateDates();
Promise.all([loadWorkspaces(), checkHealth(), checkUsage()]).catch((error) => showError(error.message));
input.focus();
