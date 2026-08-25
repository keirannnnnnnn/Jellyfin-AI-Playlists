// Jellyfin Smart Playlist Generator UI Scripts

async function apiPost(url, data = {}) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return await resp.json();
}

// -------------------------------------------------------------
// Manual Trigger Handler
// -------------------------------------------------------------
async function executeManualRun() {
  const btn = document.getElementById("btn-run-now");
  const progressBox = document.getElementById("run-progress-box");
  const resultText = document.getElementById("run-result-text");
  const userSelect = document.getElementById("scope-user");
  const mixSelect = document.getElementById("scope-mix");
  const forceCheckbox = document.getElementById("scope-force");

  const userId = userSelect ? userSelect.value : null;
  const mixKey = mixSelect ? mixSelect.value : null;
  const force = forceCheckbox ? forceCheckbox.checked : false;

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Running Generation...";
  }
  if (progressBox) {
    progressBox.style.display = "block";
    resultText.innerHTML = "Running generation pipeline across selected scope. Please wait...";
  }

  try {
    const resp = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_user_id: userId || null,
        target_mix_key: mixKey || null,
        force: force,
      }),
    });

    // 409 = another run is already in progress
    if (resp.status === 409) {
      const err = await resp.json();
      resultText.innerHTML = `⚠️ Run Not Started — Already In Progress\n\n${err.detail || "A generation run is already running. Wait for it to finish."}`;
      return;
    }

    const res = await resp.json();
    if (res.status === "completed" || res.status === "partial") {
      resultText.innerHTML = `✅ Run Completed (${res.status})!\n` +
        `Generated: ${res.summary.generated} playlists\n` +
        `Skipped (No Activity): ${res.summary.skipped_no_activity}\n` +
        `Skipped (Thin Pool): ${res.summary.skipped_thin_pool}\n` +
        `Errors: ${res.summary.errors}`;
    } else {
      resultText.innerHTML = `❌ Run Failed: ${res.error || "Check logs for details"}\n${JSON.stringify(res.summary || {}, null, 2)}`;
    }
  } catch (err) {
    if (resultText) {
      resultText.innerHTML = `❌ Error executing run: ${err.message}`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "🚀 Trigger Smart Playlist Run";
    }
  }
}

// -------------------------------------------------------------
// Push Icons to Jellyfin
// -------------------------------------------------------------
async function pushIconsNow() {
  const btn = document.getElementById("btn-push-icons");
  const out = document.getElementById("push-icons-result");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Pushing Icons...";
  }
  if (out) {
    out.style.display = "block";
    out.innerText = "Connecting to Jellyfin and pushing mix icons to all user playlists...";
  }

  try {
    const res = await apiPost("/api/playlists/push-icons");
    if (out) {
      out.innerText = `Icons pushed successfully!\nUpdated: ${res.total_updated}\nErrors: ${res.errors}\n\nDetails:\n${res.details.join("\n") || "No existing user playlists matched mix names."}`;
    }
  } catch (err) {
    if (out) out.innerText = `Error pushing icons: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "🎨 Push Icons to All Jellyfin Playlists";
    }
  }
}

// -------------------------------------------------------------
// Fix Playlist Access — retroactively set IsPublic=false
// -------------------------------------------------------------
async function fixPlaylistAccess() {
  const btn = document.getElementById("btn-fix-access");
  const out = document.getElementById("fix-access-result");

  if (!confirm(
    "This will call POST /Playlists/{id} for every playlist tracked in the DB and set IsPublic=false.\n\n" +
    "Safe to run multiple times. Proceed?"
  )) return;

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "⏳ Fixing Access...";
  }
  if (out) {
    out.style.display = "block";
    out.innerText = "Patching all tracked playlists to IsPublic=false. Please wait...";
  }

  try {
    const res = await apiPost("/api/playlists/fix-access");
    if (out) {
      const summary = `Access fix complete!\n✅ Fixed: ${res.total_fixed}  ⚠️ Not found: ${res.already_gone}  ❌ Errors: ${res.errors}`;
      const details = res.details.length > 0 ? `\n\nDetails:\n${res.details.join("\n")}` : "";
      out.innerText = summary + details;
    }
  } catch (err) {
    if (out) out.innerText = `Error fixing playlist access: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "🔒 Fix Playlist Access (Close Public Visibility)";
    }
  }
}

