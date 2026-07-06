      /* ---- Utilities ---- */
      function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }
      function cachedImageUrl(url) {
        if (isLocalInputImageUrl(url)) return url || "";
        if (!url || !/^https?:\/\//.test(url)) return url || "";
        return `/api/image-cache?url=${encodeURIComponent(url)}`;
      }
      function isLocalInputImageUrl(url) {
        try {
          const path = new URL(url, location.origin).pathname;
          return path.startsWith("/api/input-images/") || path.startsWith("/api/images/");
        } catch {
          return false;
        }
      }
      function cachedImageAttrs(url, options = {}) {
        const cached = cachedImageUrl(url);
        const shouldFallback = options.fallback !== false && cached !== url && /^https?:\/\//.test(url || "");
        const fallback = shouldFallback ? ` data-original-src="${escapeAttribute(url)}"` : "";
        return `src="${escapeAttribute(cached)}"${fallback}`;
      }
      function wireCachedImageFallbacks(root = document) {
        root.querySelectorAll("img[data-original-src]").forEach((img) => {
          if (img.dataset.cacheFallbackReady) return;
          img.dataset.cacheFallbackReady = "1";
          const fallbackToOriginal = () => {
            if (img.dataset.cacheFallbackApplied) return;
            img.dataset.cacheFallbackApplied = "1";
            img.src = img.dataset.originalSrc;
          };
          img.addEventListener("error", fallbackToOriginal);
          if (img.complete && img.naturalWidth === 0) fallbackToOriginal();
        });
      }
      function preloadImages(urls) {
        return Promise.all(urls.filter(Boolean).map((url) => new Promise((resolve) => {
          const image = new Image();
          image.onload = resolve;
          image.onerror = resolve;
          image.src = cachedImageUrl(url);
        })));
      }
      function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
      function escapeHtml(v) { return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
      function escapeAttribute(v) { return escapeHtml(v).replace(/`/g, "&#96;"); }
