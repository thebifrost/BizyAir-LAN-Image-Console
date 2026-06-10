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
        fillSelect("size", schema.sizes || [], previous.size || "auto");
        fillSelect("resolution", schema.resolutions || [], previous.resolution);
        const aspectRatios = modelEl.value === "gpt-image-2" && $("resolution").value === "4k" ? ["16:9", "9:16", "21:9"] : (schema.aspectRatios || []);
        fillSelect("aspectRatio", aspectRatios, previous.aspectRatio);
        fillSelect("quality", schema.qualities || [], previous.quality);
        fillSelect("variants", schema.variants || [], previous.variants);
        fillSelect("outputFormat", schema.outputFormats || [], previous.outputFormat || "png");
        fillSelect("moderation", schema.moderations || [], previous.moderation || "auto");
        toggleField("size", schema.sizes?.length);
        toggleField("resolution", schema.resolutions?.length);
        toggleField("quality", schema.qualities?.length);
        toggleField("variants", schema.variants?.length);
        toggleField("outputFormat", schema.outputFormats?.length);
        toggleField("moderation", schema.moderations?.length);
        toggleField("outputCompression", schema.outputFormats?.length && $("outputFormat").value && $("outputFormat").value !== "png");
        toggleField("seed", supportsSeed(modelEl.value, schema));
        const maxTemp = schema.temperature?.max ?? 2;
        $("temperature").max = maxTemp;
        $("temperatureNumber").max = maxTemp;
        if (Number($("temperature").value) > maxTemp) syncRangeValue("temperature", "temperatureNumber", "temperatureValue", maxTemp);
        if (schema.maxTokens && !$("maxTokens").value) $("maxTokens").value = schema.maxTokens;
        if (refreshPreview) refreshImageSelectors();
        else updateSubmitHint();
        if (refreshPreview && previousReferenceCount > selectedReferenceUrls.length) toast(`当前模型最多保留 ${getMaxReferenceUrls()} 张参考图。`);
      }

      function fillSelect(id, values, preferredValue = "") {
        const sel = $(id);
        sel.innerHTML = "";
        values.forEach((v) => sel.add(new Option(v, v)));
        if (preferredValue && values.map(String).includes(String(preferredValue))) sel.value = preferredValue;
      }

      function toggleField(id, visible) {
        $(id).closest(".field").style.display = visible ? "grid" : "none";
      }

      function supportsSeed(model, schema) {
        return schema?.seed === true || String(model || "").startsWith("gemini");
      }

      function bindRange(rangeId, numberId, labelId, onChange) {
        const range = $(rangeId);
        const number = $(numberId);
        const label = $(labelId);
        const sync = (v) => { range.value = v; number.value = v; label.textContent = v; if (onChange) onChange(); };
        range.addEventListener("input", () => sync(range.value));
        number.addEventListener("input", () => sync(number.value));
      }
