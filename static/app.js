// Global error handling
window.addEventListener('unhandledrejection', e => {
  console.error('[JS] Unhandled promise rejection:', e.reason);
});
window.addEventListener('error', e => {
  console.error('[JS] Uncaught error:', e.message, 'at', e.filename, e.lineno);
});

// State
let token = '';
let currentUser = '';
let currentChannel = 'general';
let evtSource = null;
let isRegistering = false;
const AVATAR_COLORS = ['#39c5ab','#58a6ff','#d29922','#f85149','#a5d6ff','#7ee787','#ff9a5a'];

function avatarColor(name) {
  let h = 0;
  for (let c of name) h = (h * 31 + c.charCodeAt(0)) & 0xffffff;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function initials(name) { return (name || '?')[0].toUpperCase(); }

function switchTab(tab) {
  isRegistering = (tab === 'register');
  document.getElementById('tab-login').classList.toggle('active', !isRegistering);
  document.getElementById('tab-register').classList.toggle('active', isRegistering);
  document.getElementById('login-btn').textContent = isRegistering ? 'Register' : 'Sign in';
  document.getElementById('login-error').textContent = '';
}

async function handleAuth() {
  const u = document.getElementById('l-username').value.trim();
  const p = document.getElementById('l-password').value;
  const errEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');
  errEl.textContent = '';
  if (!u || !p) { errEl.textContent = 'Please fill in all fields.'; return; }
  btn.disabled = true;
  btn.textContent = '…';

  try {
    if (isRegistering) {
      const reg = await api('/api/register', { username: u, password: p });
      if (!reg.success) {
        errEl.textContent = reg.message || 'Registration failed.';
        btn.disabled = false;
        btn.textContent = 'Register';
        return;
      }
    }

    const login = await api('/api/login', { username: u, password: p });
    if (!login.success) {
      errEl.textContent = login.message || 'Sign in failed.';
      btn.disabled = false;
      btn.textContent = isRegistering ? 'Register' : 'Sign in';
      return;
    }

    token = login.token || '';
    currentUser = login.username || u;
    console.log('[Auth] Login ok, token:', token.slice(0,8)+'...', 'user:', currentUser);

    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.add('visible');

    const tbUser = document.getElementById('tb-username');
    if (tbUser) tbUser.textContent = currentUser;

    const av = document.getElementById('tb-avatar');
    if (av) {
      av.textContent = initials(currentUser);
      av.style.background = avatarColor(currentUser);
    }

    await connectChat('general');
  } catch (err) {
    console.error('[Auth] Unexpected error:', err);
    errEl.textContent = 'Unexpected error: ' + err.message;
    btn.disabled = false;
    btn.textContent = isRegistering ? 'Register' : 'Sign in';
  }
}

// Connect to chat service via SSE bridge
async function connectChat(channel) {
  showBanner(true);
  currentChannel = channel;
  document.getElementById('tb-channel').textContent = channel;
  markChannelActive(channel);

  let r;
  try {
    r = await api('/api/chat/connect', { token, channel });
  } catch(e) {
    showBanner(false);
    showConnectError('Network error: ' + e.message);
    return;
  }

  if (!r || !r.success) {
    showBanner(false);
    showConnectError(r ? r.message : 'No response from server');
    return;
  }

  if (r.initial) {
    appendRaw(r.initial, true);
  }

  showBanner(false);

  const statusEl = document.getElementById('tb-status');
  if (statusEl) statusEl.classList.remove('offline');

  const rbar = document.getElementById('reconnect-bar');
  if (rbar) rbar.style.display = 'none';

  console.log('[Chat] Connected, starting SSE stream');

  startSSE();
  loadChannels();
  loadUsers();

  if (window._pollInterval) clearInterval(window._pollInterval);
  window._pollInterval = setInterval(() => {
    loadChannels();
    loadUsers();
  }, 2000);
}

function showConnectError(msg) {
  const bar = document.getElementById('reconnect-bar');
  document.getElementById('reconnect-msg').textContent =
    '⚠ Could not connect to chat server: ' + (msg || 'unknown error') +
    '. Is chat_service.py running?';
  bar.style.display = 'flex';
}

function startSSE() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource(`/api/chat/stream?token=${token}`);

  evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.error || data.disconnected) {
      addSystemMsg('🔌 Connection lost.');
      document.getElementById('tb-status').classList.add('offline');
      evtSource.close();
      return;
    }
    if (data.message) {
      appendRaw(data.message);
      const msg = data.message.trim();
      if (msg.includes(' has joined #') || msg.includes(' has left #')) {
        loadUsers();
        loadChannels();
      }
    }
  };

  evtSource.onerror = () => {
    document.getElementById('tb-status').classList.add('offline');
  };
}

