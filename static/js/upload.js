      /* ---- Upload ---- */
      function wireImageBoxDropzone(boxId, inputId, role) {
        const box = $(boxId);
        ["dragenter", "dragover"].forEach((name) => box.addEventListener(name, (event) => { event.preventDefault(); box.classList.add("is-dragging"); }));
        ["dragleave", "drop"].forEach((name) => box.addEventListener(name, (event) => { event.preventDefault(); box.classList.remove("is-dragging"); }));
        box.addEventListener("drop", (event) => {
          const files = Array.from(event.dataTransfer.files || []).filter((file) => file.type.startsWith("image/"));
          if (files.length) uploadFilesToRole(files, role);
        });
      }

      function refreshImageSelectors() {
        sanitizeImageSelections();
        renderImageBoxes();
        updateSubmitHint();
      }

      function openUploadRetryDb() {
        return new Promise((resolve, reject) => {
          if (!window.indexedDB) {
            reject(new Error("浏览器不支持本地上传队列"));
            return;
          }
          const request = indexedDB.open(UPLOAD_RETRY_DB_NAME, UPLOAD_RETRY_DB_VERSION);
          request.onupgradeneeded = () => {
            const db = request.result;
            if (!db.objectStoreNames.contains(UPLOAD_RETRY_STORE)) {
              const store = db.createObjectStore(UPLOAD_RETRY_STORE, { keyPath: "id" });
              store.createIndex("role", "role", { unique: false });
              store.createIndex("createdAt", "createdAt", { unique: false });
            }
          };
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error || new Error("打开本地上传队列失败"));
        });
      }

      function uploadRetryTransaction(mode, callback) {
        return openUploadRetryDb().then((db) => new Promise((resolve, reject) => {
          const transaction = db.transaction(UPLOAD_RETRY_STORE, mode);
          const store = transaction.objectStore(UPLOAD_RETRY_STORE);
          let result;
          transaction.oncomplete = () => { db.close(); resolve(result); };
          transaction.onerror = () => { db.close(); reject(transaction.error || new Error("本地上传队列操作失败")); };
          transaction.onabort = () => { db.close(); reject(transaction.error || new Error("本地上传队列操作已取消")); };
          result = callback(store);
        }));
      }

      function requestToPromise(request) {
        return new Promise((resolve, reject) => {
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error || new Error("本地上传队列请求失败"));
        });
      }

      async function initUploadRetryQueue() {
        try {
          await loadUploadRetryQueue();
        } catch (error) {
          log(`加载本地上传队列失败：${error.message}`, "warn");
        }
      }

      async function loadUploadRetryQueue() {
        revokeQueuedUploadUrls();
        queuedUploadPreviews = { main: [], reference: [] };
        const records = await uploadRetryTransaction("readonly", (store) => requestToPromise(store.getAll()));
        records.sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
        records.forEach((record) => addQueuedUploadPreview(record));
        refreshImageSelectors();
      }

      function addQueuedUploadPreview(record) {
        const role = record.role === "main" ? "main" : "reference";
        queuedUploadPreviews[role].push({
          id: record.id,
          role,
          name: record.name,
          type: record.type,
          size: record.size,
          lastError: record.lastError,
          createdAt: record.createdAt,
          updatedAt: record.updatedAt,
          src: URL.createObjectURL(record.blob),
        });
      }

      function revokeQueuedUploadUrls() {
        Object.values(queuedUploadPreviews).flat().forEach((item) => {
          if (item.src) URL.revokeObjectURL(item.src);
        });
      }

      async function enqueueFailedUpload(file, role, error) {
        const now = Date.now();
        const record = {
          id: `${now}-${Math.random().toString(36).slice(2)}`,
          role,
          name: file.name,
          type: file.type,
          size: file.size,
          lastError: error?.message || String(error || "上传失败"),
          createdAt: now,
          updatedAt: now,
          blob: file,
        };
        await uploadRetryTransaction("readwrite", (store) => store.put(record));
        addQueuedUploadPreview(record);
        log(`${file.name} 已加入本地待重试上传队列。`, "warn");
        return record;
      }

      async function getQueuedUpload(id) {
        return uploadRetryTransaction("readonly", (store) => requestToPromise(store.get(id)));
      }

      async function updateQueuedUploadError(id, error) {
        const record = await getQueuedUpload(id);
        if (!record) return;
        record.lastError = error?.message || String(error || "上传失败");
        record.updatedAt = Date.now();
        await uploadRetryTransaction("readwrite", (store) => store.put(record));
        const item = findQueuedUploadPreview(id);
        if (item) {
          item.lastError = record.lastError;
          item.updatedAt = record.updatedAt;
        }
      }

      async function deleteQueuedUpload(id) {
        await uploadRetryTransaction("readwrite", (store) => store.delete(id));
        removeQueuedUploadPreview(id);
      }

      function findQueuedUploadPreview(id) {
        return [...queuedUploadPreviews.main, ...queuedUploadPreviews.reference].find((item) => item.id === id);
      }

      function removeQueuedUploadPreview(id) {
        ["main", "reference"].forEach((role) => {
          const item = queuedUploadPreviews[role].find((current) => current.id === id);
          if (item?.src) URL.revokeObjectURL(item.src);
          queuedUploadPreviews[role] = queuedUploadPreviews[role].filter((current) => current.id !== id);
        });
      }

      function sanitizeImageSelections() {
        const urls = getInputUrls();
        uploadedImageUrls = [...new Set(uploadedImageUrls.filter((url) => urls.includes(url)) || [])];
        selectedMainImageUrls = [...new Set(selectedMainImageUrls.filter((url) => urls.includes(url)))];
        selectedReferenceUrls = [...new Set(selectedReferenceUrls.filter((url) => urls.includes(url) && !selectedMainImageUrls.includes(url)))];
        selectedReferenceUrls = selectedReferenceUrls.slice(0, getReferenceSlotLimit());
        nextMainImageIndex = normalizeRotationIndex(nextMainImageIndex, selectedMainImageUrls.length);
      }

      function renderImageBoxes() {
        const schema = modelSchemas[modelEl.value] || {};
        const maxUrls = Number(schema.maxUrls || 0);
        const maxReferences = getMaxReferenceUrls(maxUrls);
        const mainSlots = selectedMainImageUrls.length ? 1 : 0;
        const remainingSlots = Math.max(MAX_INPUT_IMAGES - mainSlots, 0);
        const referenceLimit = Math.min(maxReferences, remainingSlots);
        const nextText = selectedMainImageUrls.length ? ` · 下次主图 ${nextMainImageIndex + 1}/${selectedMainImageUrls.length}` : "";
        $("mainPreviewCount").textContent = `${selectedMainImageUrls.length} 张已选${nextText}`;
        $("previewCount").textContent = `${selectedReferenceUrls.length} 张已选 · ${maxUrls ? `最多 ${referenceLimit} 张参考图 · 本次提交 ${mainSlots + selectedReferenceUrls.length}/${MAX_INPUT_IMAGES}` : "当前模型不支持图片输入"}`;
        $("clearMainImages").disabled = !selectedMainImageUrls.length;
        $("clearReferenceImages").disabled = !selectedReferenceUrls.length;
        renderRolePreview("main", selectedMainImageUrls, "mainImagePreview");
        renderRolePreview("reference", selectedReferenceUrls, "referencePreview");
      }

      function renderRolePreview(role, urls, elementId) {
        const preview = $(elementId);
        const emptyText = role === "main" ? "暂无主图。拖入图片或选择历史上传。" : "暂无参考图。拖入图片或选择历史上传。";
        const pending = pendingUploadPreviews[role] || [];
        const queued = queuedUploadPreviews[role] || [];
        const cards = [
          ...pending.map((item) => pendingUploadCardMarkup(item, role)),
          ...queued.map((item) => queuedUploadCardMarkup(item, role)),
          ...urls.map((url, index) => imageBoxCardMarkup(url, role, index)),
        ];
        preview.innerHTML = cards.length ? cards.join("") : `<div class="hint">${emptyText}</div>`;
        wireCachedImageFallbacks(preview);
        preview.querySelectorAll(".remove-image:not(.remove-queued-upload)").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            removeImageFromRole(button.dataset.role, button.dataset.url);
          });
        });
        preview.querySelectorAll(".retry-upload").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            retryQueuedUpload(button.dataset.queueId, button.dataset.role);
          });
        });
        preview.querySelectorAll(".remove-queued-upload").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.stopPropagation();
            removeQueuedUpload(button.dataset.queueId);
          });
        });
        preview.querySelectorAll(".preview-card.has-remove[data-url]").forEach((card) => {
          card.addEventListener("mouseenter", (event) => showImageHoverPreview(card.dataset.url, event, card));
          card.addEventListener("mouseleave", hideImageHoverPreview);
          card.addEventListener("focusin", (event) => showImageHoverPreview(card.dataset.url, event, card));
          card.addEventListener("focusout", hideImageHoverPreview);
        });
      }

      function showImageHoverPreview(url, event, anchor) {
        const token = ++hoverPreviewToken;
        const preview = $("imageHoverPreview");
        const img = $("imageHoverPreviewImg");
        img.onload = null;
        delete img.dataset.cacheFallbackApplied;
        img.src = cachedImageUrl(url);
        img.dataset.originalSrc = url;
        wireCachedImageFallbacks(preview);
        const show = () => {
          if (token !== hoverPreviewToken || !anchor?.isConnected) return;
          positionImageHoverPreview(preview, event, anchor);
          preview.classList.add("is-visible");
        };
        if (img.complete) show();
        else img.onload = show;
      }

      function positionImageHoverPreview(preview, event, anchor) {
        const gap = 4;
        const img = $("imageHoverPreviewImg");
        const width = img.offsetWidth || Math.min(520, window.innerWidth * 0.42);
        const height = img.offsetHeight || Math.min(520, window.innerHeight * 0.7);
        const anchorRect = anchor?.getBoundingClientRect();
        let x = anchorRect ? anchorRect.right + gap : (event?.clientX || gap) + gap;
        let y = anchorRect ? anchorRect.top : (event?.clientY || gap) + gap;
        if (x + width + gap > window.innerWidth && anchorRect) x = anchorRect.left - width - gap;
        if (y + height + gap > window.innerHeight) y = window.innerHeight - height - gap;
        preview.style.left = `${Math.max(gap, x)}px`;
        preview.style.top = `${Math.max(gap, y)}px`;
      }

      function hideImageHoverPreview() {
        hoverPreviewToken += 1;
        const img = $("imageHoverPreviewImg");
        img.onload = null;
        $("imageHoverPreview").classList.remove("is-visible");
      }

      function imageBoxCardMarkup(url, role, index) {
        const next = role === "main" && index === nextMainImageIndex;
        const classes = ["preview-card", role === "main" ? "is-main" : "is-selected"].join(" ");
        const badge = role === "main" ? `<div class="selection-badge main-badge">主${index + 1}</div>` : `<div class="selection-badge">图${index + 2}</div>`;
        const nextBadge = next ? `<div class="rotation-badge">下一个</div>` : "";
        const displayUrl = cachedImageUrl(url);
        return `<div class="${classes} has-remove" data-url="${escapeAttribute(url)}" style="--preview-url: url('${escapeAttribute(displayUrl)}')"><img ${cachedImageAttrs(url)} alt="${role === "main" ? "主图" : "参考图"}" loading="lazy" decoding="async" width="180" height="100" />${nextBadge}${badge}<button class="remove-image" type="button" data-role="${role}" data-url="${escapeAttribute(url)}" aria-label="移除图片"></button><span>${escapeHtml(shortUrl(url))}</span></div>`;
      }

      function pendingUploadCardMarkup(item, role) {
        return `<div class="preview-card is-uploading is-disabled"><img src="${escapeAttribute(item.src)}" alt="${role === "main" ? "主图上传中" : "参考图上传中"}" decoding="async" width="180" height="100" /><div class="uploading-badge">上传中</div><span>${escapeHtml(item.name)}</span></div>`;
      }

      function queuedUploadCardMarkup(item, role) {
        const retrying = retryingQueuedUploadIds.has(item.id);
        const label = retrying ? "重试中" : "待重试";
        return `<div class="preview-card is-upload-failed ${retrying ? "is-uploading" : ""}" data-queue-id="${escapeAttribute(item.id)}"><img src="${escapeAttribute(item.src)}" alt="${role === "main" ? "主图待重试" : "参考图待重试"}" decoding="async" width="180" height="100" /><div class="failed-upload-badge">${label}</div><button class="remove-image remove-queued-upload" type="button" data-queue-id="${escapeAttribute(item.id)}" aria-label="移除待重试图片"></button><button class="retry-upload" type="button" data-role="${role}" data-queue-id="${escapeAttribute(item.id)}" ${retrying ? "disabled" : ""}>重试</button><span title="${escapeAttribute(item.lastError || "上传失败")}">${escapeHtml(item.name)}</span></div>`;
      }

      function addImagesToRole(role, urls, options = {}) {
        const validUrls = urls.filter(Boolean);
        if (!validUrls.length) return;
        appendUrls(validUrls, { refresh: false, save: false });
        uploadedImageUrls = [...new Set([...uploadedImageUrls, ...validUrls])];
        if (role === "main") {
          const candidates = validUrls.filter((url) => !selectedMainImageUrls.includes(url) && !selectedReferenceUrls.includes(url));
          selectedMainImageUrls = [...new Set([...selectedMainImageUrls, ...candidates])];
          selectedReferenceUrls = selectedReferenceUrls.filter((url) => !selectedMainImageUrls.includes(url)).slice(0, getReferenceSlotLimit());
        } else {
          const maxReferences = getReferenceSlotLimit();
          const candidates = validUrls.filter((url) => !selectedMainImageUrls.includes(url));
          const merged = [...new Set([...selectedReferenceUrls, ...candidates])];
          selectedReferenceUrls = merged.slice(0, maxReferences);
          if (candidates.length && selectedReferenceUrls.length < merged.length) toast(`单次提交最多 ${MAX_INPUT_IMAGES} 张。`);
        }
        if (options.refresh !== false) refreshImageSelectors();
        if (options.save !== false) saveConfig();
      }

      function removeImageFromRole(role, url) {
        hideImageHoverPreview();
        if (role === "main") {
          selectedMainImageUrls = selectedMainImageUrls.filter((item) => item !== url);
          nextMainImageIndex = normalizeRotationIndex(nextMainImageIndex, selectedMainImageUrls.length);
        } else {
          selectedReferenceUrls = selectedReferenceUrls.filter((item) => item !== url);
        }
        refreshImageSelectors();
        saveConfig();
      }

      function clearImagesFromRole(role) {
        hideImageHoverPreview();
        if (role === "main") {
          selectedMainImageUrls = [];
          nextMainImageIndex = 0;
        } else {
          selectedReferenceUrls = [];
        }
        refreshImageSelectors();
        saveConfig();
        toast(role === "main" ? "已清空主图选择。" : "已清空参考图选择。");
      }

      async function uploadFilesToRole(fileList, role) {
        const imageFiles = Array.from(fileList || []).filter((file) => file.type.startsWith("image/"));
        if (!imageFiles.length) { toast("请拖入图片文件。"); return; }
        const files = imageFiles.filter((file) => {
          if (file.size <= maxUploadBytes) return true;
          log(`${file.name} 超过 ${Math.floor(maxUploadBytes / 1024 / 1024)}MB，已跳过。`, "warn");
          return false;
        });
        if (!files.length) { toast(`图片不能超过 ${Math.floor(maxUploadBytes / 1024 / 1024)}MB。`); return; }
        const box = role === "main" ? $("mainImageBox") : $("referenceImageBox");
        const pending = files.map((file) => ({ id: `${Date.now()}-${Math.random()}`, name: file.name, src: URL.createObjectURL(file) }));
        pendingUploadPreviews[role].push(...pending);
        refreshImageSelectors();
        box.classList.add("is-uploading");
        try {
          files.forEach((file) => log(`开始上传 ${file.name}。`, "info"));
          const uploadResults = await Promise.all(files.map(async (file) => {
            try {
              const data = await uploadFile(file);
              const url = extractInputUrl(data);
              log(`上传完成 ${file.name}${url ? `：${url}` : ""}`, url ? "success" : "warn");
              return { file, url };
            } catch (error) {
              log(`${file.name} 上传失败：${error.message}`, "error");
              return { file, error };
            }
          }));
          const uploadedUrls = uploadResults.map((result) => result.url).filter(Boolean);
          const failedResults = uploadResults.filter((result) => result.error || !result.url);
          const queuedResults = await Promise.all(failedResults.map(async (result) => {
            const error = result.error || new Error("上传成功但未能解析出图片 URL");
            try {
              await enqueueFailedUpload(result.file, role, error);
              return result;
            } catch (queueError) {
              log(`${result.file.name} 保存到本地待重试队列失败：${queueError.message}`, "error");
              return null;
            }
          }));
          const queuedCount = queuedResults.filter(Boolean).length;
          await preloadImages(uploadedUrls);
          pendingUploadPreviews[role] = pendingUploadPreviews[role].filter((item) => !pending.some((current) => current.id === item.id));
          addImagesToRole(role, uploadedUrls, { refresh: false, save: false });
          refreshImageSelectors();
          pending.forEach((item) => URL.revokeObjectURL(item.src));
          saveConfig();
          await refreshInputsList(false, { refresh: false, save: true });
          if (uploadedUrls.length && failedResults.length) toast(`${role === "main" ? "主图" : "参考图"}部分上传成功：成功 ${uploadedUrls.length}，失败 ${failedResults.length}${queuedCount ? "，失败文件已加入本地待重试队列" : ""}。`);
          else if (uploadedUrls.length) toast(`${role === "main" ? "主图" : "参考图"}上传完成。`);
          else if (queuedCount) toast("上传失败，文件已加入本地待重试队列。");
          else toast("上传失败，且本地待重试保存失败，请查看日志。");
        } catch (error) {
          pendingUploadPreviews[role] = pendingUploadPreviews[role].filter((item) => !pending.some((current) => current.id === item.id));
          pending.forEach((item) => URL.revokeObjectURL(item.src));
          refreshImageSelectors();
          toast(error.message);
          log(`上传失败：${error.message}`, "error");
        } finally {
          box.classList.remove("is-uploading");
          const input = role === "main" ? $("mainImageFiles") : $("referenceImageFiles");
          input.value = "";
        }
      }

      function getInputUrls() {
        return $("urls").value.split(/\n+/).map((u) => u.trim()).filter((u) => /^https?:\/\//.test(u));
      }

      function getUploadedInputUrls(inputUrls = getInputUrls()) {
        return uploadedImageUrls.filter((url) => inputUrls.includes(url));
      }

      function getMaxReferenceUrls(maxUrls = Number((modelSchemas[modelEl.value] || {}).maxUrls || 0)) {
        if (!maxUrls) return 0;
        return Math.max(maxUrls - 1, 0);
      }

      function getReferenceSlotLimit(maxUrls = Number((modelSchemas[modelEl.value] || {}).maxUrls || 0)) {
        const mainSlots = selectedMainImageUrls.length ? 1 : 0;
        const remainingSlots = Math.max(MAX_INPUT_IMAGES - mainSlots, 0);
        return Math.min(getMaxReferenceUrls(maxUrls), remainingSlots);
      }

      function normalizeRotationIndex(index, length) {
        if (!length) return 0;
        const number = Number(index) || 0;
        return ((number % length) + length) % length;
      }

      function updateSubmitHint() {
        const schema = modelSchemas[modelEl.value] || {};
        const maxUrls = Number(schema.maxUrls || 0);
        const submissionInputCount = (selectedMainImageUrls.length ? 1 : 0) + selectedReferenceUrls.length;
        const parts = [`模型 ${modelDisplayName(modelEl.value) || "--"}`];
        if (schema.aspectRatios?.length) parts.push(`比例 ${$("aspectRatio").value || "--"}`);
        if (schema.resolutions?.length) parts.push(`分辨率 ${$("resolution").value || "--"}`);
        if (schema.qualities?.length) parts.push(`质量 ${$("quality").value || "--"}`);
        if (schema.outputFormats?.length) parts.push(`格式 ${$("outputFormat").value || "--"}`);
        if (schema.moderations?.length) parts.push(`审核 ${$("moderation").value || "--"}`);
        parts.push(`Seed ${$("seed").value || 0}`);
        if (maxUrls && submissionInputCount > Math.min(maxUrls, MAX_INPUT_IMAGES)) {
          $("submitHint").textContent = `${parts.join(" · ")} · 本次提交图片最多 ${Math.min(maxUrls, MAX_INPUT_IMAGES)} 张`;
          return;
        }
        if (!selectedMainImageUrls.length) {
          parts.push("文生图模式");
          if (selectedReferenceUrls.length) parts.push("参考图将在无主图时忽略");
          $("submitHint").textContent = parts.join(" · ");
          return;
        }
        parts.push(`主图 ${nextMainImageIndex + 1}/${selectedMainImageUrls.length}`);
        parts.push(`参考 ${selectedReferenceUrls.length}/${getMaxReferenceUrls(maxUrls)}`);
        parts.push(`本次提交 ${submissionInputCount}/${Math.min(maxUrls || MAX_INPUT_IMAGES, MAX_INPUT_IMAGES)}`);
        $("submitHint").textContent = parts.join(" · ");
      }

      function setSeedValue(value) {
        $("seed").value = Math.max(0, Math.min(2147483647, Number(value) || 0));
        saveConfig();
        updateSubmitHint();
      }

      function shortUrl(url) {
        try {
          const p = new URL(url);
          return `${p.hostname}${p.pathname.split("/").pop() ? `/${p.pathname.split("/").pop()}` : ""}`;
        } catch { return url; }
      }

      async function openHistoricalPicker(role = "main") {
        historicalPickerRole = role;
        $("historicalPickerDialog").showModal();
        setHistoricalPickerRole(role);
        $("historicalPickerGrid").innerHTML = '<div class="history-empty">正在读取历史上传...</div>';
        await refreshInputsList(false);
        renderHistoricalPicker();
      }

      function closeHistoricalPicker() {
        $("historicalPickerDialog").close();
      }

      function setHistoricalPickerRole(role) {
        historicalPickerRole = role;
        $("pickerMainTab").classList.toggle("is-active", role === "main");
        $("pickerReferenceTab").classList.toggle("is-active", role === "reference");
        renderHistoricalPicker();
      }

      function renderHistoricalPicker() {
        const grid = $("historicalPickerGrid");
        const urls = getInputUrls();
        const maxReferences = getReferenceSlotLimit();
        $("historicalPickerHint").textContent = historicalPickerRole === "main" ? `点击图片加入或移出主图框，单次提交最多 ${MAX_INPUT_IMAGES} 张。` : `点击图片加入或移出参考图框，单次提交最多 ${MAX_INPUT_IMAGES} 张。`;
        if (!urls.length) {
          grid.innerHTML = '<div class="history-empty">暂无历史上传图片。</div>';
          return;
        }
        grid.innerHTML = urls.map((url) => historicalPickerCardMarkup(url, maxReferences)).join("");
        wireCachedImageFallbacks(grid);
        grid.querySelectorAll(".preview-card[data-url]").forEach((card) => {
          card.addEventListener("click", () => {
            if (card.classList.contains("is-reference-locked")) return;
            toggleHistoricalImage(card.dataset.url);
          });
        });
      }

      function historicalPickerCardMarkup(url, maxReferences) {
        const isMain = selectedMainImageUrls.includes(url);
        const isReference = selectedReferenceUrls.includes(url);
        const maxed = historicalPickerRole === "reference" && !isReference && selectedReferenceUrls.length >= maxReferences;
        const locked = historicalPickerRole === "reference" && (isMain || maxed || !maxReferences);
        const classes = ["preview-card", isMain ? "is-main" : "", isReference ? "is-selected" : "", locked ? "is-reference-locked" : ""].filter(Boolean).join(" ");
        const badge = isMain ? '<div class="selection-badge main-badge">主图</div>' : isReference ? '<div class="selection-badge">参考</div>' : "";
        return `<div class="${classes}" data-url="${escapeAttribute(url)}"><img ${cachedImageAttrs(url)} alt="历史上传" loading="lazy" decoding="async" width="180" height="100" />${badge}<span>${escapeHtml(shortUrl(url))}</span></div>`;
      }

      function toggleHistoricalImage(url) {
        if (historicalPickerRole === "main") {
          if (selectedMainImageUrls.includes(url)) removeImageFromRole("main", url);
          else addImagesToRole("main", [url]);
        } else {
          if (selectedReferenceUrls.includes(url)) removeImageFromRole("reference", url);
          else addImagesToRole("reference", [url]);
        }
        renderHistoricalPicker();
      }

      async function retryQueuedUpload(id, role) {
        if (!id || retryingQueuedUploadIds.has(id)) return;
        retryingQueuedUploadIds.add(id);
        refreshImageSelectors();
        try {
          const record = await getQueuedUpload(id);
          if (!record) throw new Error("待重试文件不存在");
          const file = new File([record.blob], record.name, { type: record.type || "application/octet-stream", lastModified: record.updatedAt || Date.now() });
          log(`开始重试上传 ${record.name}。`, "info");
          const data = await uploadFile(file);
          const url = extractInputUrl(data);
          if (!url) throw new Error("上传成功但未能解析出图片 URL");
          await preloadImages([url]);
          await deleteQueuedUpload(id);
          addImagesToRole(role, [url], { refresh: false, save: false });
          refreshImageSelectors();
          saveConfig();
          await refreshInputsList(false, { refresh: false, save: true });
          log(`重试上传完成 ${record.name}：${url}`, "success");
          toast(`${record.name} 重试上传成功。`);
        } catch (error) {
          await updateQueuedUploadError(id, error);
          refreshImageSelectors();
          log(`重试上传失败：${error.message}`, "error");
          toast("重试上传失败，文件仍保留在本地待重试队列。");
        } finally {
          retryingQueuedUploadIds.delete(id);
          refreshImageSelectors();
        }
      }

      async function removeQueuedUpload(id) {
        if (!id) return;
        try {
          await deleteQueuedUpload(id);
          refreshImageSelectors();
          toast("已移除待重试上传。");
        } catch (error) {
          log(`移除待重试上传失败：${error.message}`, "error");
          toast("移除待重试上传失败。");
        }
      }

      async function uploadFile(file) {
        const form = new FormData();
        form.append("file", file);
        const response = await apiRequest("/api/upload", { method: "POST", body: form });
        return response.data;
      }

      async function refreshInputsList(showToast = true, options = {}) {
        try {
          const response = await apiRequest("/api/inputs?current=1&page_size=60");
          const urls = extractInputUrls(response.data);
          uploadedImageUrls = [...new Set([...uploadedImageUrls, ...urls])];
          appendUrls(urls, { refresh: false, save: false });
          if (options.refresh !== false) refreshImageSelectors();
          if (options.save !== false) saveConfig();
          log(`inputs 刷新完成，${urls.length} 个 URL。`, "success");
          if (showToast) toast("历史上传已刷新。");
          return urls;
        } catch (error) {
          log(`刷新 inputs 失败：${error.message}`, "error");
          if (showToast) toast("刷新 inputs 失败。");
          return [];
        }
      }

      function extractInputUrl(data) { return findFirstUrl(data); }
      function extractInputUrls(data) { const urls = []; collectUrls(data, urls); return [...new Set(urls)]; }

      function findFirstUrl(value) {
        if (typeof value === "string" && /^https?:\/\//.test(value)) return value;
        if (Array.isArray(value)) { for (const item of value) { const u = findFirstUrl(item); if (u) return u; } }
        if (value && typeof value === "object") {
          for (const key of ["url", "uri", "src", "download_url", "resource_url", "object_url"]) { const u = findFirstUrl(value[key]); if (u) return u; }
          for (const item of Object.values(value)) { const u = findFirstUrl(item); if (u) return u; }
        }
        return "";
      }

      function collectUrls(value, urls) {
        if (typeof value === "string" && /^https?:\/\//.test(value)) { urls.push(value); return; }
        if (Array.isArray(value)) value.forEach((i) => collectUrls(i, urls));
        else if (value && typeof value === "object") Object.values(value).forEach((i) => collectUrls(i, urls));
      }

      function appendUrl(url, options = {}) { appendUrls([url], options); }
      function appendUrls(urls, options = {}) {
        const ta = $("urls");
        const current = ta.value.split(/\n+/).map((u) => u.trim()).filter(Boolean);
        ta.value = [...new Set([...current, ...urls])].join("\n");
        if (options.refresh !== false) refreshImageSelectors();
        if (options.save !== false) saveConfig();
      }
