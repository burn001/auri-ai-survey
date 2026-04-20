const API = 'https://alris.ddns.net:8443';
let ADMIN_KEY = '';

// ── Auth ──
function doLogin() {
  ADMIN_KEY = document.getElementById('admin-key-input').value.trim();
  api('/api/admin/stats').then(data => {
    if (data.total_participants !== undefined) {
      sessionStorage.setItem('adminKey', ADMIN_KEY);
      document.getElementById('login').style.display = 'none';
      document.getElementById('app').style.display = 'flex';
      loadDashboard();
    }
  }).catch(() => {
    document.getElementById('login-error').textContent = '인증 실패';
  });
}

function logout() {
  sessionStorage.removeItem('adminKey');
  ADMIN_KEY = '';
  document.getElementById('app').style.display = 'none';
  document.getElementById('login').style.display = 'flex';
}

// Auto-login — URL ?key=... (토큰 링크) 우선, 이후 sessionStorage 복원
(function init() {
  const urlKey = new URLSearchParams(location.search).get('key');
  if (urlKey) {
    sessionStorage.setItem('adminKey', urlKey);
    history.replaceState(null, '', location.pathname + location.hash);
  }
  const saved = sessionStorage.getItem('adminKey');
  if (saved) {
    ADMIN_KEY = saved;
    document.getElementById('admin-key-input').value = saved;
    doLogin();
  }
  document.getElementById('admin-key-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') doLogin();
  });
})();

// ── Navigation ──
document.querySelectorAll('.nav-item[data-page]').forEach(el => {
  el.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    const page = el.dataset.page;
    document.getElementById('page-' + page).classList.add('active');
    if (page === 'dashboard') loadDashboard();
    if (page === 'participants') loadParticipants();
    if (page === 'email') loadEmailTargets();
    if (page === 'responses') loadResponses();
  });
});

// ── API Helper ──
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: { 'X-Admin-Key': ADMIN_KEY, 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(res.statusText);
  if (res.headers.get('content-type')?.includes('text/csv')) return res;
  return res.json();
}

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── Dashboard ──
const CAT_COLORS = { '설계': '#3b82f6', '시공': '#f59e0b', '유지관리': '#22c55e', '건축행정': '#8b5cf6', '기타': '#94a3b8', '미분류': '#d1d5db' };

async function loadDashboard() {
  const data = await api('/api/admin/stats');
  const cats = data.by_category;

  document.getElementById('stat-cards').innerHTML = `
    <div class="stat-card"><div class="label">전체 대상자</div><div class="value">${data.total_participants}</div></div>
    <div class="stat-card"><div class="label">응답 완료</div><div class="value">${data.total_responses}</div></div>
    <div class="stat-card"><div class="label">응답률</div><div class="value">${data.total_participants ? (data.total_responses / data.total_participants * 100).toFixed(1) : 0}%</div></div>
  `;

  const order = ['설계', '시공', '건축행정', '유지관리', '기타', '미분류'];
  document.getElementById('cat-bars').innerHTML = '<h3 style="font-size:14px;font-weight:600;margin-bottom:12px">직군별 응답 현황</h3>' +
    order.filter(c => cats[c]).map(c => {
      const d = cats[c];
      const pct = d.participants ? (d.responded / d.participants * 100).toFixed(0) : 0;
      return `<div class="cat-row">
        <span class="cat-label">${c}</span>
        <div class="cat-track"><div class="cat-fill" style="width:${pct}%;background:${CAT_COLORS[c] || '#aaa'}"></div></div>
        <span class="cat-count">${d.responded} / ${d.participants} (${pct}%)</span>
      </div>`;
    }).join('');
}

// ── Participants ──
let pPage = 0;
const P_LIMIT = 50;

