      /* ---- Runtime env config ---- */
      let envConfigSnapshot = null;

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
        $("envConfigStatus").textContent = envConfigSnapshot.env_path ? `配置文件：${envConfigSnapshot.env_path}` : "配置已加载。";
      }

      function renderEnvFields(fields) {
        const groups = new Map();
        fields.forEach((field) => {
          if (!groups.has(field.section)) groups.set(field.section, []);
          groups.get(field.section).push(field);
        });
        const html = [];
        groups.forEach((items, section) => {
          html.push(`<div class="env-section-title">${escapeHtml(section)}</div>`);
          items.forEach((field) => html.push(envFieldMarkup(field)));
        });
        $("envFields").innerHTML = html.join("");
      }

      function envFieldMarkup(field) {
        const value = field.sensitive ? "" : (field.value || "");
        const placeholder = field.sensitive && field.configured ? "已配置，留空不修改" : (field.default || "");
        if (field.type === "boolean") {
          return `<div class="env-field"><label for="env-${escapeAttribute(field.name)}">${escapeHtml(field.label)}</label><select id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}"><option value="false"${String(value).toLowerCase() !== "true" ? " selected" : ""}>关闭</option><option value="true"${String(value).toLowerCase() === "true" ? " selected" : ""}>开启</option></select></div>`;
        }
        if (field.type === "select" && field.name === "LOG_LEVEL") {
          const current = value || field.default || "INFO";
          const options = ["DEBUG", "INFO", "WARNING", "ERROR"].map((item) => `<option value="${item}"${item === current ? " selected" : ""}>${item}</option>`).join("");
          return `<div class="env-field"><label for="env-${escapeAttribute(field.name)}">${escapeHtml(field.label)}</label><select id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}">${options}</select></div>`;
        }
        const type = field.sensitive ? "password" : field.type === "number" ? "number" : "text";
        return `<div class="env-field"><label for="env-${escapeAttribute(field.name)}">${escapeHtml(field.label)}</label><input id="env-${escapeAttribute(field.name)}" data-env-name="${escapeAttribute(field.name)}" type="${type}" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" /></div>`;
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
            button.closest(".provider-card")?.remove();
            if (!$("providerConfigList").querySelector(".provider-card")) renderProviderConfigList([]);
          });
        });
      }

      function providerCardMarkup(provider = {}) {
        const id = provider.id || "";
        const title = id ? `${provider.label || id} · ${id}` : "新 Provider";
        return `<article class="provider-card">
          <div class="provider-card-head"><strong>${escapeHtml(title)}</strong><button class="secondary danger provider-remove" type="button">移除</button></div>
          <div class="provider-card-grid">
            ${providerInput("id", "Provider ID", id, "moyuu")}
            ${providerInput("label", "显示名称", provider.label || "", "Moyuu")}
            ${providerInput("base_url", "Base URL", provider.base_url || "", "https://api.example.com/v1", "wide")}
            ${providerInput("api_key", "API Key", "", provider.api_key_configured ? "已配置，留空不修改" : "必填", "wide", "password")}
            <div class="env-field wide"><label>模型映射</label><textarea data-provider-field="models" rows="3" placeholder="local-alias=upstream-model">${escapeHtml(provider.models || "")}</textarea></div>
            ${providerInput("timeout_seconds", "超时秒", provider.timeout_seconds ?? 300, "300", "", "number")}
            ${providerInput("concurrency", "并发上限", provider.concurrency ?? 0, "0", "", "number")}
            <div class="env-field"><label>参考图发送</label><select data-provider-field="send_reference_images_as_files"><option value="true"${provider.send_reference_images_as_files !== false ? " selected" : ""}>文件 multipart</option><option value="false"${provider.send_reference_images_as_files === false ? " selected" : ""}>URL 文本</option></select></div>
          </div>
        </article>`;
      }

      function providerInput(field, label, value, placeholder = "", extraClass = "", type = "text") {
        return `<div class="env-field ${extraClass}"><label>${escapeHtml(label)}</label><input data-provider-field="${escapeAttribute(field)}" type="${type}" value="${escapeAttribute(value)}" placeholder="${escapeAttribute(placeholder)}" /></div>`;
      }

      function addOpenaiProviderCard() {
        const current = collectProviderConfig({ allowEmpty: true });
        current.push({
          id: `provider${current.length + 1}`,
          label: "",
          base_url: "",
          models: "",
          timeout_seconds: 300,
          send_reference_images_as_files: true,
          concurrency: 0,
        });
        renderProviderConfigList(current);
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
            send_reference_images_as_files: read("send_reference_images_as_files") !== "false",
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
        try {
          const payload = {
            fields: collectEnvFields(),
            providers: collectProviderConfig(),
          };
          const response = await apiRequest("/api/admin/env", { method: "POST", body: payload });
          envConfigSnapshot = null;
          await loadEnvConfig();
          const restartText = response.data?.restart_required ? "，重启后生效" : "";
          toast(`配置已保存${restartText}。`);
          log(`环境配置已保存${restartText}。`, "success");
        } catch (error) {
          toast(error.message);
          log(`环境配置保存失败：${error.message}`, "error");
        }
      }