// Parse and render incoming messages
function appendRaw(text, isHistory = false) {
  const lines = text.split('\n').map(l => l.trimEnd()).filter(l => l.length > 0);
  const box = document.getElementById('messages');

  if (isHistory && lines.length > 0) {
    const sep = document.createElement('div');
    sep.className = 'history-sep';
    sep.textContent = 'history';
    box.appendChild(sep);
  }

  for (const line of lines) {
    // Handle channel change notification
    if (line.startsWith('CHANNEL_CHANGED:')) {
      const newCh = line.split(':')[1].trim();
      currentChannel = newCh;
      document.getElementById('tb-channel').textContent = newCh;
      markChannelActive(newCh);
      api('/api/chat/update_channel', { token, channel: newCh }).then(() => {
        loadUsers();
        loadChannels();
      });
      addSystemMsg('Switched to #' + newCh);
      continue;
    }

    const chatRe = /^\[(\d{2}:\d{2}:\d{2})\]\s+\[#[^\]]+\]\s+(.+?):\s+(.+)$/;
    const youRe  = /^\[(\d{2}:\d{2}:\d{2})\]\s+You:\s+(.+)$/;
    const histRe = /^\[(\d{2}:\d{2}:\d{2})\]\s+(.+?):\s+(.+)$/;

    let m;
    if ((m = chatRe.exec(line)) !== null) {
      addMessage(m[2], m[3], m[1]);
    } else if ((m = youRe.exec(line)) !== null) {
      addMessage(currentUser + ' (you)', m[2], m[1]);
    } else if ((m = histRe.exec(line)) !== null) {
      addMessage(m[2], m[3], m[1]);
    } else if (line.startsWith('***') || line.startsWith('---') || line.startsWith('[PM')) {
      addSystemMsg(line);
    } else if (line.startsWith('Commands:') || line.startsWith('  /')) {
      addSystemMsg(line);
    } else if (line.trim()) {
      addSystemMsg(line);
    }
  }

  box.scrollTop = box.scrollHeight;
}

function addMessage(username, text, time) {
  const box = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg';

  const cleanName = username.replace(' (you)', '');
  const color = avatarColor(cleanName);

  div.innerHTML = `
    <div class="msg-avatar" style="background:${color}">${initials(cleanName)}</div>
    <div class="msg-body">
      <div class="msg-header">
        <span class="msg-user" style="color:${color}">${escHtml(username)}</span>
        <span class="msg-time">${escHtml(time || '')}</span>
      </div>
      <div class="msg-text">${escHtml(text)}</div>
    </div>`;
  box.appendChild(div);
}

function addSystemMsg(text) {
  const box = document.getElementById('messages');
  const div = document.createElement('div');

  if (text.startsWith('***') && text.endsWith('***')) {
    div.className = 'msg-event';
    div.textContent = text;
  } else if (text.startsWith('[PM')) {
    div.className = 'msg-pm';
    div.textContent = text;
  } else if (text.startsWith('❌') || text.startsWith('ERROR') || text.startsWith('🔌')) {
    div.className = 'msg-error';
    div.textContent = text;
  } else if (text.startsWith('  /') || text.startsWith('Commands:') || text.startsWith('Available commands:')) {
    div.className = 'msg-cmd';
    div.textContent = text;
  } else if (text.startsWith('--- Last') || text.startsWith('--- End')) {
    const sep = document.createElement('div');
    sep.className = text.startsWith('--- End') ? 'history-end' : 'history-sep';
    const clean = text.replace(/^---\s*/, '').replace(/\s*---$/, '');
    sep.textContent = clean;
    box.appendChild(sep);
    return;
  } else if (
    text.startsWith('Switched to #') ||
    text.startsWith('Users in #') ||
    text.startsWith('No users in #') ||
    text.startsWith('Active channels') ||
    text.startsWith('  #') ||
    text.startsWith('Joined #') ||
    text.startsWith('You joined #') ||
    text.startsWith('Already in #') ||
    text.startsWith('No active channels') ||
    text.startsWith('No history')
  ) {
    div.className = 'msg-success';
    div.textContent = text;
  } else {
    div.className = 'msg-system';
    div.textContent = text;
  }

  box.appendChild(div);
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function sendMessage() {
  const inp = document.getElementById('msg-input');
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  inp.style.height = '';

  const r = await api('/api/chat/send', { token, message: text });
  if (!r.success) addSystemMsg('❌ Send failed: ' + (r.message || ''));
}

// Channel management
async function loadChannels() {
  const r = await fetch('/api/channels').then(x => x.json()).catch(() => ({}));
  const list = document.getElementById('channel-list');
  if (!r.channels) return;

  const channelNames = [...new Set([currentChannel, ...r.channels.map(c => c.channel)])];

  const existing = [...list.querySelectorAll('.channel-item')].map(el => el.dataset.ch);
  const same = existing.length === channelNames.length && channelNames.every((n,i) => n === existing[i]);
  if (same) { markChannelActive(currentChannel); return; }

  list.innerHTML = '';
  for (const name of channelNames) {
    const item = document.createElement('div');
    item.className = 'channel-item' + (name === currentChannel ? ' active' : '');
    item.dataset.ch = name;
    item.textContent = name;
    item.onclick = () => switchChannel(name);
    list.appendChild(item);
  }
}

async function loadUsers() {
  if (!token) return;
  const r = await fetch(`/api/chat/users?token=${encodeURIComponent(token)}&channel=${encodeURIComponent(currentChannel)}`)
    .then(x => x.json()).catch(() => ({}));
  const list = document.getElementById('user-list');
  if (!r.users) return;
  list.innerHTML = '';
  for (const u of r.users) {
    const item = document.createElement('div');
    item.className = 'user-item';
    const color = avatarColor(u);
    item.innerHTML = `<span class="user-dot" style="background:${color}"></span>${escHtml(u)}`;
    list.appendChild(item);
  }
}

async function switchChannel(name) {
  if (name === currentChannel) return;
  currentChannel = name;
  document.getElementById('tb-channel').textContent = name;
  document.getElementById('messages').innerHTML = '';
  markChannelActive(name);

  const r = await api('/api/chat/join', { token, channel: name });
  if (!r.success) {
    addSystemMsg('Failed to switch channel.');
    return;
  }

  await Promise.all([loadChannels(), loadUsers()]);
}

async function joinChannel() {
  const inp = document.getElementById('new-channel-input');
  const name = inp.value.trim().toLowerCase().replace(/[^a-z0-9-]/g, '-');
  if (!name) return;
  inp.value = '';
  await switchChannel(name);
}

function markChannelActive(name) {
  document.querySelectorAll('.channel-item').forEach(el => {
    el.classList.toggle('active', el.textContent === name);
  });
}

// Logout and cleanup
async function logout() {
  if (window._pollInterval) { clearInterval(window._pollInterval); window._pollInterval = null; }
  if (evtSource) evtSource.close();
  await api('/api/chat/disconnect', { token });
  await api('/api/logout', { token });
  token = ''; currentUser = ''; currentChannel = 'general';
  document.getElementById('app').classList.remove('visible');
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('messages').innerHTML = '';
  document.getElementById('tb-status').classList.add('offline');
  document.getElementById('l-password').value = '';
  document.getElementById('login-btn').disabled = false;
  document.getElementById('login-btn').textContent = 'Sign in';
  document.getElementById('login-error').textContent = '';
}

function showBanner(show) {
  document.getElementById('conn-banner').classList.toggle('show', show);
}

// Keyboard shortcuts and UI event handlers
document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('msg-input');

  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  inp.addEventListener('input', () => {
    inp.style.height = 'auto';
    inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
  });

  document.getElementById('l-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') handleAuth();
  });
  document.getElementById('l-username').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('l-password').focus();
  });
  document.getElementById('new-channel-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') joinChannel();
  });
});

// HTTP helper for POST requests
async function api(path, body) {
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    return await r.json();
  } catch (e) {
    return { success: false, message: e.message };
  }
}