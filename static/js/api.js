      /* ---- API ---- */
      async function apiRequest(path, options = {}) {
        ensureAuth();
        const headers = { accept: "application/json", Authorization: `Bearer ${authToken}` };
        let body = options.body;
        if (body && !(body instanceof FormData)) {
          headers["content-type"] = "application/json";
          body = JSON.stringify(body);
        }
        const response = await fetch(path, { method: options.method || "GET", headers, body });
        const text = await response.text();
        let json;
        try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text }; }
        if (!response.ok || json.status === false) {
          const error = new Error(json.message || `${response.status} ${response.statusText}`);
          error.status = response.status;
          throw error;
        }
        return json;
      }

      function ensureAuth() {
        if (authToken) return;
        const error = new Error("请先输入局域网访问口令。");
        error.status = 401;
        $("authDialog").showModal();
        throw error;
      }

      async function refreshAccountInfo(showToast = true) {
        try {
          const response = await apiRequest("/api/account");
          renderAccountInfo(response.data || {});
          if (showToast) toast("账户信息已更新。");
        } catch (error) {
          if (showToast) toast(error.message);
          log(`账户信息查询失败：${error.message}`, "warn");
        }
      }

      function renderAccountInfo(account) {
        const accounts = Array.isArray(account.keys) ? account.keys : [account];
        $("accountBox").innerHTML = accounts.map((item, index) => `<div class="key-chip"><div class="key-row"><span class="key-name">${escapeHtml(item.label || item.id || `BizyAir Key ${index + 1}`)}</span><span class="key-status">${escapeHtml(item.status || "已连接")}</span></div><div class="key-info-grid"><span>账户：${escapeHtml(item.account || "--")}</span><span>会员：${escapeHtml(item.membership || "--")}</span><span>到期：${escapeHtml(item.expire_at || "--")}</span><span>总余额：${escapeHtml(String(item.total_balance || "--"))}</span><span>充值余额：${escapeHtml(String(item.charge_balance || "--"))}</span><span>赠送余额：${escapeHtml(String(item.gift_balance || "--"))}</span></div></div>`).join("");
      }
