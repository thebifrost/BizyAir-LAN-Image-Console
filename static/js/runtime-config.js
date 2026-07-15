      /* ---- Runtime env config ---- */
      let envConfigSnapshot = null;
      let envSaveInFlight = false;

      function switchRuntimeTab(tab) {
        ["status", "env", "providers"].forEach((name) => {
          const active = name === tab;
          $(`runtimePane${capitalize(name)}`).hidden = !active;
          $(`runtimeTab${capitalize(name)}`).classList.toggle("is-active", active);
        });
        if ((tab === "env" || tab === "providers") && !envConfigSnapshot) loadEnvConfig();
      }

      function capitalize(value) {
        return String(value || "").charAt(0).toUpperCase() + String(value || "").slice(1);
      }

      async function loadEnvConfig() {
        $("envConfigStatus").textContent = "读取配置中...";
        try {
          const response = await apiRequest("/api/admin/env");
          envConfigSnapshot = response.data || { fields: [], providers: [] };
          renderEnvConfig();
          log("环境配置已加载。", "success");
        } catch (error) {
          $("envConfigStatus").textContent = error.message;
          log(`读取环境配置失败：${error.message}`, "error");
        }
      }

      function renderEnvConfig() {
        renderEnvFields(envConfigSnapshot.fields || []);
        renderProviderConfigList(envConfigSnapshot.providers || []);
        const path = envConfigSnapshot.env_path;
        $("envConfigStatus").innerHTML = path
          ? `配置文件：<code>${escapeHtml(path)}</code>`
          : "配置已加载。";
      }

      function renderEnvFields(fields) {
        const groups = new Map();
        fields.forEach((field) => {
          if (!groups.has(field.section)) groups.set(field.section, []);
          groups.get(field.section).push(field);
        });
        const html = [];
        groups.forEach((items, section) => {
          const configuredCount = items.filter((item) => item.configured).length;
          html.push(`<div class="env-section-title"><span>${escapeHtml(section)}</span><span class="env-section-count">${configuredCount}/${items.length} 已配置</span></div>`);
          items.forEach((field) => html.push(envFieldMarkup(field)));
        });
        $("envFields").innerHTML = html.join("");
        wireEnvFieldReveal();
        wireEnvFieldValidation();
      }

      function envFieldMarkup(field) {
        const value = field.sensitive ? "" : (field.value || "");
        const placeholder = field.sensitive && field.configured ? "已配置，留空不修改" : (field.default || "");
        const statusBadge = envFieldStatusBadge(field);
        const labelHtml = `<label for="env-${escapeAttribute(field.name)}">${escapeHtml(field.label)}${statusBadge}<span class="env-field-key">${escapeHtml(field.name)}</span></label>`;

        if (field.type === "boolean") {
          const isTrue = String(value).toLowerCase() === "true";
          return `<div class="env-field">${labelHtml}<select id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}"><option value="false"${!isTrue ? " selected" : ""}>关闭</option><option value="true"${isTrue ? " selected" : ""}>开启</option></select></div>`;
        }
        if (field.type === "select" && field.name === "LOG_LEVEL") {
          const current = value || field.default || "INFO";
          const options = ["DEBUG", "INFO", "WARNING", "ERROR"].map((item) => `<option value="${item}"${item === current ? " selected" : ""}>${item}</option>`).join("");
          return `<div class="env-field">${labelHtml}<select id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}">${options}</select></div>`;
        }
        const isNumber = field.type === "number";
        const type = field.sensitive ? "password" : (isNumber ? "number" : "text");
        const minAttr = isNumber ? ' min="0"' : "";
        const inputHtml = `<input id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}" type="${type}" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}"${minAttr} />`;
        if (field.sensitive) {
          return `<div class="env-field">${labelHtml}<div class="password-input">${inputHtml}<button type="button" class="reveal-btn" data-reveal-target="env-${escapeAttribute(field.name)}" aria-label="显示/隐藏" title="显示/隐藏">👁</button></div></div>`;
        }
        return `<div class="env-field">${labelHtml}${inputHtml}</div>`;
      }

      function envFieldStatusBadge(field) {
        if (field.sensitive) {
          return field.configured
            ? '<span class="env-status env-status-ok" title="已配置">●</span>'
            : '<span class="env-status env-status-warn" title="未配置">○</span>';
        }
        if (field.default && field.value && field.value !== field.default) {
          return '<span class="env-status env-status-neutral" title="已自定义">◆</span>';
        }
        return "";
      }

      function wireEnvFieldReveal() {
        $("envFields").querySelectorAll("[data-reveal-target]").forEach((btn) => {
          btn.addEventListener("click", () => toggleFieldReveal(btn));
        });
      }

      function toggleFieldReveal(btn) {
        const targetId = btn.dataset.revealTarget;
        const input = document.getElementById(targetId);
        if (!input) return;
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        btn.textContent = showing ? "👁" : "🙈";
        btn.setAttribute("aria-pressed", String(!showing));
      }

      function wireEnvFieldValidation() {
        $("envFields").querySelectorAll('[data-env-name][type="number"]').forEach((input) => {
          input.addEventListener("input", () => {
            const value = Number(input.value);
            input.classList.toggle("is-invalid", input.value !== "" && (!Number.isFinite(value) || value < 0));
          });
        });
      }

      function renderProviderConfigList(providers) {
        const list = $("providerConfigList");
        if (!providers.length) {
          list.innerHTML = '<div class="history-empty">暂无第三方 Provider，点击新增开始配置。</div>';
          return;
        }
        list.innerHTML = providers.map(providerCardMarkup).join("");
        list.querySelectorAll(".provider-remove").forEach((button) => {
          button.addEventListener("click", () => {
            const card = button.closest(".provider-card");
            if (!card) return;
            const idInput = card.querySelector('[data-provider-field="id"]');
            const providerId = idInput?.value?.trim() || "";
            const hasContent = providerId || card.querySelector('[data-provider-field="base_url"]')?.value?.trim() || card.querySelector('[data-provider-field="models"]')?.value?.trim();
            if (hasContent && !window.confirm(`确认移除 Provider${providerId ? ` "${providerId}"` : ""}？该修改要点击"保存配置"后才会写入 .env。`)) return;
            card.remove();
            if (!$("providerConfigList").querySelector(".provider-card")) renderProviderConfigList([]);
          });
        });
        list.querySelectorAll('[data-provider-field="id"]').forEach((input) => {
          input.addEventListener("input", () => {
            const value = input.value.trim();
            const invalid = value && !/^[A-Za-z0-9_.\-]+$/.test(value);
            input.classList.toggle("is-invalid", Boolean(invalid) || value === "bizyair");
          });
        });
        wireProviderRevealButtons();
      }

      function wireProviderRevealButtons() {
        document.querySelectorAll("[data-provider-reveal]").forEach((btn) => {
          if (btn.dataset.wired) return;
          btn.dataset.wired = "1";
          btn.addEventListener("click", () => {
            const target = document.getElementById(btn.dataset.providerReveal);
            if (!target) return;
            const showing = target.type === "text";
            target.type = showing ? "password" : "text";
            btn.textContent = showing ? "👁" : "🙈";
          });
        });
      }

      function providerCardMarkup(provider = {}) {
        const id = provider.id || "";
        const title = id ? `${provider.label || id} · ${id}` : "新 Provider";
        const badge = provider.api_key_configured ? '<span class="env-status env-status-ok" title="API Key 已配置">●</span>' : '<span class="env-status env-status-warn" title="缺少 API Key">○</span>';
        return `<article class="provider-card">
          <div class="provider-card-head"><strong>${badge}${escapeHtml(title)}</strong><button class="secondary danger provider-remove" type="button">移除</button></div>
          <div class="provider-card-grid">
            ${providerInput("id", "Provider ID", id, "moyuu")}
            ${providerInput("label", "显示名称", provider.label || "", "Moyuu")}
            ${providerInput("base_url", "Base URL", provider.base_url || "", "https://api.example.com/v1", "wide")}
            ${providerInput("api_key", "API Key", "", provider.api_key_configured ? "已配置，留空不修改" : "必填", "wide", "password", true)}
            <div class="env-field wide"><label>模型映射<span class="env-field-hint">格式：<code>local-alias=upstream-model</code>，一行一个或用逗号分隔</span></label><textarea data-provider-field="models" rows="3" placeholder="local-alias=upstream-model">${escapeHtml(provider.models || "")}</textarea></div>
            ${providerInput("timeout_seconds", "超时秒", provider.timeout_seconds ?? 300, "300", "", "number")}
            ${providerInput("concurrency", "并发上限", provider.concurrency ?? 0, "0（不限流）", "", "number")}
            <div class="env-field"><label>参考图发送</label><select data-provider-field="send_reference_images_as_files"><option value="false"${provider.send_reference_images_as_files !== true ? " selected" : ""}>URL 文本</option><option value="true"${provider.send_reference_images_as_files === true ? " selected" : ""}>文件 multipart</option></select></div>
          </div>
        </article>`;
      }

      function providerInput(field, label, value, placeholder = "", extraClass = "", type = "text", revealable = false) {
        const inputId = `provider-${field}-${Math.random().toString(36).slice(2, 8)}`;
        const input = `<input id="${inputId}" data-provider-field="${escapeAttribute(field)}" type="${type}" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" />`;
        if (revealable) {
          return `<div class="env-field ${extraClass}"><label for="${inputId}">${escapeHtml(label)}</label><div class="password-input">${input}<button type="button" class="reveal-btn" data-provider-reveal="${inputId}" aria-label="显示/隐藏" title="显示/隐藏">👁</button></div></div>`;
        }
        return `<div class="env-field ${extraClass}"><label for="${inputId}">${escapeHtml(label)}</label>${input}</div>`;
      }

      function addOpenaiProviderCard() {
        const current = collectProviderConfig({ allowEmpty: true });
        current.push({
          id: `provider${current.length + 1}`,
          label: "",
          base_url: "",
          models: "",
          timeout_seconds: 300,
          send_reference_images_as_files: false,
          concurrency: 0,
        });
        renderProviderConfigList(current);
        // 滚动到并聚焦新增卡片
        const list = $("providerConfigList");
        const lastCard = list.querySelector(".provider-card:last-child");
        if (lastCard) lastCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        const idInput = lastCard?.querySelector('[data-provider-field="id"]');
        if (idInput) { idInput.focus(); idInput.select(); }
      }

      function collectProviderConfig(options = {}) {
        const cards = [...$("providerConfigList").querySelectorAll(".provider-card")];
        return cards.map((card) => {
          const read = (name) => card.querySelector(`[data-provider-field="${name}"]`)?.value?.trim() || "";
          return {
            id: read("id"),
            label: read("label"),
            base_url: read("base_url"),
            api_key: read("api_key"),
            models: read("models"),
            timeout_seconds: Number(read("timeout_seconds") || 300),
            concurrency: Number(read("concurrency") || 0),
            send_reference_images_as_files: read("send_reference_images_as_files") === "true",
          };
        }).filter((provider) => options.allowEmpty || provider.id || provider.base_url || provider.models || provider.api_key);
      }

      function collectEnvFields() {
        const fields = {};
        $("envFields").querySelectorAll("[data-env-name]").forEach((input) => {
          fields[input.dataset.envName] = input.value;
        });
        return fields;
      }

      async function saveEnvConfig() {
        if (envSaveInFlight) return;
        const saveBtn = $("saveEnvConfig");
        const providerBtn = $("saveProvidersConfig");
        const buttons = [saveBtn, providerBtn].filter(Boolean);
        // 前端预校验
        const invalidNumbers = $("envFields").querySelectorAll('[data-env-name][type="number"].is-invalid');
        if (invalidNumbers.length) {
          toast("有数字字段无效，请检查数值。");
          return;
        }
        const invalidProviderIds = $("providerConfigList").querySelectorAll('[data-provider-field="id"].is-invalid');
        if (invalidProviderIds.length) {
          toast("Provider ID 只能包含字母、数字、点、下划线和短横，且不能为 bizyair。");
          return;
        }
        envSaveInFlight = true;
        buttons.forEach((btn) => { btn.disabled = true; btn.classList.add("is-loading"); });
        try {
          const payload = {
            fields: collectEnvFields(),
            providers: collectProviderConfig(),
          };
          const response = await apiRequest("/api/admin/env", { method: "POST", body: payload });
          envConfigSnapshot = null;
          await loadEnvConfig();
          await loadGateway();
          const restartRequired = response.data?.restart_required;
          const restartText = restartRequired ? "，重启后生效" : "";
          toast(`配置已保存${restartText}。`);
          log(`环境配置已保存${restartText}。`, "success");
          setEnvSavedFlash(true);
          if (restartRequired) showRestartAlert();
        } catch (error) {
          toast(error.message);
          log(`环境配置保存失败：${error.message}`, "error");
        } finally {
          envSaveInFlight = false;
          buttons.forEach((btn) => { btn.disabled = false; btn.classList.remove("is-loading"); });
        }
      }

      function setEnvSavedFlash(saved) {
        const el = $("envConfigStatus");
        if (!el) return;
        if (saved) {
          el.classList.add("is-saved");
          setTimeout(() => el.classList.remove("is-saved"), 2000);
        }
      }

      function showRestartAlert() {
        const alert = $("runtimeRestartAlert");
        if (alert) alert.hidden = false;
      }
