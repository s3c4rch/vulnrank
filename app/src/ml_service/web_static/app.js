const state = {
  token: window.localStorage.getItem("vulnrank_token"),
  user: null,
  models: [],
  activeTaskId: null,
  pollTimer: null,
};

const ROUTE_BY_VIEW = {
  landing: "/",
  register: "/register",
  login: "/login",
  "user-dashboard": "/cabinet",
  "admin-dashboard": "/admin",
};

const GUEST_ROUTES = new Set(["/", "/register", "/login"]);

const elements = {
  notice: document.getElementById("notice"),
  landingPage: document.getElementById("hero"),
  registerPage: document.getElementById("register-page"),
  loginPage: document.getElementById("login-page"),
  sessionStatus: document.getElementById("session-status"),
  sessionTitle: document.getElementById("session-title"),
  sessionSubtitle: document.getElementById("session-subtitle"),
  logoutButton: document.getElementById("logout-button"),
  guestHomeNav: document.getElementById("guest-home-nav"),
  guestRegisterNav: document.getElementById("guest-register-nav"),
  guestLoginNav: document.getElementById("guest-login-nav"),
  userCabinetNav: document.getElementById("user-cabinet-nav"),
  userHistoryNav: document.getElementById("user-history-nav"),
  adminNavButton: document.getElementById("admin-nav-button"),
  workspace: document.getElementById("workspace"),
  history: document.getElementById("history"),
  adminStudio: document.getElementById("admin-studio"),
  registerForm: document.getElementById("register-form"),
  loginForm: document.getElementById("login-form"),
  topupForm: document.getElementById("topup-form"),
  predictForm: document.getElementById("predict-form"),
  openaiForm: document.getElementById("openai-form"),
  modelSelect: document.getElementById("model-select"),
  openaiModelName: document.getElementById("openai-model-name"),
  openaiApiKey: document.getElementById("openai-api-key"),
  openaiDisableButton: document.getElementById("openai-disable-button"),
  openaiStatus: document.getElementById("openai-status"),
  scanFile: document.getElementById("scan-file"),
  uploadSummary: document.getElementById("upload-summary"),
  invalidRecords: document.getElementById("invalid-records"),
  featureRows: document.getElementById("feature-rows"),
  featureRowTemplate: document.getElementById("feature-row-template"),
  predictionStatus: document.getElementById("prediction-status"),
  predictionHistoryBody: document.getElementById("prediction-history-body"),
  transactionHistoryBody: document.getElementById("transaction-history-body"),
  adminUsersBody: document.getElementById("admin-users-body"),
  adminPendingTopupsBody: document.getElementById("admin-pending-topups-body"),
  adminCompletedBody: document.getElementById("admin-completed-body"),
  adminFailedBody: document.getElementById("admin-failed-body"),
  adminTransactionsBody: document.getElementById("admin-transactions-body"),
  adminLocalModels: document.getElementById("admin-local-models"),
  adminUserCount: document.getElementById("admin-user-count"),
  adminPendingCount: document.getElementById("admin-pending-count"),
  adminFailedCount: document.getElementById("admin-failed-count"),
  adminTransactionCount: document.getElementById("admin-transaction-count"),
  profileEmail: document.getElementById("profile-email"),
  profileRole: document.getElementById("profile-role"),
  balanceAmount: document.getElementById("balance-amount"),
  balanceUpdated: document.getElementById("balance-updated"),
  loginEmail: document.getElementById("login-email"),
  loginPassword: document.getElementById("login-password"),
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  window.addEventListener("popstate", handleRouteChange);
  hydrateSession();
});

