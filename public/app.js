const drop          = document.getElementById('drop');
const fileInput     = document.getElementById('fileInput');
const fileChip      = document.getElementById('fileChip');
const fileNameEl    = document.getElementById('fileName');
const removeFileBtn = document.getElementById('removeFile');
const siteNameInput = document.getElementById('siteName');
const uploadBtn     = document.getElementById('uploadBtn');
const progressTrack = document.getElementById('progressTrack');
const progressFill  = document.getElementById('progressFill');
const uploadAlert   = document.getElementById('uploadAlert');
const sitesList     = document.getElementById('sitesList');
const refreshBtn    = document.getElementById('refreshBtn');
const siteCount     = document.getElementById('siteCount');
const avatarEl      = document.getElementById('avatar');
const uidShortEl    = document.getElementById('uidShort');

let selectedFile = null;

document.getElementById('year').textContent = new Date().getFullYear();

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (opts.method && opts.method !== 'GET') {
    headers['X-Requested-With'] = 'XMLHttpRequest';
    if (opts.body && typeof opts.body === 'string') {
      headers['Content-Type'] = 'application/json';
    }
  }
  const r = await fetch(path, { ...opts, headers });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data };
}

function toast(msg, type = 'info') {
  const host = document.getElementById('toastHost');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateX(60px)';
    el.style.transition = 'all .3s';
    setTimeout(() => el.remove(), 300);
  }, 3500);
}

function setAlert(el, msg, type = 'error') {
  el.className = 'alert show ' + type;
  el.textContent = msg;
}
function clearAlert(el) {
  el.className = 'alert';
  el.textContent = '';
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function fmtSize(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(1) + ' ' + u[i];
}

async function loadMe() {
  try {
    const r = await fetch('/api/me');
    const d = await r.json();
    if (d.user) {
      const id = d.user.id || '';
      uidShortEl.textContent = id.slice(0, 6);
      avatarEl.textContent = (id.slice(0, 1) || '?').toUpperCase();
    }
  } catch {}
}

drop.addEventListener('click', () => fileInput.click());

['dragenter', 'dragover'].forEach(ev =>
  drop.addEventListener(ev, e => {
    e.preventDefault();
    drop.classList.add('dragover');
  })
);
['dragleave', 'drop'].forEach(ev =>
  drop.addEventListener(ev, e => {
    e.preventDefault();
    drop.classList.remove('dragover');
  })
);

drop.addEventListener('drop', e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) setFile(f);
});

fileInput.addEventListener('change', e => {
  const f = e.target.files && e.target.files[0];
  if (f) setFile(f);
});

function setFile(file) {
  clearAlert(uploadAlert);
  const lower = file.name.toLowerCase();
  const isZip = lower.endsWith('.zip');
  const isHtml = lower.endsWith('.html') || lower.endsWith('.htm');

  if (!isZip && !isHtml) {
    setAlert(uploadAlert, '.html သို့မဟုတ် .zip ဖိုင်ပဲ ခွင့်ပြုပါတယ်');
    return;
  }
  if (file.size > 25 * 1024 * 1024) {
    setAlert(uploadAlert, '25 MB ထက် ကြီးလွန်းပါတယ်');
    return;
  }

  selectedFile = file;
  fileNameEl.textContent =
    `${isZip ? '📦' : '📄'} ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  fileChip.classList.remove('hide');
  uploadBtn.disabled = false;
}

removeFileBtn.addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  fileChip.classList.add('hide');
  uploadBtn.disabled = true;
});

uploadBtn.addEventListener('click', () => {
  if (!selectedFile) return;
  clearAlert(uploadAlert);

  const fd = new FormData();
  fd.append('site', selectedFile);
  fd.append('name', siteNameInput.value.trim());

  uploadBtn.disabled = true;
  progressTrack.classList.add('show');
  progressFill.style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

  xhr.upload.addEventListener('progress', e => {
    if (e.lengthComputable) {
      progressFill.style.width = ((e.loaded / e.total) * 100) + '%';
    }
  });

  xhr.onload = () => {
    progressTrack.classList.remove('show');
    uploadBtn.disabled = false;
    try {
      const res = JSON.parse(xhr.responseText);
      if (xhr.status === 200 && res.success) {
        const fullURL = location.origin + res.url;
        setAlert(uploadAlert,
          `✅ Host လုပ်ပြီးပါပြီ! Link: ${fullURL}`, 'success');
        toast('Site တင်ပြီးပါပြီ 🎉', 'success');
        selectedFile = null;
        fileInput.value = '';
        fileChip.classList.add('hide');
        siteNameInput.value = '';
        uploadBtn.disabled = true;
        loadSites();
      } else {
        setAlert(uploadAlert, res.error || 'Upload မအောင်မြင်ပါ');
      }
    } catch {
      setAlert(uploadAlert, 'Server error');
    }
  };

  xhr.onerror = () => {
    progressTrack.classList.remove('show');
    uploadBtn.disabled = false;
    setAlert(uploadAlert, 'Network error');
  };

  xhr.send(fd);
});

async function loadSites() {
  try {
    const { ok, data } = await api('/api/sites');
    if (!ok) return;
    renderSites(data.sites || []);
  } catch {
    sitesList.innerHTML = '<div class="empty">Sites တွေ ဆွဲမရပါ</div>';
  }
}

function renderSites(sites) {
  siteCount.textContent = sites.length;

  if (!sites.length) {
    sitesList.innerHTML =
      '<div class="empty"><span class="big">📭</span>Site တွေ မရှိသေးပါ</div>';
    return;
  }

  sitesList.innerHTML = sites.map(s => {
    const url  = `/s/${s.id}/`;
    const full = location.origin + url;
    return `
      <div class="site-card">
        <div class="site-badge">🌐</div>
        <div class="site-info">
          <div class="title">${escapeHTML(s.name)}</div>
          <a class="link" href="${url}" target="_blank" rel="noopener">${full}</a>
          <div class="meta">
            📄 ${s.fileCount} ဖိုင် • 💾 ${fmtSize(s.sizeBytes)} •
            👁 ${s.views || 0} views • 🕒 ${fmtDate(s.createdAt)}
          </div>
        </div>
        <div class="site-actions">
          <button class="btn btn-cyan btn-sm"
                  data-action="copy" data-url="${full}">📋 Copy</button>
          <button class="btn btn-green btn-sm"
                  data-action="open" data-url="${url}">🌐 Open</button>
          <button class="btn btn-danger btn-sm"
                  data-action="delete" data-id="${s.id}">🗑 Delete</button>
        </div>
      </div>
    `;
  }).join('');
}

sitesList.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;

  if (action === 'copy') {
    try {
      await navigator.clipboard.writeText(btn.dataset.url);
      toast('📋 Link ကို Copy လုပ်ပြီးပါပြီ', 'success');
    } catch { toast('Copy မရပါ', 'error'); }
  } else if (action === 'open') {
    window.open(btn.dataset.url, '_blank', 'noopener');
  } else if (action === 'delete') {
    if (!confirm('ဒီ Site ကို အပြီးဖျက်မှာ သေချာလား?')) return;
    const { ok, data } = await api('/api/sites/' + btn.dataset.id,
      { method: 'DELETE' });
    if (ok) {
      toast('🗑 ဖျက်ပြီးပါပြီ', 'success');
      loadSites();
    } else {
      toast(data.error || 'ဖျက်လို့ မရပါ', 'error');
    }
  }
});

refreshBtn.addEventListener('click', loadSites);

loadMe();
loadSites();