async function loadParticipants(page = 0) {
  pPage = page;
  const cat = document.getElementById('p-category').value;
  const q = `?skip=${page * P_LIMIT}&limit=${P_LIMIT}` + (cat ? `&category=${cat}` : '');
  const data = await api('/api/admin/participants' + q);

  const search = document.getElementById('p-search').value.toLowerCase();
  const filtered = search
    ? data.data.filter(p => p.name.toLowerCase().includes(search) || (p.org || '').toLowerCase().includes(search))
    : data.data;

  document.getElementById('p-table').innerHTML = `<table>
    <thead><tr><th>이름</th><th>소속</th><th>직군</th><th>이메일</th><th>발송</th><th>토큰</th></tr></thead>
    <tbody>${filtered.map(p => `<tr>
      <td>${p.name}</td>
      <td>${p.org || ''}</td>
      <td><span class="badge badge-blue">${p.category || ''}</span></td>
      <td style="font-size:12px">${p.email}</td>
      <td>${p.email_sent ? '<span class="badge badge-green">완료</span>' : '<span class="badge badge-gray">미발송</span>'}</td>
      <td><code style="font-size:11px;cursor:pointer" onclick="navigator.clipboard.writeText('${p.token}');toast('복사됨')">${p.token}</code></td>
    </tr>`).join('')}</tbody>
  </table>`;

  const totalPages = Math.ceil(data.total / P_LIMIT);
  const pag = [];
  const btn = (i, label, disabled) => `<button class="btn btn-sm ${i === page ? 'btn-primary' : 'btn-outline'}"${disabled ? ' disabled' : ''} onclick="loadParticipants(${i})">${label}</button>`;
  if (totalPages > 1) {
    pag.push(btn(0, '«', page === 0));
    pag.push(btn(Math.max(0, page - 1), '‹', page === 0));
    const start = Math.max(0, Math.min(page - 4, totalPages - 9));
    const end = Math.min(totalPages, start + 9);
    for (let i = start; i < end; i++) pag.push(btn(i, i + 1, false));
    pag.push(btn(Math.min(totalPages - 1, page + 1), '›', page >= totalPages - 1));
    pag.push(btn(totalPages - 1, '»', page >= totalPages - 1));
    pag.push(`<span style="font-size:12px;color:var(--text3);margin-left:8px;align-self:center">${data.total}명 / ${totalPages}페이지</span>`);
  }
  document.getElementById('p-pagination').innerHTML = pag.join('');
}

document.getElementById('p-category').addEventListener('change', () => loadParticipants(0));
document.getElementById('p-search').addEventListener('input', () => loadParticipants(pPage));

function exportParticipantLinks() {
  const rows = [['name', 'email', 'org', 'category', 'token', 'survey_link']];
  const cat = document.getElementById('p-category').value;
  const q = `?skip=0&limit=5000` + (cat ? `&category=${cat}` : '');
  api('/api/admin/participants' + q).then(data => {
    data.data.forEach(p => {
      rows.push([p.name, p.email, p.org || '', p.category || '', p.token,
        `https://burn001.github.io/auri-ai-survey/?token=${p.token}`]);
    });
    const csv = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'participants_links.csv';
    a.click();
  });
}

// ── Email ──
let emailTargets = [];
let selectedTokens = new Set();

async function loadEmailTargets() {
  const cat = document.getElementById('e-category').value;
  const filter = document.getElementById('e-filter').value;
  const q = `?skip=0&limit=5000` + (cat ? `&category=${cat}` : '');
  const data = await api('/api/admin/participants' + q);

  emailTargets = data.data.filter(p => {
    if (filter === 'unsent') return !p.email_sent;
    if (filter === 'sent') return p.email_sent;
    return true;
  });
  selectedTokens.clear();
  renderEmailTable();
}

