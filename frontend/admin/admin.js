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

// ── Participants & Email (통합) ──
let pPage = 0;
const P_LIMIT = 50;
let pCache = [];                   // 현재 직군 필터 기준 전체 참가자
let pSelected = new Set();         // 선택된 토큰
let pFilteredView = [];            // 검색·상태 필터 적용된 뷰 (페이지네이션 대상)

// 한국 시간 포맷: "2026-04-20 12:34"
function fmtKST(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(d).replace(', ', ' ');
}

// 경과 시간: "3일 전", "5시간 전", "방금 전"
function relTime(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return '방금 전';
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}일 전`;
  const mo = Math.floor(d / 30);
  return `${mo}개월 전`;
}

async function loadParticipants(page = 0) {
  pPage = page;
  const cat = document.getElementById('p-category').value;
  const q = `?skip=0&limit=5000` + (cat ? `&category=${encodeURIComponent(cat)}` : '');
  const data = await api('/api/admin/participants' + q);
  pCache = data.data;
  pSelected.clear();
  renderParticipants();
}

function renderParticipants() {
  const page = pPage;
  const search = document.getElementById('p-search').value.trim().toLowerCase();
  const sendStatus = document.getElementById('p-send-status').value;
  const respStatus = document.getElementById('p-resp-status').value;

  pFilteredView = pCache.filter(p => {
    if (search && !(
      (p.name || '').toLowerCase().includes(search) ||
      (p.org || '').toLowerCase().includes(search) ||
      (p.email || '').toLowerCase().includes(search)
    )) return false;
    if (sendStatus === 'unsent' && p.email_sent) return false;
    if (sendStatus === 'sent' && !p.email_sent) return false;
    if (respStatus === 'responded' && !p.responded) return false;
    if (respStatus === 'unresponded' && p.responded) return false;
    return true;
  });

  const total = pFilteredView.length;
  const totalPages = Math.max(1, Math.ceil(total / P_LIMIT));
  if (page >= totalPages) { pPage = 0; }
  const rows = pFilteredView.slice(pPage * P_LIMIT, (pPage + 1) * P_LIMIT);

  const pageTokens = rows.map(r => r.token);
  const allChecked = rows.length > 0 && pageTokens.every(t => pSelected.has(t));

  document.getElementById('p-table').innerHTML = `<table>
    <thead><tr>
      <th class="checkbox-col"><input type="checkbox" ${allChecked ? 'checked' : ''} onchange="togglePageSelect(this.checked)"></th>
      <th>이름</th><th>소속</th><th>직군</th><th>이메일</th>
      <th>발송</th><th>응답</th><th>토큰</th>
    </tr></thead>
    <tbody>${rows.map(p => {
      const sentTime = p.email_sent_at ? `${fmtKST(p.email_sent_at)}<br><span style="color:var(--text3);font-size:11px">${relTime(p.email_sent_at)}</span>` : '';
      const sendBadge = p.email_sent
        ? `<span class="badge badge-green">발송</span><div style="font-size:11px;color:var(--text3);margin-top:2px">${sentTime}</div>`
        : '<span class="badge badge-gray">미발송</span>';
      const respBadge = p.responded
        ? `<span class="badge badge-blue">응답</span><div style="font-size:11px;color:var(--text3);margin-top:2px">${fmtKST(p.response_submitted_at)}</div>`
        : (p.email_sent ? '<span class="badge badge-orange">미응답</span>' : '<span class="badge badge-gray">-</span>');
      return `<tr>
        <td class="checkbox-col"><input type="checkbox" ${pSelected.has(p.token) ? 'checked' : ''} onchange="toggleRowSelect('${p.token}', this.checked)"></td>
        <td>${p.name}</td>
        <td>${p.org || ''}</td>
        <td><span class="badge badge-blue">${p.category || ''}</span></td>
        <td style="font-size:12px">${p.email}</td>
        <td style="min-width:170px">${sendBadge}</td>
        <td style="min-width:150px">${respBadge}</td>
        <td><code style="font-size:11px;cursor:pointer" onclick="navigator.clipboard.writeText('${p.token}');toast('복사됨')">${p.token}</code></td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;

  // 페이지네이션
  const pag = [];
  const btn = (i, label, disabled) => `<button class="btn btn-sm ${i === pPage ? 'btn-primary' : 'btn-outline'}"${disabled ? ' disabled' : ''} onclick="gotoPage(${i})">${label}</button>`;
  if (totalPages > 1) {
    pag.push(btn(0, '«', pPage === 0));
    pag.push(btn(Math.max(0, pPage - 1), '‹', pPage === 0));
    const start = Math.max(0, Math.min(pPage - 4, totalPages - 9));
    const end = Math.min(totalPages, start + 9);
    for (let i = start; i < end; i++) pag.push(btn(i, i + 1, false));
    pag.push(btn(Math.min(totalPages - 1, pPage + 1), '›', pPage >= totalPages - 1));
    pag.push(btn(totalPages - 1, '»', pPage >= totalPages - 1));
  }
  pag.push(`<span style="font-size:12px;color:var(--text3);margin-left:8px;align-self:center">${total}명${totalPages > 1 ? ` / ${totalPages}페이지` : ''} · 선택 ${pSelected.size}</span>`);
  document.getElementById('p-pagination').innerHTML = pag.join('');

  // 선택 발송 버튼
  const sendBtn = document.getElementById('btn-send');
  sendBtn.disabled = pSelected.size === 0;
  sendBtn.textContent = `선택 발송 (${pSelected.size})`;
}

function gotoPage(i) { pPage = i; renderParticipants(); }

function togglePageSelect(checked) {
  const page = pFilteredView.slice(pPage * P_LIMIT, (pPage + 1) * P_LIMIT);
  page.forEach(p => { checked ? pSelected.add(p.token) : pSelected.delete(p.token); });
  renderParticipants();
}

function toggleRowSelect(token, checked) {
  if (checked) pSelected.add(token); else pSelected.delete(token);
  renderParticipants();
}

document.getElementById('p-category').addEventListener('change', () => loadParticipants(0));
document.getElementById('p-search').addEventListener('input', () => { pPage = 0; renderParticipants(); });
document.getElementById('p-send-status').addEventListener('change', () => { pPage = 0; renderParticipants(); });
document.getElementById('p-resp-status').addEventListener('change', () => { pPage = 0; renderParticipants(); });

async function sendSelected() {
  if (pSelected.size === 0) return;
  if (!confirm(`${pSelected.size}명에게 설문 이메일을 발송합니다. 계속할까요?`)) return;
  await runSend([...pSelected]);
}

async function sendToUnresponded() {
  const targets = pFilteredView.filter(p => p.email_sent && !p.responded).map(p => p.token);
  if (targets.length === 0) { toast('현재 뷰에 미응답 대상이 없습니다', 'error'); return; }
  if (!confirm(`현재 필터에 해당하는 미응답자 ${targets.length}명에게 재발송합니다. 계속할까요?`)) return;
  await runSend(targets);
}

async function runSend(tokens) {
  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  btn.textContent = `발송 중 (${tokens.length})...`;
  try {
    const result = await api('/api/admin/email/send', {
      method: 'POST',
      body: JSON.stringify({ tokens }),
    });
    toast(`발송 완료: ${result.sent}건 성공${result.failed ? `, ${result.failed}건 실패` : ''}`);
    await loadParticipants(pPage);
  } catch (e) {
    toast('발송 실패: ' + e.message, 'error');
  }
}

function exportParticipantLinks() {
  const rows = [['name', 'email', 'org', 'category', 'token', 'email_sent_at_kst', 'responded', 'survey_link']];
  pCache.forEach(p => {
    rows.push([p.name, p.email, p.org || '', p.category || '', p.token,
      p.email_sent_at ? fmtKST(p.email_sent_at) : '',
      p.responded ? 'Y' : 'N',
      `https://burn001.github.io/auri-ai-survey/?token=${p.token}`]);
  });
  const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'participants_links.csv';
  a.click();
}

// ── Email Preview Modal ──
let _previewBlobUrl = null;
async function previewEmail() {
  try {
    const res = await fetch(API + '/api/admin/email/preview', {
      method: 'POST',
      headers: { 'X-Admin-Key': ADMIN_KEY, 'Content-Type': 'application/json' },
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const html = await res.text();
    if (_previewBlobUrl) URL.revokeObjectURL(_previewBlobUrl);
    _previewBlobUrl = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
    document.getElementById('preview-body').innerHTML = `<iframe src="${_previewBlobUrl}"></iframe>`;
    document.getElementById('preview-modal').style.display = 'flex';
  } catch (e) {
    toast('미리보기 실패: ' + e.message, 'error');
  }
}

function closePreview(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('preview-modal').style.display = 'none';
  document.getElementById('preview-body').innerHTML = '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.getElementById('preview-modal').style.display === 'flex') {
    closePreview();
  }
});

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
