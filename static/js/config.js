      /* ---- Config persistence ---- */
      function saveConfig() {
        const config = {
          prompt: $("prompt").value,
          model: modelEl.value,
          aspectRatio: $("aspectRatio").value,
          resolution: $("resolution").value,
          quality: $("quality").value,
          variants: $("variants").value,
          outputFormat: $("outputFormat").value,
          outputCompression: $("outputCompression").value,
          moderation: $("moderation").value,
          seed: $("seed").value,
          temperature: $("temperature").value,
          topP: $("topP").value,
          urls: $("urls").value,
          uploadedImageUrls: [...uploadedImageUrls],
          selectedMainImageUrls: [...selectedMainImageUrls],
          selectedReferenceUrls: [...selectedReferenceUrls],
          nextMainImageIndex,
          autoRetryEnabled,
          autoRetryMaxAttempts,
        };
        try { localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config)); } catch {}
      }

      function loadSavedConfig() {
        try {
          const raw = localStorage.getItem(CONFIG_STORAGE_KEY);
          return raw ? JSON.parse(raw) : null;
        } catch {
          return null;
        }
      }

      function restoreConfig() {
        applySavedConfig(loadSavedConfig(), { includePrompt: true });
      }

      function applySavedConfig(config, options = {}) {
        if (!config) return;
        if (options.includePrompt && "prompt" in config) $("prompt").value = config.prompt;
        if (config.model && modelSchemas[config.model]) modelEl.value = config.model;
        updateModelFields(false);
        setSelectValue("resolution", config.resolution);
        updateModelFields(false);
        setSelectValue("aspectRatio", config.aspectRatio);
        setSelectValue("quality", config.quality);
        setSelectValue("variants", config.variants);
        setSelectValue("outputFormat", config.outputFormat);
        if ("outputCompression" in config) $("outputCompression").value = config.outputCompression;
        setSelectValue("moderation", config.moderation);
        updateModelFields(false);
        if ("seed" in config) $("seed").value = config.seed;
        if ("temperature" in config) syncRangeValue("temperature", "temperatureNumber", "temperatureValue", config.temperature);
        if ("topP" in config) syncRangeValue("topP", "topPNumber", "topPValue", config.topP);
        if ("urls" in config) $("urls").value = config.urls;
        uploadedImageUrls = Array.isArray(config.uploadedImageUrls) ? [...config.uploadedImageUrls] : [];
        if (Array.isArray(config.selectedMainImageUrls)) {
          selectedMainImageUrls = [...config.selectedMainImageUrls];
          selectedReferenceUrls = Array.isArray(config.selectedReferenceUrls) ? [...config.selectedReferenceUrls] : [];
        } else if (Array.isArray(config.selectedReferenceUrls) && config.selectedReferenceUrls.length) {
          selectedMainImageUrls = [config.selectedReferenceUrls[0]];
          selectedReferenceUrls = config.selectedReferenceUrls.slice(1);
        } else {
          selectedMainImageUrls = [];
          selectedReferenceUrls = [];
        }
        nextMainImageIndex = Number.isInteger(config.nextMainImageIndex) ? config.nextMainImageIndex : 0;
        if ("autoRetryEnabled" in config) autoRetryEnabled = config.autoRetryEnabled !== false;
        autoRetryMaxAttempts = Math.max(0, Math.min(10, Number(config.autoRetryMaxAttempts ?? autoRetryMaxAttempts) || 0));
        $("autoRetryEnabled").value = String(autoRetryEnabled);
        $("autoRetryMaxAttempts").value = String(autoRetryMaxAttempts);
        refreshImageSelectors();
      }

      function setSelectValue(id, value) {
        if (value === undefined || value === null) return;
        const select = $(id);
        if ([...select.options].some((option) => option.value === String(value))) select.value = value;
      }

      function syncRangeValue(rangeId, numberId, labelId, value) {
        $(rangeId).value = value;
        $(numberId).value = value;
        $(labelId).textContent = value;
      }
