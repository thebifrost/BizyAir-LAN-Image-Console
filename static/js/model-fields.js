      /* ---- Model fields ---- */
      function updateModelFields(refreshPreview = true) {
        const previousReferenceCount = selectedReferenceUrls.length;
        const schema = modelSchemas[modelEl.value];
        if (!schema) return;
        const previous = {
          size: $("size").value,
          aspectRatio: $("aspectRatio").value,
          resolution: $("resolution").value,
          quality: $("quality").value,
          variants: $("variants").value,
          outputFormat: $("outputFormat").value,
          moderation: $("moderation").value,
        };
        fillSelect("size", schema.sizes || [], previous.size || "auto", schema.sizeLabels || {});
        fillSelect("resolution", schema.resolutions || [], previous.resolution);
        const aspectRatios = modelEl.value === "gpt-image-2" && $("resolution").value === "4k" ? ["16:9", "9:16", "21:9"] : (schema.aspectRatios || []);
        fillSelect("aspectRatio", aspectRatios, previous.aspectRatio);
        fillSelect("quality", schema.qualities || [], previous.quality);
        fillSelect("variants", schema.variants || [], previous.variants);
        fillSelect("outputFormat", schema.outputFormats || [], previous.outputFormat || "png");
        fillSelect("moderation", schema.moderations || [], previous.moderation || "auto");
        toggleField("size", schema.sizes?.length && !schema.sizeFromResolution);
        toggleField("resolution", schema.resolutions?.length);
        toggleField("quality", schema.qualities?.length);
        toggleField("variants", false);
        toggleField("outputFormat", schema.outputFormats?.length);
        toggleField("moderation", schema.moderations?.length);
        toggleField("outputCompression", schema.outputFormats?.length && $("outputFormat").value && $("outputFormat").value !== "png");
        toggleField("seed", supportsSeed(modelEl.value, schema));
        toggleField("thirdPartyReferenceImagesAsFiles", isThirdPartyModel(modelEl.value));
        const maxTemp = schema.temperature?.max ?? 2;
        $("temperature").max = maxTemp;
        $("temperatureNumber").max = maxTemp;
        if (Number($("temperature").value) > maxTemp) syncRangeValue("temperature", "temperatureNumber", "temperatureValue", maxTemp);
        if (schema.maxTokens && !$("maxTokens").value) $("maxTokens").value = schema.maxTokens;
        if (refreshPreview) refreshImageSelectors();
        else updateSubmitHint();
        if (refreshPreview && previousReferenceCount > selectedReferenceUrls.length) toast(`当前模型最多保留 ${getMaxReferenceUrls()} 张参考图。`);
      }

      function fillSelect(id, values, preferredValue = "", labels = {}) {
        const sel = $(id);
        sel.innerHTML = "";
        values.forEach((v) => sel.add(new Option(labels[v] || v, v)));
        if (preferredValue && values.map(String).includes(String(preferredValue))) sel.value = preferredValue;
      }

      function toggleField(id, visible) {
        $(id).closest(".field").style.display = visible ? "grid" : "none";
      }

      function supportsSeed(model, schema) {
        return schema?.seed === true || String(model || "").startsWith("gemini");
      }

      function isThirdPartyModel(model = modelEl.value) {
        const provider = modelSchemas[model]?.provider || "bizyair";
        return provider !== "bizyair";
      }

      function resolveSizeFromSchema(schema, resolution, aspectRatio) {
        if (!schema?.sizeFromResolution) return "";
        const tierMap = schema.sizeMap?.[resolution] || schema.sizeMap?.[String(resolution || "").toUpperCase()];
        return tierMap?.[aspectRatio] || "";
      }

      function inferSizeControlsFromParams(schema, params = {}) {
        if (!schema?.sizeFromResolution) return params;
        if (params.resolution && params.aspect_ratio) return params;
        const size = params.size;
        if (!size) return params;
        for (const [resolution, ratioMap] of Object.entries(schema.sizeMap || {})) {
          for (const [aspectRatio, mappedSize] of Object.entries(ratioMap || {})) {
            if (mappedSize === size) return { ...params, resolution, aspect_ratio: aspectRatio };
          }
        }
        return params;
      }

      function bindRange(rangeId, numberId, labelId, onChange) {
        const range = $(rangeId);
        const number = $(numberId);
        const label = $(labelId);
        const sync = (v) => { range.value = v; number.value = v; label.textContent = v; if (onChange) onChange(); };
        range.addEventListener("input", () => sync(range.value));
        number.addEventListener("input", () => sync(number.value));
      }