function renderEmailTable() {
  document.getElementById('btn-send').disabled = selectedTokens.size === 0;
  document.getElementById('e-status').textContent = `${emailTargets.length}명 조회 / ${selectedTokens.size}명 선택`;

  const allChecked = emailTargets.length > 0 && selectedTokens.size === emailTargets.length;
  document.getElementById('e-table').innerHTML = `<table>
    <thead><tr>
      <th class="checkbox-col"><input type="checkbox" ${allChecked ? 'checked' : ''} onchange="toggleAllEmail(this.checked)"></th>
      <th>이름</th><th>소속</th><th>직군</th><th>이메일</th><th>상태</th>
    </tr></thead>
    <tbody>${emailTargets.map(p => `<tr>
      <td class="checkbox-col"><input type="checkbox" data-token="${p.token}" ${selectedTokens.has(p.token) ? 'checked' : ''} onchange="toggleEmailSelect('${p.token}', this.checked)"></td>
      <td>${p.name}</td>
      <td>${p.org || ''}</td>
      <td><span class="badge badge-blue">${p.category || ''}</span></td>
      <td style="font-size:12px">${p.email}</td>
      <td>${p.email_sent ? '<span class="badge badge-green">발송완료</span>' : '<span class="badge badge-gray">미발송</span>'}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

function toggleAllEmail(checked) {
  if (checked) emailTargets.forEach(p => selectedTokens.add(p.token));
  else selectedTokens.clear();
  renderEmailTable();
}

function toggleEmailSelect(token, checked) {
  if (checked) selectedTokens.add(token);
  else selectedTokens.delete(token);
  renderEmailTable();
}

async function sendEmails() {
  if (selectedTokens.size === 0) return;
  if (!confirm(`${selectedTokens.size}명에게 설문 이메일을 발송합니다. 계속하시겠습니까?`)) return;

  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  btn.textContent = '발송 중...';
  document.getElementById('e-status').textContent = '발송 진행 중...';

  try {
    const result = await api('/api/admin/email/send', {
      method: 'POST',
      body: JSON.stringify({ tokens: [...selectedTokens] }),
    });
    toast(`발송 완료: ${result.sent}건 성공, ${result.failed}건 실패`);
    document.getElementById('e-status').textContent = `결과: ${result.sent}건 발송, ${result.failed}건 실패, ${result.skipped}건 건너뜀`;
    loadEmailTargets();
  } catch (e) {
    toast('발송 실패: ' + e.message, 'error');
  } finally {
    btn.textContent = '선택 대상자에게 발송';
    btn.disabled = false;
  }
}

let _previewBlobUrl = null;
async function previewEmail() {
  const preview = document.getElementById('email-preview');
  try {
    const res = await fetch(API + '/api/admin/email/preview', {
      method: 'POST',
      headers: { 'X-Admin-Key': ADMIN_KEY, 'Content-Type': 'application/json' },
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const html = await res.text();
    if (_previewBlobUrl) URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    preview.style.display = 'block';
    preview.innerHTML = `<iframe src="${_previewBlobUrl}"></iframe>`;
  } catch (e) {
    toast('미리보기 실패: ' + e.message, 'error');
  }
}

// ── Responses ──
async function loadResponses() {
  const cat = document.getElementById('r-category').value;
  const q = `?skip=0&limit=200` + (cat ? `&category=${cat}` : '');
  const data = await api('/api/admin/responses' + q);

  document.getElementById('r-table').innerHTML = `<table>
    <thead><tr><th>이름</th><th>소속</th><th>직군</th><th>제출일시</th><th>수정일시</th></tr></thead>
    <tbody>${data.data.map(r => `<tr>
      <td>${r.name || ''}</td>
      <td>${r.org || ''}</td>
      <td><span class="badge badge-blue">${r.category || ''}</span></td>
      <td style="font-size:12px">${r.submitted_at ? new Date(r.submitted_at).toLocaleString('ko') : ''}</td>
      <td style="font-size:12px">${r.updated_at ? new Date(r.updated_at).toLocaleString('ko') : '-'}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

async function downloadCSV() {
  try {
    const res = await fetch(API + '/api/admin/export', {
      headers: { 'X-Admin-Key': ADMIN_KEY },
    });
    if (!res.ok) throw new Error('No data');
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'survey_responses.csv';
    a.click();
    toast('CSV 다운로드 완료');
  } catch (e) {
    toast('다운로드 실패 (응답 데이터 없음)', 'error');
  }
}
