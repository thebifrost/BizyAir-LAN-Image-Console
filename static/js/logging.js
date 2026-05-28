      /* ---- Logging ---- */
      function log(message, level = "info") {
        const time = new Date().toLocaleTimeString();
        const entry = document.createElement("div");
        entry.className = `log-entry ${level}`;
        entry.innerHTML = `<span class="log-time">${time}</span><span class="log-level">${level}</span><span>${escapeHtml(message)}</span>`;
        logBox.prepend(entry);
        while (logBox.children.length > 160) logBox.lastElementChild.remove();
      }

      async function loadServerLog(type = "app") {
        try {
          const response = await apiRequest(`/api/logs?type=${encodeURIComponent(type)}&lines=80`);
          const lines = response.data?.lines || [];
          lines.slice().reverse().forEach((line) => log(`[${type}] ${line}`, type === "audit" ? "warn" : "info"));
          toast(`${type === "audit" ? "审计" : "应用"}日志已刷新。`);
        } catch (error) {
          toast(error.message);
          log(`日志读取失败：${error.message}`, "error");
        }
      }

      function toggleLogPanel() {
        logPanelCollapsed = !logPanelCollapsed;
        $("logPanel").classList.toggle("is-collapsed", logPanelCollapsed);
        $("toggleLogPanel").textContent = logPanelCollapsed ? "展开" : "折叠";
      }

      function toast(message) {
        const node = $("toast");
        clearTimeout(toastTimer);
        node.textContent = message;
        node.classList.add("show");
        toastTimer = setTimeout(() => node.classList.remove("show"), 3000);
      }
