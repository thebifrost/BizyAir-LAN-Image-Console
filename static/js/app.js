      init();

      async function init() {
        restoreTheme();
        renderModels();
        updateModelFields();
        renderAuthState();
        wireEvents();
        wireThemeToggle();
        restoreConfig();
        await initUploadRetryQueue();
        $("tokenInput").value = authToken;
        if (!authToken) {
          $("authDialog").showModal();
          log("请输入局域网访问口令后连接本地网关。", "warn");
          return;
        }
        await loadGateway();
      }

      function restoreTheme() {
        try {
          const preferred = localStorage.getItem("bizyair-theme");
          if (preferred === "dark" || preferred === "light") {
            document.documentElement.dataset.theme = preferred;
          }
        } catch {}
      }

      function wireThemeToggle() {
        const btn = document.getElementById("toggleTheme");
        if (!btn) return;
        btn.addEventListener("click", () => {
          const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
          const next = current === "dark" ? "light" : "dark";
          document.documentElement.dataset.theme = next;
          try { localStorage.setItem("bizyair-theme", next); } catch {}
        });
      }
