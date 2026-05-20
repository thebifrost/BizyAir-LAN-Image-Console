      /* ---- Auth ---- */
      async function saveAccessToken() {
        const token = $("tokenInput").value.trim();
        if (!token) { toast("请输入访问口令。"); return; }
        authToken = token;
        sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
        renderAuthState();
        $("authDialog").close();
        await loadGateway();
      }

      function clearAccessToken() {
        authToken = "";
        sessionStorage.removeItem(TOKEN_STORAGE_KEY);
        $("tokenInput").value = "";
        renderAuthState();
        toast("已清除本机口令。");
      }