function bindEvents() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => {
      showView(button.getAttribute("data-view-target"));
    });
  });

  document.querySelectorAll("[data-scroll-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.getAttribute("data-scroll-target");
      const target = document.getElementById(targetId);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  document.getElementById("demo-fill-user").addEventListener("click", () => {
    elements.loginEmail.value = "demo-user@example.com";
    elements.loginPassword.value = "demo-user-password";
    showView("login");
  });

  document.getElementById("load-demo-admin").addEventListener("click", () => {
    elements.loginEmail.value = "demo-admin@example.com";
    elements.loginPassword.value = "demo-admin-password";
    showView("login");
  });

  elements.logoutButton.addEventListener("click", logout);

  elements.registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(elements.registerForm);
    const payload = {
      email: String(formData.get("email") || "").trim(),
      password: String(formData.get("password") || ""),
    };

    const response = await apiRequest("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return;
    }

    applyAuthPayload(response.data, "Аккаунт создан и сессия открыта.");
    elements.registerForm.reset();
  });

  elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(elements.loginForm);
    const payload = {
      email: String(formData.get("email") || "").trim(),
      password: String(formData.get("password") || ""),
    };

    const response = await apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      return;
    }

    applyAuthPayload(response.data, "Вы вошли в кабинет.");
  });

  elements.topupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(elements.topupForm);
    const payload = {
      amount: String(formData.get("amount") || "0"),
    };

    const response = await apiRequest("/balance/top-up", {
      method: "POST",
      body: JSON.stringify(payload),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    showNotice(`Заявка на пополнение создана: ${response.data.transaction.amount} credits. Ожидает admin approval.`);
    await refreshWorkspace();
  });

  const addFeatureButton = document.getElementById("add-feature-row");
  if (addFeatureButton && elements.featureRows) {
    addFeatureButton.addEventListener("click", () => addFeatureRow());

    elements.featureRows.addEventListener("click", (event) => {
      const button = event.target.closest(".remove-feature-row");
      if (!button) {
        return;
      }
      const rows = elements.featureRows.querySelectorAll(".feature-row");
      if (rows.length <= 1) {
        showNotice("Нужен минимум один признак.", true);
        return;
      }
      button.closest(".feature-row").remove();
    });
  }

  elements.predictForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const file = elements.scanFile.files[0];
    if (!file) {
      showPredictionStatus("Выберите JSON, CSV или ZIP файл скана.", true);
      return;
    }

    const payload = new FormData();
    payload.append("model", elements.modelSelect.value);
    payload.append("file", file);

    const response = await apiRequest("/predict/upload", {
      method: "POST",
      body: payload,
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    renderUploadSummary(response.data);
    renderInvalidRecords(response.data.invalid_records || []);
    state.activeTaskId = response.data.task_id;
    if (response.data.status === "failed") {
      showPredictionStatus(
        `Файл отклонён: ${response.data.rejected_count} records не прошли валидацию.`,
        true
      );
      await refreshWorkspace();
      return;
    }

    showPredictionStatus(
      `Upload ${response.data.task_id}: принято ${response.data.accepted_count}, отклонено ${response.data.rejected_count}. Ждём worker...`
    );
    await refreshWorkspace();
    startTaskPolling(response.data.task_id);
  });

  elements.openaiForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(elements.openaiForm);
    const payload = {
      model_name: String(formData.get("model_name") || "").trim(),
      api_key: String(formData.get("api_key") || "").trim(),
    };

    const response = await apiRequest("/external-credentials/openai", {
      method: "PUT",
      body: JSON.stringify(payload),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    elements.openaiApiKey.value = "";
    showNotice("OpenAI credentials сохранены. Модель chatgpt доступна в списке.");
    await refreshWorkspace();
  });

  elements.openaiDisableButton.addEventListener("click", async () => {
    const response = await apiRequest("/external-credentials/openai", {
      method: "DELETE",
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    showNotice("OpenAI credentials отключены.");
    await refreshWorkspace();
  });

  elements.adminPendingTopupsBody.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-topup-action]");
    if (!button) {
      return;
    }
    event.preventDefault();

    const transactionId = button.dataset.transactionId;
    const action = button.dataset.topupAction;
    const response = await apiRequest(`/admin/top-ups/${transactionId}/${action}`, {
      method: "POST",
      body: JSON.stringify({ review_comment: `${action} via admin web` }),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    showNotice(`Заявка ${response.data.transaction.status}: ${response.data.transaction.amount} credits.`);
    await refreshWorkspace();
  });

  elements.adminLocalModels.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-local-model]");
    if (!button) {
      return;
    }
    event.preventDefault();

    const modelName = button.dataset.localModel;
    showNotice(`Запускаю pull и активацию ${modelName}. Это может занять несколько минут.`);
    const response = await apiRequest("/admin/models/local/activate", {
      method: "POST",
      body: JSON.stringify({ model: modelName }),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    showNotice(`Активная локальная модель: ${response.data.model.name}.`);
    await refreshWorkspace();
  });

  [elements.predictionHistoryBody, elements.adminCompletedBody].forEach((target) => {
    target.addEventListener("click", handleReportAction);
  });
}

