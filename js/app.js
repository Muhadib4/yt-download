(function () {
  "use strict";

  const form = document.getElementById("request-form");
  const urlInput = document.getElementById("video-url");
  const urlError = document.getElementById("url-error");
  const rangeFields = document.getElementById("range-fields");
  const startInput = document.getElementById("start-time");
  const endInput = document.getElementById("end-time");
  const rangeError = document.getElementById("range-error");
  const statusMessage = document.getElementById("status-message");
  const qualityLabel = document.getElementById("quality-label");
  const menuToggle = document.getElementById("menu-toggle");
  const primaryNav = document.getElementById("primary-nav");
  const copyButton = document.getElementById("copy-button");

  function setStatus(message, tone) {
    statusMessage.textContent = message;
    statusMessage.className = "status-message" + (tone ? " status-" + tone : "");
    statusMessage.hidden = !message;
  }

  function clearStatus() {
    setStatus("", "");
  }

  function setUrlError(message) {
    urlError.textContent = message;
    urlInput.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function setRangeError(message) {
    rangeError.textContent = message;
    startInput.setAttribute("aria-invalid", message ? "true" : "false");
    endInput.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function validateYouTubeUrl(value) {
    const trimmed = value.trim();
    if (!trimmed) {
      return { valid: false, message: "Enter a YouTube URL to continue." };
    }

    let parsed;
    try {
      parsed = new URL(trimmed);
    } catch {
      return { valid: false, message: "Enter a complete link, including https://." };
    }

    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return { valid: false, message: "Use a web link that starts with https:// or http://." };
    }

    if (parsed.username || parsed.password) {
      return { valid: false, message: "Remove sign-in details from the link and try again." };
    }

    const hostname = parsed.hostname.toLowerCase();
    const isYouTube = ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(hostname);
    const isShortDomain = hostname === "youtu.be";
    if (!isYouTube && !isShortDomain) {
      return { valid: false, message: "That domain isn’t supported. Use a youtube.com or youtu.be link." };
    }

    let videoId = "";
    if (isShortDomain) {
      videoId = parsed.pathname.split("/").filter(Boolean)[0] || "";
    } else if (parsed.pathname === "/watch") {
      videoId = parsed.searchParams.get("v") || "";
    } else {
      const match = parsed.pathname.match(/^\/(?:shorts|live|embed)\/([^/?#]+)/);
      videoId = match ? match[1] : "";
    }

    if (!videoId) {
      return { valid: false, message: "This YouTube link doesn’t include a video ID in a supported format." };
    }
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
      return { valid: false, message: "The video ID in this link doesn’t look valid." };
    }

    return { valid: true };
  }

  function isCustomRange() {
    return document.querySelector('input[name="duration-mode"]:checked').value === "custom";
  }

  function updateDurationControls() {
    const custom = isCustomRange();
    rangeFields.hidden = !custom;
    startInput.disabled = !custom;
    endInput.disabled = !custom;
    setRangeError("");
    clearStatus();
  }

  function updateFormatControls() {
    const format = document.querySelector('input[name="format"]:checked').value;
    qualityLabel.textContent = format === "mp3" ? "Audio quality" : "Video quality";
    clearStatus();
  }

  function timeToSeconds(value) {
    const match = value.trim().match(/^(\d{2,}):([0-5]\d):([0-5]\d)$/);
    if (!match) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
  }

  function validateTimeRange() {
    if (!isCustomRange()) return true;

    const start = timeToSeconds(startInput.value);
    const end = timeToSeconds(endInput.value);
    if (start === null || end === null) {
      setRangeError("Enter both times in HH:MM:SS format, such as 00:03:15.");
      (start === null ? startInput : endInput).focus();
      return false;
    }
    if (end <= start) {
      setRangeError("End time must be later than start time.");
      endInput.focus();
      return false;
    }

    setRangeError("");
    return true;
  }

  function analyzeUrl() {
    const result = validateYouTubeUrl(urlInput.value);
    if (!result.valid) {
      setUrlError(result.message);
      setStatus("Please correct the URL before continuing. No video details were requested.", "error");
      urlInput.focus();
      return false;
    }

    setUrlError("");
    setStatus("The link uses a supported YouTube URL format. This check is local; no video details or media were fetched.", "info");
    return true;
  }

  document.getElementById("analyze-button").addEventListener("click", analyzeUrl);

  form.addEventListener("submit", function (event) {
    event.preventDefault();

    const urlResult = validateYouTubeUrl(urlInput.value);
    if (!urlResult.valid) {
      setUrlError(urlResult.message);
      setStatus("Please correct the URL before continuing. No video details were requested.", "error");
      urlInput.focus();
      return;
    }
    setUrlError("");

    if (!validateTimeRange()) {
      setStatus("Please correct the time range before continuing.", "error");
      return;
    }

    setStatus("Your request settings are valid. A media processor isn’t connected, so this page can’t retrieve, convert, trim, or download the video.", "info");
  });

  urlInput.addEventListener("input", function () {
    setUrlError("");
    clearStatus();
    copyButton.hidden = !urlInput.value.trim();
  });

  document.querySelectorAll('input[name="format"]').forEach(function (input) {
    input.addEventListener("change", updateFormatControls);
  });

  document.querySelectorAll('input[name="duration-mode"]').forEach(function (input) {
    input.addEventListener("change", updateDurationControls);
  });

  [startInput, endInput].forEach(function (input) {
    input.addEventListener("input", function () {
      setRangeError("");
      clearStatus();
    });
  });

  document.getElementById("paste-button").addEventListener("click", async function () {
    urlInput.focus();
    if (!navigator.clipboard || typeof navigator.clipboard.readText !== "function") {
      setStatus("Clipboard access isn’t available here. Paste into the URL field with your keyboard or device controls.", "info");
      return;
    }

    try {
      const clipboardText = await navigator.clipboard.readText();
      if (!clipboardText.trim()) {
        setStatus("Your clipboard is empty. Copy a YouTube link, then try Paste again.", "info");
        return;
      }
      urlInput.value = clipboardText.trim();
      copyButton.hidden = false;
      setUrlError("");
      setStatus("Link pasted. Select Analyze URL to check its format.", "info");
    } catch {
      setStatus("Clipboard access was blocked. Paste into the URL field with your keyboard or device controls.", "info");
    }
  });

  function fallbackCopy(text) {
    const temporaryInput = document.createElement("textarea");
    temporaryInput.value = text;
    temporaryInput.setAttribute("readonly", "");
    temporaryInput.style.position = "fixed";
    temporaryInput.style.opacity = "0";
    document.body.appendChild(temporaryInput);
    temporaryInput.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    temporaryInput.remove();
    return copied;
  }

  copyButton.addEventListener("click", async function () {
    const value = urlInput.value.trim();
    if (!value) return;

    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(value);
        setStatus("Link copied to your clipboard.", "info");
        return;
      }
    } catch {
      // Try the local selection-based fallback below when clipboard permission is denied.
    }

    setStatus(fallbackCopy(value) ? "Link copied to your clipboard." : "Copy isn’t available here. Select the link and copy it manually.", "info");
  });

  menuToggle.addEventListener("click", function () {
    const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!isOpen));
    menuToggle.setAttribute("aria-label", isOpen ? "Open navigation" : "Close navigation");
    primaryNav.classList.toggle("is-open", !isOpen);
  });

  primaryNav.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", function () {
      menuToggle.setAttribute("aria-expanded", "false");
      menuToggle.setAttribute("aria-label", "Open navigation");
      primaryNav.classList.remove("is-open");
    });
  });

  copyButton.hidden = !urlInput.value.trim();
})();
