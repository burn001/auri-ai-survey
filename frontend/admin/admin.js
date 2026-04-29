const API = 'https://alris.ddns.net:8443';
const SURVEY_BASE = 'https://burn001.github.io/auri-ai-survey';
let ADMIN_TOKEN = '';
let ADMIN_PROFILE = null;  // { name, email, role }

// ── Auth ──
async function doLogin() {
  const input = document.getElementById('admin-token-input').value.trim();
  if (!input) return;
  ADMIN_TOKEN = input;
  try {
    const who = await api('/ai/api/admin/me');
    ADMIN_PROFILE = who;
    sessionStorage.setItem('adminToken', ADMIN_TOKEN);
    document.getElementById('login').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    const label = document.getElementById('admin-profile');
    if (label) label.textContent = `${who.name || ''} (${who.email || ''})`;
    loadDashboard();
  } catch {
    document.getElementById('login-error').textContent = '관리자 토큰이 유효하지 않습니다';
    ADMIN_TOKEN = '';
  }
}

function logout() {
  sessionStorage.removeItem('adminToken');
  ADMIN_TOKEN = '';
  ADMIN_PROFILE = null;
  document.getElementById('app').style.display = 'none';
  document.getElementById('login').style.display = 'flex';
}

// Auto-login — URL ?token=... 우선, 이후 sessionStorage 복원
(function init() {
  const urlToken = new URLSearchParams(location.search).get('token');
  if (urlToken) {
    sessionStorage.setItem('adminToken', urlToken);
    history.replaceState(null, '', location.pathname + location.hash);
  }
  const saved = sessionStorage.getItem('adminToken');
  if (saved) {
    ADMIN_TOKEN = saved;
    const el = document.getElementById('admin-token-input');
    if (el) el.value = saved;
    doLogin();
  }
  const inp = document.getElementById('admin-token-input');
  if (inp) inp.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
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
    headers: { 'X-Admin-Token': ADMIN_TOKEN, 'Content-Type': 'application/json', ...(opts.headers || {}) },
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
const CAT_COLORS = {
  '설계': '#3b82f6',
  '시공': '#22c55e',
  '유지관리': '#8b5cf6',
  '건축행정': '#14b8a6',
  '연구진': '#f59e0b',
  '기타': '#94a3b8',
  '미분류': '#d1d5db',
};
const CAT_ORDER = ['설계', '시공', '유지관리', '건축행정', '연구진', '기타', '미분류'];

async function loadDashboard() {
  const data = await api('/ai/api/admin/stats');
  const cats = data.by_category;

  document.getElementById('stat-cards').innerHTML = `
    <div class="stat-card"><div class="label">전체 대상자</div><div class="value">${data.total_participants}</div></div>
    <div class="stat-card"><div class="label">응답 완료</div><div class="value">${data.total_responses}</div></div>
    <div class="stat-card"><div class="label">응답률</div><div class="value">${data.total_participants ? (data.total_responses / data.total_participants * 100).toFixed(1) : 0}%</div></div>
  `;

  document.getElementById('cat-bars').innerHTML = '<h3 style="font-size:14px;font-weight:600;margin-bottom:12px">직군별 응답 현황</h3>' +
    CAT_ORDER.filter(c => cats[c]).map(c => {
      const d = cats[c];
      const pct = d.participants ? (d.responded / d.participants * 100).toFixed(0) : 0;
      return `<div class="cat-row">
        <span class="cat-label">${c}</span>
        <div class="cat-track"><div class="cat-fill" style="width:${pct}%;background:${CAT_COLORS[c] || '#aaa'}"></div></div>
        <span class="cat-count">${d.responded} / ${d.participants} (${pct}%)</span>
      </div>`;
    }).join('');
}

// ── Participants & Email ──
let pPage = 0;
const P_LIMIT = 50;
let pCache = [];
let pSelected = new Set();
let pFilteredView = [];

function fmtKST(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(d).replace(', ', ' ');
}

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
  const src = document.getElementById('p-source')?.value || '';
  const params = ['skip=0', 'limit=5000'];
  if (cat) params.push(`category=${encodeURIComponent(cat)}`);
  if (src) params.push(`source=${encodeURIComponent(src)}`);
  const data = await api('/ai/api/admin/participants?' + params.join('&'));
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
      <th>이름</th><th>소속</th><th>직군</th><th>출처</th><th>이메일</th>
      <th>발송</th><th>응답</th><th>토큰</th>
    </tr></thead>
    <tbody>${rows.map(p => {
      const count = p.email_sent_count || 0;
      const lastAt = p.email_last_sent_at || p.email_sent_at;
      const lastStatus = p.email_last_status || (p.email_sent ? 'sent' : '');
      const lastType = p.email_last_type || '';
      const typeLabel = { invite: '초대', reminder: '추가요청', deadline: '마감알림', custom: '사용자' }[lastType] || '';
      const sentTime = lastAt ? `${fmtKST(lastAt)}<br><span style="color:var(--text3);font-size:11px">${relTime(lastAt)}</span>` : '';

      let sendBadge;
      if (lastStatus === 'failed') {
        sendBadge = `<span class="badge badge-red">실패</span>` +
          (p.email_last_error ? `<div style="font-size:10px;color:#c00;margin-top:2px;max-width:160px;word-break:break-all">${(p.email_last_error || '').slice(0, 60)}</div>` : '');
      } else if (count > 0 || p.email_sent) {
        sendBadge = `<span class="badge badge-green">발송 ${count || 1}회</span>` +
          (typeLabel ? `<span style="font-size:10px;color:var(--text3);margin-left:4px">${typeLabel}</span>` : '') +
          `<div style="font-size:11px;color:var(--text3);margin-top:2px">${sentTime}</div>`;
      } else {
        sendBadge = '<span class="badge badge-gray">미발송</span>';
      }
      const logBtn = (count > 0 || lastStatus === 'failed')
        ? `<button class="btn-log" title="발송 이력" onclick="showEmailLogs('${p.token}', '${(p.name || '').replace(/'/g, "\\'")}')">📜</button>`
        : '';

      const respBadge = p.responded
        ? `<span class="badge badge-blue">응답</span><div style="font-size:11px;color:var(--text3);margin-top:2px">${fmtKST(p.response_submitted_at)}</div>`
        : ((count > 0 || p.email_sent) ? '<span class="badge badge-orange">미응답</span>' : '<span class="badge badge-gray">-</span>');
      const link = `${SURVEY_BASE}/?token=${p.token}`;
      const sourceBadge = p.source === 'staff'
        ? '<span class="badge badge-orange">직원 테스트</span>'
        : (p.source === 'self'
          ? '<span class="badge badge-purple">자가등록</span>'
          : '<span class="badge badge-gray">사전 import</span>');
      return `<tr>
        <td class="checkbox-col"><input type="checkbox" ${pSelected.has(p.token) ? 'checked' : ''} onchange="toggleRowSelect('${p.token}', this.checked)"></td>
        <td>${p.name || (p.reward_name ? p.reward_name : '<span style="color:var(--text3)">(이름 미수집)</span>')}</td>
        <td>${p.org || ''}</td>
        <td><span class="badge badge-blue">${p.category || ''}</span></td>
        <td>${sourceBadge}</td>
        <td style="font-size:12px">${p.email}</td>
        <td style="min-width:180px">${sendBadge} ${logBtn}</td>
        <td style="min-width:150px">${respBadge}</td>
        <td><code style="font-size:11px;cursor:pointer" title="클릭하여 링크 복사" onclick="navigator.clipboard.writeText('${link}');toast('링크 복사됨')">${p.token}</code></td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;

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
document.getElementById('p-source')?.addEventListener('change', () => loadParticipants(0));
document.getElementById('p-search').addEventListener('input', () => { pPage = 0; renderParticipants(); });
document.getElementById('p-send-status').addEventListener('change', () => { pPage = 0; renderParticipants(); });
document.getElementById('p-resp-status').addEventListener('change', () => { pPage = 0; renderParticipants(); });

async function sendSelected() {
  if (pSelected.size === 0) return;
  const type = await promptEmailType(pSelected.size);
  if (!type) return;
  await runSend([...pSelected], type);
}

async function sendToUnresponded() {
  const targets = pFilteredView.filter(p => (p.email_sent_count || 0) > 0 || p.email_sent).filter(p => !p.responded).map(p => p.token);
  if (targets.length === 0) { toast('현재 뷰에 미응답 대상이 없습니다', 'error'); return; }
  if (!confirm(`현재 필터에 해당하는 미응답자 ${targets.length}명에게 추가 요청 메일을 발송합니다. 계속할까요?`)) return;
  await runSend(targets, 'reminder');
}

function promptEmailType(count) {
  return new Promise((resolve) => {
    const typeLabels = {
      invite: '① 초대 (invite)',
      reminder: '② 추가 요청 (reminder)',
      deadline: '③ 마감 알림 (deadline)',
    };
    const msg = `${count}명에게 이메일을 발송합니다.\n\n` +
      `발송 타입을 선택하세요:\n` +
      `  1 = 초대 (최초 발송)\n` +
      `  2 = 추가 요청 (리마인더)\n` +
      `  3 = 마감 알림\n\n` +
      `번호 입력 (취소하려면 빈 값):`;
    const input = prompt(msg, '1');
    if (!input) return resolve(null);
    const map = { '1': 'invite', '2': 'reminder', '3': 'deadline' };
    const type = map[input.trim()] || null;
    if (!type) { toast('잘못된 입력', 'error'); return resolve(null); }
    if (!confirm(`${typeLabels[type]} 타입으로 ${count}명에게 발송합니다. 계속할까요?`)) return resolve(null);
    resolve(type);
  });
}

async function runSend(tokens, type = 'invite') {
  const btn = document.getElementById('btn-send');
  btn.disabled = true;
  btn.textContent = `발송 중 (${tokens.length})...`;
  try {
    const result = await api('/ai/api/admin/email/send', {
      method: 'POST',
      body: JSON.stringify({ tokens, type }),
    });
    toast(`발송 완료 [${type}]: ${result.sent}건 성공${result.failed ? `, ${result.failed}건 실패` : ''}`);
    await loadParticipants(pPage);
  } catch (e) {
    toast('발송 실패: ' + e.message, 'error');
  }
}

// ── Email Log Modal ──
async function showEmailLogs(token, name) {
  try {
    const data = await api(`/ai/api/admin/email/logs?token=${encodeURIComponent(token)}&limit=100`);
    const logs = data.data || [];
    const typeLabels = { invite: '초대', reminder: '추가요청', deadline: '마감알림', custom: '사용자' };

    const rows = logs.map(l => {
      const statusBadge = l.status === 'sent'
        ? '<span class="badge badge-green">성공</span>'
        : '<span class="badge badge-red">실패</span>';
      return `<tr>
        <td style="font-size:11px">${fmtKST(l.sent_at)}<br><span style="color:var(--text3);font-size:10px">${relTime(l.sent_at)}</span></td>
        <td>${statusBadge}</td>
        <td><span class="badge badge-blue">${typeLabels[l.type] || l.type || '-'}</span></td>
        <td style="font-size:11px">${l.subject || ''}</td>
        <td style="font-size:11px">${l.admin_name || l.admin_email || ''}</td>
        <td style="font-size:10px;color:#c00;max-width:240px;word-break:break-all">${l.error || ''}</td>
      </tr>`;
    }).join('');

    const body = logs.length === 0
      ? '<p style="text-align:center;color:var(--text3);padding:40px">이력이 없습니다.</p>'
      : `<table class="log-table">
          <thead><tr><th>시각</th><th>상태</th><th>타입</th><th>제목</th><th>발송자</th><th>오류</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;

    document.getElementById('log-modal-title').textContent = `이메일 발송 이력 — ${name} (${logs.length}건)`;
    document.getElementById('log-modal-body').innerHTML = body;
    document.getElementById('log-modal').style.display = 'flex';
  } catch (e) {
    toast('이력 조회 실패: ' + e.message, 'error');
  }
}

function closeLogModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('log-modal').style.display = 'none';
}

function exportParticipantLinks() {
  const rows = [['name', 'email', 'org', 'category', 'token', 'email_sent_at_kst', 'responded', 'survey_link']];
  pCache.forEach(p => {
    rows.push([p.name, p.email, p.org || '', p.category || '', p.token,
      p.email_sent_at ? fmtKST(p.email_sent_at) : '',
      p.responded ? 'Y' : 'N',
      `${SURVEY_BASE}/?token=${p.token}`]);
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
    const res = await fetch(API + '/ai/api/admin/email/preview', {
      method: 'POST',
      headers: { 'X-Admin-Token': ADMIN_TOKEN, 'Content-Type': 'application/json' },
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
let rCache = [];
async function loadResponses() {
  const cat = document.getElementById('r-category').value;
  const src = document.getElementById('r-source')?.value || '';
  const q = `?skip=0&limit=200`
    + (cat ? `&category=${cat}` : '')
    + (src ? `&source=${src}` : '');
  const data = await api('/ai/api/admin/responses' + q);
  rCache = data.data;

  document.getElementById('r-table').innerHTML = `<table>
    <thead><tr><th>이름</th><th>소속</th><th>직군</th><th>출처</th><th>제출일시</th><th>수정일시</th><th>상세</th></tr></thead>
    <tbody>${data.data.map(r => {
      const cnt = r.comment_count || 0;
      const commentBadge = cnt > 0
        ? `<span class="badge badge-orange" style="margin-left:4px">💬 ${cnt}</span>`
        : '';
      const sourceBadge = r.source === 'staff'
        ? '<span class="badge badge-orange">직원 테스트</span>'
        : (r.source === 'self'
          ? '<span class="badge badge-purple">자가등록</span>'
          : '<span class="badge badge-gray">사전 import</span>');
      return `<tr>
        <td>${r.name || ''}</td>
        <td>${r.org || ''}</td>
        <td><span class="badge badge-blue">${r.category || ''}</span></td>
        <td>${sourceBadge}</td>
        <td style="font-size:12px">${r.submitted_at ? new Date(r.submitted_at).toLocaleString('ko') : ''}</td>
        <td style="font-size:12px">${r.updated_at ? new Date(r.updated_at).toLocaleString('ko') : '-'}</td>
        <td><button class="btn btn-sm btn-outline" onclick="showResponseDetail('${r.token}')">열기</button>${commentBadge}</td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;
}

// ── Threads (관리자 측) ──
const STATUS_META = {
  open:      { label: '열림',     icon: '📌', color: '#6b7280', bg: '#f3f4f6' },
  in_review: { label: '검토중',   icon: '🟡', color: '#b45309', bg: '#fef3c7' },
  resolved:  { label: '반영완료', icon: '🟢', color: '#15803d', bg: '#dcfce7' },
  rejected:  { label: '보류',     icon: '⚪', color: '#4b5563', bg: '#e5e7eb' },
};

let THREADS_CACHE = {};
let THREAD_DRAFTS = {};
let THREAD_REPLY_DRAFTS = {};
let THREAD_OPEN_REPLY = null;
let THREAD_OPEN_EDIT = null;
let THREAD_EDIT_DRAFTS = {};

async function fetchThreads() {
  try {
    const data = await api('/ai/api/admin/threads');
    THREADS_CACHE = data.threads || {};
  } catch (e) {
    console.warn('threads load failed', e);
    THREADS_CACHE = {};
  }
}

function buildTree(qid) {
  const all = THREADS_CACHE[qid] || [];
  const byId = new Map(all.map(c => [c.id, { ...c, children: [] }]));
  const roots = [];
  for (const node of byId.values()) {
    if (node.parent_id && byId.has(node.parent_id)) {
      byId.get(node.parent_id).children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function fmtRel(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return '방금';
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days}일 전`;
  return fmtKST(iso);
}

function renderThreadAdmin(qid) {
  const roots = buildTree(qid);
  const totalCount = (THREADS_CACHE[qid] || []).length;
  const body = totalCount === 0
    ? '<p class="thread-empty">등록된 코멘트 없음.</p>'
    : roots.map(r => renderCommentNodeAdmin(qid, r, 0)).join('');
  const draft = THREAD_DRAFTS[qid] || '';
  const newForm = `
    <div class="thread-new-form">
      <textarea class="thread-textarea" data-thread-new="${qid}" rows="2"
        placeholder="이 문항에 대한 관리자 코멘트…">${escapeHtml(draft)}</textarea>
      <div class="thread-form-actions">
        <button class="btn btn-thread btn-sm" data-thread-submit="${qid}">관리자 코멘트 등록</button>
      </div>
    </div>
  `;
  return `
    <div class="reviewer-thread admin-thread" data-qid="${qid}">
      <div class="thread-header">
        <span class="thread-title">💬 검토 코멘트 (${qid})</span>
        <span class="thread-count">${totalCount}건</span>
        <button class="thread-refresh-btn" data-thread-refresh="${qid}" title="새로고침">↻</button>
      </div>
      <div class="thread-body">${body}</div>
      ${newForm}
    </div>
  `;
}

function renderCommentNodeAdmin(qid, c, depth) {
  const status = STATUS_META[c.status] || STATUS_META.open;
  const isMine = c.author_token === ADMIN_TOKEN;
  const roleBadge = c.author_role === 'admin'
    ? '<span class="role-badge role-admin">관리자</span>'
    : '<span class="role-badge role-reviewer">연구진</span>';
  const orgPart = c.author_org ? ` · ${escapeHtml(c.author_org)}` : '';
  const editedMark = c.updated_at ? ' <span class="edited-mark">(수정됨)</span>' : '';
  const isEditing = THREAD_OPEN_EDIT === c.id;
  const editDraft = THREAD_EDIT_DRAFTS[c.id] ?? c.text;

  const textHtml = isEditing
    ? `
      <div class="comment-edit-form">
        <textarea class="thread-textarea" data-thread-edit="${c.id}" rows="2">${escapeHtml(editDraft)}</textarea>
        <div class="thread-form-actions">
          <button class="btn btn-thread btn-sm" data-thread-edit-save="${c.id}" data-qid="${qid}">저장</button>
          <button class="btn btn-thread-cancel btn-sm" data-thread-edit-cancel="${c.id}">취소</button>
        </div>
      </div>
    `
    : `<div class="comment-text">${escapeHtml(c.text).replace(/\n/g, '<br>')}${editedMark}</div>`;

  const statusOptions = Object.entries(STATUS_META).map(
    ([k, v]) => `<option value="${k}" ${c.status === k ? 'selected' : ''}>${v.icon} ${v.label}</option>`
  ).join('');
  const statusChangedNote = c.status_changed_by
    ? ` <span class="status-changed-note">${fmtRel(c.status_changed_at)} · ${escapeHtml(c.status_changed_by)}</span>`
    : '';

  const actions = [];
  actions.push(`<button class="btn-link-sm" data-thread-reply="${c.id}" data-qid="${qid}">↳ 답글</button>`);
  if (isMine && !isEditing) {
    actions.push(`<button class="btn-link-sm" data-thread-edit-open="${c.id}">편집</button>`);
  }
  actions.push(`<button class="btn-link-sm danger" data-thread-delete="${c.id}" data-qid="${qid}">삭제</button>`);

  const replyOpen = THREAD_OPEN_REPLY === c.id;
  const replyDraft = THREAD_REPLY_DRAFTS[c.id] || '';
  const replyForm = replyOpen
    ? `
      <div class="comment-reply-form">
        <textarea class="thread-textarea" data-thread-reply-input="${c.id}" rows="2"
          placeholder="답글 작성…">${escapeHtml(replyDraft)}</textarea>
        <div class="thread-form-actions">
          <button class="btn btn-thread btn-sm" data-thread-reply-submit="${c.id}" data-qid="${qid}">답글 등록</button>
          <button class="btn btn-thread-cancel btn-sm" data-thread-reply-cancel="${c.id}">취소</button>
        </div>
      </div>
    `
    : '';

  const statusBar = `
    <div class="comment-status-bar">
      <span class="comment-status" style="background:${status.bg};color:${status.color}">${status.icon} ${status.label}</span>
      <select class="status-select" data-thread-status="${c.id}" data-qid="${qid}">${statusOptions}</select>
      ${statusChangedNote}
    </div>
  `;

  const childrenHtml = (c.children || [])
    .map(cc => renderCommentNodeAdmin(qid, cc, depth + 1))
    .join('');

  return `
    <div class="comment-node depth-${Math.min(depth, 3)}" data-cid="${c.id}">
      <div class="comment-card">
        <div class="comment-meta">
          <span class="comment-author">${escapeHtml(c.author_name || '익명')}</span>
          ${roleBadge}
          <span class="comment-org">${orgPart}</span>
          <span class="comment-time">· ${fmtRel(c.created_at)}</span>
        </div>
        ${textHtml}
        ${statusBar}
        <div class="comment-actions">${actions.join(' · ')}</div>
      </div>
      ${replyForm}
      ${childrenHtml ? `<div class="comment-children">${childrenHtml}</div>` : ''}
    </div>
  `;
}

async function adminPostComment(qid, text, parentId) {
  const body = parentId ? { text, parent_id: parentId } : { text };
  const res = await api(`/ai/api/admin/threads/${qid}`, { method: 'POST', body: JSON.stringify(body) });
  if (!THREADS_CACHE[qid]) THREADS_CACHE[qid] = [];
  THREADS_CACHE[qid].push(res.comment);
  return res.comment;
}

async function adminUpdateComment(qid, cid, payload) {
  const res = await api(`/ai/api/admin/threads/${qid}/${cid}`, { method: 'PATCH', body: JSON.stringify(payload) });
  const list = THREADS_CACHE[qid] || [];
  const idx = list.findIndex(c => c.id === cid);
  if (idx >= 0) list[idx] = res.comment;
  return res.comment;
}

async function adminDeleteComment(qid, cid) {
  const res = await api(`/ai/api/admin/threads/${qid}/${cid}`, { method: 'DELETE' });
  if (res.status === 'deleted') {
    THREADS_CACHE[qid] = (THREADS_CACHE[qid] || []).filter(c => c.id !== cid);
  } else {
    const list = THREADS_CACHE[qid] || [];
    const idx = list.findIndex(c => c.id === cid);
    if (idx >= 0) list[idx] = { ...list[idx], text: '(관리자가 삭제한 코멘트)' };
  }
}

function bindThreadEventsAdmin(rootEl) {
  rootEl.querySelectorAll('[data-thread-new]').forEach(el => {
    const qid = el.dataset.threadNew;
    el.addEventListener('input', () => { THREAD_DRAFTS[qid] = el.value; });
  });
  rootEl.querySelectorAll('[data-thread-submit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const qid = btn.dataset.threadSubmit;
      const text = (THREAD_DRAFTS[qid] || '').trim();
      if (!text) return toast('내용을 입력해 주세요', 'error');
      try {
        await adminPostComment(qid, text);
        THREAD_DRAFTS[qid] = '';
        refreshThreadAdmin(qid);
      } catch (e) { toast('등록 실패: ' + e.message, 'error'); }
    });
  });

  rootEl.querySelectorAll('[data-thread-reply]').forEach(btn => {
    btn.addEventListener('click', () => {
      THREAD_OPEN_REPLY = btn.dataset.threadReply;
      refreshThreadAdmin(btn.dataset.qid);
    });
  });
  rootEl.querySelectorAll('[data-thread-reply-input]').forEach(el => {
    const pid = el.dataset.threadReplyInput;
    el.addEventListener('input', () => { THREAD_REPLY_DRAFTS[pid] = el.value; });
  });
  rootEl.querySelectorAll('[data-thread-reply-submit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const pid = btn.dataset.threadReplySubmit;
      const qid = btn.dataset.qid;
      const text = (THREAD_REPLY_DRAFTS[pid] || '').trim();
      if (!text) return toast('내용을 입력해 주세요', 'error');
      try {
        await adminPostComment(qid, text, pid);
        delete THREAD_REPLY_DRAFTS[pid];
        THREAD_OPEN_REPLY = null;
        refreshThreadAdmin(qid);
      } catch (e) { toast('등록 실패: ' + e.message, 'error'); }
    });
  });
  rootEl.querySelectorAll('[data-thread-reply-cancel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const pid = btn.dataset.threadReplyCancel;
      delete THREAD_REPLY_DRAFTS[pid];
      THREAD_OPEN_REPLY = null;
      const wrap = btn.closest('.reviewer-thread');
      if (wrap) refreshThreadAdmin(wrap.dataset.qid);
    });
  });

  rootEl.querySelectorAll('[data-thread-edit-open]').forEach(btn => {
    btn.addEventListener('click', () => {
      const cid = btn.dataset.threadEditOpen;
      THREAD_OPEN_EDIT = cid;
      const wrap = btn.closest('.reviewer-thread');
      const qid = wrap?.dataset.qid;
      const list = THREADS_CACHE[qid] || [];
      const c = list.find(x => x.id === cid);
      if (c) THREAD_EDIT_DRAFTS[cid] = c.text;
      if (qid) refreshThreadAdmin(qid);
    });
  });
  rootEl.querySelectorAll('[data-thread-edit]').forEach(el => {
    const cid = el.dataset.threadEdit;
    el.addEventListener('input', () => { THREAD_EDIT_DRAFTS[cid] = el.value; });
  });
  rootEl.querySelectorAll('[data-thread-edit-save]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const cid = btn.dataset.threadEditSave;
      const qid = btn.dataset.qid;
      const text = (THREAD_EDIT_DRAFTS[cid] || '').trim();
      if (!text) return toast('내용을 입력해 주세요', 'error');
      try {
        await adminUpdateComment(qid, cid, { text });
        delete THREAD_EDIT_DRAFTS[cid];
        THREAD_OPEN_EDIT = null;
        refreshThreadAdmin(qid);
      } catch (e) { toast('수정 실패: ' + e.message, 'error'); }
    });
  });
  rootEl.querySelectorAll('[data-thread-edit-cancel]').forEach(btn => {
    btn.addEventListener('click', () => {
      const cid = btn.dataset.threadEditCancel;
      delete THREAD_EDIT_DRAFTS[cid];
      THREAD_OPEN_EDIT = null;
      const wrap = btn.closest('.reviewer-thread');
      if (wrap) refreshThreadAdmin(wrap.dataset.qid);
    });
  });
  rootEl.querySelectorAll('[data-thread-delete]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('이 코멘트를 삭제하시겠습니까?')) return;
      const cid = btn.dataset.threadDelete;
      const qid = btn.dataset.qid;
      try {
        await adminDeleteComment(qid, cid);
        refreshThreadAdmin(qid);
      } catch (e) { toast('삭제 실패: ' + e.message, 'error'); }
    });
  });
  rootEl.querySelectorAll('[data-thread-status]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const cid = sel.dataset.threadStatus;
      const qid = sel.dataset.qid;
      const newStatus = sel.value;
      try {
        await adminUpdateComment(qid, cid, { status: newStatus });
        refreshThreadAdmin(qid);
        toast(`상태 → ${STATUS_META[newStatus]?.label}`);
      } catch (e) { toast('상태 변경 실패: ' + e.message, 'error'); }
    });
  });
  rootEl.querySelectorAll('[data-thread-refresh]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const qid = btn.dataset.threadRefresh;
      await fetchThreads();
      refreshThreadAdmin(qid);
    });
  });
}

function refreshThreadAdmin(qid) {
  const wrap = document.querySelector(`#resp-modal .reviewer-thread[data-qid="${qid}"]`);
  if (!wrap) return;
  wrap.outerHTML = renderThreadAdmin(qid);
  const fresh = document.querySelector(`#resp-modal .reviewer-thread[data-qid="${qid}"]`);
  if (fresh) bindThreadEventsAdmin(fresh);
}

// ── Response Detail Modal ──
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatAnswer(q, val, allResp) {
  if (val === undefined || val === null || val === '') {
    return '<span style="color:var(--text3)">(무응답)</span>';
  }
  const QT = window.Q_TYPE || {};
  if (q.type === QT.SINGLE || q.type === QT.SINGLE_WITH_OTHER) {
    if (val === 'other') {
      const other = allResp[q.id + '_other'] || '';
      return `<span class="badge badge-blue">기타</span> <span>${escapeHtml(other)}</span>`;
    }
    const idx = typeof val === 'number' ? val : parseInt(val, 10);
    const opt = q.options?.[idx];
    return opt ? `<span>${escapeHtml(opt)}</span> <code style="color:var(--text3);font-size:11px">(${idx})</code>` : `<code>${escapeHtml(String(val))}</code>`;
  }
  if ([QT.MULTI, QT.MULTI_WITH_OTHER, QT.MULTI_LIMIT, QT.MULTI_LIMIT_OTHER].includes(q.type)) {
    if (!Array.isArray(val)) return `<code>${escapeHtml(JSON.stringify(val))}</code>`;
    const parts = val.map(v => {
      if (v === 'other') {
        const other = allResp[q.id + '_other'] || '';
        return `<span class="chip chip-other">기타: ${escapeHtml(other)}</span>`;
      }
      const idx = typeof v === 'number' ? v : parseInt(v, 10);
      const opt = q.options?.[idx];
      return `<span class="chip">${escapeHtml(opt || String(v))}</span>`;
    });
    return parts.join(' ');
  }
  if (q.type === QT.LIKERT_TABLE) {
    if (typeof val !== 'object') return `<code>${escapeHtml(JSON.stringify(val))}</code>`;
    const rows = q.items.map((item, i) => {
      const v = val[i];
      const label = v ? (q.scaleLabels?.[v - 1] || v) : '(무응답)';
      return `<tr><td style="font-size:12px">${escapeHtml(item)}</td><td><strong>${escapeHtml(String(label))}</strong></td></tr>`;
    });
    return `<table class="inline-likert"><tbody>${rows.join('')}</tbody></table>`;
  }
  if (q.type === QT.IPA_MATRIX) {
    if (typeof val !== 'object') return `<code>${escapeHtml(JSON.stringify(val))}</code>`;
    const rows = q.items.map((item, i) => {
      const cell = val[i] || {};
      const imp = cell.imp;
      const exp = cell.exp;
      const impLabel = imp ? `${imp} (${q.scaleLabelsImportance?.[imp - 1] || ''})` : '(무응답)';
      const expLabel = exp === 'N' ? 'N (해당 없음)' : (exp ? `${exp} (${q.scaleLabelsExperience?.[exp - 1] || ''})` : '(무응답)');
      return `<tr><td style="font-size:12px">${escapeHtml(item)}</td><td><strong>${escapeHtml(impLabel)}</strong></td><td><strong>${escapeHtml(expLabel)}</strong></td></tr>`;
    });
    return `<table class="inline-likert"><thead><tr><th>항목</th><th>중요도</th><th>체감도</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }
  if (q.type === QT.TOOL_MATRIX) {
    if (typeof val !== 'object') return `<code>${escapeHtml(JSON.stringify(val))}</code>`;
    const cur = Array.isArray(val.current) ? val.current : [];
    const fut = Array.isArray(val.future) ? val.future : [];
    const otherIdx = q.items.length;
    const rows = q.items.map((item, i) => {
      const c = cur.includes(i) ? '✓' : '';
      const f = fut.includes(i) ? '✓' : '';
      return `<tr><td style="font-size:12px">${escapeHtml(item)}</td><td style="text-align:center">${c}</td><td style="text-align:center">${f}</td></tr>`;
    });
    if (q.otherLabel) {
      const otherText = (val.other_text || '').trim();
      const c = cur.includes(otherIdx) ? '✓' : '';
      const f = fut.includes(otherIdx) ? '✓' : '';
      const label = otherText ? `${q.otherLabel}: ${otherText}` : q.otherLabel;
      rows.push(`<tr><td style="font-size:12px">${escapeHtml(label)}</td><td style="text-align:center">${c}</td><td style="text-align:center">${f}</td></tr>`);
    }
    return `<table class="inline-likert"><thead><tr><th>도구</th><th>${escapeHtml(q.columnLabels?.[0] || '현재')}</th><th>${escapeHtml(q.columnLabels?.[1] || '희망')}</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }
  if (q.type === QT.TEXT) {
    return `<div class="response-text">${escapeHtml(String(val)).replace(/\n/g, '<br>')}</div>`;
  }
  return `<code>${escapeHtml(JSON.stringify(val))}</code>`;
}

async function showResponseDetail(token) {
  const row = rCache.find(r => r.token === token);
  if (!row) { toast('응답을 찾을 수 없습니다', 'error'); return; }
  const respMap = row.responses || {};
  const sections = window.SURVEY_SECTIONS;
  const QT = window.Q_TYPE || {};

  await fetchThreads();

  const meta = `
    <div class="resp-meta">
      <dl>
        <dt>응답자</dt><dd>${escapeHtml(row.name || '-')}</dd>
        <dt>소속</dt><dd>${escapeHtml(row.org || '-')}</dd>
        <dt>직군</dt><dd><span class="badge badge-blue">${escapeHtml(row.category || '-')}</span></dd>
        <dt>토큰</dt><dd><code style="font-size:11px">${escapeHtml(token)}</code></dd>
        <dt>제출</dt><dd style="font-size:12px">${row.submitted_at ? new Date(row.submitted_at).toLocaleString('ko') : '-'}</dd>
        ${row.updated_at ? `<dt>수정</dt><dd style="font-size:12px">${new Date(row.updated_at).toLocaleString('ko')}</dd>` : ''}
        <dt>이 응답자 코멘트</dt><dd>${row.comment_count || 0}건</dd>
      </dl>
    </div>
  `;

  function threadBlockIfAny(qid) {
    const cnt = (THREADS_CACHE[qid] || []).length;
    if (cnt === 0) return '';
    return renderThreadAdmin(qid);
  }

  let body;
  if (!sections) {
    body = `<p style="color:#c00;font-size:12px;margin-bottom:8px">(questions.js 스키마 미로드 — raw JSON으로 표시합니다)</p>
            <pre style="white-space:pre-wrap;font-size:11px;background:#f9fafb;padding:12px;border-radius:6px">${escapeHtml(JSON.stringify(respMap, null, 2))}</pre>`;
  } else {
    const items = [];
    for (const s of sections) {
      items.push(`<h3 class="resp-section">${escapeHtml(s.title)}</h3>`);
      for (const q of s.questions) {
        if (q.type === QT.SUB_QUESTIONS) {
          items.push(`<div class="resp-q"><div class="resp-q-id">${q.id}</div><div class="resp-q-text">${escapeHtml(q.text)}</div>`);
          for (const sq of q.subQuestions) {
            const v = respMap[sq.id];
            items.push(`<div class="resp-sub"><span class="resp-sub-label">${escapeHtml(sq.label)}</span> ${formatAnswer(sq, v, respMap)}</div>`);
            items.push(threadBlockIfAny(sq.id));
          }
          items.push(threadBlockIfAny(q.id));
          items.push(`</div>`);
        } else {
          const v = respMap[q.id];
          items.push(`<div class="resp-q"><div class="resp-q-id">${q.id}</div><div class="resp-q-text">${escapeHtml(q.text)}</div><div class="resp-a">${formatAnswer(q, v, respMap)}</div>`);
          items.push(threadBlockIfAny(q.id));
          items.push(`</div>`);
        }
      }
    }
    body = items.join('');
  }

  document.getElementById('resp-modal-title').textContent = `응답 상세 — ${row.name || ''} (${row.org || ''})`;
  document.getElementById('resp-modal-body').innerHTML = meta + body;
  document.getElementById('resp-modal').style.display = 'flex';
  bindThreadEventsAdmin(document.getElementById('resp-modal-body'));
}

function closeRespModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('resp-modal').style.display = 'none';
}

async function downloadCSV() {
  try {
    const src = document.getElementById('r-source')?.value || '';
    const q = src ? `?source=${src}` : '';
    const res = await fetch(API + '/ai/api/admin/export' + q, {
      headers: { 'X-Admin-Token': ADMIN_TOKEN },
    });
    if (!res.ok) throw new Error('No data');
    const blob = await res.blob();
    const suffix = src ? `_${src}` : '';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `survey_responses${suffix}.csv`;
    a.click();
    toast('CSV 다운로드 완료');
  } catch (e) {
    toast('다운로드 실패 (응답 데이터 없음)', 'error');
  }
}
