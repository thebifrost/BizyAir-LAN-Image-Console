      const TOKEN_STORAGE_KEY = "bizyair-lan-admin-token";
      const CONFIG_STORAGE_KEY = "bizyair-task-config";
      const HISTORY_VIEW_STORAGE_KEY = "bizyair-history-view";
      const UPLOAD_RETRY_DB_NAME = "bizyair-upload-retry";
      const UPLOAD_RETRY_DB_VERSION = 1;
      const UPLOAD_RETRY_STORE = "failedUploads";
      const DEFAULT_POLL_INTERVAL = 5000;
      const IDLE_RUNTIME_REFRESH_INTERVAL = 30000;
      const MAX_INPUT_IMAGES = 10;
      const DEFAULT_MAX_UPLOAD_MB = 20;
      const TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"];

      let pollInterval = DEFAULT_POLL_INTERVAL;
      let maxUploadBytes = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024;
      const MODEL_DISPLAY_NAMES = {
        "gemini-2.5-flash-image": "NanoBanana",
        "gemini-3-pro-image-preview": "NanoBanana Pro",
        "gemini-3-pro-image-preview-official": "NanoBanana Pro -official",
        "gemini-3.1-flash-image-preview": "NanoBanana 2",
        "gemini-3.1-flash-image-preview-official": "NanoBanana 2 -official",
        "moyuu-gpt-image-2": "Moyuu GPT Image 2",
      };
      const modelDisplayName = (model) => MODEL_DISPLAY_NAMES[model] || model;

      let modelSchemas = {
        "gpt-image-1": { aspectRatios: ["1:1", "2:3", "3:2"], resolutions: [], qualities: [], variants: [1, 2, 4], maxUrls: 99 },
        "gpt-image-2": { aspectRatios: ["1:1", "2:3", "3:2", "4:5", "5:4", "3:4", "4:3", "16:9", "9:16", "21:9"], resolutions: ["1k", "2k", "4k"], qualities: [], variants: [], maxUrls: 99 },
        "gpt-image-2-official": { aspectRatios: ["1:1", "1:3", "3:1", "2:3", "3:2", "4:5", "5:4", "3:4", "4:3", "16:9", "9:16", "21:9"], resolutions: ["1k", "2k", "4k"], qualities: ["low", "medium", "high"], variants: [], maxUrls: 99 },
        "gemini-2.5-flash-image": { aspectRatios: ["1:1", "16:9", "9:16", "4:3", "3:4"], resolutions: [], qualities: [], variants: [], maxUrls: 5, temperature: { max: 1 }, topP: 1, maxTokens: 8192 },
        "gemini-3-pro-image-preview": { aspectRatios: ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], resolutions: ["1K", "2K", "4K"], qualities: [], variants: [], maxUrls: 14 },
        "gemini-3-pro-image-preview-official": { aspectRatios: ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], resolutions: ["1K", "2K", "4K"], qualities: [], variants: [], maxUrls: 14 },
        "gemini-3.1-flash-image-preview": { aspectRatios: ["1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], resolutions: ["1K", "2K", "4K"], qualities: [], variants: [], maxUrls: 14 },
        "gemini-3.1-flash-image-preview-official": { aspectRatios: ["1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], resolutions: ["1K", "2K", "4K"], qualities: [], variants: [], maxUrls: 14 },
      };

      let authToken = sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
      let activeJobId = "";
      let uploadedImageUrls = [];
      let selectedMainImageUrls = [];
      let selectedReferenceUrls = [];
      let pendingUploadPreviews = { main: [], reference: [] };
      let queuedUploadPreviews = { main: [], reference: [] };
      let nextMainImageIndex = 0;
      let submittedTasks = [];
      let historyImageUrls = new Set();
      let historyRecords = [];
      let historyPage = 1;
      let historyPageSize = 24;
      let historyColumns = 4;
      let historySearchText = "";
      let historyModelFilter = "";
      let historySort = "newest";
      let retryingTaskIds = new Set();
      let retryingQueuedUploadIds = new Set();
      let retryingAllFailedTasks = false;
      let autoRetryingTaskIds = new Set();
      let autoRetryEnabled = true;
      let autoRetryMaxAttempts = 2;
      let thirdPartyReferenceImagesAsFiles = true;
      let pollingJobs = new Set();
      let taskQueueCollapsed = false;
      let logPanelCollapsed = false;
      let historicalPickerRole = "main";
      let activeTaskPreviewImageUrl = "";
      let activeLightboxRecord = null;
      let pendingDeleteImageRecord = null;
      let hoverPreviewToken = 0;
      let toastTimer = null;
      let runtimeRefreshTimer = null;
      let lastSeenJobIds = new Set();

      const $ = (id) => document.getElementById(id);
      const modelEl = $("model");
      const resultsEl = $("results");
      const logBox = $("log");
