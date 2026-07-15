      /* ---- Gateway ---- */
      async function loadGateway() {
        try {
          const [configResponse, modelsResponse, jobsResponse, runtimeResponse] = await Promise.all([
            apiRequest("/api/config"),
            apiRequest("/api/models"),
            apiRequest(`/api/jobs?limit=${HISTORY_JOB_BATCH_SIZE}&offset=0`),
            apiRequest("/api/admin/runtime"),
          ]);
          const config = configResponse.data || {};
          const savedConfig = loadSavedConfig();
          const hasSavedReferenceMode = savedConfig && Number(savedConfig.configVersion || 0) >= 2 && "thirdPartyReferenceImagesAsFiles" in savedConfig;
          if (!hasSavedReferenceMode) {
            thirdPartyReferenceImagesAsFiles = config.openai_compatible_send_reference_images_as_files !== false;
            $("thirdPartyReferenceImagesAsFiles").value = String(thirdPartyReferenceImagesAsFiles);
          }
          pollInterval = Math.max(Number(config.poll_interval_seconds || 5) * 1000, 1000);
          maxUploadBytes = Math.max(Number(config.max_upload_mb || DEFAULT_MAX_UPLOAD_MB), 1) * 1024 * 1024;
          if (modelsResponse.data && Object.keys(modelsResponse.data).length) {
            const previousModel = modelEl.value;
            modelSchemas = modelsResponse.data;
            renderModels(previousModel);
            applySavedConfig(savedConfig, { includePrompt: false });
          }
          const jobs = jobsResponse.data || [];
          renderHistory(jobs);
          restoreActiveJobs(jobs);
          lastSeenJobIds = new Set(jobs.map((job) => job.id).filter(Boolean));
          void loadRemainingHistoryJobs();
          renderAuthState(config);
          renderRuntime(runtimeResponse.data || {});
          startRuntimeRefresh();
          await refreshAccountInfo(false);
          log(`已连接本地网关 ${config.version || ""}。`, "success");
        } catch (error) {
          log(`连接本地网关失败：${error.message}`, "error");
          toast(error.status === 401 ? "访问口令无效。" : "连接本地网关失败。");
          if (error.status === 401) $("authDialog").showModal();
        }
      }

      function renderModels(preferredModel = "gpt-image-2") {
        const current = preferredModel || modelEl.value;
        const names = Object.keys(modelSchemas);
        modelEl.innerHTML = "";
        const groups = new Map();
        names.forEach((name) => {
          const schema = modelSchemas[name] || {};
          const groupName = schema.provider === "bizyair" || !schema.provider ? "BizyAir" : (schema.providerLabel || schema.provider || "第三方");
          if (!groups.has(groupName)) groups.set(groupName, []);
          groups.get(groupName).push(name);
        });
        groups.forEach((models, groupName) => {
          const group = document.createElement("optgroup");
          group.label = groupName;
          models.forEach((name) => group.appendChild(new Option(modelDisplayName(name), name)));
          modelEl.appendChild(group);
        });
        modelEl.value = names.includes(current) ? current : names[0];
        renderHistoryFilters();
      }

      function renderAuthState(config = null) {
        const pill = $("activeKey");
        const status = $("gatewayStatus");
        const badge = $("authStatusBadge");
        const setState = (target, text, state) => {
          if (!target) return;
          target.textContent = text;
          target.classList.remove("is-ok", "is-pending", "is-error");
          if (state) target.classList.add(state);
        };
        const baseText = authToken ? "已认证" : "待认证";
        const state = authToken ? "is-ok" : "is-pending";
        setState(pill, baseText, state);
        setState(status, baseText, state);
        setState(badge, authToken ? "已连接" : "未连接", state);
        if (config) {
          const keys = Array.isArray(config.bizyair_keys) ? config.bizyair_keys : [];
          const providers = Array.isArray(config.openai_providers) ? config.openai_providers : [];
          const keyParts = [];
          if (keys.length) keyParts.push(`${keys.length} 个 Key`);
          if (providers.length) keyParts.push(`${providers.length} 个第三方`);
          keyParts.push(`工作线程 ${config.worker_threads || "--"}`);
          const keyText = ` · ${keyParts.join(" · ")}`;
          setState(pill, authToken ? `已认证${keyText}` : "待认证", state);
          setState(status, authToken ? `已认证${keyText}` : "待认证", state);
        }
      }

      async function refreshRuntime(showToast = false) {
        try {
          const [runtimeResponse, jobsResponse] = await Promise.all([
            apiRequest("/api/admin/runtime"),
            apiRequest(`/api/jobs?limit=${HISTORY_JOB_BATCH_SIZE}&offset=0`),
          ]);
          renderRuntime(runtimeResponse.data || {});
          refreshExternalJobs(jobsResponse.data || []);
          if (showToast) toast("运行状态已刷新。");
        } catch (error) {
          log(`运行状态刷新失败：${error.message}`, "warn");
        }
      }

      function renderRuntime(runtime) {
        $("openaiBaseUrl").textContent = runtime.openai_base_url || `${location.origin}/v1`;
        $("runtimeQueueLength").textContent = String(runtime.queue_length ?? "--");
        const workerEl = $("runtimeWorkerThreads");
        if (workerEl) workerEl.textContent = String(runtime.worker_threads ?? "--");
        const logDirEl = $("runtimeLogDir");
        if (logDirEl) logDirEl.textContent = runtime.log_dir || "--";
        if (runtime.port) $("runtimePort").value = runtime.port;
        $("runtimeHint").textContent = runtime.log_dir ? `日志目录：${runtime.log_dir}` : "修改端口后需要重启服务生效。";
      }

      async function copyBaseUrl() {
        const text = $("openaiBaseUrl").textContent.trim();
        if (!text || text === "--") return;
        try {
          await navigator.clipboard.writeText(text);
          toast("已复制 Base URL。");
        } catch {
          toast("复制失败，请手动选中 Base URL 复制。");
        }
      }

      function refreshExternalJobs(jobs) {
        const unseenJobs = jobs.filter((job) => job.id && !lastSeenJobIds.has(job.id));
        unseenJobs.forEach((job) => lastSeenJobIds.add(job.id));
        unseenJobs.forEach((job) => appendToHistory(job));
        const newJobs = unseenJobs.filter((job) => !TERMINAL_STATUSES.includes(job.status));
        if (newJobs.length) {
          newJobs.forEach((job) => {
            const task = normalizeTaskFromJob(job);
            upsertSubmittedTask(task);
            if (!TERMINAL_STATUSES.includes(task.status)) pollJob(task.id);
          });
          renderTaskQueue();
          renderTaskLargePreview();
          log(`检测到 ${newJobs.length} 个外部 OpenAI 任务入队。`, "info");
        }
      }

      function hasActiveTasks() {
        return submittedTasks.some((task) => !TERMINAL_STATUSES.includes(task.status)) || pollingJobs.size > 0;
      }

      function runtimeRefreshDelay() {
        return hasActiveTasks() ? Math.max(pollInterval, 3000) : Math.max(IDLE_RUNTIME_REFRESH_INTERVAL, pollInterval);
      }

      function startRuntimeRefresh() {
        clearTimeout(runtimeRefreshTimer);
        runtimeRefreshTimer = setTimeout(async () => {
          if (authToken) await refreshRuntime(false);
          startRuntimeRefresh();
        }, runtimeRefreshDelay());
      }

      async function saveRuntimePort() {
        const port = Number($("runtimePort").value);
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
          toast("端口必须是 1-65535 的整数。");
          return;
        }
        const btn = $("saveRuntimePort");
        btn.disabled = true;
        btn.classList.add("is-loading");
        try {
          const response = await apiRequest("/api/admin/config", { method: "POST", body: { port } });
          const restartRequired = response.data?.restart_required;
          const restartText = restartRequired ? "，重启后生效" : "";
          toast(`端口已保存${restartText}。`);
          log(`端口配置已保存为 ${port}${restartText}。`, "success");
          if (restartRequired) {
            const alert = $("runtimeRestartAlert");
            if (alert) alert.hidden = false;
          }
        } catch (error) {
          toast(error.message);
          log(`端口保存失败：${error.message}`, "error");
        } finally {
          btn.disabled = false;
          btn.classList.remove("is-loading");
        }
      }

      async function restartServer() {
        if (!window.confirm("确认重启服务？正在运行的任务会继续执行，页面将短暂断连。")) return;
        const btn = $("restartServer");
        btn.disabled = true;
        btn.classList.add("is-loading");
        try {
          await apiRequest("/api/admin/restart", { method: "POST" });
          toast("服务正在重启，请稍后刷新页面。");
          log("已发送服务重启请求。", "warn");
        } catch (error) {
          toast(error.message);
          log(`重启失败：${error.message}`, "error");
        } finally {
          setTimeout(() => { btn.disabled = false; btn.classList.remove("is-loading"); }, 1500);
        }
      }