function ensureFeatureRows() {
  if (!elements.featureRows) {
    return;
  }
  if (elements.featureRows.children.length > 0) {
    return;
  }
  addFeatureRow("x1", "1.20");
  addFeatureRow("x2", "5.70");
}

function addFeatureRow(name = "", value = "") {
  if (!elements.featureRowTemplate || !elements.featureRows) {
    return;
  }
  const fragment = elements.featureRowTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".feature-row");
  row.querySelector("input[name='feature-name']").value = name;
  row.querySelector("input[name='feature-value']").value = value;
  elements.featureRows.appendChild(fragment);
}

function buildFeaturePayload() {
  if (!elements.featureRows) {
    return { ok: false, message: "Feature editor is not available in upload mode." };
  }
  const rows = Array.from(elements.featureRows.querySelectorAll(".feature-row"));
  const features = {};

  for (const row of rows) {
    const name = row.querySelector("input[name='feature-name']").value.trim();
    const rawValue = row.querySelector("input[name='feature-value']").value.trim();

    if (!name || !rawValue) {
      return { ok: false, message: "Каждый feature должен иметь имя и числовое значение." };
    }
    if (Number.isNaN(Number(rawValue))) {
      return { ok: false, message: `Значение признака ${name} должно быть числом.` };
    }
    features[name] = Number(rawValue);
  }

  return { ok: true, features };
}

function applyAuthPayload(payload, successMessage) {
  state.token = payload.access_token;
  window.localStorage.setItem("vulnrank_token", state.token);
  state.user = payload.user;
  showNotice(successMessage);
  navigateToRoleDashboard({ replace: true });
}

async function hydrateSession() {
  if (!state.token) {
    renderRoute();
    return;
  }

  const response = await apiRequest("/users/me", { auth: true, silent: true });
  if (!response.ok) {
    logout({ quiet: true });
    return;
  }

  state.user = response.data;
  await refreshWorkspace();
}

async function refreshWorkspace() {
  if (!state.token) {
    renderRoute();
    return;
  }

  const userResponse = await apiRequest("/users/me", { auth: true, silent: true });
  if (!userResponse.ok) {
    logout({ quiet: true });
    return;
  }

  state.user = userResponse.data;
  renderSession();

  if (state.user.role === "admin") {
    await refreshAdminWorkspace();
  } else {
    await refreshUserWorkspace();
  }
}

async function refreshUserWorkspace() {
  const requests = [
    apiRequest("/balance", { auth: true, silent: true }),
    apiRequest("/models", { auth: true, silent: true }),
    apiRequest("/external-credentials/openai", { auth: true, silent: true }),
    apiRequest("/history/predictions", { auth: true, silent: true }),
    apiRequest("/history/transactions", { auth: true, silent: true }),
  ];
  const responses = await Promise.all(requests);
  const failedResponse = responses.find((response) => !response.ok);
  if (failedResponse) {
    logout({ quiet: true });
    return;
  }

  const [balanceResponse, modelsResponse, openaiResponse, predictionsResponse, transactionsResponse] = responses;
  state.models = modelsResponse.data.items;

  renderModels();
  renderOpenAICredential(openaiResponse.data);
  renderHistory(predictionsResponse.data.items, transactionsResponse.data.items);

  elements.balanceAmount.textContent = formatCredits(balanceResponse.data.amount);
  elements.balanceUpdated.textContent = formatDate(balanceResponse.data.updated_at);
}

async function refreshAdminWorkspace() {
  const requests = [
    apiRequest("/admin/users", { auth: true, silent: true }),
    apiRequest("/admin/models/local", { auth: true, silent: true }),
    apiRequest("/admin/top-ups/pending", { auth: true, silent: true }),
    apiRequest("/admin/history/predictions", { auth: true, silent: true }),
    apiRequest("/admin/history/transactions", { auth: true, silent: true }),
  ];
  const responses = await Promise.all(requests);
  const failedResponse = responses.find((response) => !response.ok);
  if (failedResponse) {
    logout({ quiet: true });
    return;
  }

  const [
    adminUsersResponse,
    localModelsResponse,
    pendingTopupsResponse,
    adminPredictionsResponse,
    adminTransactionsResponse,
  ] = responses;
  renderAdmin(
    adminUsersResponse.data.items,
    localModelsResponse.data,
    pendingTopupsResponse.data.items,
    adminPredictionsResponse.data.items,
    adminTransactionsResponse.data.items
  );
}

