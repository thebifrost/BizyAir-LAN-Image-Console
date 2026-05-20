      /* ---- Gateway ---- */
      async function loadGateway() {
        try {
          const [configResponse, modelsResponse, jobsResponse] = await Promise.all([
            apiRequest("/api/config"),
            apiRequest("/api/models"),
            apiRequest("/api/jobs"),
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
          renderAuthState(config);
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
