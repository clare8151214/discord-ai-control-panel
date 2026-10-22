/* Relay control panel. Credentials are never stored in browser storage. */
'use strict';

const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const state = { profiles: [], selectedId: null, draft: null, runtime: new Map(), discovered: new Map(), dirty: false, loading: false, authenticated: false, desktop: false, discordVerified: false, saving: false, action: false, activeTab: 'connection', toastTimer: null, imageCheck: null, imageCheckRevision: 0 };
const statusNames = { stopped: '未啟動', starting: '啟動中', online: '已連線', running: '執行中', failed: '啟動失敗' };
const providerPresets = {
  ollama: { name: '本地 Ollama', base_url: 'http://host.docker.internal:11434', subtitle: 'OLLAMA', help: 'Ollama 填根網址，不需加 /api。直接在主機執行時也可用 http://127.0.0.1:11434。' },
  openai: { name: 'OpenAI 相容 API', base_url: 'https://api.openai.com/v1', subtitle: 'OPENAI COMPATIBLE', help: '使用 OpenAI 相容 API 的 Base URL，通常需包含 /v1。也支援 Grok、vLLM 等相容服務。' },
  gemini: { name: 'Google Gemini', base_url: 'https://generativelanguage.googleapis.com/v1beta', subtitle: 'GEMINI NATIVE', help: 'Gemini 原生 API，填入 https://generativelanguage.googleapis.com/v1beta，並設定 Google API 金鑰。' },
  anthropic: { name: 'Claude · Anthropic', base_url: 'https://api.anthropic.com/v1', subtitle: 'ANTHROPIC NATIVE', help: 'Claude 原生 Messages API：填入 Anthropic API 金鑰，網址使用 https://api.anthropic.com/v1。此服務使用 Anthropic 原生協定。' }
};
const connectionPresets = {
  local: { type: 'ollama', name: '這台電腦的 Ollama', base_url: 'http://127.0.0.1:11434' },
  remote: { type: 'ollama', name: '遠端 Ollama', base_url: '' },
  openai: { type: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1' },
  xai: { type: 'openai', name: 'Grok · xAI', base_url: 'https://api.x.ai/v1' },
  deepseek: { type: 'openai', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1' },
  gemini: { type: 'gemini', name: 'Google Gemini', base_url: 'https://generativelanguage.googleapis.com/v1beta' },
  claude: { type: 'anthropic', name: 'Claude · Anthropic', base_url: 'https://api.anthropic.com/v1', send_temperature: false }
};

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function text(element, value) { if (element) element.textContent = String(value ?? ''); }
function toast(message, error = false) {
  const element = $('#toast');
  text(element, message); element.classList.toggle('error', error); element.hidden = false;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { element.hidden = true; }, error ? 7000 : 4000);
}
function errorMessage(error) { return error?.message || '操作失敗，請稍後再試。'; }
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { 'X-Control-Panel': '1', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== '/api/login') { showLogin(); throw new Error('登入已過期，請重新登入。'); }
  if (!response.ok) {
    let detail = data.detail || data.error || `伺服器回應 ${response.status}`;
    if (Array.isArray(detail)) detail = detail.map(item => `${(item.loc || []).join('.')}: ${item.msg || ''}`).join('；');
    if (typeof detail !== 'string') detail = JSON.stringify(detail);
    throw new Error(detail);
  }
  return data;
}
function post(path, payload) { return api(path, { method: 'POST', ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) }); }
function newProvider(type = 'ollama', index = 1) { const preset = providerPresets[type]; return { id: index === 1 ? type : `${type}-${index}`, name: preset.name, type, base_url: type === 'ollama' && state.desktop ? connectionPresets.local.base_url : preset.base_url, api_key: '', has_api_key: false, models: [], token_parameter: 'max_tokens', send_temperature: type !== 'anthropic' }; }
function newProfile() {
  return { name: '新的機器人', discord_token: '', has_discord_token: false, application_id: '', guild_id: '', message_content_intent: true, admin_only_settings: true, providers: [newProvider()], default_provider: 'ollama', default_model: '', system_prompt: '', context_length: 8192, output_tokens: 768, retention_days: 7, compact_trigger: 0.7, image_service_enabled: false, comfyui_url: '', comfyui_checkpoint: 'NoobAI-XL-v1.1.safetensors', ipadapter_model: 'ip_adapter_Noobtest_800000.bin', clip_vision_model: 'CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors', tts_voice: 'zh-TW-HsiaoChenNeural' };
}
function normalizedProfile(profile) {
  const result = { ...newProfile(), ...clone(profile), discord_token: '' };
  result.image_service_enabled = typeof profile.image_service_enabled === 'boolean' ? profile.image_service_enabled : Boolean(profile.comfyui_url?.trim());
  result.providers = (profile.providers || []).map(provider => ({ ...newProvider(provider.type in providerPresets ? provider.type : 'ollama'), ...provider, api_key: '', models: Array.isArray(provider.models) ? provider.models : [] }));
  return result;
}
function showLogin() {
  state.authenticated = false; state.draft = null; state.selectedId = null; state.dirty = false; state.profiles = []; state.runtime.clear(); state.discovered.clear(); state.desktop = false; state.discordVerified = false;
  $('#desktop-data').hidden = true; text($('#desktop-data-path'), '');
  $('#profile-form').reset(); $('#provider-list').replaceChildren();
  $('#app-screen').hidden = true; $('#login-screen').hidden = false; $('#login-password').value = ''; $('#login-password').focus();
}
function setDirty(value = true) { state.dirty = value; const label = $('#save-state'); text(label, value ? '有尚未儲存的變更' : '所有設定已儲存'); label.classList.toggle('dirty', value); updateRuntime(); updateSetupGuide(); }
function canLeaveDraft() { return !state.dirty || window.confirm('有尚未儲存的設定，確定放棄這些變更嗎？'); }
function setTab(name) {
  state.activeTab = name;
  $$('.tab').forEach(button => { const active = button.dataset.tab === name; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); });
  $$('.tab-panel').forEach(panel => { panel.hidden = panel.dataset.panel !== name; });
  if (name === 'logs') refreshLogs().catch(error => toast(errorMessage(error), true));
}
function renderProfiles() {
  const list = $('#profile-list'); list.replaceChildren(); text($('#profile-count'), state.profiles.length);
  const items = state.draft && !state.draft.id ? [...state.profiles, { name: state.draft.name || '新的機器人', id: null }] : state.profiles;
  items.forEach(profile => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'profile-item';
    const active = profile.id === state.selectedId; button.classList.toggle('active', active); button.setAttribute('aria-current', active ? 'page' : 'false');
    const avatar = document.createElement('span'); avatar.className = 'profile-avatar'; text(avatar, (profile.name || 'B').slice(0, 1).toUpperCase());
    const name = document.createElement('span'); name.className = 'profile-name'; text(name, profile.name); button.title = profile.name;
    const status = document.createElement('span'); status.className = `profile-status status-dot ${state.runtime.get(profile.id)?.status || 'stopped'}`; status.title = statusNames[state.runtime.get(profile.id)?.status] || '未啟動';
    button.append(avatar, name, status); button.addEventListener('click', () => { if (active || !canLeaveDraft()) return; selectProfile(profile.id); }); list.append(button);
  });
}
function selectProfile(id) {
  const profile = state.profiles.find(item => item.id === id);
  state.selectedId = profile?.id || null; state.draft = profile ? normalizedProfile(profile) : newProfile(); state.discovered.clear(); state.discordVerified = false; state.imageCheck = null; state.imageCheckRevision++;
  renderForm(); setDirty(!profile); renderProfiles(); setTab('connection');
}
function renderForm() {
  const form = $('#profile-form'); const draft = state.draft;
  state.imageCheck = null; state.imageCheckRevision++; $('#image-check-result').hidden = true; $('#image-check-result').classList.remove('success', 'error'); $('#image-check-list').replaceChildren(); $('#check-image-service').disabled = false;
  text($('#image-check-caption'), '可先儲存設定；啟動生圖 Bot 前，必須通過依賴檢查。');
  for (const [key, value] of Object.entries(draft)) {
    const input = form.elements.namedItem(key); if (!input || key === 'default_provider') continue;
    if (input.type === 'checkbox') input.checked = Boolean(value); else input.value = typeof value === 'object' ? '' : value ?? '';
  }
  const tokenInput = form.elements.namedItem('discord_token'); tokenInput.type = 'password'; tokenInput.placeholder = draft.has_discord_token ? '已儲存 Token · 留空保留原值' : '貼上 Discord Bot Token';
  text($('#token-help'), draft.has_discord_token ? '✓ Token 已安全儲存。留空保留；輸入新值會更新。' : 'Discord Developer Portal → Bot → Reset Token。');
  $$('.reveal-secret', form).forEach(button => text(button, '顯示'));
  text($('#page-title'), draft.name || '新的機器人'); text($('#breadcrumb-name'), draft.name || '新的機器人');
  text($('#page-description'), draft.id ? '管理 Discord 連線、模型與對話體驗。' : '連接模型，設定個性，讓機器人加入你的社群。');
  $('#chat-test-result').hidden = true; text($('#discord-verify-result'), '貼上 Token 後可自動取得 Application ID，不用手動查找。'); $('#discord-verify-result').classList.remove('error');
  renderProviders(); renderDefaultProvider(); updateStats(); updateRuntime(); updateDiscordInvite(); updateSetupGuide(); updateImageSettings();
}
const imageSettingFields = ['comfyui_url', 'comfyui_checkpoint', 'ipadapter_model', 'clip_vision_model'];
function updateImageSettings() {
  const enabled = state.draft?.image_service_enabled === true;
  $('#image-service-config').hidden = !enabled; $('#image-disabled-note').hidden = enabled; $('#image-start-guide').hidden = !enabled;
  text($('#image-start-status'), state.imageCheck?.ok ? '✓ 本次生圖依賴檢查通過；啟動時會再次確認。' : state.imageCheck ? '生圖依賴尚未通過，請查看各項結果並補齊。' : '已啟用生圖，啟動前會檢查 ComfyUI 與角色模型。');
}
function invalidateImageCheck() {
  state.imageCheck = null; state.imageCheckRevision++; $('#image-check-result').hidden = true;
  $('#image-check-result').classList.remove('success', 'error'); $('#image-check-list').replaceChildren(); text($('#image-check-summary'), '');
  text($('#image-check-caption'), '設定已變更，請重新檢查；仍可先儲存設定。'); $('#check-image-service').disabled = false; updateImageSettings();
}
async function checkImageService() {
  readFields(); if (!state.draft?.image_service_enabled) return;
  const draft = state.draft; const revision = ++state.imageCheckRevision; state.imageCheck = null;
  const payload = Object.fromEntries(imageSettingFields.map(key => [key, draft[key]]));
  const button = $('#check-image-service'); const result = $('#image-check-result'); button.disabled = true; result.hidden = false; result.classList.remove('success', 'error'); $('#image-check-list').replaceChildren();
  text($('#image-check-summary'), '正在檢查 ComfyUI、節點與模型檔案…'); text($('#image-check-caption'), '檢查會查詢 API 所在的電腦，不會下載模型。'); updateImageSettings();
  try {
    const data = await post('/api/image/check', payload);
    if (state.draft !== draft || state.imageCheckRevision !== revision) return;
    const checks = Array.isArray(data.checks) ? data.checks : [];
    const passed = data.ok === true && checks.length > 0 && checks.every(check => check.ok === true);
    state.imageCheck = { ok: passed }; result.classList.add(passed ? 'success' : 'error');
    text($('#image-check-summary'), passed ? '✓ 安裝檢查通過。' : data.error || '⚠ 生圖安裝檢查尚未通過，請補齊下列依賴後重新檢查。');
    for (const check of checks) {
      const item = document.createElement('li'); item.className = check.ok === true ? 'passed' : 'missing';
      const label = document.createElement('strong'); text(label, `${check.ok === true ? '✓' : '✕'} ${check.label || check.key || '依賴項目'}`); item.append(label);
      const detail = document.createElement('span'); text(detail, check.ok === true ? '已確認' : check.error || '缺少或無法確認'); item.append(detail); $('#image-check-list').append(item);
    }
    if (!checks.length) { text($('#image-check-summary'), data.error || '未取得依賴檢查結果，請確認 ComfyUI API 後重新檢查。'); }
    text($('#image-check-caption'), passed ? '檢查通過；啟動時會由服務端重新確認，設定可隨時儲存。' : '設定仍可儲存；依賴通過檢查後才能啟動已開啟生圖的 Bot。'); updateImageSettings();
  } catch (error) {
    if (state.draft !== draft || state.imageCheckRevision !== revision) return;
    state.imageCheck = { ok: false }; result.classList.add('error'); text($('#image-check-summary'), `檢查失敗：${errorMessage(error)}`); text($('#image-check-caption'), '請確認 API 網址、ComfyUI 是否啟動，以及工作台能否連線到該電腦。'); updateImageSettings();
  } finally { if (state.draft === draft && state.imageCheckRevision === revision) button.disabled = false; }
}
function normalizeProviderUrl(provider) {
  const value = provider.base_url.trim();
  if (provider.type !== 'ollama' || !value || /^https?:\/\//i.test(value)) return value;
  try { const url = new URL(`http://${value}`); if (!url.port) url.port = '11434'; return url.toString().replace(/\/$/, ''); }
  catch { return value; }
}
function updateDiscordInvite() {
  const id = state.draft?.application_id?.trim() || ''; const link = $('#invite-bot');
  link.hidden = !/^\d{15,22}$/.test(id);
  if (!link.hidden) link.href = `https://discord.com/oauth2/authorize?${new URLSearchParams({ client_id: id, permissions: '3263488', scope: 'bot applications.commands' })}`;
}
function updateSetupGuide() {
  if (!state.draft) return;
  const draft = state.draft; const provider = draft.providers.find(item => item.id === draft.default_provider);
  const tokenReady = Boolean(draft.discord_token.trim() || draft.has_discord_token);
  const discovered = state.discovered.get(provider?.id);
  const modelReady = Boolean(draft.default_model.trim());
  const running = ['online', 'running'].includes(state.runtime.get(state.selectedId)?.status);
  const statuses = {
    discord: [tokenReady, state.discordVerified ? '✓ Discord 身分已驗證' : tokenReady ? 'Token 已填妥 · 可驗證並取得邀請連結' : '驗證身分，取得邀請連結'],
    provider: [Boolean(discovered?.length), discovered ? discovered.length ? `✓ 已連線 · ${discovered.length} 個可用模型` : '服務已回應，但尚無可用模型' : '選擇服務後，按「測試連線」'],
    launch: [Boolean(running), running ? '✓ Bot 已連線，可以開始對話' : modelReady ? '模型已選好 · 可以試聊並儲存啟動' : '測試連線後，選擇一個模型']
  };
  for (const [key, [done, caption]] of Object.entries(statuses)) {
    const button = $(`[data-setup-step="${key}"]`); button.classList.toggle('complete', done);
    text($(`#setup-${key}-status`), caption); text($('.setup-check', button), done ? '✓' : '↗');
  }
  text($('#setup-caption'), state.desktop ? '直接設定這台電腦的 Bot，不用安裝 Python 或修改設定檔。' : '不用編輯設定檔，完成三步就能開始。');
}
async function verifyDiscord() {
  readFields(); const draft = state.draft; const token = draft.discord_token;
  if (!token.trim() && !draft.has_discord_token) { toast('請先貼上 Discord Bot Token。', true); $('[name="discord_token"]').focus(); return; }
  const button = $('#verify-discord'); const result = $('#discord-verify-result'); button.disabled = true; result.classList.remove('error'); text(result, '正在向 Discord 驗證機器人身分…');
  try {
    const data = await post('/api/discord/verify', { discord_token: token, ...(state.selectedId ? { profile_id: state.selectedId } : {}) });
    if (state.draft !== draft || state.draft.discord_token !== token) return;
    if (!data.ok) throw new Error(data.error || 'Discord Token 驗證失敗。');
    if (!/^\d{15,22}$/.test(String(data.application_id || ''))) throw new Error('Discord 未傳回有效的 Application ID，請稍後重試。');
    draft.application_id = String(data.application_id); $('[name="application_id"]').value = draft.application_id; state.discordVerified = true;
    text(result, `✓ ${data.username || 'Bot'} 驗證成功，Application ID 已自動填入。可點右側連結邀請 Bot。`); updateDiscordInvite(); setDirty();
  } catch (error) { if (state.draft === draft && state.draft.discord_token === token) { text(result, errorMessage(error)); result.classList.add('error'); } }
  finally { button.disabled = false; }
}
function readFields() {
  if (!state.draft) return;
  for (const input of $$('[name]', $('#profile-form'))) {
    if (!(input.name in state.draft)) continue;
    state.draft[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
  }
}
function renderProviders() {
  const list = $('#provider-list'); list.replaceChildren();
  state.draft.providers.forEach((provider, index) => {
    const card = $('#provider-template').content.firstElementChild.cloneNode(true); card.dataset.providerIndex = String(index);
    for (const input of $$('[data-key]', card)) {
      const key = input.dataset.key;
      if (input.type === 'checkbox') input.checked = Boolean(provider[key]);
      else input.value = key === 'models' ? provider.models.join('\n') : provider[key] ?? '';
      input.addEventListener('input', () => {
        const oldId = provider.id;
        provider[key] = input.type === 'checkbox' ? input.checked : key === 'models' ? input.value.split(/[,\n]/).map(item => item.trim()).filter(Boolean) : input.value;
        if (key === 'id' && state.draft.default_provider === oldId) state.draft.default_provider = provider.id;
        if (['id', 'base_url', 'api_key', 'type'].includes(key)) { state.discovered.delete(oldId); state.discovered.delete(provider.id); resetProbe(card); }
        if (key === 'api_key' && input.value) { provider.clear_api_key = false; $('[data-clear-key]', card).checked = false; }
        if (['name', 'id', 'base_url', 'type', 'api_key'].includes(key)) updateProviderAppearance(card, provider);
        renderDefaultProvider(); updateStats(); setDirty();
      });
      if (key === 'type') input.addEventListener('change', () => {
        const preset = providerPresets[provider.type];
        provider.send_temperature = provider.type !== 'anthropic'; $('[data-key="send_temperature"]', card).checked = provider.send_temperature;
        if (Object.values(providerPresets).some(item => item.base_url === provider.base_url) || !provider.base_url) provider.base_url = preset.base_url;
        if (Object.values(providerPresets).some(item => item.name === provider.name) || !provider.name) provider.name = preset.name;
        $('[data-key="base_url"]', card).value = provider.base_url; $('[data-key="name"]', card).value = provider.name;
        updateProviderAppearance(card, provider); renderDefaultProvider(); setDirty();
      });
      if (key === 'base_url') input.addEventListener('change', () => { provider.base_url = normalizeProviderUrl(provider); input.value = provider.base_url; updateProviderAppearance(card, provider); setDirty(); });
    }
    $('.provider-preset', card).addEventListener('change', event => {
      const preset = connectionPresets[event.target.value]; if (!preset) return;
      const remote = event.target.value === 'remote'; const changed = provider.base_url !== preset.base_url || provider.type !== preset.type;
      Object.assign(provider, { send_temperature: preset.type !== 'anthropic' }, preset);
      $('[data-key="send_temperature"]', card).checked = provider.send_temperature;
      if (changed) { provider.api_key = ''; provider.models = []; state.discovered.delete(provider.id); resetProbe(card); if (state.draft.default_provider === provider.id) { state.draft.default_model = ''; $('#default-model').value = ''; } }
      for (const key of ['name', 'type', 'base_url', 'api_key', 'models']) $('[data-key="' + key + '"]', card).value = key === 'models' ? provider.models.join('\n') : provider[key];
      $('[data-key="base_url"]', card).placeholder = remote ? '192.168.1.100 或 http://主機IP:11434' : preset.base_url;
      updateProviderAppearance(card, provider); renderDefaultProvider(); updateStats(); setDirty();
      $('[data-key="' + (remote || preset.type === 'ollama' ? 'base_url' : 'api_key') + '"]', card).focus();
    });
    updateProviderAppearance(card, provider);
    $('[data-clear-key]', card).checked = Boolean(provider.clear_api_key);
    $('[data-clear-key]', card).addEventListener('change', event => { provider.clear_api_key = event.target.checked; if (provider.clear_api_key) { provider.api_key = ''; $('[data-key="api_key"]', card).value = ''; } state.discovered.delete(provider.id); resetProbe(card); updateProviderAppearance(card, provider); renderModelOptions(); setDirty(); });
    $('.remove-provider', card).addEventListener('click', () => {
      if (!window.confirm(`移除「${provider.name || provider.id}」供應商設定？`)) return;
      state.draft.providers.splice(index, 1); state.discovered.delete(provider.id);
      if (state.draft.default_provider === provider.id) state.draft.default_provider = state.draft.providers[0]?.id || '';
      renderProviders(); renderDefaultProvider(); updateStats(); setDirty();
    });
    $('.probe-provider', card).addEventListener('click', () => probeProvider(provider, card));
    list.append(card);
  });
}
function updateProviderAppearance(card, provider) {
  const preset = providerPresets[provider.type] || providerPresets.ollama;
  $('.provider-advanced', card).hidden = provider.type !== 'openai';
  text($('.provider-title', card), provider.name || '模型供應商'); text($('.provider-subtitle', card), preset.subtitle); text($('.base-url-help', card), provider.type === 'ollama' ? '可直接輸入 IP（例如 192.168.1.100），會自動補上 http:// 與 11434；也可以填完整 API 網址，不需加 /api。' : preset.help);
  const quickPreset = Object.entries(connectionPresets).find(([key, item]) => key !== 'remote' && item.type === provider.type && item.base_url === provider.base_url)?.[0];
  $('.provider-preset', card).value = quickPreset || (provider.type === 'ollama' && provider.name === connectionPresets.remote.name ? 'remote' : 'custom');
  const saved = state.profiles.find(item => item.id === state.selectedId)?.providers.find(item => item.id === provider.id);
  const retained = provider.has_api_key && saved?.type === provider.type && saved?.base_url === provider.base_url && !provider.clear_api_key;
  const input = $('[data-key="api_key"]', card); input.placeholder = retained ? '已儲存金鑰 · 留空保留原值' : provider.type === 'ollama' ? '本地 Ollama 通常不需要' : '填入供應商 API 金鑰';
  text($('.api-key-help', card), retained ? '✓ 已安全儲存。留空保留；輸入新值會更新。' : provider.clear_api_key ? '儲存後會清除已存金鑰。' : provider.has_api_key ? '供應商 ID、類型或網址已變更；如需認證，請重新輸入金鑰。' : '金鑰僅保存在伺服器端。');
  $('.clear-key', card).hidden = !provider.has_api_key;
  const found = state.discovered.get(provider.id); if (found) { text($('.probe-status', card), found.length ? '連線成功' : '尚無模型'); $('.probe-status', card).classList.toggle('success', found.length > 0); text($('.provider-result', card), found.length ? `取得 ${found.length} 個可用模型` : '服務已回應，但尚無可用模型；請先安裝或設定模型。'); }
}
function resetProbe(card) { const element = $('.probe-status', card); text(element, '尚未測試'); element.className = 'probe-status'; text($('.provider-result', card), '連線設定已變更，請重新測試。'); $('.provider-result', card).classList.remove('error'); }
function renderDefaultProvider() {
  const select = $('#default-provider'); const current = state.draft.default_provider; select.replaceChildren();
  if (!state.draft.providers.length) { const option = document.createElement('option'); option.value = ''; text(option, '請先新增供應商'); select.append(option); }
  state.draft.providers.forEach(provider => { const option = document.createElement('option'); option.value = provider.id; text(option, `${provider.name || '未命名'} (${provider.id})`); select.append(option); });
  select.value = current;
  if (select.selectedIndex === -1 && state.draft.providers.length) { state.draft.default_provider = state.draft.providers[0].id; select.value = state.draft.default_provider; }
  renderModelOptions();
}
function renderModelOptions() {
  const provider = state.draft?.providers.find(item => item.id === state.draft.default_provider); const list = $('#available-models'); list.replaceChildren();
  const discovered = (state.discovered.get(provider?.id) || []).map(item => typeof item === 'string' ? item : item.id);
  for (const id of [...new Set([...(provider?.models || []), ...discovered])]) { const option = document.createElement('option'); option.value = id; list.append(option); }
}
function updateStats() { text($('#model-stat'), state.draft?.default_model || '尚未選擇'); text($('#provider-stat'), `${state.draft?.providers.length || 0} 個供應商`); }
async function probeProvider(provider, card) {
  provider.base_url = normalizeProviderUrl(provider); $('[data-key="base_url"]', card).value = provider.base_url;
  const draft = state.draft; const snapshot = JSON.stringify(provider);
  const button = $('.probe-provider', card), status = $('.probe-status', card), result = $('.provider-result', card);
  button.disabled = true; text(status, '測試中…'); status.className = 'probe-status'; text(result, '正在連線模型服務…'); result.classList.remove('error');
  try {
    const data = await post('/api/probe', { provider: clone(provider), ...(state.selectedId ? { profile_id: state.selectedId } : {}) });
    if (state.draft !== draft || JSON.stringify(provider) !== snapshot) return;
    if (!data.ok) throw new Error(data.error || '無法連線到模型供應商。');
    const models = Array.isArray(data.models) ? data.models : []; state.discovered.set(provider.id, models);
    text(status, models.length ? '連線成功' : '尚無模型'); status.classList.toggle('success', models.length > 0); text(result, models.length ? `連線正常 · ${models.length} 個可用模型` : '服務已回應，但尚無可用模型；請先安裝或設定模型。'); renderModelOptions();
    if (!state.draft.default_model && provider.id === state.draft.default_provider && models.length) {
      const first = typeof models[0] === 'string' ? models[0] : models[0].id;
      state.draft.default_model = first; $('#default-model').value = first; setDirty(); updateStats();
    }
    toast(models.length ? `「${provider.name}」連線成功` : '服務已連線，但未找到可用模型。'); updateSetupGuide();
  } catch (error) { if (state.draft === draft && JSON.stringify(provider) === snapshot) { state.discovered.delete(provider.id); text(status, '連線失敗'); status.classList.add('error'); text(result, errorMessage(error)); result.classList.add('error'); renderModelOptions(); updateSetupGuide(); } }
  finally { button.disabled = false; }
}
function updateRuntime() {
  const runtime = state.runtime.get(state.selectedId) || { status: 'stopped' }; const status = runtime.status || 'stopped';
  const running = ['starting', 'online', 'running'].includes(status);
  text($('#runtime-status'), statusNames[status] || status); text($('#runtime-detail-status'), statusNames[status] || status);
  $('#runtime-dot').className = `status-dot ${status}`; text($('#runtime-pid'), runtime.pid ?? '—'); text($('#runtime-exit'), runtime.exit_code ?? '—');
  $('#start-bot').disabled = !state.selectedId || running || state.action || state.dirty;
  $('#save-start-bot').disabled = running || state.action || state.saving;
  $('#stop-bot').disabled = !state.selectedId || !running || state.action;
  $('#restart-bot').disabled = !state.selectedId || state.action || state.dirty;
  $('#delete-profile').disabled = !state.selectedId || running || state.action;
  $('#restart-notice').hidden = !runtime.config_changed || !running;
  text($('#runtime-help'), !state.selectedId ? '填好 Token 與模型後，可直接「儲存並啟動」。' : state.dirty ? '設定有變更；可儲存後重新啟動。' : '新設定不會自動啟動機器人。');
  updateSetupGuide();
}
function renderLogs(lines) {
  const element = $('#runtime-logs'); text(element, Array.isArray(lines) && lines.length ? lines.join('\n') : '尚無執行紀錄。儲存設定後啟動機器人。');
  if ($('#log-autoscroll').checked) element.scrollTop = element.scrollHeight;
  text($('#log-updated'), `更新於 ${new Date().toLocaleTimeString('zh-TW', { hour12: false })}`);
}
async function refreshLogs() { if (!state.selectedId || !state.authenticated) { renderLogs([]); return; } const id = state.selectedId; const data = await api(`/api/profiles/${encodeURIComponent(id)}/logs`); if (id === state.selectedId) renderLogs(data.logs); }
async function refreshRuntime() {
  if (!state.authenticated || state.loading) return;
  state.loading = true;
  try { const data = await api('/api/runtime'); state.runtime = new Map((data.bots || []).map(bot => [bot.id, bot])); updateRuntime(); renderProfiles(); if (state.activeTab === 'logs') await refreshLogs(); }
  finally { state.loading = false; }
}
async function loadWorkspace() {
  const [data, session] = await Promise.all([api('/api/profiles'), api('/api/session')]); state.profiles = data.profiles || []; state.authenticated = true;
  state.desktop = session.desktop === true; document.body.classList.toggle('desktop-mode', state.desktop);
  $('#logout').hidden = state.desktop; text($('#deployment-mode'), state.desktop ? '本機桌面模式' : 'SELF-HOSTED');
  $('#container-network-tip').hidden = state.desktop; $('#desktop-network-tip').hidden = !state.desktop; $('#desktop-runtime-note').hidden = !state.desktop;
  text($('#storage-note'), state.desktop ? '設定保存在這台電腦的使用者資料夾' : '設定留在你的基礎設施中');
  $('#import-legacy').disabled = session.legacy_import_allowed === false;
  $('#import-legacy').title = session.legacy_import_allowed === false ? '在部署環境設定 CONTROL_PANEL_ALLOW_LEGACY_IMPORT=true 後可使用。' : '匯入此部署主機原本的 .env，建立新機器人設定。';
  $('#login-screen').hidden = true; $('#app-screen').hidden = false;
  selectProfile(state.profiles[0]?.id || null); await refreshRuntime();
  $('#desktop-data').hidden = true;
  if (state.desktop) {
    try { const info = await api('/api/desktop-info'); if (state.desktop && info.desktop && info.data_dir) { text($('#desktop-data-path'), info.data_dir); text($('#desktop-version'), info.version ? `Relay ${info.version}` : ''); $('#desktop-data').hidden = false; } }
    catch { /* Optional desktop metadata does not prevent configuring a bot. */ }
  }
}
function validateDraft() {
  readFields(); const draft = state.draft;
  if (!draft.name.trim()) return { error: '請填寫機器人名稱。', tab: 'connection', field: '[name="name"]' };
  if (!draft.discord_token.trim() && !draft.has_discord_token) return { error: '請填入 Discord Bot Token。', tab: 'connection', field: '[name="discord_token"]' };
  if (!draft.providers.length) return { error: '至少需要一個模型供應商。', tab: 'connection' };
  const ids = new Set();
  for (const provider of draft.providers) {
    provider.base_url = normalizeProviderUrl(provider);
    const card = $(`[data-provider-index="${draft.providers.indexOf(provider)}"]`); if (card) $('[data-key="base_url"]', card).value = provider.base_url;
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(provider.id)) return { error: '供應商 ID 須以小寫字母開頭，僅含小寫字母、數字與連字號，最多 32 字元。', tab: 'connection' };
    if (ids.has(provider.id)) return { error: `供應商 ID「${provider.id}」重複，請使用不同 ID。`, tab: 'connection' }; ids.add(provider.id);
    if (!provider.name.trim()) return { error: '請填寫供應商顯示名稱。', tab: 'connection' };
    try { const url = new URL(provider.base_url); if (!['http:', 'https:'].includes(url.protocol)) throw new Error(); } catch { return { error: `「${provider.name}」的 API 網址格式不正確，請使用 http:// 或 https://。`, tab: 'connection' }; }
  }
  if (!draft.default_model.trim()) return { error: '請選擇或填寫預設模型。', tab: 'connection', field: '[name="default_model"]' };
  for (const name of ['context_length', 'output_tokens', 'retention_days', 'compact_trigger']) {
    const input = $(`[name="${name}"]`); if (!input.checkValidity()) return { error: '請檢查記憶與生成設定的數值範圍。', tab: 'behavior', field: `[name="${name}"]` };
  }
  if (draft.output_tokens > Math.floor(draft.context_length / 2)) return { error: '單次回覆上限不可超過上下文長度的一半。', tab: 'behavior', field: '[name="output_tokens"]' };
  if (draft.image_service_enabled) {
    const labels = { comfyui_url: 'ComfyUI API 網址', comfyui_checkpoint: 'NoobAI Checkpoint 檔名', ipadapter_model: 'IP-Adapter 模型檔名', clip_vision_model: 'CLIP Vision 模型檔名' };
    for (const key of imageSettingFields) { if (!String(draft[key] || '').trim()) return { error: `啟用生圖時，請填入${labels[key]}。`, tab: 'media', field: `[name="${key}"]` }; }
  }
  if (draft.comfyui_url) { try { const url = new URL(draft.comfyui_url); if (!['http:', 'https:'].includes(url.protocol)) throw new Error(); } catch { return { error: 'ComfyUI 網址格式不正確。', tab: 'media', field: '[name="comfyui_url"]' }; } }
  return null;
}
async function saveProfile(event, startAfterSave = false) {
  event?.preventDefault(); if (state.saving || state.action) return; const invalid = validateDraft();
  if (invalid) { setTab(invalid.tab); toast(invalid.error, true); if (invalid.field) $(invalid.field)?.focus(); return; }
  const button = $('#save-profile'); state.saving = true; button.disabled = true; text(button, '儲存中…'); updateRuntime();
  try {
    const payload = clone(state.draft); const result = await api(state.selectedId ? `/api/profiles/${encodeURIComponent(state.selectedId)}` : '/api/profiles', { method: state.selectedId ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    const profile = result.profile || result; const index = state.profiles.findIndex(item => item.id === profile.id);
    if (index >= 0) state.profiles[index] = profile; else state.profiles.push(profile);
    state.selectedId = profile.id; state.draft = normalizedProfile(profile); renderForm(); renderProfiles(); setDirty(false); await refreshRuntime();
    if (startAfterSave) await runtimeAction('start'); else toast('設定已儲存。可使用右側控制啟動機器人。');
  } catch (error) { toast(errorMessage(error), true); }
  finally { state.saving = false; button.disabled = false; text(button, '儲存設定'); updateRuntime(); }
}
async function runtimeAction(action) {
  if (!state.selectedId || state.action) return;
  state.action = true; updateRuntime();
  try { await post(`/api/profiles/${encodeURIComponent(state.selectedId)}/${action}`); await refreshRuntime(); toast({ start: '已送出啟動要求，登入狀態請查看日誌。', stop: '機器人已停止。', restart: '已送出重新啟動要求。' }[action]); }
  catch (error) { toast(errorMessage(error), true); }
  finally { state.action = false; updateRuntime(); }
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('#login-submit'); button.disabled = true; text($('#login-error'), '');
  try { await post('/api/login', { password: $('#login-password').value }); $('#login-password').value = ''; await loadWorkspace(); }
  catch (error) { text($('#login-error'), errorMessage(error)); }
  finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => { if (!canLeaveDraft()) return; try { await post('/api/logout'); state.profiles = []; state.draft = null; state.runtime.clear(); setDirty(false); showLogin(); } catch (error) { toast(errorMessage(error), true); } });
$('#new-profile').addEventListener('click', () => { if (canLeaveDraft()) selectProfile(null); });
$('#profile-form').addEventListener('submit', saveProfile);
$('#profile-form').addEventListener('input', event => {
  if (!event.target.name) return; readFields(); setDirty(); updateStats();
  if (event.target.name === 'name') { text($('#page-title'), state.draft.name || '新的機器人'); text($('#breadcrumb-name'), state.draft.name || '新的機器人'); }
  if (event.target.name === 'default_provider') renderModelOptions();
  if (event.target.name === 'discord_token') { state.discordVerified = false; text($('#discord-verify-result'), 'Token 已變更，請重新驗證身分。'); $('#discord-verify-result').classList.remove('error'); updateSetupGuide(); }
  if (event.target.name === 'application_id') updateDiscordInvite();
  if (event.target.name === 'image_service_enabled' || imageSettingFields.includes(event.target.name)) invalidateImageCheck();
});
$('#profile-form').addEventListener('change', event => { if (event.target.name) { readFields(); setDirty(); updateStats(); if (event.target.name === 'default_provider') renderModelOptions(); if (event.target.name === 'image_service_enabled' || imageSettingFields.includes(event.target.name)) invalidateImageCheck(); } });
$$('.tab').forEach(button => button.addEventListener('click', () => setTab(button.dataset.tab)));
document.addEventListener('click', event => { const button = event.target.closest('.reveal-secret'); if (!button) return; const input = $('input', button.parentElement); input.type = input.type === 'password' ? 'text' : 'password'; text(button, input.type === 'password' ? '顯示' : '隱藏'); });
$('#add-provider').addEventListener('click', () => { let index = state.draft.providers.length + 1; let provider = newProvider('ollama', index); while (state.draft.providers.some(item => item.id === provider.id)) provider = newProvider('ollama', ++index); state.draft.providers.push(provider); renderProviders(); renderDefaultProvider(); setDirty(); updateStats(); });
$('#verify-discord').addEventListener('click', verifyDiscord);
$('#check-image-service').addEventListener('click', checkImageService);
$('#open-image-settings').addEventListener('click', () => { setTab('media'); $('#image-service-enabled').scrollIntoView({ behavior: 'smooth', block: 'center' }); $('#image-service-enabled').focus({ preventScroll: true }); });
$('#save-start-bot').addEventListener('click', event => saveProfile(event, true));
$$('[data-setup-step]').forEach(button => button.addEventListener('click', () => {
  setTab('connection'); const selector = { discord: '[name="discord_token"]', provider: '.provider-preset', launch: '#default-model' }[button.dataset.setupStep];
  const input = $(selector); if (input) { input.scrollIntoView({ behavior: 'smooth', block: 'center' }); input.focus({ preventScroll: true }); }
}));
$('#start-bot').addEventListener('click', () => runtimeAction('start'));
$('#stop-bot').addEventListener('click', () => runtimeAction('stop'));
$('#restart-bot').addEventListener('click', () => runtimeAction('restart'));
$('#refresh-logs').addEventListener('click', () => refreshLogs().catch(error => toast(errorMessage(error), true)));
$('#delete-profile').addEventListener('click', async () => {
  if (!state.selectedId || !window.confirm(`確定永久刪除「${state.draft.name}」的設定、對話及此機器人資料？此操作無法復原。`)) return;
  try { await api(`/api/profiles/${encodeURIComponent(state.selectedId)}`, { method: 'DELETE' }); state.profiles = state.profiles.filter(item => item.id !== state.selectedId); selectProfile(state.profiles[0]?.id || null); toast('機器人設定已刪除。'); }
  catch (error) { toast(errorMessage(error), true); }
});
$('#import-legacy').addEventListener('click', async () => {
  if (!canLeaveDraft()) return; const button = $('#import-legacy'); button.disabled = true;
  try { const result = await post('/api/import-legacy'); const profile = result.profile || result; const index = state.profiles.findIndex(item => item.id === profile.id); if (index >= 0) state.profiles[index] = profile; else state.profiles.push(profile); selectProfile(profile.id); toast('已匯入既有 .env 設定，請檢查連線資訊。'); }
  catch (error) { toast(errorMessage(error), true); }
  finally { button.disabled = false; }
});
$('#chat-test').addEventListener('click', async () => {
  readFields(); const provider = state.draft.providers.find(item => item.id === state.draft.default_provider); const result = $('#chat-test-result');
  if (!provider || !state.draft.default_model.trim()) { toast('請先設定預設供應商與模型。', true); return; }
  const message = $('#chat-test-message').value.trim(); if (!message) { toast('請輸入試聊訊息。', true); return; }
  const button = $('#chat-test'); button.disabled = true; result.hidden = false; result.classList.remove('error'); text(result, '正在等待模型回覆…冷啟動可能需要較長時間。');
  try { const data = await post('/api/chat-test', { provider: clone(provider), ...(state.selectedId ? { profile_id: state.selectedId } : {}), model: state.draft.default_model, message, context_length: state.draft.context_length, output_tokens: state.draft.output_tokens }); if (!data.ok) throw new Error(data.error || '模型測試失敗。'); text(result, data.reply || '模型已回應，但沒有傳回文字。'); }
  catch (error) { text(result, errorMessage(error)); result.classList.add('error'); }
  finally { button.disabled = false; }
});
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshRuntime().catch(() => {}); });
setInterval(() => { if (state.authenticated && !document.hidden) refreshRuntime().catch(error => { text($('#runtime-help'), `狀態更新失敗：${errorMessage(error)}`); }); }, 5000);
(async () => { try { const session = await api('/api/session'); if (session.authenticated) await loadWorkspace(); else showLogin(); } catch (error) { showLogin(); text($('#login-error'), `無法載入工作台：${errorMessage(error)}`); } })();