function navigateToRoleDashboard(options = {}) {
  navigateToPath(roleDashboardPath(), options);
}

function navigateToPath(path, options = {}) {
  setRoute(path, { replace: Boolean(options.replace) });
  if (options.render === false) {
    return;
  }
  handleRouteChange();
}

function setRoute(path, options = {}) {
  if (window.location.pathname === path) {
    return;
  }
  const method = options.replace ? "replaceState" : "pushState";
  window.history[method]({}, "", path);
}

function roleDashboardPath() {
  return state.user?.role === "admin" ? "/admin" : "/cabinet";
}

function viewForPath(path) {
  if (path === "/register") {
    return "register";
  }
  if (path === "/login") {
    return "login";
  }
  return "landing";
}

function handleRouteChange() {
  if (state.token) {
    refreshWorkspace();
    return;
  }
  renderRoute();
}

function renderRoute() {
  if (state.user) {
    renderSession();
    return;
  }

  const currentPath = window.location.pathname;
  if (currentPath === "/cabinet" || currentPath === "/admin") {
    setRoute("/login", { replace: true });
    renderGuestState("login");
    return;
  }

  if (!GUEST_ROUTES.has(currentPath)) {
    setRoute("/", { replace: true });
    renderGuestState("landing");
    return;
  }

  renderGuestState(viewForPath(currentPath));
}

function renderGuestState(viewName = viewForPath(window.location.pathname)) {
  state.user = null;
  elements.landingPage.hidden = viewName !== "landing";
  elements.registerPage.hidden = viewName !== "register";
  elements.loginPage.hidden = viewName !== "login";
  elements.sessionStatus.hidden = true;
  elements.workspace.hidden = true;
  elements.history.hidden = true;
  elements.adminStudio.hidden = true;
  elements.guestHomeNav.hidden = false;
  elements.guestRegisterNav.hidden = false;
  elements.guestLoginNav.hidden = false;
  elements.userCabinetNav.hidden = true;
  elements.userHistoryNav.hidden = true;
  elements.adminNavButton.hidden = true;
  elements.logoutButton.hidden = true;
  elements.sessionTitle.textContent = "Гость";
  elements.sessionSubtitle.textContent = "Авторизуйтесь, чтобы открыть рабочие панели.";
}

function renderSession() {
  const currentPath = window.location.pathname;
  const dashboardPath = roleDashboardPath();
  if (state.user.role !== "admin" && currentPath === "/admin") {
    setRoute("/cabinet", { replace: true });
  } else if (state.user.role === "admin" && currentPath === "/cabinet") {
    setRoute("/admin", { replace: true });
  } else if (currentPath !== dashboardPath) {
    setRoute(dashboardPath, { replace: true });
  }

  elements.landingPage.hidden = true;
  elements.registerPage.hidden = true;
  elements.loginPage.hidden = true;
  elements.sessionStatus.hidden = false;
  elements.logoutButton.hidden = false;
  elements.sessionTitle.textContent = state.user.email;

  if (state.user.role === "admin") {
    elements.workspace.hidden = true;
    elements.history.hidden = true;
    elements.adminStudio.hidden = false;
    elements.guestHomeNav.hidden = true;
    elements.guestRegisterNav.hidden = true;
    elements.guestLoginNav.hidden = true;
    elements.userCabinetNav.hidden = true;
    elements.userHistoryNav.hidden = true;
    elements.adminNavButton.hidden = false;
    elements.sessionSubtitle.textContent =
      "Админский интерфейс активен: доступны заявки, пользователи, транзакции и ошибки.";
    return;
  }

  elements.workspace.hidden = false;
  elements.history.hidden = false;
  elements.adminStudio.hidden = true;
  elements.guestHomeNav.hidden = true;
  elements.guestRegisterNav.hidden = true;
  elements.guestLoginNav.hidden = true;
  elements.userCabinetNav.hidden = false;
  elements.userHistoryNav.hidden = false;
  elements.adminNavButton.hidden = true;
  elements.sessionSubtitle.textContent =
    "Рабочая сессия активна. Можно создавать заявки на пополнение и отправлять ML-задачи.";

  elements.profileEmail.textContent = state.user.email;
  elements.profileRole.textContent = state.user.role;
}

function renderModels() {
  elements.modelSelect.innerHTML = "";
  if (state.models.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Нет активных моделей";
    elements.modelSelect.appendChild(option);
    return;
  }
  state.models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.name;
    option.textContent = `${model.name} ${model.version} - ${formatCredits(model.cost_per_prediction)}`;
    option.title = model.description;
    elements.modelSelect.appendChild(option);
  });
}

