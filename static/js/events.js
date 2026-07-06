      /* ---- Events ---- */
      function wireEvents() {
        modelEl.addEventListener("change", () => { updateModelFields(); saveConfig(); });
        $("generateForm").addEventListener("submit", (event) => event.preventDefault());
        $("generateForm").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") runSingleTask(); });
        $("prompt").addEventListener("input", debounce(() => { saveConfig(); updateSubmitHint(); }, 500));
        $("size").addEventListener("change", () => { saveConfig(); updateSubmitHint(); });
        $("aspectRatio").addEventListener("change", () => { saveConfig(); updateSubmitHint(); });
        $("resolution").addEventListener("change", () => { updateModelFields(false); saveConfig(); });
        $("quality").addEventListener("change", () => { saveConfig(); updateSubmitHint(); });
        $("variants").addEventListener("change", () => { saveConfig(); updateSubmitHint(); });
        $("outputFormat").addEventListener("change", () => { updateModelFields(false); saveConfig(); updateSubmitHint(); });
        $("outputCompression").addEventListener("input", debounce(() => { $("outputCompression").value = String(Math.max(0, Math.min(100, Number($("outputCompression").value) || 0))); saveConfig(); updateSubmitHint(); }, 250));
        $("moderation").addEventListener("change", () => { saveConfig(); updateSubmitHint(); });
        $("seed").addEventListener("input", debounce(() => { saveConfig(); updateSubmitHint(); }, 500));
        $("autoRetryEnabled").addEventListener("change", () => { autoRetryEnabled = $("autoRetryEnabled").value === "true"; saveConfig(); });
        $("autoRetryMaxAttempts").addEventListener("input", debounce(() => { autoRetryMaxAttempts = Math.max(0, Math.min(10, Number($("autoRetryMaxAttempts").value) || 0)); $("autoRetryMaxAttempts").value = String(autoRetryMaxAttempts); saveConfig(); }, 250));
        $("thirdPartyReferenceImagesAsFiles").addEventListener("change", () => { thirdPartyReferenceImagesAsFiles = $("thirdPartyReferenceImagesAsFiles").value !== "false"; refreshImageSelectors(); saveConfig(); });
        $("randomSeed").addEventListener("click", () => setSeedValue(Math.floor(Math.random() * 2147483648)));
        $("resetSeed").addEventListener("click", () => setSeedValue(0));

        $("openRuntimeSettings").addEventListener("click", () => { $("runtimeDialog").showModal(); switchRuntimeTab("status"); refreshRuntime(false); });
        $("closeRuntimeSettings").addEventListener("click", () => $("runtimeDialog").close());
        $("openAuth").addEventListener("click", () => { $("tokenInput").value = authToken; $("authDialog").showModal(); });
        $("closeAuth").addEventListener("click", () => $("authDialog").close());
        $("saveToken").addEventListener("click", saveAccessToken);
        $("clearToken").addEventListener("click", clearAccessToken);
        $("refreshAccount").addEventListener("click", () => refreshAccountInfo(true));
        $("refreshRuntime").addEventListener("click", () => refreshRuntime(true));
        $("saveRuntimePort").addEventListener("click", saveRuntimePort);
        $("restartServer").addEventListener("click", restartServer);
        $("runtimeTabStatus").addEventListener("click", () => switchRuntimeTab("status"));
        $("runtimeTabEnv").addEventListener("click", () => switchRuntimeTab("env"));
        $("runtimeTabProviders").addEventListener("click", () => switchRuntimeTab("providers"));
        $("saveEnvConfig").addEventListener("click", saveEnvConfig);
        $("addOpenaiProvider").addEventListener("click", addOpenaiProviderCard);
        $("loadAppLog").addEventListener("click", () => loadServerLog("app"));
        $("loadAuditLog").addEventListener("click", () => loadServerLog("audit"));
        $("refreshJobs").addEventListener("click", loadGateway);
        $("mainImageFiles").addEventListener("change", (event) => uploadFilesToRole(event.target.files, "main"));
        $("referenceImageFiles").addEventListener("change", (event) => uploadFilesToRole(event.target.files, "reference"));
        $("chooseHistoricalMain").addEventListener("click", () => openHistoricalPicker("main"));
        $("chooseHistoricalReference").addEventListener("click", () => openHistoricalPicker("reference"));
        $("clearMainImages").addEventListener("click", () => clearImagesFromRole("main"));
        $("clearReferenceImages").addEventListener("click", () => clearImagesFromRole("reference"));
        $("closeHistoricalPicker").addEventListener("click", closeHistoricalPicker);
        $("pickerMainTab").addEventListener("click", () => setHistoricalPickerRole("main"));
        $("pickerReferenceTab").addEventListener("click", () => setHistoricalPickerRole("reference"));
        $("closeImageLightbox").addEventListener("click", closeImageLightbox);
        $("imageLightbox").addEventListener("close", resetImageLightbox);
        $("cancelDeleteImage").addEventListener("click", () => { pendingDeleteImageRecord = null; $("deleteImageDialog").close(); });
        $("deleteImageDialog").addEventListener("close", () => { pendingDeleteImageRecord = null; });
        $("confirmDeleteImage").addEventListener("click", confirmDeleteHistoryImage);
        $("loadLightboxParams").addEventListener("click", () => { if (activeLightboxRecord) loadHistoryParams(activeLightboxRecord); });
        $("copyLightboxImage").addEventListener("click", () => { if (activeLightboxRecord) copyImageUrl(activeLightboxRecord.url); });
        $("rerunLightboxImage").addEventListener("click", () => { if (activeLightboxRecord) rerunRecord(activeLightboxRecord); });
        $("setLightboxMain").addEventListener("click", () => { if (activeLightboxRecord) useResultAsInput(activeLightboxRecord, "main"); });
        $("addLightboxReference").addEventListener("click", () => { if (activeLightboxRecord) useResultAsInput(activeLightboxRecord, "reference"); });
        $("urls").addEventListener("input", () => { refreshImageSelectors(); saveConfig(); });
        wireImageBoxDropzone("mainImageBox", "mainImageFiles", "main");
        wireImageBoxDropzone("referenceImageBox", "referenceImageFiles", "reference");
        $("submitJobs").addEventListener("click", runSingleTask);
        $("batchSubmitJobs").addEventListener("click", runBatchTasks);
        $("stopJobs").addEventListener("click", stopActiveJob);
        $("retryFailedTasks").addEventListener("click", retryAllFailedTasks);
        $("toggleTaskQueue").addEventListener("click", toggleTaskQueue);
        $("historyPageSize").addEventListener("change", () => {
          historyPageSize = Number($("historyPageSize").value) || 24;
          historyPage = 1;
          saveHistoryView();
          renderHistoryPage();
        });
        $("historyColumns").addEventListener("change", () => {
          historyColumns = Number($("historyColumns").value) || 4;
          applyHistoryColumns();
          saveHistoryView();
        });
        $("historySearch").addEventListener("input", debounce(() => {
          historySearchText = $("historySearch").value.trim();
          historyPage = 1;
          saveHistoryView();
          renderHistoryPage();
        }, 250));
        $("historyModelFilter").addEventListener("change", () => {
          historyModelFilter = $("historyModelFilter").value;
          historyPage = 1;
          saveHistoryView();
          renderHistoryPage();
        });
        $("historySort").addEventListener("change", () => {
          historySort = $("historySort").value || "newest";
          historyPage = 1;
          saveHistoryView();
          renderHistoryPage();
        });
        $("historyPrevPage").addEventListener("click", () => {
          historyPage = Math.max(historyPage - 1, 1);
          renderHistoryPage();
        });
        $("historyNextPage").addEventListener("click", () => {
          historyPage = Math.min(historyPage + 1, getHistoryTotalPages());
          renderHistoryPage();
        });
        $("clearResults").addEventListener("click", () => {
          historyImageUrls.clear();
          historyRecords = [];
          historyPage = 1;
          renderHistoryPage();
          toast("已清空当前视图，刷新历史可重新载入记录。");
          log("已清空当前画廊视图。", "info");
        });
        $("toggleLogPanel").addEventListener("click", toggleLogPanel);
        $("clearLog").addEventListener("click", () => { logBox.innerHTML = ""; log("日志已清空。", "info"); });
        bindRange("temperature", "temperatureNumber", "temperatureValue", () => { saveConfig(); updateSubmitHint(); });
        bindRange("topP", "topPNumber", "topPValue", () => { saveConfig(); updateSubmitHint(); });
        restoreHistoryView();
      }
