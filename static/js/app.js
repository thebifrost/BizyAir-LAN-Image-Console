      init();

      async function init() {
        renderModels();
        updateModelFields();
        renderAuthState();
        wireEvents();
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
