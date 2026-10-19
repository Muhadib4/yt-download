(function () {
  "use strict";

  const validation = window.YtDownloadValidation;
  const state = {
    health: null,
    file: null,
    source: null,
    formats: new Map(),
    selectedFormat: null,
    busy: false,
    activeJobId: null,
    maxUploadBytes: 2 * 1024 * 1024 * 1024,
  };

  const byId = (id) => document.getElementById(id);
  const fileInput = byId("media-file");
  const rightsCheckbox = byId("rights-checkbox");
  const convertButton = byId("convert-button");
  const dropzone = byId("dropzone");
  const uploadError = byId("upload-error");
  const capabilityPanel = byId("capability-panel");
  const statusPanel = byId("status-panel");
  const statusText = byId("status-text");
  const statusDetail = byId("status-detail");
  const rangeFields = byId("range-fields");
  const startInput = byId("start-time");
  const endInput = byId("end-time");
  const rangeError = byId("range-error");
  let urlCheckTimer = null;
  let urlAnalysisToken = 0;

  function clearStatus() {
    statusPanel.hidden = true;
    statusText.textContent = "";
    statusDetail.replaceChildren();
  }

  function showStatus(message, tone, progress = null, progressLabel = "") {
    statusPanel.hidden = false;
    statusPanel.dataset.tone = tone || "info";
    statusText.textContent = message;
    statusDetail.replaceChildren();
    if (progress !== null || progressLabel) {
      const bar = document.createElement("progress");
      bar.className = "status-progress";
      bar.max = 100;
      bar.setAttribute("aria-label", "Progres unggah atau konversi aktual");
      if (typeof progress === "number") bar.value = progress;
      const label = document.createElement("span");
      label.className = "status-detail-label";
      label.textContent = progressLabel || "";
      statusDetail.append(bar, label);
    }
  }

  function setUploadError(message) {
    uploadError.textContent = message || "";
    fileInput.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function setRangeError(message) {
    rangeError.textContent = message || "";
    startInput.setAttribute("aria-invalid", message ? "true" : "false");
    endInput.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function uploadLimitLabel(bytes) {
    if (bytes >= 1024 * 1024 * 1024) {
      const gigabytes = bytes / (1024 * 1024 * 1024);
      return `${gigabytes.toFixed(Number.isInteger(gigabytes) ? 0 : 1)} GB`;
    }
    return `${Math.round(bytes / (1024 * 1024))} MB`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "Durasi tidak diketahui";
    const wholeSeconds = Math.floor(seconds);
    const hours = Math.floor(wholeSeconds / 3600);
    const minutes = Math.floor((wholeSeconds % 3600) / 60);
    const remainder = wholeSeconds % 60;
    return [hours, minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
  }

  async function requestJson(url, options) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let body = {};
    try {
      body = await response.json();
    } catch {
      body = {};
    }
    if (!response.ok) throw new Error(body.error || `Processor merespons dengan status ${response.status}.`);
    return body;
  }

  function updateActionButtons() {
    const healthReady = Boolean(state.health && state.health.ready);
    fileInput.disabled = state.busy || !healthReady;
    rightsCheckbox.disabled = state.busy || !healthReady;

    const format = state.formats.get(state.selectedFormat);
    const qualitySelect = byId("quality-select");
    const qualityReady = Boolean(format && format.qualities.length && qualitySelect.value);
    byId("format-mp4").disabled = state.busy || !state.formats.has("mp4");
    byId("format-mp3").disabled = state.busy || !state.formats.has("mp3");
    qualitySelect.disabled = state.busy || !format || !format.qualities.length;
    const durationKnown = Boolean(state.source && Number.isFinite(state.source.durationSeconds) && state.source.durationSeconds > 0);
    const customMode = document.querySelector('input[name="duration-mode"][value="custom"]');
    const fullMode = document.querySelector('input[name="duration-mode"][value="full"]');
    customMode.disabled = state.busy || !durationKnown;
    fullMode.disabled = state.busy || !state.source;
    const isCustom = customMode.checked && durationKnown;
    startInput.disabled = state.busy || !isCustom;
    endInput.disabled = state.busy || !isCustom;
    convertButton.disabled = state.busy || !healthReady || !state.source || !qualityReady || !rightsCheckbox.checked;
  }

  function updateHealth() {
    requestJson("/api/health")
      .then((health) => {
        state.health = health;
        state.maxUploadBytes = health.maxUploadBytes || state.maxUploadBytes;
        byId("max-upload-label").textContent = uploadLimitLabel(state.maxUploadBytes);
        const badge = byId("processor-badge");
        badge.classList.toggle("processor-offline", !health.ready);
        byId("processor-state").textContent = health.ready
          ? "Processor siap · FFmpeg lokal"
          : "FFmpeg atau ffprobe tidak tersedia";
        if (!health.ready) {
          showStatus("Instal FFmpeg dan ffprobe, lalu jalankan python server.py.", "error");
        }
        updateActionButtons();
        maybeInspectSelectedFile();
        maybeAnalyzeSourceUrl();
      })
      .catch(() => {
        state.health = { ready: false };
        byId("processor-badge").classList.add("processor-offline");
        byId("processor-state").textContent = "Processor lokal tidak terhubung";
        showStatus("Jalankan python server.py, lalu buka http://127.0.0.1:8765 untuk menggunakan processor.", "error");
        updateActionButtons();
      });
  }

  function resetSourceState() {
    state.source = null;
    state.formats = new Map();
    state.selectedFormat = null;
    capabilityPanel.hidden = true;
    setRangeError("");
    clearStatus();
    updateActionButtons();
  }

  function setUrlError(message) {
    const input = byId("youtube-link");
    byId("youtube-url-error").textContent = message || "";
    input.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function setUrlStatus(message, tone) {
    const status = byId("url-status");
    status.hidden = !message;
    status.dataset.tone = tone || "info";
    status.textContent = message || "";
  }

  function selectFile(file) {
    if (state.busy) return;
    resetSourceState();
    setUploadError("");
    state.file = file || null;
    if (!state.file) {
      dropzone.classList.remove("has-file");
      byId("selected-file").hidden = true;
      updateActionButtons();
      return;
    }
    if (state.file.size > state.maxUploadBytes) {
      setUploadError(`File melewati batas ${formatBytes(state.maxUploadBytes)}. Pilih file yang lebih kecil.`);
      state.file = null;
      fileInput.value = "";
      dropzone.classList.remove("has-file");
      byId("selected-file").hidden = true;
      updateActionButtons();
      return;
    }
    dropzone.classList.add("has-file");
    byId("dropzone-title").textContent = "File siap diperiksa";
    byId("file-name").textContent = state.file.name;
    byId("file-size").textContent = formatBytes(state.file.size);
    byId("selected-file").hidden = false;
    updateActionButtons();
    maybeInspectSelectedFile();
  }

  function uploadForAnalysis(file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      let lastProgressStep = -1;
      request.open("POST", "/api/analyze");
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.setRequestHeader("X-Processing-Permission", "confirmed");
      request.setRequestHeader("X-Upload-Filename", encodeURIComponent(file.name));
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable || !event.total) return;
        const percent = Math.min(100, Math.floor((event.loaded / event.total) * 100));
        const step = Math.floor(percent / 4);
        if (step === lastProgressStep) return;
        lastProgressStep = step;
        showStatus("Mengirim file ke processor di perangkat ini…", "info", percent, `${percent}%`);
      });
      request.upload.addEventListener("load", () => {
        showStatus("Memeriksa durasi, trek, dan pratinjau file…", "info");
      });
      request.addEventListener("load", () => {
        let response = {};
        try {
          response = JSON.parse(request.responseText);
        } catch {
          reject(new Error("Respons processor lokal tidak terbaca."));
          return;
        }
        if (request.status < 200 || request.status >= 300) {
          reject(new Error(response.error || `Processor merespons dengan status ${request.status}.`));
          return;
        }
        resolve(response);
      });
      request.addEventListener("error", () => reject(new Error("Koneksi ke processor lokal terputus.")));
      request.addEventListener("abort", () => reject(new Error("Pemeriksaan file dibatalkan.")));
      request.send(file);
    });
  }

  function renderMediaSummary(source) {
    const summary = byId("media-summary");
    summary.replaceChildren();
    if (source.thumbnailUrl) {
      const thumbnail = document.createElement("img");
      thumbnail.className = "media-thumbnail";
      thumbnail.src = source.thumbnailUrl;
      thumbnail.alt = `Pratinjau ${source.fileName}`;
      summary.append(thumbnail);
    }
    const details = document.createElement("div");
    details.className = "media-summary-details";
    const name = document.createElement("strong");
    name.textContent = source.title || source.fileName;
    details.append(name);
    if (source.video) {
      const dimensions = document.createElement("span");
      dimensions.textContent = `${source.video.width} × ${source.video.height}`;
      details.append(dimensions);
    }
    const duration = document.createElement("span");
    duration.textContent = formatDuration(source.durationSeconds);
    details.append(duration);
    summary.append(details);
    summary.dataset.bytes = formatBytes(source.sizeBytes);
  }

  function selectedFormatInput() {
    return document.querySelector('input[name="format"]:checked');
  }

  function renderQualityOptions() {
    const format = state.formats.get(state.selectedFormat);
    const select = byId("quality-select");
    const label = byId("quality-label");
    const help = byId("quality-help");
    select.replaceChildren();
    label.textContent = state.selectedFormat === "mp3" ? "Kualitas audio" : "Resolusi video";
    if (!format || !format.qualities.length) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "Tidak tersedia untuk file ini";
      select.append(empty);
      select.disabled = true;
      help.textContent = "Processor tidak menemukan opsi output yang dapat dibuat.";
      return;
    }

    for (const quality of format.qualities) {
      const option = document.createElement("option");
      option.value = quality.id;
      option.textContent = quality.label;
      select.append(option);
    }
    select.disabled = false;
    help.textContent = state.selectedFormat === "mp3"
      ? "Bitrate MP3 yang bisa dibuat processor lokal."
      : "Resolusi yang tidak melebihi tinggi sumber.";
  }

  function renderCapabilities(source) {
    state.source = source;
    state.formats = new Map(source.formats.map((format) => [format.id, format]));
    renderMediaSummary(source);

    const mp4 = byId("format-mp4");
    const mp3 = byId("format-mp3");
    const mp4Available = state.formats.has("mp4");
    const mp3Available = state.formats.has("mp3");
    mp4.disabled = !mp4Available;
    mp3.disabled = !mp3Available;
    byId("format-mp4-card").classList.toggle("is-unavailable", !mp4Available);
    byId("format-mp3-card").classList.toggle("is-unavailable", !mp3Available);

    const formats = source.formats.map((format) => format.id);
    state.selectedFormat = formats.includes("mp4") ? "mp4" : (formats[0] || null);
    mp4.checked = state.selectedFormat === "mp4";
    mp3.checked = state.selectedFormat === "mp3";
    const availabilityNotes = [];
    if (formats.length) {
      availabilityNotes.push(`Tersedia: ${formats.map((id) => id.toUpperCase()).join(" dan ")}.`);
    }
    for (const [formatId, reason] of Object.entries(source.unavailableReasons || {})) {
      availabilityNotes.push(`${formatId.toUpperCase()} tidak tersedia: ${reason}`);
    }
    byId("format-help").textContent = availabilityNotes.join(" ") || "Tidak ada format yang dapat dibuat oleh processor ini.";
    renderQualityOptions();

    const durationKnown = Number.isFinite(source.durationSeconds) && source.durationSeconds > 0;
    const customInput = document.querySelector('input[name="duration-mode"][value="custom"]');
    customInput.disabled = !durationKnown;
    byId("duration-help").textContent = durationKnown
      ? `Durasi sumber: ${formatDuration(source.durationSeconds)}.`
      : "Durasi file tidak diketahui; pemotongan khusus dinonaktifkan.";
    document.querySelector('input[name="duration-mode"][value="full"]').checked = true;
    customInput.checked = false;
    rangeFields.hidden = true;
    setRangeError("");
    capabilityPanel.hidden = false;
    updateActionButtons();

    if (formats.length) {
      showStatus("Kemampuan file dan encoder sudah diperiksa. Pilihan yang tampil berasal dari processor lokal.", "info");
    } else {
      showStatus("File terbaca, tetapi FFmpeg tidak memiliki encoder yang sesuai untuk menghasilkan MP4 atau MP3.", "error");
    }
  }

  function timeRangeForRequest() {
    const custom = document.querySelector('input[name="duration-mode"]:checked').value === "custom";
    if (!custom) {
      setRangeError("");
      return { valid: true, range: null };
    }
    const result = validation.validateTimeRange(startInput.value, endInput.value, state.source.durationSeconds);
    setRangeError(result.valid ? "" : result.message);
    if (!result.valid) {
      (result.field === "end" ? endInput : startInput).focus();
      return { valid: false };
    }
    return { valid: true, range: { startSeconds: result.startSeconds, endSeconds: result.endSeconds } };
  }

  async function inspectSelectedFile() {
    if (state.busy || !state.file || !state.health || !state.health.ready) return;
    if (!rightsCheckbox.checked) {
      setUploadError("Konfirmasi izin pemrosesan sebelum file dikirim ke processor lokal.");
      rightsCheckbox.focus();
      return;
    }

    state.busy = true;
    updateActionButtons();
    setUploadError("");
    showStatus("Mengirim file untuk pemeriksaan format dan durasi…", "info");
    try {
      const source = await uploadForAnalysis(state.file);
      renderCapabilities(source);
    } catch (error) {
      showStatus(error.message, "error");
    } finally {
      state.busy = false;
      updateActionButtons();
    }
  }

  function maybeInspectSelectedFile() {
    if (state.file && rightsCheckbox.checked && !state.source && !state.busy) {
      void inspectSelectedFile();
    }
  }

  function updateDurationControls() {
    const custom = document.querySelector('input[name="duration-mode"]:checked').value === "custom";
    rangeFields.hidden = !custom;
    setRangeError("");
    updateActionButtons();
  }

  async function startConversion() {
    if (state.busy || !state.source || !state.selectedFormat) return;
    if (!rightsCheckbox.checked) {
      showStatus("Konfirmasi bahwa Anda berhak memproses file ini.", "error");
      rightsCheckbox.focus();
      return;
    }
    const timeRange = timeRangeForRequest();
    if (!timeRange.valid) return;

    const quality = byId("quality-select").value;
    state.busy = true;
    updateActionButtons();
    showStatus("Mengirim permintaan konversi ke FFmpeg lokal…", "info");
    try {
      const job = await requestJson("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceId: state.source.sourceId,
          format: state.selectedFormat,
          quality,
          range: timeRange.range,
        }),
      });
      state.activeJobId = job.jobId;
      showStatus("Processor menerima pekerjaan. Menunggu status FFmpeg…", "info", 0, "Antrean");
      pollJob(job.jobId);
    } catch (error) {
      state.busy = false;
      showStatus(error.message, "error");
      updateActionButtons();
    }
  }

  function pollJob(jobId) {
    requestJson(`/api/jobs/${jobId}`)
      .then((job) => {
        if (job.status === "queued") {
          showStatus("Menunggu FFmpeg mulai memproses…", "info", 0, "Antrean");
          window.setTimeout(() => pollJob(jobId), 700);
          return;
        }
        if (job.status === "processing") {
          const progress = Number.isFinite(job.progress) ? job.progress : null;
          const progressLabel = progress === null ? "Sedang diproses" : `${progress}%`;
          showStatus("Mengonversi media dengan FFmpeg…", "info", progress, progressLabel);
          window.setTimeout(() => pollJob(jobId), 700);
          return;
        }
        if (job.status === "complete") {
          state.activeJobId = null;
          state.busy = false;
          showStatus(`Selesai: ${job.fileName} · ${formatBytes(job.outputBytes)}.`, "success");
          const link = document.createElement("a");
          link.className = "download-result";
          link.href = job.downloadUrl;
          link.download = job.fileName;
          link.textContent = "Unduh file hasil";
          statusDetail.append(link);
          updateActionButtons();
          return;
        }
        state.activeJobId = null;
        state.busy = false;
        showStatus(job.error || "FFmpeg gagal memproses file.", "error");
        updateActionButtons();
      })
      .catch(() => {
        showStatus("Koneksi ke processor terputus. Status pekerjaan akan diperiksa kembali.", "error");
        window.setTimeout(() => pollJob(jobId), 1800);
      });
  }

  async function analyzeSourceUrl() {
    const urlInput = byId("youtube-link");
    const result = validation.validateMediaSourceUrl(urlInput.value);
    if (!result.valid || !rightsCheckbox.checked || state.busy || !state.health || !state.health.ready) return;
    const requestedUrl = urlInput.value.trim();
    const token = ++urlAnalysisToken;
    state.file = null;
    fileInput.value = "";
    dropzone.classList.remove("has-file");
    byId("selected-file").hidden = true;
    resetSourceState();
    state.busy = true;
    updateActionButtons();
    setUrlStatus("Mengunduh dan memeriksa file media…", "info");
    showStatus("Mengunduh file media dari sumber yang dipilih…", "info");
    try {
      const source = await requestJson("/api/analyze-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: requestedUrl, permissionConfirmed: true }),
      });
      if (token !== urlAnalysisToken || urlInput.value.trim() !== requestedUrl) return;
      renderCapabilities(source);
      setUrlStatus("Media siap diatur untuk diunduh.", "success");
    } catch (error) {
      if (token === urlAnalysisToken) {
        setUrlError(error.message);
        setUrlStatus(error.message, "error");
        showStatus(error.message, "error");
      }
    } finally {
      if (token === urlAnalysisToken) {
        state.busy = false;
        updateActionButtons();
      }
    }
  }

  function maybeAnalyzeSourceUrl() {
    const value = byId("youtube-link").value.trim();
    if (value && rightsCheckbox.checked && !state.source && !state.busy) void analyzeSourceUrl();
  }

  function scheduleSourceUrlAnalysis() {
    window.clearTimeout(urlCheckTimer);
    const urlInput = byId("youtube-link");
    urlAnalysisToken += 1;
    if (state.source && !state.busy) resetSourceState();
    setUrlError("");
    if (!urlInput.value.trim()) {
      setUrlStatus("");
      return;
    }
    const result = validation.validateMediaSourceUrl(urlInput.value);
    if (!result.valid) {
      setUrlError(result.message);
      setUrlStatus(result.message, "error");
      return;
    }
    setUrlStatus(rightsCheckbox.checked ? "Menyiapkan pemeriksaan URL…" : "Konfirmasikan izin untuk memulai pemeriksaan.", "info");
    if (rightsCheckbox.checked) urlCheckTimer = window.setTimeout(() => void analyzeSourceUrl(), 450);
  }

  async function pasteSourceUrl() {
    const input = byId("youtube-link");
    input.focus();
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      byId("url-status").hidden = false;
      byId("url-status").dataset.tone = "info";
      byId("url-status").textContent = "Akses clipboard tidak tersedia. Tempelkan URL dengan keyboard.";
      return;
    }
    try {
      input.value = await navigator.clipboard.readText();
      scheduleSourceUrlAnalysis();
    } catch {
      byId("url-status").hidden = false;
      byId("url-status").dataset.tone = "info";
      byId("url-status").textContent = "Akses clipboard ditolak. Tempelkan URL dengan keyboard atau kontrol perangkat.";
    }
  }

  fileInput.addEventListener("change", () => selectFile(fileInput.files && fileInput.files[0]));
  rightsCheckbox.addEventListener("change", () => {
    updateActionButtons();
    maybeInspectSelectedFile();
    maybeAnalyzeSourceUrl();
  });
  convertButton.addEventListener("click", startConversion);
  byId("remove-file").addEventListener("click", () => {
    fileInput.value = "";
    selectFile(null);
    byId("dropzone-title").textContent = "Seret file ke sini atau pilih dari perangkat";
  });

  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!state.busy) dropzone.classList.add("is-dragging");
  });
  dropzone.addEventListener("dragleave", (event) => {
    if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragging");
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
    if (state.busy || !state.health || !state.health.ready) return;
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (!file) return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files;
    } catch {
      // Selection still works through the accessible file picker on browsers without DataTransfer.
    }
    selectFile(file);
  });

  document.querySelectorAll('input[name="format"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) {
        state.selectedFormat = input.value;
        renderQualityOptions();
        updateActionButtons();
        clearStatus();
      }
    });
  });
  document.querySelectorAll('input[name="duration-mode"]').forEach((input) => {
    input.addEventListener("change", updateDurationControls);
  });
  [startInput, endInput].forEach((input) => input.addEventListener("input", () => setRangeError("")));
  byId("youtube-link").addEventListener("input", scheduleSourceUrlAnalysis);
  byId("paste-url").addEventListener("click", pasteSourceUrl);

  updateActionButtons();
  updateHealth();
})();
