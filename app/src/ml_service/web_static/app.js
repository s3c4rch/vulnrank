const state = {
  token: window.localStorage.getItem("vulnrank_token"),
  user: null,
  models: [],
  activeTaskId: null,
  pollTimer: null,
};

const elements = {
  notice: document.getElementById("notice"),
  sessionTitle: document.getElementById("session-title"),
  sessionSubtitle: document.getElementById("session-subtitle"),
  logoutButton: document.getElementById("logout-button"),
  workspace: document.getElementById("workspace"),
  history: document.getElementById("history"),
  adminStudio: document.getElementById("admin-studio"),
  registerForm: document.getElementById("register-form"),
  loginForm: document.getElementById("login-form"),
  topupForm: document.getElementById("topup-form"),
  predictForm: document.getElementById("predict-form"),
  modelSelect: document.getElementById("model-select"),
  featureRows: document.getElementById("feature-rows"),
  featureRowTemplate: document.getElementById("feature-row-template"),
  predictionStatus: document.getElementById("prediction-status"),
  predictionHistoryBody: document.getElementById("prediction-history-body"),
  transactionHistoryBody: document.getElementById("transaction-history-body"),
  adminUsersBody: document.getElementById("admin-users-body"),
  adminFailedBody: document.getElementById("admin-failed-body"),
  adminTransactionsBody: document.getElementById("admin-transactions-body"),
  profileEmail: document.getElementById("profile-email"),
  profileRole: document.getElementById("profile-role"),
  balanceAmount: document.getElementById("balance-amount"),
  balanceUpdated: document.getElementById("balance-updated"),
  loginEmail: document.getElementById("login-email"),
  loginPassword: document.getElementById("login-password"),
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  ensureFeatureRows();
  hydrateSession();
});

function bindEvents() {
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
    document.getElementById("auth-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("load-demo-admin").addEventListener("click", () => {
    elements.loginEmail.value = "demo-admin@example.com";
    elements.loginPassword.value = "demo-admin-password";
    document.getElementById("auth-panel").scrollIntoView({ behavior: "smooth", block: "start" });
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

    showNotice(`Баланс пополнен: +${response.data.transaction.amount} credits.`);
    await refreshWorkspace();
  });

  document.getElementById("add-feature-row").addEventListener("click", () => addFeatureRow());

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

  elements.predictForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const buildResult = buildFeaturePayload();
    if (!buildResult.ok) {
      showPredictionStatus(buildResult.message, true);
      return;
    }

    const payload = {
      model: elements.modelSelect.value,
      features: buildResult.features,
    };

    const response = await apiRequest("/predict", {
      method: "POST",
      body: JSON.stringify(payload),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    state.activeTaskId = response.data.task_id;
    showPredictionStatus(
      `Задача ${response.data.task_id} поставлена в очередь. Ждём worker и обновляем историю...`
    );
    await refreshWorkspace();
    startTaskPolling(response.data.task_id);
  });

  elements.adminUsersBody.addEventListener("submit", async (event) => {
    const form = event.target.closest(".admin-topup-form");
    if (!form) {
      return;
    }
    event.preventDefault();

    const userId = form.dataset.userId;
    const amountInput = form.querySelector("input[name='amount']");
    const response = await apiRequest(`/admin/users/${userId}/balance/top-up`, {
      method: "POST",
      body: JSON.stringify({ amount: amountInput.value }),
      auth: true,
    });
    if (!response.ok) {
      return;
    }

    amountInput.value = "5.00";
    showNotice(`Баланс пользователя обновлён: +${response.data.transaction.amount}.`);
    await refreshWorkspace();
  });
}

function ensureFeatureRows() {
  if (elements.featureRows.children.length > 0) {
    return;
  }
  addFeatureRow("x1", "1.20");
  addFeatureRow("x2", "5.70");
}

function addFeatureRow(name = "", value = "") {
  const fragment = elements.featureRowTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".feature-row");
  row.querySelector("input[name='feature-name']").value = name;
  row.querySelector("input[name='feature-value']").value = value;
  elements.featureRows.appendChild(fragment);
}

function buildFeaturePayload() {
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
  refreshWorkspace();
}

async function hydrateSession() {
  if (!state.token) {
    renderGuestState();
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
    renderGuestState();
    return;
  }

  const requests = [
    apiRequest("/users/me", { auth: true, silent: true }),
    apiRequest("/balance", { auth: true, silent: true }),
    apiRequest("/models", { auth: true, silent: true }),
    apiRequest("/history/predictions", { auth: true, silent: true }),
    apiRequest("/history/transactions", { auth: true, silent: true }),
  ];

  if (state.user?.role === "admin") {
    requests.push(
      apiRequest("/admin/users", { auth: true, silent: true }),
      apiRequest("/admin/history/predictions?failed_only=true", { auth: true, silent: true }),
      apiRequest("/admin/history/transactions", { auth: true, silent: true })
    );
  }

  const responses = await Promise.all(requests);
  const failedResponse = responses.find((response) => !response.ok);
  if (failedResponse) {
    logout({ quiet: true });
    return;
  }

  const [userResponse, balanceResponse, modelsResponse, predictionsResponse, transactionsResponse] = responses;
  state.user = userResponse.data;
  state.models = modelsResponse.data.items;

  renderSession();
  renderModels();
  renderHistory(predictionsResponse.data.items, transactionsResponse.data.items);

  if (state.user.role === "admin") {
    const adminUsersResponse = responses[5];
    const adminFailedResponse = responses[6];
    const adminTransactionsResponse = responses[7];
    renderAdmin(adminUsersResponse.data.items, adminFailedResponse.data.items, adminTransactionsResponse.data.items);
  } else {
    elements.adminStudio.hidden = true;
  }

  elements.balanceAmount.textContent = formatCredits(balanceResponse.data.amount);
  elements.balanceUpdated.textContent = formatDate(balanceResponse.data.updated_at);
}

