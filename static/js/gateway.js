      /* ---- Gateway ---- */
      async function loadGateway() {
        try {
          const [configResponse, modelsResponse, jobsResponse, runtimeResponse] = await Promise.all([
            apiRequest("/api/config"),
            apiRequest("/api/models"),
            apiRequest("/api/jobs"),
            apiRequest("/api/admin/runtime"),
          ]);
          const config = configResponse.data || {};
          pollInterval = Math.max(Number(config.poll_interval_seconds || 5) * 1000, 1000);
          maxUploadBytes = Math.max(Number(config.max_upload_mb || DEFAULT_MAX_UPLOAD_MB), 1) * 1024 * 1024;
          if (modelsResponse.data && Object.keys(modelsResponse.data).length) {
            const previousModel = modelEl.value;
            modelSchemas = modelsResponse.data;
            renderModels(previousModel);
            applySavedConfig(loadSavedConfig(), { includePrompt: false });
          }
          const jobs = jobsResponse.data || [];
          renderHistory(jobs);
          restoreActiveJobs(jobs);
          lastSeenJobIds = new Set(jobs.map((job) => job.id).filter(Boolean));
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
        names.forEach((name) => modelEl.add(new Option(modelDisplayName(name), name)));
        modelEl.value = names.includes(current) ? current : names[0];
        renderHistoryFilters();
      }

      function renderAuthState(config = null) {
        $("activeKey").textContent = authToken ? "已认证" : "待认证";
        $("gatewayStatus").textContent = authToken ? "已认证" : "待认证";
        if (config) {
          const keys = Array.isArray(config.bizyair_keys) ? config.bizyair_keys : [];
          const keyText = keys.length ? ` · ${keys.length} 个 Key · 工作线程 ${config.worker_threads || "--"}` : "";
          $("activeKey").textContent = authToken ? `已认证${keyText}` : "待认证";
          $("gatewayStatus").textContent = authToken ? `已认证${keyText}` : "待认证";
        }
      }

      async function refreshRuntime(showToast = false) {
        try {
          const [runtimeResponse, jobsResponse] = await Promise.all([
            apiRequest("/api/admin/runtime"),
            apiRequest("/api/jobs"),
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
        if (runtime.port) $("runtimePort").value = runtime.port;
        $("runtimeHint").textContent = runtime.log_dir ? `日志目录：${runtime.log_dir}` : "修改端口后需要重启服务生效。";
      }

      function refreshExternalJobs(jobs) {
        const newJobs = jobs.filter((job) => job.id && !lastSeenJobIds.has(job.id));
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
        lastSeenJobIds = new Set(jobs.map((job) => job.id).filter(Boolean));
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
        try {
          const response = await apiRequest("/api/admin/config", { method: "POST", body: { port } });
          const restartText = response.data?.restart_required ? "，重启后生效" : "";
          toast(`端口已保存${restartText}。`);
          log(`端口配置已保存为 ${port}${restartText}。`, "success");
        } catch (error) {
          toast(error.message);
          log(`端口保存失败：${error.message}`, "error");
        }
      }

      async function restartServer() {
        try {
          await apiRequest("/api/admin/restart", { method: "POST" });
          toast("服务正在重启，请稍后刷新页面。");
          log("已发送服务重启请求。", "warn");
        } catch (error) {
          toast(error.message);
          log(`重启失败：${error.message}`, "error");
        }
      }
