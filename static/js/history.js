      /* ---- History (bottom gallery) ---- */
      function renderHistory(jobs) {
        historyImageUrls.clear();
        historyRecords = [];
        jobs.forEach((job) => {
          getJobImageRecords(job).forEach((record) => addHistoryRecord(record, false));
        });
        historyPage = 1;
        renderHistoryFilters();
        renderHistoryPage();
      }

      function appendToHistory(job) {
        const records = getJobImageRecords(job);
        if (!records.length) return;
        const shouldStay = historyPage === 1 && !historySearchText && !historyModelFilter;
        records.slice().reverse().forEach((record) => addHistoryRecord(record, true));
        renderHistoryFilters();
        if (shouldStay) historyPage = 1;
        else toast("有新结果，回到第一页可查看。");
        renderHistoryPage();
      }

      function addHistoryRecord(record, prepend) {
        const key = record.id || record.displayUrl || record.url || record.originalUrl;
        if (historyImageUrls.has(key)) return;
        historyImageUrls.add(key);
        if (prepend) historyRecords.unshift(record);
        else historyRecords.push(record);
      }

      function renderHistoryPage() {
        applyHistoryColumns();
        const filtered = getFilteredHistoryRecords();
        const totalPages = getHistoryTotalPages(filtered);
        historyPage = Math.min(Math.max(historyPage, 1), totalPages);
        resultsEl.innerHTML = "";
        if (!filtered.length) {
          resultsEl.innerHTML = emptyHistoryMarkup();
        } else {
          const start = (historyPage - 1) * historyPageSize;
          const fragment = document.createDocumentFragment();
          filtered.slice(start, start + historyPageSize).forEach((record) => fragment.appendChild(createResultCard(record)));
          resultsEl.appendChild(fragment);
        }
        updateHistoryCount(filtered);
      }

      function getFilteredHistoryRecords() {
        const search = historySearchText.toLowerCase();
        return historyRecords
          .filter((record) => !search || `${record.prompt || ""} ${record.label || ""}`.toLowerCase().includes(search))
          .filter((record) => !historyModelFilter || record.model === historyModelFilter)
          .slice()
          .sort((a, b) => {
            const av = Date.parse(a.finishedAt || a.createdAt || "") || 0;
            const bv = Date.parse(b.finishedAt || b.createdAt || "") || 0;
            return historySort === "oldest" ? av - bv : bv - av;
          });
      }

      function getHistoryTotalPages(records = getFilteredHistoryRecords()) {
        return Math.max(Math.ceil(records.length / historyPageSize), 1);
      }

      function createResultCard(record) {
        const card = document.createElement("article");
        card.className = "card";
        card.dataset.imageUrl = record.displayUrl;
        card.innerHTML = resultCardMarkup(record);
        wireCachedImageFallbacks(card);
        card.addEventListener("click", () => openImageLightbox(record));
        wireResultActions(card, record);
        return card;
      }

      function getJobImageRecords(job) {
        if (Array.isArray(job.image_records) && job.image_records.length) return job.image_records.map(normalizeImageRecord);
        const records = [];
        (job.items || []).forEach((item) => {
          const result = item.result || {};
          const outputs = result.outputs || result.data?.outputs || {};
          const images = Array.isArray(outputs.images) ? outputs.images : [];
          images.forEach((url) => records.push(normalizeImageRecord({
            url,
            label: `${job.model || ""} ${job.id.slice(0, 6)}`,
            prompt: item.prompt || "",
            model: job.model || "",
            params: item.payload || job.params || {},
            job_id: job.id,
            item_id: item.id,
            created_at: item.created_at || job.created_at,
            finished_at: item.finished_at,
          })));
        });
        if (!records.length) {
          (job.latest_images || []).forEach((url) => records.push(normalizeImageRecord({ url, label: `${job.model || ""} ${job.id.slice(0, 6)}`, prompt: "", model: job.model || "", params: job.params || {}, job_id: job.id, created_at: job.created_at })));
        }
        return records;
      }

      function normalizeImageRecord(record) {
        const displayUrl = record.displayUrl || record.display_url || record.url || "";
        const originalUrl = record.originalUrl || record.original_url || record.sourceUrl || record.source_url || record.url || displayUrl;
        const downloadUrl = record.downloadUrl || record.download_url || displayUrl;
        return {
          id: record.id || "",
          url: displayUrl,
          displayUrl,
          downloadUrl,
          originalUrl,
          sourceUrl: originalUrl || displayUrl,
          local: Boolean(record.local),
          label: record.label || `${record.model || "图片"}`,
          prompt: record.prompt || "",
          model: record.model || "",
          params: record.params || {},
          jobId: record.jobId || record.job_id || "",
          itemId: record.itemId || record.item_id || "",
          createdAt: record.createdAt || record.created_at || "",
          finishedAt: record.finishedAt || record.finished_at || "",
          status: record.status || "succeeded",
        };
      }

      function getJobImages(job) {
        return getJobImageRecords(job).map((record) => record.displayUrl || record.url);
      }

      function loadHistoryParams(record) {
        const params = record.params || {};
        if (record.prompt) $("prompt").value = record.prompt;
        if (record.model && modelSchemas[record.model]) {
          modelEl.value = record.model;
          updateModelFields(false);
        }
        setSelectValue("resolution", params.resolution);
        updateModelFields(false);
        setSelectValue("aspectRatio", params.aspect_ratio);
        setSelectValue("quality", params.quality);
        setSelectValue("variants", params.variants);
        if ("seed" in params) $("seed").value = params.seed;
        if ("temperature" in params) syncRangeValue("temperature", "temperatureNumber", "temperatureValue", params.temperature);
        if ("top_p" in params) syncRangeValue("topP", "topPNumber", "topPValue", params.top_p);
        if ("max_tokens" in params) $("maxTokens").value = params.max_tokens;
        if (Array.isArray(params.urls) && params.urls.length) {
          addImagesToRole("main", [params.urls[0]]);
          selectedMainImageUrls = [params.urls[0]];
          selectedReferenceUrls = params.urls.slice(1, 1 + getReferenceSlotLimit());
          uploadedImageUrls = [...new Set([...uploadedImageUrls, ...params.urls])];
          appendUrls(params.urls, { refresh: false });
          refreshImageSelectors();
        }
        saveConfig();
        toast("已加载该图片的生成参数。");
      }

      function updateHistoryCount(records = getFilteredHistoryRecords()) {
        const totalPages = getHistoryTotalPages(records);
        $("historyCount").textContent = `${records.length}/${historyRecords.length} 张`;
        $("historyPageInfo").textContent = `${historyPage} / ${totalPages}`;
        $("historyPrevPage").disabled = historyPage <= 1;
        $("historyNextPage").disabled = historyPage >= totalPages;
      }

      function renderHistoryFilters() {
        const select = $("historyModelFilter");
        const current = historyModelFilter;
        const models = [...new Set(historyRecords.map((record) => record.model).filter(Boolean))].sort();
        select.innerHTML = '<option value="">全部模型</option>';
        models.forEach((model) => select.add(new Option(modelDisplayName(model), model)));
        select.value = models.includes(current) ? current : "";
        historyModelFilter = select.value;
      }

      function saveHistoryView() {
        try { localStorage.setItem(HISTORY_VIEW_STORAGE_KEY, JSON.stringify({ historyPageSize, historyColumns, historySearchText, historyModelFilter, historySort })); } catch {}
      }

      function restoreHistoryView() {
        try {
          const view = JSON.parse(localStorage.getItem(HISTORY_VIEW_STORAGE_KEY) || "{}");
          historyPageSize = Number(view.historyPageSize || 24);
          historyColumns = Number(view.historyColumns || 4);
          historySearchText = view.historySearchText || "";
          historyModelFilter = view.historyModelFilter || "";
          historySort = view.historySort || "newest";
          $("historyPageSize").value = String(historyPageSize);
          $("historyColumns").value = String(historyColumns);
          $("historySearch").value = historySearchText;
          $("historySort").value = historySort;
          applyHistoryColumns();
        } catch {}
      }

      function applyHistoryColumns() {
        resultsEl.style.setProperty("--history-columns", String(Math.max(1, Math.min(12, historyColumns))));
      }

      function openImageLightbox(record) {
        activeLightboxRecord = normalizeImageRecord(record);
        delete $("lightboxImage").dataset.cacheFallbackApplied;
        $("lightboxImage").src = cachedImageUrl(activeLightboxRecord.displayUrl);
        if (activeLightboxRecord.local) delete $("lightboxImage").dataset.originalSrc;
        else $("lightboxImage").dataset.originalSrc = activeLightboxRecord.originalUrl;
        wireCachedImageFallbacks($("imageLightbox"));
        $("downloadLightboxImage").href = activeLightboxRecord.downloadUrl;
        $("imageLightbox").showModal();
      }

      function closeImageLightbox() {
        const lightbox = $("imageLightbox");
        if (lightbox.open) lightbox.close();
        else resetImageLightbox();
      }

      function resetImageLightbox() {
        activeLightboxRecord = null;
        const image = $("lightboxImage");
        image.removeAttribute("src");
        delete image.dataset.originalSrc;
        delete image.dataset.cacheFallbackApplied;
        delete image.dataset.cacheFallbackReady;
        $("downloadLightboxImage").removeAttribute("href");
      }

      function resultCardMarkup(record) {
        const normalized = normalizeImageRecord(record);
        const deleteButton = normalized.local && normalized.id ? deleteResultButtonMarkup() : "";
        return `${deleteButton}<img ${cachedImageAttrs(normalized.displayUrl, { fallback: !normalized.local })} alt="生成结果" loading="lazy" decoding="async" fetchpriority="low" width="320" height="240" /><div class="card-foot">${resultActionMarkup(normalized, "card")}</div>`;
      }

      const DOWNLOAD_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
      const DELETE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v5"/><path d="M14 11v5"/></svg>';

      function deleteResultButtonMarkup() {
        return `<button class="delete-result-image card-delete-button" type="button" title="从历史移除" aria-label="从历史移除">${DELETE_SVG}</button>`;
      }

      function resultActionMarkup(record, mode = "card") {
        const normalized = normalizeImageRecord(record);
        const large = mode === "large";
        const buttonClass = "icon-action";
        const copyButton = large ? `<button class="copy-image-url ${buttonClass}" type="button" title="复制图片链接" aria-label="复制图片链接">⧉</button>` : "";
        const deleteButton = large && normalized.local && normalized.id ? `<button class="delete-result-image ${buttonClass} action-delete" type="button" title="从历史移除" aria-label="从历史移除">×</button>` : "";
        return `<span class="card-actions${large ? " result-action-row" : ""}">
          <button class="use-as-reference ${buttonClass} action-reference" type="button" title="添加为参考图" aria-label="添加为参考图">+</button>
          <button class="use-as-main ${buttonClass}" type="button" title="设为主图" aria-label="设为主图">★</button>
          <button class="rerun-record ${buttonClass} action-rerun" type="button" title="再次生成" aria-label="再次生成">▶</button>
          <button class="load-history-params ${buttonClass}" type="button" title="加载参数" aria-label="加载参数">↺</button>
          ${copyButton}
          ${deleteButton}
          <a class="open-original-image download-icon" href="${escapeAttribute(normalized.downloadUrl)}" download target="_blank" rel="noopener" aria-label="下载原图" title="下载原图">${DOWNLOAD_SVG}</a>
        </span>`;
      }

      function wireResultActions(root, record) {
        const bind = (selector, handler) => {
          root.querySelectorAll(selector).forEach((node) => node.addEventListener("click", (event) => { event.stopPropagation(); handler(event); }));
        };
        bind(".load-history-params", () => loadHistoryParams(record));
        bind(".copy-image-url", () => copyImageUrl(normalizeImageRecord(record).downloadUrl));
        bind(".rerun-record", () => rerunRecord(record));
        bind(".use-as-main", () => useResultAsInput(record, "main"));
        bind(".use-as-reference", () => useResultAsInput(record, "reference"));
        bind(".delete-result-image", () => requestDeleteHistoryImage(record));
        bind(".open-original-image", () => {});
      }

      function requestDeleteHistoryImage(record) {
        const normalized = normalizeImageRecord(record);
        if (!normalized.local || !normalized.id) {
          toast("这张图片没有本地数据库记录，无法移除。");
          return;
        }
        pendingDeleteImageRecord = normalized;
        $("deleteImageDialog").showModal();
      }

      async function confirmDeleteHistoryImage() {
        const record = pendingDeleteImageRecord;
        if (!record?.id) return;
        const button = $("confirmDeleteImage");
        button.disabled = true;
        try {
          await apiRequest(`/api/images/${record.id}`, { method: "DELETE" });
          removeDeletedImageFromState(record);
          $("deleteImageDialog").close();
          toast("已从历史画廊移除。");
          log(`已移除历史图片 ${record.id.slice(0, 8)}。`, "warn");
        } catch (error) {
          toast(error.message);
          log(`移除历史图片失败：${error.message}`, "error");
        } finally {
          button.disabled = false;
        }
      }

      function removeDeletedImageFromState(record) {
        const matches = (item) => item.id === record.id || item.displayUrl === record.displayUrl || item.url === record.url;
        historyRecords = historyRecords.filter((item) => !matches(item));
        historyImageUrls = new Set(historyRecords.map((item) => item.id || item.displayUrl || item.url || item.originalUrl));
        submittedTasks.forEach((task) => {
          task.imageRecords = (task.imageRecords || []).filter((item) => !matches(item));
          task.images = (task.images || []).filter((url) => url !== record.displayUrl && url !== record.url);
          if (activeTaskPreviewImageUrl === record.displayUrl || activeTaskPreviewImageUrl === record.url) activeTaskPreviewImageUrl = task.images[0] || "";
        });
        if (activeLightboxRecord?.id === record.id) closeImageLightbox();
        renderHistoryFilters();
        renderHistoryPage();
        renderTaskQueue();
        renderTaskLargePreview();
      }

      async function copyImageUrl(url) {
        try {
          await navigator.clipboard.writeText(url);
          toast("已复制图片链接。");
        } catch {
          toast("复制失败，请在打开原图后手动复制地址。");
        }
      }

      async function rerunRecord(record) {
        const params = record.params || {};
        const prompt = record.prompt || $("prompt").value.trim();
        const model = record.model || modelEl.value;
        if (!prompt) { toast("该记录缺少提示词，请先加载参数后补充提示词。"); return; }
        try {
          const task = await submitTaskWithParams({ prompt, model, params: { ...params }, mainImageUrl: params.urls?.[0] || "", referenceUrls: params.urls?.slice(1) || [] });
          activeJobId = task.id;
          activeTaskPreviewImageUrl = "";
          renderTaskLargePreview();
          toast("已按该记录再次生成。");
        } catch (error) {
          toast(error.message);
          log(`再次生成失败：${error.message}`, "error");
        }
      }

      function useResultAsInput(record, role) {
        const normalized = normalizeImageRecord(record);
        addImagesToRole(role, [normalized.sourceUrl || normalized.displayUrl]);
        toast(role === "main" ? "已设为主图。" : "已添加为参考图。");
      }

      function taskRecord(task, url = task.images?.[0] || "") {
        const record = task.imageRecords?.find((item) => item.displayUrl === url || item.url === url) || {};
        return normalizeImageRecord({ ...record, url, label: `${task.model} ${task.id.slice(0, 8)}`, prompt: task.prompt, model: task.model, params: task.params, jobId: task.id });
      }

      function emptyTaskQueueMarkup() { return '<div class="task-queue-empty">提交任务后在这里查看进度和结果。</div>'; }
      function emptyHistoryMarkup() { return '<div class="history-empty">还没有历史结果。</div>'; }

      function statusLabel(status) {
        return { queued: "排队", running: "生成中", succeeded: "完成", failed: "失败", cancelled: "已取消" }[status] || status;
      }

      function queueHint(task) {
        if (task.status === "queued") {
          const ahead = Number(task.queueAhead || 0);
          const wait = task.estimatedWaitSeconds ? ` · 约 ${formatDuration(task.estimatedWaitSeconds)}` : "";
          return ahead ? `前方 ${ahead} 个${wait}` : `即将开始${wait}`;
        }
        if (task.status === "running") return `已运行 ${formatDuration(task.elapsedSeconds || 0)}`;
        if (TERMINAL_STATUSES.includes(task.status) && task.elapsedSeconds) return `耗时 ${formatDuration(task.elapsedSeconds)}`;
        return task.mainImageUrl ? "图1 已用" : "等待输入";
      }

      function formatDuration(seconds) {
        const value = Math.max(0, Number(seconds || 0));
        if (value < 60) return `${Math.round(value)} 秒`;
        const minutes = Math.floor(value / 60);
        const rest = Math.round(value % 60);
        if (minutes < 60) return rest ? `${minutes}分${rest}秒` : `${minutes} 分钟`;
        const hours = Math.floor(minutes / 60);
        return `${hours}小时${minutes % 60}分`;
      }

      function formatLocalTime(value) {
        if (!value) return "";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
      }