function renderOpenAICredential(credential) {
  if (!credential?.is_configured) {
    elements.openaiStatus.textContent = "OpenAI не подключён. После сохранения API key модель chatgpt появится в списке.";
    elements.openaiStatus.className = "callout neutral-callout";
    elements.openaiDisableButton.disabled = true;
    return;
  }

  elements.openaiModelName.value = credential.model_name || elements.openaiModelName.value;
  elements.openaiStatus.textContent = `OpenAI подключён: ${credential.model_name} (${credential.key_preview}).`;
  elements.openaiStatus.className = "callout success-callout";
  elements.openaiDisableButton.disabled = false;
}

function renderHistory(predictions, transactions) {
  renderPredictionRows(elements.predictionHistoryBody, predictions, { showUser: false });
  renderTransactionRows(elements.transactionHistoryBody, transactions, { showUser: false });
}

function renderAdmin(users, localModels, pendingTopups, predictions, transactions) {
  const failedPredictions = predictions.filter((item) => item.status === "failed");
  const completedPredictions = predictions.filter((item) => item.status === "completed");
  elements.adminStudio.hidden = false;
  elements.adminUserCount.textContent = String(users.length);
  elements.adminPendingCount.textContent = String(pendingTopups.length);
  elements.adminFailedCount.textContent = String(failedPredictions.length);
  elements.adminTransactionCount.textContent = String(transactions.length);
  renderAdminUsers(users);
  renderAdminLocalModels(localModels);
  renderAdminPendingTopups(pendingTopups);
  renderAdminCompletedReports(completedPredictions);
  renderPredictionRows(elements.adminFailedBody, failedPredictions, { showUser: true, failedOnly: true });
  renderTransactionRows(elements.adminTransactionsBody, transactions, { showUser: true });
}