// -------------------------------------------------------------
// Test Connections
// -------------------------------------------------------------
async function testJellyfinConnection() {
  const btn = document.getElementById("btn-test-jellyfin");
  const out = document.getElementById("jellyfin-test-output");
  const url = document.getElementById("jellyfin_url").value;
  const key = document.getElementById("jellyfin_api_key").value;
  const dbPath = document.getElementById("playback_db_path").value;

  if (btn) {
    btn.disabled = true;
    btn.innerText = "Testing...";
  }
  if (out) {
    out.style.display = "block";
    out.innerText = "Contacting Jellyfin Server...";
  }

  try {
    const res = await apiPost("/api/test/jellyfin", {
      url: url,
      api_key: key,
      playback_db_path: dbPath,
    });
    if (out) {
      if (res.connected) {
        out.innerHTML = `✅ Successfully connected to Jellyfin!\n` +
          `Server Name: ${res.server_name}\n` +
          `Server Version: ${res.version}\n` +
          `Users found: ${res.user_count}\n` +
          `Audio tracks in library: ${res.audio_count}\n` +
          `Playback Reporting Plugin: ${res.playback_reporting_available ? "Active (" + res.playback_reporting_mode + ")" : "Not Detected (Using UserData fallback)"}`;
      } else {
        out.innerHTML = `❌ Connection Failed:\n${res.error || "Unable to reach server"}`;
      }
    }
  } catch (err) {
    if (out) out.innerText = `❌ Error: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = "Test Jellyfin Connection";
    }
  }
}

async function testGeminiConnection() {
  const btn = document.getElementById("btn-test-gemini");
  const out = document.getElementById("gemini-test-output");
  const key = document.getElementById("gemini_api_key").value;
  const model = document.getElementById("gemini_model").value;

  if (btn) {
    btn.disabled = true;
    btn.innerText = "Testing...";
  }
  if (out) {
    out.style.display = "block";
    out.innerText = `Connecting to Google Gemini API (${model})...`;
  }

  try {
    const res = await apiPost("/api/test/gemini", {
      api_key: key,
      model: model,
    });
    if (out) {
      if (res.success) {
        out.innerHTML = `✅ Gemini API is Working!\nModel: ${res.model}\nStatus: OK`;
      } else {
        out.innerHTML = `❌ Gemini API Error:\n${res.error}\n\nTips to fix:\n- Ensure your API key has Generative Language API enabled.\n- Check quota/billing or try model 'gemini-1.5-flash'.`;
      }
    }
  } catch (err) {
    if (out) out.innerText = `❌ Error: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = "Test Gemini API";
    }
  }
}

// -------------------------------------------------------------
// User Toggle
// -------------------------------------------------------------
async function toggleUserEnabled(userId, enabled) {
  try {
    await apiPost("/api/users/toggle", { user_id: userId, enabled: enabled });
  } catch (e) {
    alert("Failed to toggle user: " + e.message);
  }
}

// -------------------------------------------------------------
// Mix Icon Upload
// -------------------------------------------------------------
async function handleIconUpload(mixKey, fileInput) {
  if (!fileInput.files || fileInput.files.length === 0) return;
  const file = fileInput.files[0];
  const formData = new FormData();
  formData.append("icon_file", file);

  try {
    const resp = await fetch(`/api/mixes/${mixKey}/icon`, {
      method: "POST",
      body: formData,
    });
    const res = await resp.json();
    if (resp.ok) {
      window.location.reload();
    } else {
      alert("Error uploading icon: " + (res.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Upload error: " + err.message);
  }
}

// -------------------------------------------------------------
// Modal Management for Mix Editing
// -------------------------------------------------------------
let currentEditMixKey = null;

function openEditMixModal(mixKey, displayName, mixType, configJson) {
  currentEditMixKey = mixKey;
  document.getElementById("modal-mix-key").value = mixKey;
  document.getElementById("modal-display-name").value = displayName;
  document.getElementById("modal-mix-type").value = mixType;
  document.getElementById("modal-config-json").value = JSON.stringify(configJson, null, 2);
  document.getElementById("mix-modal").style.display = "flex";
}

function closeMixModal() {
  document.getElementById("mix-modal").style.display = "none";
}

async function saveMixFromModal() {
  const mixKey = document.getElementById("modal-mix-key").value;
  const displayName = document.getElementById("modal-display-name").value;
  const mixType = document.getElementById("modal-mix-type").value;
  const configStr = document.getElementById("modal-config-json").value;

  try {
    const configObj = JSON.parse(configStr);
    const res = await apiPost("/api/mixes/update", {
      mix_key: mixKey,
      display_name: displayName,
      type: mixType,
      config: configObj,
    });
    if (res.success) {
      window.location.reload();
    } else {
      alert("Error saving mix: " + (res.error || "Unknown"));
    }
  } catch (err) {
    alert("Invalid JSON in mix configuration: " + err.message);
  }
}
