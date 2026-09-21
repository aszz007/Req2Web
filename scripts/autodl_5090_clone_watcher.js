/*
 * AutoDL RTX 5090 clone-page watcher.
 *
 * Run this as a Chrome DevTools Snippet while the AutoDL clone page is open.
 * The script stays inside the current page and refreshes the host inventory by
 * toggling the RTX 5090 filter. It does not read cookies or export credentials.
 *
 * Safe monitoring example (never submits):
 *
 *   startAutoDL5090CloneWatcher({
 *     sourceInstanceId: "8z3ltky94f-55269871",
 *     pollIntervalMs: 12000,
 *     autoSubmit: false,
 *     requireDataDiskCopy: true
 *   });
 *
 * Automatic paid clone example (requires an explicit hourly price cap):
 *
 *   startAutoDL5090CloneWatcher({
 *     sourceInstanceId: "8z3ltky94f-55269871",
 *     pollIntervalMs: 12000,
 *     autoSubmit: true,
 *     maxRatePerHourCny: 2.80,
 *     billingAcknowledged: true,
 *     requireDataDiskCopy: true
 *   });
 *
 * Stop manually with:
 *
 *   stopAutoDL5090CloneWatcher();
 */

(() => {
  "use strict";

  const GLOBAL_KEY = "__autodl5090CloneWatcherV1";
  const SUBMISSION_LOCK_KEY = "autodl-5090-clone-submission-lock-v1";
  const TARGET_GPU = "RTX 5090";
  const MIN_POLL_INTERVAL_MS = 8000;

  const sleep = (milliseconds) =>
    new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  const normalizedText = (element) =>
    (element?.innerText || element?.textContent || "")
      .replace(/\s+/g, " ")
      .trim();

  const log = (message, details) => {
    const prefix = `[AutoDL 5090 watcher ${new Date().toLocaleTimeString()}]`;
    if (details === undefined) {
      console.log(prefix, message);
    } else {
      console.log(prefix, message, details);
    }
  };

  const fail = (message) => {
    throw new Error(`[AutoDL 5090 watcher] ${message}`);
  };

  const findExactButton = (name) =>
    [...document.querySelectorAll("button")].find(
      (button) => normalizedText(button) === name
    );

  const findTargetFilter = () =>
    document.querySelector(`input[type="checkbox"][value="${TARGET_GPU}"]`);

  const targetRows = () =>
    [...document.querySelectorAll("tr")].filter((row) => {
      const text = normalizedText(row);
      return /(^|\s)RTX 5090(\s|$)/.test(text) && !text.includes("RTX 5090 D");
    });

  const availableTargetRows = () =>
    targetRows().filter((row) => {
      const radio = row.querySelector('input[type="radio"]');
      const availability = normalizedText(row).match(/(\d+)\s*\/\s*\d+/);
      return (
        radio &&
        !radio.disabled &&
        availability &&
        Number.parseInt(availability[1], 10) > 0
      );
    });

  const parseConfiguredHourlyRate = () => {
    const match = normalizedText(document.body).match(
      /配置费用：\s*￥\s*([0-9]+(?:\.[0-9]+)?)\s*\/时/
    );
    return match ? Number.parseFloat(match[1]) : null;
  };

  const pageLooksUnsafeToAutomate = () => {
    const text = normalizedText(document.body);
    return ["验证码", "滑块验证", "请登录", "登录已失效"].some((marker) =>
      text.includes(marker)
    );
  };

  const refreshTargetInventory = async () => {
    const checkbox = findTargetFilter();
    if (!checkbox) {
      fail("The exact RTX 5090 filter was not found. Stop instead of guessing selectors.");
    }

    const label = checkbox.closest("label");
    if (!label) {
      fail("The RTX 5090 filter label was not found.");
    }

    if (checkbox.checked) {
      label.click();
      await sleep(700);
    }

    label.click();
    await sleep(1400);
  };

  const selectHost = async (row) => {
    const radio = row.querySelector('input[type="radio"]');
    const label = radio?.closest("label");
    if (!radio || radio.disabled || !label) {
      fail("The selected host became unavailable before selection.");
    }

    label.click();
    await sleep(900);

    if (!radio.checked) {
      fail("AutoDL did not retain the host selection; another user may have taken it.");
    }
  };

  const submitClone = async (config, row) => {
    await selectHost(row);

    const hourlyRate = parseConfiguredHourlyRate();
    if (!Number.isFinite(hourlyRate)) {
      fail("The configured hourly rate could not be read; refusing to submit.");
    }
    if (hourlyRate > config.maxRatePerHourCny) {
      fail(
        `Configured rate CNY ${hourlyRate.toFixed(2)}/h exceeds the cap ` +
          `CNY ${config.maxRatePerHourCny.toFixed(2)}/h.`
      );
    }

    const createButton = findExactButton("创建并开机");
    if (!createButton || createButton.disabled) {
      fail("The create-and-start button is missing or disabled after host selection.");
    }

    const lock = {
      sourceInstanceId: config.sourceInstanceId,
      host: row.querySelector('input[type="radio"]')?.value || "unknown",
      hourlyRateCny: hourlyRate,
      createdAt: new Date().toISOString()
    };
    sessionStorage.setItem(SUBMISSION_LOCK_KEY, JSON.stringify(lock));
    log("Submitting one paid clone.", lock);
    createButton.click();
  };

  const validateConfig = (rawConfig) => {
    const config = {
      sourceInstanceId: String(rawConfig?.sourceInstanceId || "").trim(),
      pollIntervalMs: Number(rawConfig?.pollIntervalMs ?? 12000),
      autoSubmit: rawConfig?.autoSubmit === true,
      maxRatePerHourCny: Number(rawConfig?.maxRatePerHourCny),
      billingAcknowledged: rawConfig?.billingAcknowledged === true,
      requireDataDiskCopy: rawConfig?.requireDataDiskCopy !== false
    };

    if (!config.sourceInstanceId) {
      fail("sourceInstanceId is required.");
    }
    if (
      !Number.isFinite(config.pollIntervalMs) ||
      config.pollIntervalMs < MIN_POLL_INTERVAL_MS
    ) {
      fail(`pollIntervalMs must be at least ${MIN_POLL_INTERVAL_MS}.`);
    }
    if (config.autoSubmit) {
      if (!Number.isFinite(config.maxRatePerHourCny) || config.maxRatePerHourCny <= 0) {
        fail("Automatic submission requires a positive maxRatePerHourCny.");
      }
      if (!config.billingAcknowledged) {
        fail("Automatic submission requires billingAcknowledged: true.");
      }
    }

    const url = new URL(window.location.href);
    if (url.pathname !== "/cloneInstance") {
      fail("Open the AutoDL clone-instance page before starting the watcher.");
    }
    if (url.searchParams.get("id") !== config.sourceInstanceId) {
      fail("The clone page source instance does not match sourceInstanceId.");
    }
    if (
      config.requireDataDiskCopy &&
      url.searchParams.get("copy_data_disk") !== "1"
    ) {
      fail(
        "Data-disk copying is required, but this clone page was opened without it. " +
          "Return to the source instance and select the data disk before continuing."
      );
    }
    if (sessionStorage.getItem(SUBMISSION_LOCK_KEY)) {
      fail("A submission lock already exists in this tab; refusing a duplicate clone.");
    }

    return config;
  };

  const start = (rawConfig) => {
    if (window[GLOBAL_KEY]?.running) {
      fail("A watcher is already running in this tab.");
    }

    const config = validateConfig(rawConfig);
    const state = {
      running: true,
      timer: null,
      polls: 0,
      config
    };
    window[GLOBAL_KEY] = state;

    const stop = (reason) => {
      state.running = false;
      if (state.timer !== null) {
        window.clearTimeout(state.timer);
      }
      log(`Stopped: ${reason}`);
    };
    state.stop = stop;

    const poll = async () => {
      if (!state.running) return;
      try {
        if (pageLooksUnsafeToAutomate()) {
          stop("login or human verification is required");
          return;
        }

        state.polls += 1;
        await refreshTargetInventory();
        const candidates = availableTargetRows();
        log(`Poll ${state.polls}: ${candidates.length} available host(s).`);

        if (candidates.length > 0) {
          if (!config.autoSubmit) {
            stop("an RTX 5090 is available; automatic submission is disabled");
            return;
          }
          await submitClone(config, candidates[0]);
          stop("one clone submission was sent");
          return;
        }
      } catch (error) {
        console.error(error);
        stop(error instanceof Error ? error.message : String(error));
        return;
      }

      state.timer = window.setTimeout(poll, config.pollIntervalMs);
    };

    log("Started.", config);
    void poll();
    return state;
  };

  const stop = () => {
    const state = window[GLOBAL_KEY];
    if (state?.running && typeof state.stop === "function") {
      state.stop("manual stop");
    } else {
      log("No active watcher exists.");
    }
  };

  window.startAutoDL5090CloneWatcher = start;
  window.stopAutoDL5090CloneWatcher = stop;
  log("Loaded. Call startAutoDL5090CloneWatcher({...}) to begin.");
})();
