      /* ---- Task submission ---- */
      async function runSingleTask() {
        const promptText = $("prompt").value.trim();
        if (!promptText) { toast("请输入提示词。"); return; }

        setSubmitButtonsDisabled(true);
        $("submitHint").textContent = "提交中...";

        try {
          const taskModel = modelEl.value;
          const schema = modelSchemas[taskModel] || {};
          const maxUrls = Number(schema.maxUrls || 0);
          const mainImageUrl = getNextMainImageUrl();
          const referenceUrls = mainImageUrl ? selectedReferenceUrls.slice() : [];
          const totalInputImages = (mainImageUrl ? 1 : 0) + referenceUrls.length;
          if (maxUrls && totalInputImages > Math.min(maxUrls, MAX_INPUT_IMAGES)) throw new Error(`本次提交图片不能超过 ${Math.min(maxUrls, MAX_INPUT_IMAGES)} 张。`);
          const params = buildParams(mainImageUrl, referenceUrls);
          const task = await submitTaskWithParams({ prompt: promptText, model: taskModel, params, mainImageUrl, referenceUrls });
          if (mainImageUrl) advanceMainImageRotation();
          saveConfig();
          activeJobId = task.id;
          activeTaskPreviewImageUrl = "";
          renderTaskLargePreview();
        } catch (error) {
          toast(error.message);
          log(`错误：${error.message}`, "error");
        } finally {
          setSubmitButtonsDisabled(false);
          updateSubmitHint();
        }
      }

      async function runBatchTasks() {
        const promptText = $("prompt").value.trim();
        if (!promptText) { toast("请输入提示词。"); return; }

        const taskModel = modelEl.value;
        const schema = modelSchemas[taskModel] || {};
        const maxUrls = Number(schema.maxUrls || 0);
        if (!maxUrls) { toast("当前模型不支持主图/参考图输入，请切换支持图片输入的模型。"); return; }

        const mainUrls = getMainImageBatchOrder();
        if (!mainUrls.length) { toast("请先在左侧主图区域上传或选择 Image 1。"); return; }

        const referenceUrls = selectedReferenceUrls.slice();
        const paramsSnapshot = collectParamsSnapshot(taskModel, schema, referenceUrls);
        const totalInputImages = 1 + referenceUrls.length;
        if (totalInputImages > Math.min(maxUrls, MAX_INPUT_IMAGES)) { toast(`本次提交图片不能超过 ${Math.min(maxUrls, MAX_INPUT_IMAGES)} 张。`); return; }

        setSubmitButtonsDisabled(true);
        const batchButton = $("batchSubmitJobs");
        const originalBatchText = batchButton.textContent;
        let succeeded = 0;
        const failures = [];

        try {
          for (let index = 0; index < mainUrls.length; index += 1) {
            const mainImageUrl = mainUrls[index];
            batchButton.textContent = `提交中 ${index + 1}/${mainUrls.length}`;
            $("submitHint").textContent = `批次提交中 ${index + 1}/${mainUrls.length}...`;
            try {
              const params = buildParamsFromSnapshot(paramsSnapshot, mainImageUrl);
              const task = await submitTaskWithParams({ prompt: promptText, model: taskModel, params, mainImageUrl, referenceUrls });
              activeJobId = task.id;
              activeTaskPreviewImageUrl = "";
              succeeded += 1;
            } catch (error) {
              failures.push({ index: index + 1, error });
              log(`批次第 ${index + 1} 张提交失败：${error.message}`, "error");
            }
          }
          renderTaskLargePreview();
          const message = failures.length ? `批次提交完成：成功 ${succeeded}，失败 ${failures.length}。` : `批次提交完成：成功 ${succeeded} 个任务。`;
          toast(message);
          log(message, failures.length ? "warn" : "success");
        } finally {
          batchButton.textContent = originalBatchText;
          setSubmitButtonsDisabled(false);
          refreshImageSelectors();
          saveConfig();
        }
      }

      async function submitTaskWithParams({ prompt, model, params, mainImageUrl = "", referenceUrls = [], retryMeta = null }) {
        log(`提交任务：模型 ${model}，prompt "${prompt.slice(0, 40)}..."`, "info");
        const response = await apiRequest("/api/jobs", {
          method: "POST",
          body: { model, prompts: [prompt], params },
        });
        const job = response.data;
        const task = normalizeTaskFromJob(job, {
          prompt,
          model,
          params: { ...params },
          inputUrls: params.urls || [],
          mainImageUrl: mainImageUrl || params.urls?.[0] || "",
          referenceUrls: referenceUrls.length ? referenceUrls.slice() : (params.urls || []).slice(1),
          createdAt: new Date().toLocaleTimeString(),
          retryRootId: retryMeta?.retryRootId || job.id,
          retryAttempt: Number(retryMeta?.retryAttempt || 0),
          autoRetryEnabled: retryMeta?.autoRetryEnabled ?? autoRetryEnabled,
          autoRetryMaxAttempts: Number(retryMeta?.autoRetryMaxAttempts ?? autoRetryMaxAttempts),
          autoRetrySubmitted: false,
        });
        upsertSubmittedTask(task);
        activeJobId = job.id;
        renderTaskQueue();
        pollJob(job.id);
        return task;
      }

      function buildParams(mainImageUrl, referenceUrls = selectedReferenceUrls) {
        return buildParamsFromSnapshot(collectParamsSnapshot(modelEl.value, modelSchemas[modelEl.value] || {}, referenceUrls), mainImageUrl);
      }

      function collectParamsSnapshot(model, schema, referenceUrls = selectedReferenceUrls) {
        const params = {};
        if (schema.aspectRatios?.length) params.aspect_ratio = $("aspectRatio").value;
        if (schema.resolutions?.length) params.resolution = $("resolution").value;
        if (schema.qualities?.length) params.quality = $("quality").value;
        if (schema.variants?.length) {
          params.variants = Number($("variants").value);
          if (params.variants === 4 && (!schema.provider || schema.provider === "bizyair")) params.provider = "KieAI";
        }
        if (schema.outputFormats?.length) {
          params.output_format = $("outputFormat").value;
          if (params.output_format && params.output_format !== "png") params.output_compression = Number($("outputCompression").value);
        }
        if (schema.moderations?.length) params.moderation = $("moderation").value;
        if (supportsSeed(model, schema)) params.seed = Number($("seed").value);
        if (model.startsWith("gemini")) {
          params.temperature = Number($("temperature").value);
          params.top_p = Number($("topP").value);
          params.max_tokens = Number($("maxTokens").value);
        }
        return { params, referenceUrls: referenceUrls.slice(), maxUrls: Number(schema.maxUrls || 0) };
      }

      function buildParamsFromSnapshot(snapshot, mainImageUrl) {
        const params = { ...snapshot.params };
        if (mainImageUrl && snapshot.maxUrls > 0) params.urls = [mainImageUrl, ...snapshot.referenceUrls].slice(0, Math.min(snapshot.maxUrls, MAX_INPUT_IMAGES));
        return params;
      }

      function getMainImageBatchOrder() {
        if (!selectedMainImageUrls.length) return [];
        const start = normalizeRotationIndex(nextMainImageIndex, selectedMainImageUrls.length);
        return [...selectedMainImageUrls.slice(start), ...selectedMainImageUrls.slice(0, start)];
      }

      function setSubmitButtonsDisabled(disabled) {
        $("submitJobs").disabled = disabled;
        $("batchSubmitJobs").disabled = disabled;
      }

      function getNextMainImageUrl() {
        if (!selectedMainImageUrls.length) return "";
        nextMainImageIndex = normalizeRotationIndex(nextMainImageIndex, selectedMainImageUrls.length);
        return selectedMainImageUrls[nextMainImageIndex];
      }

      function advanceMainImageRotation() {
        if (!selectedMainImageUrls.length) {
          nextMainImageIndex = 0;
          return;
        }
        nextMainImageIndex = (nextMainImageIndex + 1) % selectedMainImageUrls.length;
        refreshImageSelectors();
        saveConfig();
      }

      /* ---- Polling ---- */
      async function stopActiveJob() {
        if (!activeJobId) { toast("没有正在执行的任务。"); return; }
        await cancelTask(activeJobId);
      }

      async function cancelTask(jobId) {
        try {
          const response = await apiRequest(`/api/jobs/${jobId}/cancel`, { method: "POST" });
          updateTaskFromJob(response.data);
          pollingJobs.delete(jobId);
          if (activeJobId === jobId) activeJobId = "";
          toast("已请求取消。");
          log(`已取消任务 ${jobId.slice(0, 8)}。`, "warn");
        } catch (error) {
          toast(error.message);
          log(`取消失败：${error.message}`, "error");
        }
      }

      async function pollJob(jobId) {
        if (pollingJobs.has(jobId)) return;
        pollingJobs.add(jobId);
        let failures = 0;
        while (pollingJobs.has(jobId)) {
          await delay(pollInterval);
          if (!pollingJobs.has(jobId)) return;
          try {
            const response = await apiRequest(`/api/jobs/${jobId}`);
            const job = response.data;
            failures = 0;
            const changed = updateTaskFromJob(job);
            if (changed) log(`任务 ${job.id.slice(0, 8)}：${statusLabel(job.status)} ${job.completed}/${job.total}。`, job.status === "failed" ? "error" : "info");
            if (TERMINAL_STATUSES.includes(job.status)) {
              pollingJobs.delete(jobId);
              log(`任务 ${job.id.slice(0, 8)} 已结束：${job.status}。`, job.status === "succeeded" ? "success" : "warn");
              if (job.status === "failed") await maybeAutoRetryTask(job.id);
              return;
            }
          } catch (error) {
            failures += 1;
            log(`轮询失败：${error.message}`, "error");
            if (failures >= 5) {
              pollingJobs.delete(jobId);
              const task = submittedTasks.find((t) => t.id === jobId);
              if (task) {
                task.status = "failed";
                task.error = "连续轮询失败，请检查本地网关或稍后重试。";
                renderTaskQueue();
              }
              return;
            }
          }
        }
      }

      function updateTaskFromJob(job) {
        const existing = submittedTasks.find((t) => t.id === job.id);
        const previousSnapshot = existing ? JSON.stringify({ status: existing.status, completed: existing.completed, total: existing.total, images: existing.images, error: existing.error, queueAhead: existing.queueAhead }) : "";
        const task = normalizeTaskFromJob(job, existing || {});
        upsertSubmittedTask(task);
        const changed = JSON.stringify({ status: task.status, completed: task.completed, total: task.total, images: task.images, error: task.error, queueAhead: task.queueAhead }) !== previousSnapshot;
        if (activeJobId === job.id && !activeTaskPreviewImageUrl && task.images.length) activeTaskPreviewImageUrl = task.images[0];
        if (changed) {
          renderTaskQueue();
          renderTaskLargePreview();
        }
        appendToHistory(job);
        return changed;
      }

      function normalizeTaskFromJob(job, existing = {}) {
        const records = getJobImageRecords(job);
        const params = existing.params || job.params || records[0]?.params || {};
        const urls = Array.isArray(params.urls) ? params.urls : existing.inputUrls || [];
        const prompt = existing.prompt || records[0]?.prompt || job.items?.[0]?.prompt || "";
        return {
          id: job.id,
          prompt,
          model: existing.model || job.model || records[0]?.model || "",
          params: { ...params },
          inputUrls: [...urls],
          mainImageUrl: existing.mainImageUrl || urls[0] || "",
          referenceUrls: existing.referenceUrls?.length ? existing.referenceUrls.slice() : urls.slice(1),
          status: job.status || existing.status || "queued",
          images: records.length ? records.map((record) => record.displayUrl || record.url) : existing.images || [],
          imageRecords: records,
          completed: job.completed || 0,
          total: job.total || 1,
          error: getJobError(job) || existing.error || "",
          createdAt: existing.createdAt || formatLocalTime(job.created_at) || new Date().toLocaleTimeString(),
          createdAtIso: job.created_at || existing.createdAtIso || "",
          firstStartedAt: job.first_started_at || existing.firstStartedAt || "",
          lastFinishedAt: job.last_finished_at || existing.lastFinishedAt || "",
          queueAhead: Number(job.queue_ahead || 0),
          queuePosition: job.queue_position || null,
          estimatedWaitSeconds: job.estimated_wait_seconds,
          elapsedSeconds: job.elapsed_seconds,
          queuedCount: job.queued_count || 0,
          runningCount: job.running_count || 0,
          retryRootId: existing.retryRootId || job.retryRootId || job.id,
          retryAttempt: Number(existing.retryAttempt || job.retryAttempt || 0),
          autoRetryEnabled: existing.autoRetryEnabled ?? job.autoRetryEnabled ?? autoRetryEnabled,
          autoRetryMaxAttempts: Number(existing.autoRetryMaxAttempts ?? job.autoRetryMaxAttempts ?? autoRetryMaxAttempts),
          autoRetrySubmitted: Boolean(existing.autoRetrySubmitted || job.autoRetrySubmitted),
        };
      }

      function upsertSubmittedTask(task) {
        const index = submittedTasks.findIndex((item) => item.id === task.id);
        if (index >= 0) submittedTasks[index] = task;
        else submittedTasks.unshift(task);
      }

      function restoreActiveJobs(jobs) {
        (jobs || []).filter((job) => !TERMINAL_STATUSES.includes(job.status)).forEach((job) => {
          const task = normalizeTaskFromJob(job);
          upsertSubmittedTask(task);
          pollJob(task.id);
        });
        renderTaskQueue();
      }

      function getJobError(job) {
        const item = (job.items || []).find((current) => current.error);
        return item?.error || "";
      }

      /* ---- Task queue rendering ---- */
      function renderTaskQueue() {
        const list = $("taskList");
        const queued = submittedTasks.filter((task) => task.status === "queued").length;
        const running = submittedTasks.filter((task) => task.status === "running").length;
        const failedTasks = getRetryableFailedTasks();
        $("taskCount").textContent = `${submittedTasks.length} 个任务 · 排队 ${queued} · 生成中 ${running}`;
        $("retryFailedTasks").hidden = failedTasks.length === 0;
        $("retryFailedTasks").disabled = retryingAllFailedTasks || failedTasks.every((task) => retryingTaskIds.has(task.id));
        $("retryFailedTasks").textContent = retryingAllFailedTasks || failedTasks.some((task) => retryingTaskIds.has(task.id)) ? "重试中" : `重试失败 ${failedTasks.length}`;
        $("taskQueueSection").classList.toggle("is-collapsed", taskQueueCollapsed);
        $("taskQueueSection").closest(".sidebar")?.classList.toggle("is-task-queue-collapsed", taskQueueCollapsed);
        $("toggleTaskQueue").textContent = taskQueueCollapsed ? "展开" : "折叠";

        if (!submittedTasks.length) {
          list.innerHTML = emptyTaskQueueMarkup();
          renderTaskLargePreview();
          return;
        }

        list.innerHTML = submittedTasks.map(taskCardMarkup).join("");
        wireCachedImageFallbacks(list);
        list.querySelectorAll(".task-item").forEach((el) => {
          el.addEventListener("click", () => {
            const task = submittedTasks.find((item) => item.id === el.dataset.taskId);
            if (task?.id) {
              activeJobId = task.id;
              activeTaskPreviewImageUrl = task.images[0] || "";
            }
            renderTaskQueue();
            renderTaskLargePreview();
          });
        });
        list.querySelectorAll(".task-retry").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            retryTask(button.dataset.taskId);
          });
        });
        list.querySelectorAll(".task-load-params").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            const task = submittedTasks.find((item) => item.id === button.dataset.taskId);
            if (task) loadHistoryParams(taskRecord(task));
          });
        });
      }

      function toggleTaskQueue() {
        taskQueueCollapsed = !taskQueueCollapsed;
        renderTaskQueue();
      }

      function getRetryableFailedTasks() {
        return submittedTasks.filter((task) => task.status === "failed");
      }

      async function retryAllFailedTasks() {
        if (retryingAllFailedTasks) return;
        const tasks = getRetryableFailedTasks().filter((task) => !retryingTaskIds.has(task.id));
        if (!tasks.length) { toast("没有可重试的失败任务。"); return; }
        retryingAllFailedTasks = true;
        renderTaskQueue();
        log(`开始批量重试 ${tasks.length} 个失败任务。`, "warn");
        let succeeded = 0;
        try {
          for (const task of tasks) {
            const retry = await retryTask(task.id, { silent: true });
            if (retry) succeeded += 1;
          }
          const failed = tasks.length - succeeded;
          const message = failed ? `批量重试完成：成功 ${succeeded}，失败 ${failed}。` : `已批量重试 ${succeeded} 个失败任务。`;
          toast(message);
          log(message, failed ? "warn" : "success");
        } finally {
          retryingAllFailedTasks = false;
          renderTaskQueue();
        }
      }

      function taskCardMarkup(task) {
        const promptShort = task.prompt.length > 58 ? task.prompt.slice(0, 58) + "..." : task.prompt;
        const status = statusLabel(task.status);
        const progress = `${task.completed || 0}/${task.total || 1}`;
        const isActive = task.id === activeJobId;
        const retry = task.status === "failed" ? `<button class="task-retry" type="button" data-task-id="${escapeAttribute(task.id)}"${retryingTaskIds.has(task.id) ? " disabled" : ""}>${retryingTaskIds.has(task.id) ? "提交中" : "重试此任务"}</button><button class="task-load-params" type="button" data-task-id="${escapeAttribute(task.id)}">加载参数</button>` : "";
        const error = task.error ? `<div class="task-item-error" title="${escapeAttribute(task.error)}">${escapeHtml(task.error)}</div>` : "";
        return `<article class="task-item${isActive ? " is-active" : ""}" data-task-id="${escapeAttribute(task.id)}">
          <span class="task-item-status ${escapeAttribute(task.status)}">${status}</span>
          ${taskPreviewMarkup(task)}
          <div class="task-item-body">
            <div class="task-item-prompt">${escapeHtml(promptShort)}</div>
            <div class="task-item-meta"><span class="model-tag">${escapeHtml(modelDisplayName(task.model))}</span><span>${escapeHtml(progress)}</span></div>
            <div class="task-item-meta"><span>${escapeHtml(queueHint(task))}</span><span>参考 ${task.referenceUrls?.length || 0} 张</span></div>
            ${error}
            <div class="task-item-footer"><span class="task-created-at">${escapeHtml(task.createdAt)}</span><span class="task-action-row">${retry || escapeHtml(task.id.slice(0, 6))}</span></div>
          </div>
        </article>`;
      }

      async function retryTask(taskId, options = {}) {
        const automatic = Boolean(options.automatic);
        const silent = Boolean(options.silent);
        const task = submittedTasks.find((item) => item.id === taskId);
        if (!task || retryingTaskIds.has(taskId)) return null;
        retryingTaskIds.add(taskId);
        renderTaskQueue();
        try {
          const retryAttempt = automatic ? Number(task.retryAttempt || 0) + 1 : 0;
          const response = await apiRequest(`/api/jobs/${taskId}/retry`, { method: "POST" });
          const retry = normalizeTaskFromJob(response.data, {
            ...task,
            status: "queued",
            images: [],
            imageRecords: [],
            completed: 0,
            error: "",
            retryAttempt,
            autoRetrySubmitted: false,
          });
          upsertSubmittedTask(retry);
          activeJobId = retry.id;
          activeTaskPreviewImageUrl = "";
          renderTaskQueue();
          renderTaskLargePreview();
          pollJob(retry.id);
          if (automatic) log(`任务 ${task.id.slice(0, 8)} 自动重试 ${retryAttempt}/${task.autoRetryMaxAttempts}。`, "warn");
          else if (!silent) toast("已在原队列中重新提交失败任务。");
          return retry;
        } catch (error) {
          if (!automatic && !silent) toast(error.message);
          log(`${automatic ? "自动重试" : "重试"}失败：${error.message}`, "error");
          return null;
        } finally {
          retryingTaskIds.delete(taskId);
          renderTaskQueue();
        }
      }

      async function maybeAutoRetryTask(taskId) {
        const task = submittedTasks.find((item) => item.id === taskId);
        if (!task || task.status !== "failed" || !task.autoRetryEnabled || task.autoRetrySubmitted || autoRetryingTaskIds.has(taskId)) return;
        const maxAttempts = Math.max(0, Number(task.autoRetryMaxAttempts || 0));
        const nextAttempt = Number(task.retryAttempt || 0) + 1;
        if (nextAttempt > maxAttempts) {
          log(`任务 ${task.id.slice(0, 8)} 自动重试次数已用尽。`, "warn");
          return;
        }
        task.autoRetrySubmitted = true;
        autoRetryingTaskIds.add(taskId);
        try {
          await retryTask(taskId, { automatic: true });
        } finally {
          autoRetryingTaskIds.delete(taskId);
          renderTaskQueue();
        }
      }

      function taskPreviewMarkup(task) {
        if (task.images.length === 1) return `<div class="task-preview"><img ${cachedImageAttrs(task.images[0], { fallback: !taskRecord(task, task.images[0]).local })} alt="任务结果" loading="lazy" /></div>`;
        if (task.images.length > 1) {
          const images = task.images.slice(0, 4).map((url) => `<img ${cachedImageAttrs(url, { fallback: !taskRecord(task, url).local })} alt="任务结果" loading="lazy" />`).join("");
          return `<div class="task-preview task-preview-grid">${images}</div>`;
        }
        const animation = task.status === "running" ? '<div class="mini-loader"></div><span>生成中</span>' : task.status === "queued" ? '<div class="pulse-dot"></div><span>等待中</span>' : `<span>${escapeHtml(statusLabel(task.status))}</span>`;
        return `<div class="task-preview"><div class="task-waiting">${animation}</div></div>`;
      }

      function renderTaskLargePreview() {
        const box = $("taskLargePreview");
        const task = submittedTasks.find((item) => item.id === activeJobId);
        if (!task) {
          box.innerHTML = '<div class="task-large-empty">点击任务队列预览生成大图。</div>';
          return;
        }
        if (!task.images.length) {
          const text = task.status === "running" ? `任务生成中，${queueHint(task)}。完成后会自动显示大图。` : task.status === "queued" ? `任务排队中，${queueHint(task)}。` : `任务状态：${statusLabel(task.status)}${task.error ? `：${task.error}` : ""}`;
          const retry = task.status === "failed" ? `<button class="task-retry secondary" type="button" data-task-id="${escapeAttribute(task.id)}"${retryingTaskIds.has(task.id) ? " disabled" : ""}>${retryingTaskIds.has(task.id) ? "提交中" : "重新生成"}</button>` : "";
          box.innerHTML = `<div class="task-large-empty"><span>${escapeHtml(text)}</span>${retry}</div>`;
          const retryButton = box.querySelector(".task-retry");
          if (retryButton) retryButton.addEventListener("click", () => retryTask(task.id));
          return;
        }
        const imageUrl = task.images.includes(activeTaskPreviewImageUrl) ? activeTaskPreviewImageUrl : task.images[0];
        activeTaskPreviewImageUrl = imageUrl;
        const record = taskRecord(task, imageUrl);
        const thumbs = task.images.length > 1 ? `<div class="task-large-thumbs">${task.images.map((url) => `<button type="button" class="${url === imageUrl ? "is-active" : ""}" data-url="${escapeAttribute(url)}"><img ${cachedImageAttrs(url, { fallback: !taskRecord(task, url).local })} alt="任务缩略图" loading="lazy" /></button>`).join("")}</div>` : "";
        box.innerHTML = `<div class="task-large-image"><div class="task-large-frame"><img class="task-large-main" ${cachedImageAttrs(record.displayUrl, { fallback: !record.local })} alt="任务大图" />${resultActionMarkup(record, "large")}</div>${thumbs}<div class="task-item-meta"><span>${escapeHtml(modelDisplayName(task.model))}</span><span>${escapeHtml(task.id.slice(0, 8))}</span></div></div>`;
        wireCachedImageFallbacks(box);
        const mainImage = box.querySelector(".task-large-main");
        if (mainImage) mainImage.addEventListener("click", () => openImageLightbox(record));
        wireResultActions(box, record);
        box.querySelectorAll(".task-large-thumbs button").forEach((button) => {
          button.addEventListener("click", () => {
            activeTaskPreviewImageUrl = button.dataset.url;
            renderTaskLargePreview();
          });
        });
      }
