      /* ---- Model fields ---- */
      function updateModelFields(refreshPreview = true) {
        const previousReferenceCount = selectedReferenceUrls.length;
        const schema = modelSchemas[modelEl.value];
        if (!schema) return;
        const previous = {
          aspectRatio: $("aspectRatio").value,
          resolution: $("resolution").value,
          quality: $("quality").value,
          variants: $("variants").value,
        };
        fillSelect("resolution", schema.resolutions || [], previous.resolution);
        const aspectRatios = modelEl.value === "gpt-image-2" && $("resolution").value === "4k" ? ["16:9", "9:16", "21:9"] : (schema.aspectRatios || []);
        fillSelect("aspectRatio", aspectRatios, previous.aspectRatio);
        fillSelect("quality", schema.qualities || [], previous.quality);
        fillSelect("variants", schema.variants || [], previous.variants);
        toggleField("resolution", schema.resolutions?.length);
        toggleField("quality", schema.qualities?.length);
        toggleField("variants", schema.variants?.length);
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

      function bindRange(rangeId, numberId, labelId, onChange) {
        const range = $(rangeId);
        const number = $(numberId);
        const label = $(labelId);
        const sync = (v) => { range.value = v; number.value = v; label.textContent = v; if (onChange) onChange(); };
        range.addEventListener("input", () => sync(range.value));
        number.addEventListener("input", () => sync(number.value));
      }
