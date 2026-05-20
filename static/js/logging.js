      /* ---- Logging ---- */
      function log(message, level = "info") {
        const time = new Date().toLocaleTimeString();
        const entry = document.createElement("div");
        entry.className = `log-entry ${level}`;
        entry.innerHTML = `<span class="log-time">${time}</span><span class="log-level">${level}</span><span>${escapeHtml(message)}</span>`;
        logBox.prepend(entry);
        while (logBox.children.length > 80) logBox.lastElementChild.remove();
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