function renderGuestState() {
  state.user = null;
  elements.workspace.hidden = true;
  elements.history.hidden = true;
  elements.adminStudio.hidden = true;
  elements.logoutButton.hidden = true;
  elements.sessionTitle.textContent = "Гость";
  elements.sessionSubtitle.textContent = "Авторизуйтесь, чтобы открыть рабочие панели.";
}

function renderSession() {
  elements.workspace.hidden = false;
  elements.history.hidden = false;
  elements.logoutButton.hidden = false;
  elements.sessionTitle.textContent = state.user.email;
  elements.sessionSubtitle.textContent =
    state.user.role === "admin"
      ? "Админский режим активирован: доступен общий обзор пользователей и ошибок."
      : "Рабочая сессия активна. Можно пополнять баланс и отправлять ML-задачи.";

  elements.profileEmail.textContent = state.user.email;
  elements.profileRole.textContent = state.user.role;
}

function renderModels() {
  elements.modelSelect.innerHTML = "";
  state.models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.name;
    option.textContent = `${model.name} ${model.version} - ${formatCredits(model.cost_per_prediction)}`;
    elements.modelSelect.appendChild(option);
  });
}

function renderHistory(predictions, transactions) {
  renderPredictionRows(elements.predictionHistoryBody, predictions, { showUser: false });
  renderTransactionRows(elements.transactionHistoryBody, transactions, { showUser: false });
}

function renderAdmin(users, failedPredictions, transactions) {
  elements.adminStudio.hidden = false;
  renderAdminUsers(users);
  renderPredictionRows(elements.adminFailedBody, failedPredictions, { showUser: true, failedOnly: true });
  renderTransactionRows(elements.adminTransactionsBody, transactions, { showUser: true });
}

function renderAdminUsers(users) {
  if (users.length === 0) {
    elements.adminUsersBody.innerHTML = `<tr><td colspan="4" class="table-empty">Пользователи пока отсутствуют.</td></tr>`;
    return;
  }

  elements.adminUsersBody.innerHTML = users
    .map((user) => {
      return `
        <tr>
          <td>${escapeHtml(user.email)}</td>
          <td>${escapeHtml(user.role)}</td>
          <td>${formatCredits(user.balance.amount)}</td>
          <td>
            <form class="admin-topup-form admin-user-actions" data-user-id="${escapeHtml(user.id)}">
              <input type="number" step="0.01" min="0.01" name="amount" value="5.00" required />
              <button type="submit" class="primary-button small-button">Пополнить</button>
            </form>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderPredictionRows(target, predictions, options) {
  if (predictions.length === 0) {
    const colspan = options.showUser ? 5 : 6;
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
            <td>${escapeHtml(item.model_name)}</td>
            <td>${escapeHtml(item.error_message || "-")}</td>
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
          <td><code>${escapeHtml(item.task_id)}</code></td>
          <td>${statusBadge(item.status)}</td>
          <td>${escapeHtml(item.predicted_priority || "-")}</td>
          <td>${item.prediction_value ?? "-"}</td>
          <td>${escapeHtml(item.worker_id || "-")}</td>
          <td>${formatCredits(item.spent_credits)}</td>
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
            <td>${escapeHtml(item.status)}</td>
            <td>${escapeHtml(item.review_comment || "-")}</td>
          </tr>
        `;
      }

      return `
        <tr>
          <td>${escapeHtml(item.type)}</td>
          <td>${formatCredits(item.amount)}</td>
          <td>${escapeHtml(item.status)}</td>
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
    const response = await apiRequest("/history/predictions", { auth: true, silent: true });
    if (!response.ok) {
      return;
    }

    renderPredictionRows(elements.predictionHistoryBody, response.data.items, { showUser: false });
    const task = response.data.items.find((item) => item.task_id === taskId);
    if (!task) {
      state.pollTimer = window.setTimeout(poll, 2000);
      return;
    }

    if (task.status === "completed") {
      showPredictionStatus(
        `Задача ${task.task_id} завершена: priority ${task.predicted_priority}, value ${task.prediction_value}, worker ${task.worker_id}.`,
        false,
        true
      );
      await refreshWorkspace();
      return;
    }

    if (task.status === "failed") {
      showPredictionStatus(
        `Задача ${task.task_id} завершилась ошибкой: ${task.error_message || "неизвестная ошибка"}.`,
        true
      );
      await refreshWorkspace();
      return;
    }

    showPredictionStatus(`Задача ${task.task_id} сейчас в статусе ${task.status}. Продолжаем опрос...`);
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

async function apiRequest(path, options = {}) {
  const requestOptions = {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
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
  renderGuestState();
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