function scrollToActivePanel() {
  const target = state.user?.role === "admin" ? elements.adminStudio : elements.workspace;
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function showView(viewName) {
  const targetPath = ROUTE_BY_VIEW[viewName] || "/";
  navigateToPath(targetPath);
}

function renderAdminUsers(users) {
  if (users.length === 0) {
    elements.adminUsersBody.innerHTML = `<tr><td colspan="3" class="table-empty">Пользователи пока отсутствуют.</td></tr>`;
    return;
  }

  elements.adminUsersBody.innerHTML = users
    .map((user) => {
      return `
        <tr>
          <td>${escapeHtml(user.email)}</td>
          <td>${escapeHtml(user.role)}</td>
          <td>${formatCredits(user.balance.amount)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderAdminLocalModels(payload) {
  const models = payload?.items || [];
  if (models.length === 0) {
    elements.adminLocalModels.innerHTML = `<p class="table-empty">Локальные модели не настроены.</p>`;
    return;
  }

  const runtimeWarning = payload?.runtime_error
    ? `<div class="callout error-callout">${escapeHtml(payload.runtime_error)}</div>`
    : "";
  elements.adminLocalModels.innerHTML = `
    ${runtimeWarning}
    ${models
      .map((model) => {
        const activeLabel = model.is_active ? "active" : "inactive";
        const pulledLabel =
          model.is_pulled === null || model.is_pulled === undefined
            ? "runtime unknown"
            : model.is_pulled
              ? "pulled"
              : "not pulled";
        return `
          <div class="model-admin-item">
            <div>
              <strong>${escapeHtml(model.name)}</strong>
              <p class="model-admin-meta">
                ${escapeHtml(activeLabel)} · ${escapeHtml(pulledLabel)} · ${formatCredits(model.cost_per_prediction)}
              </p>
            </div>
            <button type="button" class="primary-button small-button" data-local-model="${escapeHtml(model.name)}">
              Pull & activate
            </button>
          </div>
        `;
      })
      .join("")}
  `;
}

function renderAdminPendingTopups(transactions) {
  if (transactions.length === 0) {
    elements.adminPendingTopupsBody.innerHTML =
      `<tr><td colspan="4" class="table-empty">Pending-заявок нет.</td></tr>`;
    return;
  }

  elements.adminPendingTopupsBody.innerHTML = transactions
    .map((item) => {
      return `
        <tr>
          <td>${escapeHtml(item.user_email || "-")}</td>
          <td>${formatCredits(item.amount)}</td>
          <td>${statusBadge(item.status)}</td>
          <td>
            <div class="admin-user-actions">
              <button type="button" class="primary-button small-button" data-topup-action="approve" data-transaction-id="${escapeHtml(item.id)}">Approve</button>
              <button type="button" class="ghost-button small-button" data-topup-action="reject" data-transaction-id="${escapeHtml(item.id)}">Reject</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderPredictionRows(target, predictions, options) {
  if (predictions.length === 0) {
    const colspan = options.failedOnly ? 6 : 7;
    target.innerHTML = `<tr><td colspan="${colspan}" class="table-empty">Пока нет записей.</td></tr>`;
    return;
  }

  if (options.failedOnly) {
    target.innerHTML = predictions
      .map((item) => {
        return `
          <tr>
            <td>${escapeHtml(item.user_email || "-")}</td>
            <td><code>${escapeHtml(item.task_id)}</code></td>
            <td>${formatFileCell(item)}</td>
            <td>${escapeHtml(item.model_name)}</td>
            <td>${formatAdminErrorCell(item)}</td>
            <td>${formatDate(item.created_at)}</td>
          </tr>
        `;
      })
      .join("");
    return;
  }

  target.innerHTML = predictions
    .map((item) => {
      return `
        <tr>
          <td><code>${escapeHtml(item.task_id)}</code>${renderInlineReportActions(item)}</td>
          <td>${formatFileCell(item)}</td>
          <td>${statusBadge(item.status)}</td>
          <td>${formatRecordCounts(item)}</td>
          <td>${escapeHtml(item.predicted_priority || "-")}</td>
          <td>${escapeHtml(item.worker_id || "-")}</td>
          <td>${formatCredits(item.spent_credits)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderAdminCompletedReports(predictions) {
  if (predictions.length === 0) {
    elements.adminCompletedBody.innerHTML =
      `<tr><td colspan="6" class="table-empty">Completed tasks пока отсутствуют.</td></tr>`;
    return;
  }

  elements.adminCompletedBody.innerHTML = predictions
    .map((item) => {
      return `
        <tr>
          <td>${escapeHtml(item.user_email || "-")}</td>
          <td><code>${escapeHtml(item.task_id)}</code></td>
          <td>${formatFileCell(item)}</td>
          <td>${escapeHtml(item.predicted_priority || "-")}</td>
          <td>${formatDate(item.finished_at)}</td>
          <td>${renderReportButtons(item)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderTransactionRows(target, transactions, options) {
  if (transactions.length === 0) {
    const colspan = options.showUser ? 5 : 5;
    target.innerHTML = `<tr><td colspan="${colspan}" class="table-empty">Пока нет операций.</td></tr>`;
    return;
  }

  target.innerHTML = transactions
    .map((item) => {
      if (options.showUser) {
        return `
          <tr>
            <td>${escapeHtml(item.user_email || "-")}</td>
            <td>${escapeHtml(item.type)}</td>
            <td>${formatCredits(item.amount)}</td>
            <td>${statusBadge(item.status)}</td>
            <td>${escapeHtml(item.review_comment || "-")}</td>
          </tr>
        `;
      }

      return `
        <tr>
          <td>${escapeHtml(item.type)}</td>
          <td>${formatCredits(item.amount)}</td>
          <td>${statusBadge(item.status)}</td>
          <td>${escapeHtml(item.review_comment || "-")}</td>
          <td>${formatDate(item.created_at)}</td>
        </tr>
      `;
    })
    .join("");
}

function startTaskPolling(taskId) {
  window.clearTimeout(state.pollTimer);

  const poll = async () => {
    const response = await apiRequest(`/predict/${taskId}`, { auth: true, silent: true });
    if (!response.ok) {
      return;
    }

    const task = response.data;

    if (task.status === "completed") {
      showPredictionStatus(
        `Задача ${task.task_id} завершена: processed ${task.processed_count || 0}, rejected ${task.rejected_count || 0}, priority ${task.predicted_priority}, worker ${task.worker_id}.`,
        false,
        true
      );
      renderUploadSummary(task);
      renderInvalidRecords(task.invalid_records || []);
      await refreshWorkspace();
      return;
    }

    if (task.status === "failed") {
      renderUploadSummary(task);
      showPredictionStatus(
        `Задача ${task.task_id} завершилась ошибкой: ${task.error_message || "неизвестная ошибка"}.`,
        true
      );
      await refreshWorkspace();
      return;
    }

    showPredictionStatus(`Задача ${task.task_id} сейчас в статусе ${task.status}. Продолжаем опрос...`);
    await refreshWorkspace();
    state.pollTimer = window.setTimeout(poll, 2000);
  };

  poll();
}

function showPredictionStatus(message, isError = false, isSuccess = false) {
  elements.predictionStatus.textContent = message;
  elements.predictionStatus.className = "callout";
  if (isSuccess) {
    elements.predictionStatus.classList.add("success-callout");
  } else if (isError) {
    elements.predictionStatus.classList.add("error-callout");
  } else {
    elements.predictionStatus.classList.add("neutral-callout");
  }
}

function renderInvalidRecords(records) {
  if (!elements.invalidRecords) {
    return;
  }
  if (!records || records.length === 0) {
    elements.invalidRecords.hidden = true;
    elements.invalidRecords.innerHTML = "";
    return;
  }

  elements.invalidRecords.hidden = false;
  elements.invalidRecords.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Index</th>
            <th>Record</th>
            <th>Errors</th>
          </tr>
        </thead>
        <tbody>
          ${records
            .map((item) => {
              return `
                <tr>
                  <td>${item.index}</td>
                  <td><code>${escapeHtml(JSON.stringify(item.record))}</code></td>
                  <td>${escapeHtml(item.errors.map((error) => error.msg).join("; "))}</td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderUploadSummary(task) {
  if (!elements.uploadSummary) {
    return;
  }

  const sourceFiles = task?.source_files || [];
  const invalidFiles = task?.invalid_files || [];
  const uploadKind = task?.upload_kind || "single_file";

  if (sourceFiles.length === 0 && invalidFiles.length === 0) {
    elements.uploadSummary.hidden = true;
    elements.uploadSummary.innerHTML = "";
    return;
  }

  elements.uploadSummary.hidden = false;
  elements.uploadSummary.innerHTML = `
    <div class="callout neutral-callout upload-summary-callout">
      <strong>${escapeHtml(uploadKind === "archive" ? "ZIP archive" : "Single file")}</strong>
      ${sourceFiles.length > 0 ? renderSourceFileSummary(sourceFiles) : ""}
      ${invalidFiles.length > 0 ? renderInvalidFileSummary(invalidFiles) : ""}
    </div>
  `;
}

function renderSourceFileSummary(sourceFiles) {
  return `
    <div class="upload-summary-section">
      <span class="upload-summary-title">Файлы в обработке</span>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Файл</th>
              <th>Формат</th>
              <th>Tool</th>
              <th>Records</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            ${sourceFiles
              .map((item) => {
                return `
                  <tr>
                    <td>${escapeHtml(item.filename)}</td>
                    <td>${escapeHtml(item.format)}</td>
                    <td>${escapeHtml(item.tool || "-")}</td>
                    <td>${item.accepted_count}/${item.accepted_count + item.rejected_count} ok, ${item.rejected_count} rejected</td>
                    <td>${statusBadge(item.status)}</td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderInvalidFileSummary(invalidFiles) {
  return `
    <div class="upload-summary-section">
      <span class="upload-summary-title">Пропущенные файлы</span>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Файл</th>
              <th>Причина</th>
            </tr>
          </thead>
          <tbody>
            ${invalidFiles
              .map((item) => {
                return `
                  <tr>
                    <td>${escapeHtml(item.filename)}</td>
                    <td>${escapeHtml((item.errors || []).join("; "))}</td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

async function handleReportAction(event) {
  const button = event.target.closest("[data-report-action]");
  if (!button) {
    return;
  }
  event.preventDefault();

  const taskId = button.dataset.taskId;
  const action = button.dataset.reportAction;
  const suffix = action === "download" ? "/download" : "";
  const response = await window.fetch(`/predict/${taskId}/report${suffix}`, {
    headers: {
      Authorization: `Bearer ${state.token}`,
    },
  });

  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    showNotice(extractErrorMessage(payload) || `HTTP ${response.status}`, true);
    return;
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);

  if (action === "download") {
    const link = document.createElement("a");
    link.href = url;
    link.download = `vulnrank-report-${taskId}.html`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    showNotice(`HTML report для ${taskId} скачан.`);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }

  window.setTimeout(() => window.URL.revokeObjectURL(url), 60000);
}

async function apiRequest(path, options = {}) {
  const isFormData = options.body instanceof FormData;
  const requestOptions = {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
      ...(options.body && !isFormData ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  };

  if (options.auth && state.token) {
    requestOptions.headers.Authorization = `Bearer ${state.token}`;
  }
  if (options.body) {
    requestOptions.body = options.body;
  }

  try {
    const response = await window.fetch(path, requestOptions);
    const isJson = response.headers.get("content-type")?.includes("application/json");
    const data = isJson ? await response.json() : null;

    if (!response.ok && !options.silent) {
      showNotice(extractErrorMessage(data) || `HTTP ${response.status}`, true);
    }

    return {
      ok: response.ok,
      status: response.status,
      data,
    };
  } catch (error) {
    if (!options.silent) {
      showNotice("Не удалось связаться с backend-сервисом.", true);
    }
    return {
      ok: false,
      status: 0,
      data: null,
    };
  }
}

function logout(options = {}) {
  window.localStorage.removeItem("vulnrank_token");
  window.clearTimeout(state.pollTimer);
  state.token = null;
  state.user = null;
  state.models = [];
  state.activeTaskId = null;
  if (!options.quiet) {
    setRoute("/", { replace: true });
  }
  renderRoute();
  if (!options.quiet) {
    showNotice("Сессия завершена.");
  }
}

function extractErrorMessage(data) {
  if (!data?.error) {
    return null;
  }
  if (Array.isArray(data.error.details) && data.error.details.length > 0) {
    return `${data.error.message}: ${JSON.stringify(data.error.details[0])}`;
  }
  return data.error.message;
}

function formatCredits(value) {
  const number = Number(value);
  if (Number.isNaN(number)) {
    return "-";
  }
  return `${number.toFixed(2)} credits`;
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatRecordCounts(item) {
  const processed = item.processed_count ?? 0;
  const rejected = item.rejected_count ?? 0;
  const accepted = item.accepted_count ?? processed;
  return `${processed}/${accepted} ok, ${rejected} rejected`;
}

function formatFileCell(item) {
  const sourceFiles = item.source_files || [];
  const invalidFiles = item.invalid_files || [];
  const lines = [`<strong>${escapeHtml(item.original_filename || "-")}</strong>`];

  if (item.upload_kind === "archive" && sourceFiles.length > 0) {
    lines.push(
      `<div class="table-subtext">${escapeHtml(
        sourceFiles
          .map((entry) => `${entry.filename}: ${entry.accepted_count}/${entry.accepted_count + entry.rejected_count} ok`)
          .join(" | ")
      )}</div>`
    );
  }

  if (invalidFiles.length > 0) {
    lines.push(`<div class="table-subtext table-subtext-error">${escapeHtml(`${invalidFiles.length} skipped file(s)`)}</div>`);
  }

  return lines.join("");
}

function formatAdminErrorCell(item) {
  const invalidFiles = item.invalid_files || [];
  const parts = [escapeHtml(item.error_message || "-")];

  if (invalidFiles.length > 0) {
    parts.push(
      `<div class="table-subtext table-subtext-error">${escapeHtml(
        invalidFiles.map((entry) => `${entry.filename}: ${entry.errors.join(", ")}`).join(" | ")
      )}</div>`
    );
  }

  return parts.join("");
}

function renderInlineReportActions(item) {
  if (item.status !== "completed") {
    return "";
  }
  return `<div class="table-action-row">${renderReportButtons(item)}</div>`;
}

function renderReportButtons(item) {
  if (item.status !== "completed") {
    return "-";
  }
  return `
    <div class="table-action-row">
      <button type="button" class="ghost-button small-button" data-report-action="open" data-task-id="${escapeHtml(item.task_id)}">
        Открыть отчёт
      </button>
      <button type="button" class="ghost-button small-button" data-report-action="download" data-task-id="${escapeHtml(item.task_id)}">
        Скачать HTML
      </button>
    </div>
  `;
}

function statusBadge(status) {
  return `<span class="status-pill status-${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

function showNotice(message, isError = false) {
  elements.notice.hidden = false;
  elements.notice.textContent = message;
  elements.notice.style.background = isError
    ? "linear-gradient(135deg, rgba(138, 48, 56, 0.96), rgba(200, 93, 102, 0.96))"
    : "linear-gradient(135deg, rgba(17, 32, 44, 0.96), rgba(44, 72, 88, 0.96))";

  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => {
    elements.notice.hidden = true;
  }, 4200);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
