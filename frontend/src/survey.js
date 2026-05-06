import { sections, Q_TYPE, SURVEY_META, SURVEY_VERSION, REWARD_CONSENT_NOTICE, REWARD_META_LABEL } from './questions.js';

const STORAGE_KEY = 'auri_survey_responses';
const STORAGE_PAGE_KEY = 'auri_survey_page';
const API_BASE = import.meta.env.VITE_API_BASE || '';

const STATUS_META = {
  open:      { label: '열림',     icon: '📌', color: '#6b7280', bg: '#f3f4f6' },
  in_review: { label: '검토중',   icon: '🟡', color: '#b45309', bg: '#fef3c7' },
  resolved:  { label: '반영완료', icon: '🟢', color: '#15803d', bg: '#dcfce7' },
  rejected:  { label: '보류',     icon: '⚪', color: '#4b5563', bg: '#e5e7eb' },
};

const GATE = {
  LOADING: 'loading',
  DENIED: 'denied',
  RESUBMIT_CHOICE: 'resubmit_choice',
  READ_ONLY: 'read_only',
  OPEN: 'open',
  REGISTER: 'register',           // 토큰 없는 공개 진입 — 랜딩/동의/정보입력 단계 진행
  IDENTITY_REQUIRED: 'identity',  // 익명 토큰(name='') — 응답 시작 전 신원(이름·휴대폰) 보강 필수
  CLOSED: 'closed',               // 4직군 합산 정원(300부) 도달 — 모든 신규/이어작성 차단
};

// 4직군 균등 75부 = 합산 300부 (백엔드 QUOTA_PER_CATEGORY와 일치)
const SURVEY_LIMIT = 300;

const REG_STEP = {
  LANDING: 'landing',
  CONSENT: 'consent',
  INFO: 'info',
  RECOVER: 'recover',
};

const REGISTER_DRAFT_KEY = 'auri_ai_register_draft';

// Q6 직군 옵션 텍스트 → 자가등록 category 값으로 매핑하기 위한 인덱스
const CATEGORY_TO_Q6_INDEX = {
  '설계': 0,
  '시공': 1,
  '유지관리': 2,
  '건축행정': 3,
};

const EDIT_MODE = {
  NEW: 'new',
  EDIT: 'edit',
};

export class SurveyEngine {
  constructor(container) {
    this.container = container;
    const urlParams = new URLSearchParams(window.location.search);
    this.token = urlParams.get('token');
    this.justRegistered = urlParams.get('just_registered') === '1';
    // 직원 테스트 모드: ?source=staff 진입 시 분석 제외 마커 부여
    this.isStaffMode = urlParams.get('source') === 'staff';
    this.participant = null;
    this.submitted = false;
    this.submittedAt = null;
    this.updatedAt = null;
    this.editMode = EDIT_MODE.NEW;
    this.gate = this.token ? GATE.LOADING : GATE.REGISTER;
    this.regStep = REG_STEP.LANDING;
    this.regDraft = this.loadRegisterDraft();
    this.regError = '';
    this.regSubmitting = false;
    this.surveyStatus = null;
    this.responses = this.loadResponses();
    this.threads = {};                // { qid: [comment, ...] } — fetched from server
    this.threadsLoading = false;
    this.threadDrafts = {};           // { qid: "draft text" } — top-level new comment
    this.threadReplyDrafts = {};      // { parent_id: "draft text" } — reply form
    this.threadOpenReply = null;      // 현재 답글 입력창이 열려 있는 parent_id
    this.threadOpenEdit = null;       // 현재 편집 중인 comment id
    this.threadEditDrafts = {};       // { comment_id: "edited text" }
    this.threadError = '';            // 마지막 스레드 오류 메시지
    this.currentPage = 0;
    this.visibleSections = [];
    this.editingParticipant = false;
    this.participantFormError = '';
    // 익명 토큰 신원 보강 폼 상태
    this.identityDraft = { name: '', phone: '', org: '' };
    this.identityError = '';
    this.identitySubmitting = false;

    if (this.token) {
      this.verifyToken().then(async () => {
        if (this.isReviewer()) {
          await this.fetchThreads();
        }
        this.render();
      });
    } else {
      this.fetchSurveyStatus().finally(() => this.render());
    }
  }

  async fetchSurveyStatus() {
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/status`);
      if (!res.ok) return;
      const data = await res.json();
      this.surveyStatus = data;
      if (data.is_closed) this.gate = GATE.CLOSED;
    } catch {}
  }

  async verifyToken() {
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}`);
      if (res.status === 410) {
        this.gate = GATE.CLOSED;
        return;
      }
      if (!res.ok) {
        this.gate = GATE.DENIED;
        return;
      }
      const data = await res.json();
      this.participant = data;
      this.submittedAt = data.submitted_at || null;
      this.updatedAt = data.updated_at || null;
      if (data.has_responded && data.responses) {
        this.responses = { ...this.responses, ...data.responses };
        this.saveResponses();
        this.submitted = true;
        this.gate = GATE.RESUBMIT_CHOICE;
      } else if (data.needs_identity) {
        // 익명(name 결측) 토큰 — 응답 시작 전 신원 보강 필수.
        this.identityDraft = {
          name: '',
          phone: '',
          org: data.org || '',
        };
        this.gate = GATE.IDENTITY_REQUIRED;
      } else {
        this.gate = GATE.OPEN;
      }
      // 자가등록자(또는 import 시 이미 분류된) category가 4직군 중 하나면
      // Q6 응답을 자동 채워 분기 흐름을 즉시 활성화한다.
      if (this.responses['Q6'] === undefined) {
        const idx = CATEGORY_TO_Q6_INDEX[data.category];
        if (idx !== undefined) {
          this.responses['Q6'] = idx;
          this.saveResponses();
        }
      }
    } catch {
      this.gate = GATE.DENIED;
    }
  }

  // ── Persistence: Register Draft ──
  loadRegisterDraft() {
    try {
      const saved = localStorage.getItem(REGISTER_DRAFT_KEY);
      return saved ? JSON.parse(saved) : this.emptyRegisterDraft();
    } catch { return this.emptyRegisterDraft(); }
  }

  emptyRegisterDraft() {
    return {
      email: '', name: '', org: '', category: '',
      dept: '', team: '', position: '', duty: '',
      consent_pi: false,
    };
  }

  saveRegisterDraft() {
    try {
      localStorage.setItem(REGISTER_DRAFT_KEY, JSON.stringify(this.regDraft));
    } catch {}
  }

  clearRegisterDraft() {
    try { localStorage.removeItem(REGISTER_DRAFT_KEY); } catch {}
  }

  // ── Persistence ──
  loadResponses() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved ? JSON.parse(saved) : {};
    } catch { return {}; }
  }

  saveResponses() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.responses));
    localStorage.setItem(STORAGE_PAGE_KEY, String(this.currentPage));
  }

  getResponse(id) { return this.responses[id]; }
  setResponse(id, value) {
    this.responses[id] = value;
    this.saveResponses();
  }

  isReviewer() {
    return this.participant && this.participant.category === '연구진';
  }

  // ── Review Comment Threads ──
  async fetchThreads() {
    if (!this.isReviewer() || !this.token) return;
    this.threadsLoading = true;
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/threads`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.threads = data.threads || {};
      this.threadError = '';
    } catch (e) {
      console.warn('threads fetch failed', e);
      this.threadError = '코멘트 스레드를 불러오지 못했습니다.';
    } finally {
      this.threadsLoading = false;
    }
  }

  /** qid에 달린 모든 코멘트(parent + replies)를 부모-자식 트리로 재구성 */
  buildThreadTree(qid) {
    const all = this.threads[qid] || [];
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

  renderReviewerThread(qid) {
    const roots = this.buildThreadTree(qid);
    const totalCount = (this.threads[qid] || []).length;

    let body = '';
    if (totalCount === 0) {
      body = '<p class="thread-empty">아직 등록된 코멘트가 없습니다. 첫 코멘트를 남겨보세요.</p>';
    } else {
      body = roots.map(r => this.renderCommentNode(qid, r, 0)).join('');
    }

    const draft = this.threadDrafts[qid] || '';
    const newForm = `
      <div class="thread-new-form">
        <textarea class="thread-textarea" data-thread-new="${qid}" rows="2"
          placeholder="이 문항에 대한 검토 의견·수정 요청·논의 사항을 작성하세요.">${this.escape(draft)}</textarea>
        <div class="thread-form-actions">
          <button class="btn btn-thread" data-thread-submit="${qid}">코멘트 등록</button>
        </div>
      </div>
    `;

    return `
      <div class="reviewer-thread" data-qid="${qid}">
        <div class="thread-header">
          <span class="thread-title">💬 검토 코멘트</span>
          <span class="thread-count">${totalCount}건</span>
          <span class="thread-sub">(연구진·관리자 모두에게 공개)</span>
          <button class="thread-refresh-btn" data-thread-refresh="${qid}" title="새로고침">↻</button>
        </div>
        <div class="thread-body">${body}</div>
        ${newForm}
      </div>
    `;
  }

  async refetchAndRefreshThread(qid) {
    await this.fetchThreads();
    this.refreshThread(qid);
  }

  renderCommentNode(qid, c, depth) {
    const status = STATUS_META[c.status] || STATUS_META.open;
    const isMine = c.author_token === this.token;
    const roleBadge = c.author_role === 'admin'
      ? '<span class="role-badge role-admin">관리자</span>'
      : '<span class="role-badge role-reviewer">연구진</span>';
    const orgPart = c.author_org ? ` · ${this.escape(c.author_org)}` : '';
    const editedMark = c.updated_at ? ' <span class="edited-mark">(수정됨)</span>' : '';

    const isEditing = this.threadOpenEdit === c.id;
    const editDraft = this.threadEditDrafts[c.id] ?? c.text;

    const textHtml = isEditing
      ? `
        <div class="comment-edit-form">
          <textarea class="thread-textarea" data-thread-edit="${c.id}" rows="2">${this.escape(editDraft)}</textarea>
          <div class="thread-form-actions">
            <button class="btn btn-thread btn-sm" data-thread-edit-save="${c.id}" data-qid="${qid}">저장</button>
            <button class="btn btn-thread-cancel btn-sm" data-thread-edit-cancel="${c.id}">취소</button>
          </div>
        </div>
      `
      : `<div class="comment-text">${this.escape(c.text).replace(/\n/g, '<br>')}${editedMark}</div>`;

    const actions = [];
    actions.push(`<button class="btn-link-sm" data-thread-reply="${c.id}" data-qid="${qid}">↳ 답글</button>`);
    if (isMine && !isEditing) {
      actions.push(`<button class="btn-link-sm" data-thread-edit-open="${c.id}">편집</button>`);
      actions.push(`<button class="btn-link-sm danger" data-thread-delete="${c.id}" data-qid="${qid}">삭제</button>`);
    }

    const replyOpen = this.threadOpenReply === c.id;
    const replyDraft = this.threadReplyDrafts[c.id] || '';
    const replyForm = replyOpen
      ? `
        <div class="comment-reply-form">
          <textarea class="thread-textarea" data-thread-reply-input="${c.id}" rows="2"
            placeholder="답글 작성…">${this.escape(replyDraft)}</textarea>
          <div class="thread-form-actions">
            <button class="btn btn-thread btn-sm" data-thread-reply-submit="${c.id}" data-qid="${qid}">답글 등록</button>
            <button class="btn btn-thread-cancel btn-sm" data-thread-reply-cancel="${c.id}">취소</button>
          </div>
        </div>
      `
      : '';

    const childrenHtml = (c.children || [])
      .map(cc => this.renderCommentNode(qid, cc, depth + 1))
      .join('');

    return `
      <div class="comment-node depth-${Math.min(depth, 3)}" data-cid="${c.id}">
        <div class="comment-card">
          <div class="comment-meta">
            <span class="comment-author">${this.escape(c.author_name || '익명')}</span>
            ${roleBadge}
            <span class="comment-org">${orgPart}</span>
            <span class="comment-time">· ${this.formatRelativeTime(c.created_at)}</span>
            <span class="comment-status" style="background:${status.bg};color:${status.color}">${status.icon} ${status.label}</span>
          </div>
          ${textHtml}
          <div class="comment-actions">${actions.join(' · ')}</div>
        </div>
        ${replyForm}
        ${childrenHtml ? `<div class="comment-children">${childrenHtml}</div>` : ''}
      </div>
    `;
  }

  async submitNewComment(qid) {
    const text = (this.threadDrafts[qid] || '').trim();
    if (!text) {
      alert('코멘트 내용을 입력해 주세요.');
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/threads/${qid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (!this.threads[qid]) this.threads[qid] = [];
      this.threads[qid].push(data.comment);
      this.threadDrafts[qid] = '';
      this.refreshThread(qid);
    } catch (e) {
      alert(`코멘트 등록 실패: ${e.message}`);
    }
  }

  async submitReply(parentId, qid) {
    const text = (this.threadReplyDrafts[parentId] || '').trim();
    if (!text) { alert('답글 내용을 입력해 주세요.'); return; }
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/threads/${qid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, parent_id: parentId }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (!this.threads[qid]) this.threads[qid] = [];
      this.threads[qid].push(data.comment);
      this.threadReplyDrafts[parentId] = '';
      this.threadOpenReply = null;
      this.refreshThread(qid);
    } catch (e) {
      alert(`답글 등록 실패: ${e.message}`);
    }
  }

  async submitEdit(commentId, qid) {
    const text = (this.threadEditDrafts[commentId] || '').trim();
    if (!text) { alert('내용을 입력해 주세요.'); return; }
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/threads/${qid}/${commentId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const list = this.threads[qid] || [];
      const idx = list.findIndex(c => c.id === commentId);
      if (idx >= 0) list[idx] = data.comment;
      this.threadOpenEdit = null;
      delete this.threadEditDrafts[commentId];
      this.refreshThread(qid);
    } catch (e) {
      alert(`수정 실패: ${e.message}`);
    }
  }

  async deleteComment(commentId, qid) {
    if (!confirm('이 코멘트를 삭제하시겠습니까?')) return;
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/threads/${qid}/${commentId}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.status === 'deleted') {
        this.threads[qid] = (this.threads[qid] || []).filter(c => c.id !== commentId);
      } else {
        const list = this.threads[qid] || [];
        const idx = list.findIndex(c => c.id === commentId);
        if (idx >= 0) list[idx] = { ...list[idx], text: '(작성자가 삭제한 코멘트)' };
      }
      this.refreshThread(qid);
    } catch (e) {
      alert(`삭제 실패: ${e.message}`);
    }
  }

  /** 특정 qid의 thread 영역만 다시 그린다 (전체 페이지 재렌더 회피) */
  refreshThread(qid) {
    const wrap = this.container.querySelector(`.reviewer-thread[data-qid="${qid}"]`);
    if (!wrap) return;
    wrap.outerHTML = this.renderReviewerThread(qid);
    const fresh = this.container.querySelector(`.reviewer-thread[data-qid="${qid}"]`);
    if (fresh) this.bindThreadEvents(fresh);
  }

  bindThreadEvents(scope) {
    if (!this.isReviewer()) return;
    const root = scope || this.container;

    root.querySelectorAll('[data-thread-new]').forEach(el => {
      const qid = el.dataset.threadNew;
      el.addEventListener('input', () => { this.threadDrafts[qid] = el.value; });
    });
    root.querySelectorAll('[data-thread-submit]').forEach(btn => {
      btn.addEventListener('click', () => this.submitNewComment(btn.dataset.threadSubmit));
    });

    root.querySelectorAll('[data-thread-reply]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.threadOpenReply = btn.dataset.threadReply;
        this.refreshThread(btn.dataset.qid);
      });
    });
    root.querySelectorAll('[data-thread-reply-input]').forEach(el => {
      const pid = el.dataset.threadReplyInput;
      el.addEventListener('input', () => { this.threadReplyDrafts[pid] = el.value; });
    });
    root.querySelectorAll('[data-thread-reply-submit]').forEach(btn => {
      btn.addEventListener('click', () => this.submitReply(btn.dataset.threadReplySubmit, btn.dataset.qid));
    });
    root.querySelectorAll('[data-thread-reply-cancel]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pid = btn.dataset.threadReplyCancel;
        this.threadOpenReply = null;
        delete this.threadReplyDrafts[pid];
        const card = btn.closest('.comment-node');
        const qid = card?.closest('.reviewer-thread')?.dataset.qid;
        if (qid) this.refreshThread(qid);
      });
    });

    root.querySelectorAll('[data-thread-edit-open]').forEach(btn => {
      btn.addEventListener('click', () => {
        const cid = btn.dataset.threadEditOpen;
        this.threadOpenEdit = cid;
        const card = btn.closest('.comment-node');
        const qid = card?.closest('.reviewer-thread')?.dataset.qid;
        const list = this.threads[qid] || [];
        const c = list.find(x => x.id === cid);
        if (c) this.threadEditDrafts[cid] = c.text;
        if (qid) this.refreshThread(qid);
      });
    });
    root.querySelectorAll('[data-thread-edit]').forEach(el => {
      const cid = el.dataset.threadEdit;
      el.addEventListener('input', () => { this.threadEditDrafts[cid] = el.value; });
    });
    root.querySelectorAll('[data-thread-edit-save]').forEach(btn => {
      btn.addEventListener('click', () => this.submitEdit(btn.dataset.threadEditSave, btn.dataset.qid));
    });
    root.querySelectorAll('[data-thread-edit-cancel]').forEach(btn => {
      btn.addEventListener('click', () => {
        const cid = btn.dataset.threadEditCancel;
        this.threadOpenEdit = null;
        delete this.threadEditDrafts[cid];
        const card = btn.closest('.comment-node');
        const qid = card?.closest('.reviewer-thread')?.dataset.qid;
        if (qid) this.refreshThread(qid);
      });
    });
    root.querySelectorAll('[data-thread-delete]').forEach(btn => {
      btn.addEventListener('click', () => this.deleteComment(btn.dataset.threadDelete, btn.dataset.qid));
    });
    root.querySelectorAll('[data-thread-refresh]').forEach(btn => {
      btn.addEventListener('click', () => this.refetchAndRefreshThread(btn.dataset.threadRefresh));
    });
  }

  formatRelativeTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const diff = Date.now() - d.getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return '방금';
    if (min < 60) return `${min}분 전`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h}시간 전`;
    const days = Math.floor(h / 24);
    if (days < 7) return `${days}일 전`;
    return this.formatDateTime(iso);
  }

  // ── Section Visibility (branching) ──
  updateVisibleSections() {
    const q6 = this.responses['Q6'];
    this.visibleSections = sections.filter(s => {
      if (!s.showWhen) return true;
      return q6 !== undefined && q6 === s.showWhen.value;
    });
  }

  // ── Render Router ──
  render() {
    if (this.gate === GATE.LOADING) {
      this.renderLoading();
      return;
    }
    if (this.gate === GATE.CLOSED) {
      this.renderClosed();
      return;
    }
    if (this.gate === GATE.DENIED) {
      this.renderAccessDenied();
      return;
    }
    if (this.gate === GATE.REGISTER) {
      this.renderRegister();
      return;
    }
    if (this.gate === GATE.IDENTITY_REQUIRED) {
      this.renderIdentityRequired();
      return;
    }
    if (this.gate === GATE.RESUBMIT_CHOICE) {
      this.renderResubmitChoice();
      return;
    }

    this.updateVisibleSections();
    if (this.currentPage === 0) {
      this.renderIntro();
    } else if (this.currentPage > this.visibleSections.length) {
      this.renderCompletion();
    } else {
      this.renderSection(this.visibleSections[this.currentPage - 1]);
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ── Loading ──
  renderLoading() {
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="completion" style="padding:160px 20px">
          <div class="spinner"></div>
          <style>@keyframes spin{to{transform:rotate(360deg)}}.spinner{width:40px;height:40px;border:3px solid #e0e0e0;border-top:3px solid #2c2c2c;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 24px}</style>
          <p style="color:var(--c-text-secondary)">설문 링크를 확인 중입니다…</p>
        </div>
      </div>
    `;
  }

  // ── Closed (4직군 합산 정원 도달) ──
  renderClosed() {
    const m = SURVEY_META;
    const completed = this.surveyStatus?.completed ?? SURVEY_LIMIT;
    const byCat = this.surveyStatus?.by_category || [];
    const catRows = byCat.map(c => `
      <tr>
        <td style="color:var(--c-text-secondary);padding:6px 0">${this.escape(c.category)} 직군</td>
        <td align="right" style="padding:6px 0"><strong>${c.completed}</strong> / ${c.quota}부 ${c.is_full ? '<span style="color:#a04040">· 마감</span>' : ''}</td>
      </tr>
    `).join('');
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-landing">
          <div class="register-institution">${m.institution}</div>
          <h1 class="register-title">설문이 마감되었습니다</h1>
          <div class="register-card" style="text-align:center;padding:40px 24px">
            <p style="font-size:16px;line-height:1.8;margin:0 0 12px">
              4직군 합산 목표 응답 <strong>${SURVEY_LIMIT}부</strong>가 모두 채워져 추가 응답을 받지 않습니다.
            </p>
            <p style="color:var(--c-text-secondary);margin:0 0 16px">현재 완료 응답: <strong>${completed}부</strong></p>
            ${byCat.length ? `<table style="margin:0 auto 16px;border-collapse:collapse;font-size:14px">${catRows}</table>` : ''}
            <p style="font-size:15px;color:var(--c-text-secondary);margin:0">
              본 조사에 관심 가져 주셔서 진심으로 감사드립니다.<br>
              결과 활용 및 후속 안내는 추후 별도 공지를 통해 알려드리겠습니다.
            </p>
          </div>
          <div class="register-meta">
            <dl>
              <dt>조사기관</dt><dd>${m.institution}</dd>
              <dt>연구책임</dt><dd>${m.researcher}</dd>
              <dt>문의</dt><dd>${m.contact}</dd>
            </dl>
          </div>
        </div>
      </div>
    `;
  }

  // ── Identity Required (익명 토큰 신원 보강 게이트) ──
  renderIdentityRequired() {
    const m = SURVEY_META;
    const d = this.identityDraft;
    const errBox = this.identityError
      ? `<div class="reg-error">${this.escape(this.identityError)}</div>`
      : '';
    const btnLabel = this.identitySubmitting ? '확인 중…' : '확인하고 설문 시작';
    const btnAttr = this.identitySubmitting ? 'disabled' : '';
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-landing">
          <div class="register-institution">${m.institution}</div>
          <h1 class="register-title">설문 참여자 정보 확인</h1>
          <div class="register-card">
            <p style="margin:0 0 12px;line-height:1.7">
              본 설문 응답의 1인 1회 보장과 데이터 신뢰성 확보를 위해
              <strong>이름</strong>과 <strong>휴대폰 번호</strong>를 먼저 확인합니다.
            </p>
            <p style="margin:0 0 16px;color:var(--c-text-secondary);font-size:13px;line-height:1.7">
              · 한 분당 1회 응답 원칙에 따라 동일 이름·휴대폰 조합은 중복 응답이 차단됩니다.<br>
              · 입력하신 정보는 본 연구의 응답 식별 외 용도로 사용되지 않습니다.<br>
              · 사례품 발송 동의는 응답 시작 페이지에서 별도로 안내드립니다.
            </p>
            ${errBox}
            <div class="form-row">
              <label>이름 <span style="color:#a04040">*</span></label>
              <input type="text" data-identity="name" value="${this.escape(d.name || '')}" placeholder="홍길동" autocomplete="name">
            </div>
            <div class="form-row">
              <label>휴대폰 번호 <span style="color:#a04040">*</span></label>
              <input type="tel" data-identity="phone" value="${this.escape(d.phone || '')}" placeholder="010-1234-5678" autocomplete="tel">
            </div>
            <div class="form-row">
              <label>소속 (선택)</label>
              <input type="text" data-identity="org" value="${this.escape(d.org || '')}" placeholder="회사·기관명" autocomplete="organization">
            </div>
            <div style="margin-top:20px;text-align:center">
              <button class="btn btn-primary" data-action="submit-identity" ${btnAttr}>${btnLabel}</button>
            </div>
          </div>
          <div class="register-meta">
            <dl>
              <dt>조사기관</dt><dd>${m.institution}</dd>
              <dt>연구책임</dt><dd>${m.researcher}</dd>
              <dt>문의</dt><dd>${m.contact}</dd>
            </dl>
          </div>
        </div>
      </div>
    `;
    // 입력 바인딩
    this.container.querySelectorAll('[data-identity]').forEach(el => {
      el.addEventListener('input', (e) => {
        const key = e.target.getAttribute('data-identity');
        this.identityDraft[key] = e.target.value;
      });
    });
    const btn = this.container.querySelector('[data-action="submit-identity"]');
    if (btn) btn.addEventListener('click', () => this.submitIdentity());
  }

  async submitIdentity() {
    const d = this.identityDraft;
    const name = (d.name || '').trim();
    const phone = (d.phone || '').trim();
    const org = (d.org || '').trim();
    if (!name) { this.identityError = '이름을 입력해 주십시오.'; this.render(); return; }
    const phoneDigits = phone.replace(/\D+/g, '');
    if (phoneDigits.length < 9) {
      this.identityError = '연락 가능한 휴대폰 번호(숫자 9자리 이상)를 입력해 주십시오.';
      this.render();
      return;
    }
    this.identitySubmitting = true;
    this.identityError = '';
    this.render();
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/identity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: this.token, name, phone, org }),
      });
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}));
        this.identityError = body.detail || '동일한 이름·휴대폰으로 이미 등록된 응답자가 있습니다.';
        this.identitySubmitting = false;
        this.render();
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        this.identityError = body.detail || `요청 실패 (HTTP ${res.status})`;
        this.identitySubmitting = false;
        this.render();
        return;
      }
      // 성공 — participant doc 다시 가져와 OPEN gate 진입.
      this.identitySubmitting = false;
      await this.verifyToken();
      this.render();
    } catch (e) {
      this.identityError = '네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주십시오.';
      this.identitySubmitting = false;
      this.render();
    }
  }

  // ── Register (공개 단일 링크 자가등록) ──
  renderRegister() {
    if (this.regStep === REG_STEP.LANDING) return this.renderRegisterLanding();
    if (this.regStep === REG_STEP.CONSENT) return this.renderRegisterConsent();
    if (this.regStep === REG_STEP.INFO) return this.renderRegisterInfo();
    if (this.regStep === REG_STEP.RECOVER) return this.renderRecover();
  }

  renderRegisterLanding() {
    const m = SURVEY_META;
    const byCat = this.surveyStatus?.by_category || [];
    const quotaHtml = byCat.length ? `
      <div class="register-card">
        <h2>직군별 응답 현황</h2>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          ${byCat.map(c => `
            <tr>
              <td style="padding:8px 0;color:var(--c-text-secondary)">${this.escape(c.category)} 직군</td>
              <td align="right" style="padding:8px 0">
                <strong>${c.completed}</strong> / ${c.quota}부
                ${c.is_full ? '<span style="color:#a04040;margin-left:8px">· 마감</span>' : ''}
              </td>
            </tr>
          `).join('')}
        </table>
        <p class="register-hint" style="margin-top:12px">각 직군 정원이 충족되면 해당 직군은 자동으로 마감됩니다. 조기 마감을 피하시려면 가급적 빠른 참여를 부탁드립니다.</p>
      </div>
    ` : '';

    const staffBanner = this.isStaffMode ? `
      <div class="register-card" style="background:#fef3c7;border-color:#fcd34d">
        <p style="margin:0;color:#92400e;font-weight:500">
          🧪 <strong>직원 테스트 모드</strong> — 이 링크로 등록된 응답은 정원·통계·분석 대상에서 자동 제외됩니다.
        </p>
      </div>
    ` : '';
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-landing">
          <div class="register-institution">${m.institution}</div>
          <h1 class="register-title">${m.title}</h1>
          <div class="register-subtitle">${m.subtitle}</div>

          ${staffBanner}

          <div class="register-card">
            <h2>조사 목적</h2>
            <p>본 조사는 인공지능(AI) 기술이 건축 산업의 설계·시공·유지관리·건축행정 전 단계에 걸쳐 가져올 구조적 변화를 분석하고,
               이에 대응하는 법·제도 및 정책 전략을 제시하는 것을 목적으로 합니다.
               건축공간연구원(AURI) 2026년도 기본연구과제로 수행됩니다.</p>
          </div>

          <div class="register-card">
            <h2>참여 안내</h2>
            <ul>
              <li>응답 대상: 건축 <strong>설계·시공·유지관리·건축행정</strong> 분야 실무자·연구자</li>
              <li>소요 시간: 약 ${m.duration}</li>
              <li>응답 중간에 자동 저장되며 링크를 다시 열면 이어서 작성할 수 있습니다.</li>
              <li>응답 제출 시 등록하신 이메일로 <strong>완료 안내 메일이 자동 발송</strong>됩니다 (본인 응답 확인 링크 포함).</li>
              <li>모든 응답은 통계 처리 후 <strong>익명</strong>으로 활용됩니다.</li>
            </ul>
          </div>

          ${quotaHtml}

          <div class="register-card register-reward">
            <h2>🎁 사례품 안내</h2>
            <p>설문에 끝까지 응답해 주신 분께는 감사의 표시로 <strong>2만원 상당 모바일 상품권</strong>을 발송해 드립니다.
               (수령을 원하시는 경우 응답 시작 페이지에서 휴대폰 번호 활용에 동의해 주시기 바랍니다.)</p>
          </div>

          <div class="register-meta">
            <dl>
              <dt>조사기관</dt><dd>${m.institution}</dd>
              <dt>연구책임</dt><dd>${m.researcher}</dd>
              <dt>문의</dt><dd>${m.contact}</dd>
            </dl>
          </div>

          <button class="btn-start" id="btn-reg-start">참여하기 →</button>

          <p class="register-hint" style="text-align:center;margin-top:20px">
            이미 등록하셨나요?
            <a href="#" id="btn-reg-recover" style="color:var(--c-text-primary);text-decoration:underline;margin-left:6px">토큰 재발송 받기 →</a>
          </p>
        </div>
      </div>
    `;
    this.container.querySelector('#btn-reg-start')?.addEventListener('click', () => {
      this.regStep = REG_STEP.CONSENT;
      this.render();
    });
    this.container.querySelector('#btn-reg-recover')?.addEventListener('click', (e) => {
      e.preventDefault();
      this.regStep = REG_STEP.RECOVER;
      this.recoverEmail = '';
      this.recoverError = '';
      this.recoverSent = false;
      this.recoverSubmitting = false;
      this.render();
    });
  }

  renderRecover() {
    const errHtml = this.recoverError ? `<p class="register-error">${this.escape(this.recoverError)}</p>` : '';
    const sentHtml = this.recoverSent ? `
      <div class="register-card" style="background:#f0fdf4;border-color:#86efac">
        <p style="margin:0;color:#166534">
          <strong>입력하신 이메일이 등록되어 있다면 토큰 링크를 발송했습니다.</strong><br>
          몇 분 내에 메일이 도착하지 않으면 스팸함을 확인해 주십시오.
        </p>
      </div>
    ` : '';
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-info">
          <h1 class="register-title">토큰 재발송</h1>
          <p class="register-subtitle">이전에 자가등록 시 사용하신 이메일을 입력해 주십시오. 응답 진행 상태에 맞는 링크를 그 이메일로 다시 보내드립니다.</p>

          ${sentHtml}

          <div class="register-card">
            <div class="register-form">
              <label class="full">
                <span>이메일 *</span>
                <input type="email" id="recover-email" value="${this.escape(this.recoverEmail || '')}" placeholder="example@auri.re.kr" />
              </label>
            </div>
            ${errHtml}
            <div class="register-actions">
              <button class="btn btn-prev" id="btn-recover-back">← 이전</button>
              <button class="btn btn-next" id="btn-recover-submit" ${this.recoverSubmitting ? 'disabled' : ''}>
                ${this.recoverSubmitting ? '발송 중…' : '재발송 요청'}
              </button>
            </div>
          </div>
        </div>
      </div>
    `;
    this.container.querySelector('#recover-email')?.addEventListener('input', (e) => {
      this.recoverEmail = e.target.value;
    });
    this.container.querySelector('#btn-recover-back')?.addEventListener('click', () => {
      this.regStep = REG_STEP.LANDING;
      this.recoverError = '';
      this.recoverSent = false;
      this.render();
    });
    this.container.querySelector('#btn-recover-submit')?.addEventListener('click', () => {
      this.submitRecover();
    });
  }

  async submitRecover() {
    const email = (this.recoverEmail || '').trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      this.recoverError = '올바른 이메일을 입력해 주십시오.';
      this.render();
      return;
    }
    this.recoverError = '';
    this.recoverSubmitting = true;
    this.render();
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/recover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `요청 실패 (${res.status})`);
      }
      this.recoverSent = true;
      this.recoverSubmitting = false;
      this.render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      this.recoverError = e.message || '재발송 중 오류가 발생했습니다. 잠시 후 다시 시도해 주십시오.';
      this.recoverSubmitting = false;
      this.render();
    }
  }

  renderRegisterConsent() {
    const d = this.regDraft;
    const errHtml = this.regError ? `<p class="register-error">${this.escape(this.regError)}</p>` : '';

    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-stepper">
          <span class="step done">①</span><span class="step-line"></span>
          <span class="step active">②</span><span class="step-line"></span>
          <span class="step">③</span>
        </div>

        <div class="register-card">
          <h2>개인정보 수집·이용 동의</h2>
          <p class="register-hint">「개인정보 보호법」 제15조에 따라 아래 사항을 안내드립니다.</p>

          <div class="consent-info">
            <h3>ⓘ 본 설문은 익명으로 진행됩니다 — 통계 처리 안내 (동의 불요)</h3>
            <p>
              직군·소속·부서·팀·직위/직급·담당업무 등 <strong>분류 정보</strong>와
              <strong>설문 응답 내용</strong>은 통계 처리되어
              <strong>개인을 식별할 수 없는 형태</strong>로만 분석·공표됩니다.
              연구 결과의 보고서·논문 등에서 개별 응답자 또는 기관의 답변이 그대로 노출되지 않으며,
              위 분류 정보와 응답 내용은 별도의 동의 절차를 거치지 않습니다.
            </p>
          </div>

          <div class="consent-block">
            <h3>① 필수동의 — 응답 완료 안내 메일 발송 목적</h3>
            <table class="consent-table">
              <tbody>
                <tr><th>수집 항목</th>
                  <td><strong>이메일 주소</strong></td></tr>
                <tr><th>수집·이용 목적</th>
                  <td>설문 응답 완료 안내 메일 발송 (본인 응답 확인 링크 포함).
                      <strong>응답 분석·통계 처리 단계에서는 사용하지 않으며, 개인 식별 자료로 활용되지 않습니다.</strong></td></tr>
                <tr><th>보유·이용 기간</th>
                  <td>완료 메일 발송 후 즉시 파기 (응답 수정·재발송 요청 처리 시 연구 종료 시점까지 한정 보존)</td></tr>
                <tr><th>거부 권리</th>
                  <td>동의를 거부할 수 있으며, 거부 시 본 설문에 참여하실 수 없습니다.</td></tr>
              </tbody>
            </table>
            <label class="consent-check">
              <input type="checkbox" id="reg-consent-pi" ${d.consent_pi ? 'checked' : ''}>
              <span>위 사항을 충분히 이해하였으며, 응답 완료 안내 메일 발송 목적의 이메일 수집·이용에 <strong>동의합니다(필수).</strong></span>
            </label>
          </div>

          <p class="register-hint" style="margin-top:8px">
            🎁 <strong>사례품(2만원 상당 모바일 상품권) 발송용 휴대폰 번호 수집·이용 동의는 응답 시작 페이지에서 별도로 받습니다.</strong>
            동의하지 않으셔도 설문 참여는 가능합니다.
          </p>

          ${errHtml}

          <div class="register-actions">
            <button class="btn btn-prev" id="btn-reg-back">← 이전</button>
            <button class="btn btn-next" id="btn-reg-next">다음 →</button>
          </div>
        </div>
      </div>
    `;
    this.bindConsentEvents();
  }

  bindConsentEvents() {
    this.container.querySelector('#btn-reg-back')?.addEventListener('click', () => {
      this.regStep = REG_STEP.LANDING;
      this.regError = '';
      this.render();
    });
    this.container.querySelector('#reg-consent-pi')?.addEventListener('change', (e) => {
      this.regDraft.consent_pi = e.target.checked;
      this.saveRegisterDraft();
    });
    this.container.querySelector('#btn-reg-next')?.addEventListener('click', () => {
      if (!this.regDraft.consent_pi) {
        this.regError = '필수동의(①)에 체크해 주셔야 다음 단계로 진행할 수 있습니다.';
        this.render();
        return;
      }
      this.regError = '';
      this.regStep = REG_STEP.INFO;
      this.render();
    });
  }

  renderRegisterInfo() {
    const d = this.regDraft;
    const recoverLinkHtml = this.regErrorRecover
      ? '<a href="#" id="btn-reg-error-recover" style="color:var(--c-text-primary);text-decoration:underline;margin-left:6px">토큰 재발송 받기 →</a>'
      : '';
    const errHtml = this.regError
      ? `<p class="register-error">${this.escape(this.regError)}${recoverLinkHtml}</p>`
      : '';
    const submittingHtml = this.regSubmitting
      ? '<span class="register-submitting">등록 중…</span>' : '';

    const byCat = this.surveyStatus?.by_category || [];
    const fullCats = new Set(byCat.filter(c => c.is_full).map(c => c.category));


    const catRadio = (val, label, helper) => {
      const isFull = fullCats.has(val);
      const checked = d.category === val ? 'checked' : '';
      const disabled = isFull ? 'disabled' : '';
      const tag = isFull ? '<span style="margin-left:6px;color:#a04040;font-size:12px">· 정원 마감</span>' : '';
      return `
        <label class="register-radio ${isFull ? 'is-disabled' : ''}">
          <input type="radio" name="reg-category" value="${val}" ${checked} ${disabled}>
          <span><strong>${label}</strong>${tag}<br><small style="color:var(--c-text-secondary)">${helper}</small></span>
        </label>
      `;
    };

    this.container.innerHTML = `
      <div class="survey-container">
        <div class="register-stepper">
          <span class="step done">①</span><span class="step-line"></span>
          <span class="step done">②</span><span class="step-line"></span>
          <span class="step active">③</span>
        </div>

        <div class="register-card">
          <h2>응답자 정보 입력</h2>
          <p class="register-hint">
            <span class="required">*</span> 표시는 필수 항목입니다.
            응답 분석에는 <strong>분류 정보(직군·소속·담당업무 등)</strong>만 활용되며,
            이메일은 응답 완료 안내 메일 발송에만 사용됩니다.
          </p>

          <div class="register-section">
            <h3>① 응답자 본인 정보 <span class="register-section-tag">(필수)</span></h3>
            <p class="register-hint">제출 직후 이메일로 응답 확인 링크가 자동 발송됩니다. 이름은 1인 1회 응답 보장과 사례품 안내에 사용되며, 분석·통계 처리에는 활용되지 않습니다.</p>
            <div class="register-grid">
              <label>
                <span>이름 *</span>
                <input type="text" id="reg-name" value="${this.escape(d.name || '')}" placeholder="홍길동" autocomplete="name" />
              </label>
              <label>
                <span>이메일 *</span>
                <input type="email" id="reg-email" value="${this.escape(d.email)}" placeholder="example@example.co.kr" autocomplete="email" />
              </label>
            </div>
          </div>

          <div class="register-section">
            <h3>② 직군 및 소속 <span class="register-section-tag">(분류 통계용 — 동의 불요)</span></h3>
            <p class="register-hint">주된 직무 기준 1개 직군을 선택해 주십시오. 정원이 마감된 직군은 선택할 수 없습니다.</p>
            <div class="register-grid">
              <label class="full">
                <span>직군 *</span>
                <div class="register-radio-row register-radio-grid">
                  ${catRadio('설계', '설계 직군', '건축사, 구조·설비·소방 엔지니어, 계획·설계 전공 연구자 등')}
                  ${catRadio('시공', '시공 직군', '현장관리자, CM, 시공 기술자, 시공 전공 연구자 등')}
                  ${catRadio('유지관리', '유지관리 직군', '시설관리(FM), 자산관리, 유지관리 전공 연구자 등')}
                  ${catRadio('건축행정', '건축행정 직군', '인허가·심의·건축물관리 담당 공무원, 행정·제도 전공 연구자 등')}
                </div>
              </label>
              <label class="full">
                <span>소속 기관·회사명 * <small>(예: ○○건축사사무소 / ○○건설 / ○○구청 / ○○대학교)</small></span>
                <input type="text" id="reg-org" value="${this.escape(d.org)}" placeholder="소속 기관·회사명" />
              </label>
              <label>
                <span>부서</span>
                <input type="text" id="reg-dept" value="${this.escape(d.dept)}" placeholder="예) 설계1팀 / 안전관리팀" />
              </label>
              <label>
                <span>팀</span>
                <input type="text" id="reg-team" value="${this.escape(d.team)}" placeholder="예) 계획설계팀" />
              </label>
              <label>
                <span>직위/직급</span>
                <input type="text" id="reg-position" value="${this.escape(d.position)}" placeholder="예) 팀장 / 책임연구원 / 행정5급" />
              </label>
              <label class="full">
                <span>담당업무</span>
                <input type="text" id="reg-duty" value="${this.escape(d.duty)}" placeholder="예) 공동주택 계획설계 / 안전관리 총괄" />
              </label>
            </div>
          </div>

          ${errHtml}

          <div class="register-actions">
            <button class="btn btn-prev" id="btn-reg-back">← 이전</button>
            <button class="btn btn-next" id="btn-reg-submit" ${this.regSubmitting ? 'disabled' : ''}>
              ${this.regSubmitting ? '등록 중…' : '등록하고 설문 시작 →'}
            </button>
          </div>
          ${submittingHtml}
        </div>
      </div>
    `;
    this.bindRegisterInfoEvents();
  }

  bindRegisterInfoEvents() {
    const bind = (id, key) => {
      const el = this.container.querySelector(`#${id}`);
      if (!el) return;
      el.addEventListener('input', () => {
        this.regDraft[key] = el.value;
        this.saveRegisterDraft();
      });
    };
    bind('reg-name', 'name');
    bind('reg-email', 'email');
    bind('reg-org', 'org');
    bind('reg-dept', 'dept');
    bind('reg-team', 'team');
    bind('reg-position', 'position');
    bind('reg-duty', 'duty');

    this.container.querySelectorAll('input[name="reg-category"]').forEach(radio => {
      radio.addEventListener('change', (e) => {
        if (e.target.checked) {
          this.regDraft.category = e.target.value;
          this.saveRegisterDraft();
        }
      });
    });

    this.container.querySelector('#btn-reg-back')?.addEventListener('click', () => {
      this.regStep = REG_STEP.CONSENT;
      this.regError = '';
      this.render();
    });
    this.container.querySelector('#btn-reg-submit')?.addEventListener('click', () => {
      this.submitRegistration();
    });
  }

  async submitRegistration() {
    const d = this.regDraft;
    const errors = [];
    const email = (d.email || '').trim();
    if (!(d.name || '').trim()) errors.push('이름을 입력해 주십시오.');
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.push('올바른 이메일을 입력해 주십시오.');
    if (!d.consent_pi) errors.push('이메일 수집·이용에 동의해 주십시오 (필수).');
    if (!['설계', '시공', '유지관리', '건축행정'].includes(d.category)) errors.push('직군(설계/시공/유지관리/건축행정)을 선택해 주십시오.');
    if (!(d.org || '').trim()) errors.push('소속 기관·회사명을 입력해 주십시오.');
    if (errors.length) {
      this.regError = errors.join(' / ');
      this.render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    this.regError = '';
    this.regSubmitting = true;
    this.render();

    try {
      const payload = {
        email: email,
        name: (d.name || '').trim(),
        org: (d.org || '').trim(),
        category: d.category,
        dept: (d.dept || '').trim(),
        team: (d.team || '').trim(),
        position: (d.position || '').trim(),
        duty: (d.duty || '').trim(),
        consent_pi: !!d.consent_pi,
        is_staff: !!this.isStaffMode,
      };
      const res = await fetch(`${API_BASE}/ai/api/survey/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.status === 410) {
        this.gate = GATE.CLOSED;
        this.regSubmitting = false;
        await this.fetchSurveyStatus();
        this.render();
        return;
      }
      if (res.status === 409) {
        const err = await res.json().catch(() => ({}));
        this.regError = err.detail || '해당 직군 응답 정원이 충족되어 신규 참여가 마감되었습니다.';
        this.regErrorRecover = /이미 등록/.test(this.regError);
        this.regSubmitting = false;
        await this.fetchSurveyStatus();
        this.render();
        if (this.regErrorRecover) {
          this.container.querySelector('#btn-reg-error-recover')?.addEventListener('click', (e) => {
            e.preventDefault();
            this.regStep = REG_STEP.RECOVER;
            this.recoverEmail = (this.regDraft.email || '').trim();
            this.recoverError = '';
            this.recoverSent = false;
            this.recoverSubmitting = false;
            this.regError = '';
            this.regErrorRecover = false;
            this.render();
          });
        }
        window.scrollTo({ top: 0, behavior: 'smooth' });
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `등록 실패 (${res.status})`);
      }
      const data = await res.json();
      this.clearRegisterDraft();
      // 발급된 토큰으로 이동 — 페이지 reload하여 토큰 인증 흐름 진입.
      const url = new URL(window.location.href);
      url.searchParams.set('token', data.token);
      url.searchParams.set('just_registered', '1');
      if (this.isStaffMode) url.searchParams.set('source', 'staff');
      window.location.href = url.toString();
    } catch (e) {
      this.regError = e.message || '등록 중 오류가 발생했습니다. 잠시 후 다시 시도해 주십시오.';
      this.regSubmitting = false;
      this.render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  // ── Access Denied ──
  renderAccessDenied() {
    const m = SURVEY_META;
    this.container.innerHTML = `
      <div class="survey-container">
        <div class="access-denied">
          <div class="access-denied-icon">
            <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.6">
              <circle cx="12" cy="12" r="9"></circle>
              <line x1="5.6" y1="5.6" x2="18.4" y2="18.4"></line>
            </svg>
          </div>
          <h1>접근 권한이 없습니다</h1>
          <p class="access-denied-msg">
            본 설문은 사전에 발송된 개별 링크를 통해서만 참여할 수 있습니다.<br/>
            이메일로 수신한 링크를 다시 확인하시거나, 아래 연락처로 문의해 주십시오.
          </p>
          <div class="access-denied-meta">
            <dl>
              <dt>조사기관</dt><dd>${m.institution}</dd>
              <dt>연구책임</dt><dd>${m.researcher}</dd>
              <dt>문의</dt><dd>${m.contact}</dd>
            </dl>
          </div>
        </div>
      </div>
    `;
  }

  // ── Resubmit Choice (이미 제출한 토큰 재접근) ──
  renderResubmitChoice() {
    const p = this.participant || {};
    const submittedStr = this.submittedAt ? this.formatDateTime(this.submittedAt) : '';
    const updatedStr = this.updatedAt ? this.formatDateTime(this.updatedAt) : '';

    // 사례품 미동의자 안내 — 응답 완료자 중 동의가 없는 경우만 노출. 연구진·직원은 정원 외라 미노출.
    const isReviewerOrStaff = p.category === '연구진' || p.source === 'staff';
    const rewardPending = !p.consent_reward && !isReviewerOrStaff;
    const rewardBanner = rewardPending ? `
      <div style="margin:18px 0 4px;padding:18px 20px;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;text-align:left">
        <p style="margin:0 0 8px;font-weight:600;color:#92400e;font-size:15px">🎁 사례품 안내가 아직 완료되지 않았습니다</p>
        <p style="margin:0;font-size:13px;color:#78350f;line-height:1.7">
          사례품(2만원 모바일 상품권) 발송에는 동의와 휴대전화 번호가 필요합니다.
          아래 <strong>[응답 수정하기]</strong>를 누르신 뒤 응답 시작 페이지의 <strong>[🎁 사례품 동의만 저장하고 끝내기]</strong> 버튼으로 1분 내에 완료하실 수 있습니다 (기존 응답은 그대로 유지됩니다).
        </p>
      </div>
    ` : '';

    this.container.innerHTML = `
      <div class="survey-container">
        <div class="resubmit-choice">
          <div class="resubmit-badge">제출 완료</div>
          <h1>이미 응답을 제출하셨습니다</h1>
          <div class="resubmit-meta">
            <dl>
              <dt>응답자</dt><dd>${this.escape(p.name || '-')}${p.org ? ` · ${this.escape(p.org)}` : ''}</dd>
              <dt>최초 제출</dt><dd>${submittedStr || '-'}</dd>
              ${updatedStr ? `<dt>최근 수정</dt><dd>${updatedStr}</dd>` : ''}
            </dl>
          </div>
          ${rewardBanner}
          <p class="resubmit-msg">
            응답 내용을 <strong>수정</strong>하시거나, 제출한 응답을 <strong>확인</strong>만 하실 수 있습니다.
          </p>
          <div class="resubmit-actions">
            <button class="btn btn-next" id="btn-edit-mode">응답 수정하기</button>
            <button class="btn btn-prev" id="btn-view-mode">내 응답 확인 (읽기전용)</button>
          </div>
        </div>
      </div>
    `;
    this.container.querySelector('#btn-edit-mode').addEventListener('click', () => {
      this.editMode = EDIT_MODE.EDIT;
      this.gate = GATE.OPEN;
      this.currentPage = 0;
      this.render();
    });
    this.container.querySelector('#btn-view-mode').addEventListener('click', () => {
      this.gate = GATE.READ_ONLY;
      this.render();
    });
  }

  // ── Status Bar (공통 상단) ──
  renderStatusBar() {
    let status, statusClass;
    if (this.submitted && this.editMode === EDIT_MODE.EDIT) {
      status = '수정 중';
      statusClass = 'status-editing';
    } else if (this.submitted) {
      status = '제출 완료';
      statusClass = 'status-done';
    } else {
      status = '미제출';
      statusClass = 'status-pending';
    }

    const submittedInfo = this.submittedAt
      ? `<span class="status-time">제출: ${this.formatDateTime(this.submittedAt)}</span>`
      : '';
    const updatedInfo = this.updatedAt
      ? `<span class="status-time">수정: ${this.formatDateTime(this.updatedAt)}</span>`
      : '';

    return `
      <div class="status-info-bar">
        <div class="status-info-inner">
          <span class="status-badge ${statusClass}">${status}</span>
          <div class="status-times">
            ${submittedInfo}
            ${updatedInfo}
          </div>
        </div>
      </div>
    `;
  }

  // ── Participant Info Card ──
  renderParticipantCard() {
    const p = this.participant;
    if (!p) return '';

    if (this.editingParticipant) {
      const errHtml = this.participantFormError
        ? `<p class="participant-error">${this.escape(this.participantFormError)}</p>`
        : '';
      return `
        <div class="participant-card editing">
          <div class="participant-card-header">
            <h3>내 정보 수정</h3>
            <p class="participant-hint">부서 이동·인사 변동이 있으셨다면 이 화면에서 갱신해 주십시오.</p>
          </div>
          <div class="participant-form">
            <label>
              <span>이름</span>
              <input type="text" id="p-name" value="${this.escape(p.name || '')}" />
            </label>
            <label>
              <span>이메일</span>
              <input type="email" id="p-email" value="${this.escape(p.email || '')}" />
            </label>
            <label>
              <span>소속 기관</span>
              <input type="text" id="p-org" value="${this.escape(p.org || '')}" />
            </label>
            <label>
              <span>부서명</span>
              <input type="text" id="p-dept" value="${this.escape(p.dept || '')}" placeholder="예) 건축설계실" />
            </label>
            <label>
              <span>팀명</span>
              <input type="text" id="p-team" value="${this.escape(p.team || '')}" placeholder="예) 설계1팀 (없으면 비워두세요)" />
            </label>
            <label>
              <span>직위</span>
              <input type="text" id="p-position" value="${this.escape(p.position || '')}" placeholder="예) 팀장, 책임" />
            </label>
            <label>
              <span>직급</span>
              <input type="text" id="p-rank" value="${this.escape(p.rank || '')}" placeholder="예) 차장, 건축사" />
            </label>
            <label>
              <span>담당업무</span>
              <input type="text" id="p-duty" value="${this.escape(p.duty || '')}" placeholder="예) BIM 설계 총괄" />
            </label>
            <label>
              <span>연락처</span>
              <input type="tel" id="p-phone" value="${this.escape(p.phone || '')}" placeholder="010-0000-0000" />
            </label>
          </div>
          ${errHtml}
          <div class="participant-actions">
            <button class="btn btn-prev" id="btn-p-cancel">취소</button>
            <button class="btn btn-next" id="btn-p-save">저장</button>
          </div>
        </div>
      `;
    }

    return `
      <div class="participant-card">
        <div class="participant-card-header">
          <h3>내 정보</h3>
          <button class="btn-link" id="btn-p-edit">수정</button>
        </div>
        <p class="participant-hint">아래 정보는 응답자 DB에서 미리 채워둔 값입니다. 변경된 사항이 있으면 <strong>수정</strong> 버튼으로 갱신해 주십시오.</p>
        <dl class="participant-info">
          <dt>이름</dt><dd>${this.escape(p.name || '-')}</dd>
          <dt>이메일</dt><dd>${this.escape(p.email || '-')}</dd>
          <dt>소속</dt><dd>${this.escape(p.org || '-')}</dd>
          <dt>부서</dt><dd>${this.escape(p.dept || '-')}</dd>
          <dt>팀</dt><dd>${this.escape(p.team || '-')}</dd>
          <dt>직위</dt><dd>${this.escape(p.position || '-')}</dd>
          <dt>직급</dt><dd>${this.escape(p.rank || '-')}</dd>
          <dt>담당업무</dt><dd>${this.escape(p.duty || '-')}</dd>
          <dt>연락처</dt><dd>${this.escape(p.phone || '-')}</dd>
          <dt>직군</dt><dd class="readonly">${this.escape(p.category || '-')} <span class="hint">(사전 분류)</span></dd>
        </dl>
      </div>
    `;
  }

  bindParticipantEvents() {
    this.container.querySelector('#btn-p-edit')?.addEventListener('click', () => {
      this.editingParticipant = true;
      this.participantFormError = '';
      this.render();
    });
    this.container.querySelector('#btn-p-cancel')?.addEventListener('click', () => {
      this.editingParticipant = false;
      this.participantFormError = '';
      this.render();
    });
    this.container.querySelector('#btn-p-save')?.addEventListener('click', () => {
      this.saveParticipant();
    });
  }

  async saveParticipant() {
    const val = (id) => this.container.querySelector(`#${id}`)?.value.trim() ?? '';

    const payload = {
      name: val('p-name'),
      email: val('p-email'),
      org: val('p-org'),
      phone: val('p-phone'),
      dept: val('p-dept'),
      team: val('p-team'),
      position: val('p-position'),
      rank: val('p-rank'),
      duty: val('p-duty'),
    };

    if (!payload.name) {
      this.participantFormError = '이름을 입력해 주십시오.';
      this.render();
      return;
    }
    if (!payload.email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email)) {
      this.participantFormError = '올바른 이메일을 입력해 주십시오.';
      this.render();
      return;
    }

    const saveBtn = this.container.querySelector('#btn-p-save');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '저장 중…'; }

    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}/participant`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `저장 실패 (${res.status})`);
      }
      const data = await res.json();
      this.participant = { ...this.participant, ...data.participant };
      this.editingParticipant = false;
      this.participantFormError = '';
      this.render();
    } catch (err) {
      this.participantFormError = err.message || '저장 중 오류가 발생했습니다.';
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '저장'; }
      this.render();
    }
  }

  // ── Intro ──
  renderIntro() {
    const m = SURVEY_META;
    const statusBar = this.renderStatusBar();
    const participantCard = this.renderParticipantCard();
    const startLabel = this.submitted && this.editMode === EDIT_MODE.EDIT
      ? '응답 수정 시작하기'
      : '설문 시작하기';

    const consent = !!this.responses['CONSENT_REWARD'];
    const phone = this.responses['PHONE'] || '';
    const N = REWARD_CONSENT_NOTICE;
    const consentRows = N.rows.map(([k, v]) =>
      `<tr><th>${k}</th><td>${v}</td></tr>`
    ).join('');

    // 미동의 + 이미 제출 + EDIT 모드일 때만 [동의만 저장] 단축 경로 노출.
    // reward_notice 메일 수신자가 전체 응답을 다시 넘기지 않고 한 번에 끝내도록 허용.
    const p = this.participant || {};
    const isReviewerOrStaff = p.category === '연구진' || p.source === 'staff';
    const showRewardOnly = this.submitted
      && this.editMode === EDIT_MODE.EDIT
      && !p.consent_reward
      && !isReviewerOrStaff;
    const rewardOnlyBtn = showRewardOnly ? `
      <button class="btn-start" id="btn-reward-only" style="margin-top:14px;background:#92400e">
        🎁 사례품 동의만 저장하고 끝내기
      </button>
      <p style="margin:8px 0 0;font-size:12px;color:#6b7280;text-align:center;line-height:1.6">
        위 동의 체크 + 휴대전화 번호 입력 후 클릭하시면 사례품 정보만 저장됩니다.<br>
        기존 응답·진행 상태는 그대로 유지됩니다.
      </p>
    ` : '';

    this.container.innerHTML = `
      ${statusBar}
      <div class="progress-bar-wrap"><div class="progress-bar-inner">
        <div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>
        <span class="progress-label">0%</span>
      </div></div>
      <div class="survey-container with-status-bar">
        <div class="survey-header">
          <div class="institution">${m.institution}</div>
          <h1>${m.title}</h1>
          <div class="subtitle">${m.subtitle}</div>
        </div>

        ${participantCard}

        <div class="intro-card">
          <h2>연구 소개</h2>
          <p>건축공간연구원(AURI)에서는 2026년도 기본연구과제로 본 연구를 수행하고 있습니다. 인공지능(AI) 기술이 건축 산업의 설계·시공·유지관리·건축행정 전 단계에 걸쳐 가져오는 구조적 변화를 실증 분석하고, 이에 대응하는 법·제도 및 정책 전략을 제시하는 것을 목적으로 합니다.</p>
        </div>

        <div class="intro-card">
          <h2>설문 구성</h2>
          <p>본 설문은 <strong>[공통 파트]</strong>와 <strong>[직무별 특화 파트]</strong>로 구성됩니다.</p>
          <ul style="margin-top:12px">
            <li>PART I~II (공통) 응답</li>
            <li>PART III: 귀하의 직군에 해당하는 구역만 응답</li>
            <li>PART IV~IX (공통) 응답</li>
          </ul>
          <dl class="intro-meta">
            <dt>소요 시간</dt><dd>${m.duration}</dd>
            <dt>비밀보장</dt><dd>모든 응답은 통계 처리 후 익명 활용</dd>
            <dt>설문 답례품</dt><dd>${REWARD_META_LABEL}</dd>
            <dt>연구책임</dt><dd>${m.researcher} (${m.contact})</dd>
          </dl>
        </div>

        <div class="intro-card consent-block">
          <h2>${N.title}</h2>
          <p class="consent-intro">${N.lead}</p>
          <table class="consent-table">
            <tbody>${consentRows}</tbody>
          </table>
          <label class="consent-check">
            <input type="checkbox" id="intro-consent" ${consent ? 'checked' : ''} />
            <span>${N.consentLabel}</span>
          </label>
          <div id="intro-phone-wrap" style="${consent ? '' : 'display:none'};margin-top:12px">
            <label style="display:block;font-size:13px;color:#374151;margin-bottom:6px">
              휴대전화 번호 <span style="color:#dc2626">*</span>
              <span style="font-size:11px;color:#6b7280;font-weight:400;margin-left:6px">동의 시 필수 · 발송 후 즉시 파기</span>
            </label>
            <input type="tel" id="intro-phone" value="${phone.replace(/"/g, '&quot;')}" placeholder="010-1234-5678"
              style="width:100%;max-width:320px;padding:10px 12px;border:1px solid #d1d5db;border-radius:6px;font-size:14px" />
            <p id="intro-phone-error" style="display:none;color:#b91c1c;font-size:12px;margin-top:6px"></p>
          </div>
        </div>

        <button class="btn-start" id="btn-start">${startLabel}</button>
        ${rewardOnlyBtn}
      </div>
    `;
    this.bindParticipantEvents();
    this.bindIntroConsentEvents();
    this.container.querySelector('#btn-start')?.addEventListener('click', () => {
      if (!this.commitIntroConsent()) return;
      this.currentPage = 1;
      this.render();
    });
    this.container.querySelector('#btn-reward-only')?.addEventListener('click', async () => {
      const cb = this.container.querySelector('#intro-consent');
      if (!cb || !cb.checked) {
        alert('사례품 수령을 원하시면 동의 체크박스를 선택하고 휴대전화 번호를 입력해 주세요.\n\n사례품을 원하지 않으시면 본 안내는 그대로 두셔도 됩니다.');
        return;
      }
      if (!this.commitIntroConsent()) return;
      await this.submitRewardConsentOnly();
    });
  }

  bindIntroConsentEvents() {
    const cb = this.container.querySelector('#intro-consent');
    const wrap = this.container.querySelector('#intro-phone-wrap');
    const phoneInput = this.container.querySelector('#intro-phone');
    if (!cb) return;
    cb.addEventListener('change', () => {
      if (cb.checked) {
        wrap.style.display = '';
        phoneInput?.focus();
      } else {
        wrap.style.display = 'none';
        if (phoneInput) phoneInput.value = '';
        const err = this.container.querySelector('#intro-phone-error');
        if (err) err.style.display = 'none';
      }
    });
  }

  commitIntroConsent() {
    const cb = this.container.querySelector('#intro-consent');
    const phoneInput = this.container.querySelector('#intro-phone');
    const errEl = this.container.querySelector('#intro-phone-error');
    if (!cb) return true;
    if (cb.checked) {
      const phone = (phoneInput?.value || '').trim();
      const re = new RegExp(REWARD_CONSENT_NOTICE.phonePattern);
      if (!re.test(phone)) {
        if (errEl) {
          errEl.textContent = REWARD_CONSENT_NOTICE.phonePatternMessage;
          errEl.style.display = '';
        }
        phoneInput?.focus();
        return false;
      }
      this.setResponse('CONSENT_REWARD', true);
      this.setResponse('PHONE', phone);
    } else {
      this.setResponse('CONSENT_REWARD', false);
      this.setResponse('PHONE', '');
    }
    return true;
  }

  // ── Section ──
  renderSection(section) {
    const pct = Math.round((this.currentPage / (this.visibleSections.length + 1)) * 100);
    const isLast = this.currentPage === this.visibleSections.length;
    const statusBar = this.renderStatusBar();
    const submitLabel = this.submitted && this.editMode === EDIT_MODE.EDIT ? '수정 내용 제출' : '제출하기';

    let html = `
      ${statusBar}
      <div class="progress-bar-wrap"><div class="progress-bar-inner">
        <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
        <span class="progress-label">${pct}%</span>
      </div></div>
      <div class="survey-container with-status-bar">
        <div class="section">
          <div class="section-header">
            <span class="section-tag">${section.tag}</span>
            <h2>${section.title}</h2>
            <p class="section-subtitle">${section.subtitle}</p>
          </div>
    `;

    for (const q of section.questions) {
      html += this.renderQuestion(q);
      if (this.isReviewer()) {
        if (q.type === Q_TYPE.SUB_QUESTIONS) {
          // sub-question별 스레드는 renderSubQuestions 내부에 이미 주입됨.
          // parent qid 스레드는 코멘트가 있을 때만 노출 (빈 폼 중복 방지).
          const parentCount = (this.threads[q.id] || []).length;
          if (parentCount > 0) {
            html += this.renderReviewerThread(q.id);
          }
        } else {
          html += this.renderReviewerThread(q.id);
        }
      }
    }

    html += `</div></div>`;
    const reviewerSkipBtn = this.isReviewer() && !isLast
      ? '<button class="btn btn-skip" id="btn-skip" title="연구진 전용 — 필수 응답 검증 없이 다음으로">⏭ 건너뛰기 (검토용)</button>'
      : '';
    const reviewerForceSubmit = this.isReviewer() && isLast
      ? '<button class="btn btn-skip" id="btn-force-submit" title="연구진 전용 — 필수 응답 검증 없이 제출">⏭ 강제 제출 (검토용)</button>'
      : '';

    html += `
      <div class="nav-bar"><div class="nav-inner">
        <button class="btn btn-prev" id="btn-prev">&larr; 이전</button>
        ${reviewerSkipBtn}${reviewerForceSubmit}
        ${isLast
          ? `<button class="btn btn-submit" id="btn-next">${submitLabel}</button>`
          : '<button class="btn btn-next" id="btn-next">다음 &rarr;</button>'
        }
      </div></div>
    `;

    this.container.innerHTML = html;
    this.bindEvents(section);
    this.restoreValues(section);
  }

  renderQuestion(q) {
    if (q.type === Q_TYPE.SUB_QUESTIONS) {
      return this.renderSubQuestions(q);
    }

    let inner = '';
    const noteHtml = q.note ? `<p class="question-note">${q.note}</p>` : '';

    switch (q.type) {
      case Q_TYPE.SINGLE:
      case Q_TYPE.SINGLE_WITH_OTHER:
        inner = this.renderOptions(q, 'radio', q.type === Q_TYPE.SINGLE_WITH_OTHER);
        break;
      case Q_TYPE.MULTI:
      case Q_TYPE.MULTI_LIMIT:
        inner = this.renderOptions(q, 'checkbox');
        break;
      case Q_TYPE.MULTI_WITH_OTHER:
      case Q_TYPE.MULTI_LIMIT_OTHER:
        inner = this.renderOptions(q, 'checkbox', true);
        break;
      case Q_TYPE.LIKERT_TABLE:
        inner = this.renderLikertTable(q);
        break;
      case Q_TYPE.IPA_MATRIX:
        inner = this.renderIpaMatrix(q);
        break;
      case Q_TYPE.TOOL_MATRIX:
        inner = this.renderToolMatrix(q);
        break;
      case Q_TYPE.TEXT:
        inner = this.renderTextInput(q);
        break;
    }

    return `
      <div class="question-block" data-qid="${q.id}">
        <div class="question-label">
          <span class="question-id">${q.id.replace(/([A-Z]+)(\d)/, '$1-$2')}</span>
          <span class="question-text">${q.text}</span>
        </div>
        ${noteHtml}
        ${inner}
        <p class="question-error" data-error="${q.id}"></p>
      </div>
    `;
  }

  renderOptions(q, inputType, hasOther = false) {
    let html = `<div class="option-list" data-qid="${q.id}" data-type="${inputType}">`;
    const name = q.id;
    q.options.forEach((opt, i) => {
      html += `
        <label class="option-item" data-index="${i}">
          <input type="${inputType}" name="${name}" value="${i}" />
          <span class="option-text">${opt}</span>
        </label>
      `;
    });
    if (hasOther) {
      html += `
        <label class="option-item other-row" data-index="other">
          <input type="${inputType}" name="${name}" value="other" />
          <span class="option-text">${q.otherLabel || '기타'}:</span>
          <input type="text" class="other-text" data-qid="${q.id}_other" placeholder="직접 입력" />
        </label>
      `;
    }
    html += '</div>';
    return html;
  }

  renderLikertTable(q) {
    let html = '<div class="likert-table-wrap"><table class="likert-table" data-qid="' + q.id + '">';
    html += '<thead><tr><th></th>';
    q.scaleLabels.forEach((l, i) => { html += `<th>${i + 1}<br><span style="font-weight:400">${l}</span></th>`; });
    html += '</tr></thead><tbody>';
    q.items.forEach((item, idx) => {
      html += `<tr data-row="${idx}">`;
      html += `<td><span class="item-number">(${idx + 1})</span>${item}</td>`;
      for (let v = 1; v <= q.scaleLabels.length; v++) {
        html += `<td class="likert-cell"><label class="likert-cell-label"><input type="radio" class="likert-radio" name="${q.id}_${idx}" value="${v}" /></label></td>`;
      }
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  // ── IPA Matrix (중요도 + 체감도) ──
  renderIpaMatrix(q) {
    const val = this.responses[q.id] || {};
    const labelsImp = q.scaleLabelsImportance || ['1', '2', '3', '4', '5'];
    const labelsExp = q.scaleLabelsExperience || ['1', '2', '3', '4', '5'];
    const radio = (idx, axis, v) => {
      const cur = (val[idx] || {})[axis];
      const checked = String(cur) === String(v) ? 'checked' : '';
      return `<td class="ipa-cell"><input type="radio" name="ipa-${q.id}-${idx}-${axis}" value="${v}" ${checked} onchange="window._engine.setIpaValue('${q.id}', ${idx}, '${axis}', '${v}')"></td>`;
    };
    let html = '<div class="ipa-matrix-wrap"><table class="ipa-matrix" data-qid="' + q.id + '">';
    html += '<thead><tr><th rowspan="2" class="ipa-item-h">항목</th>';
    html += `<th colspan="${labelsImp.length}" class="ipa-axis">(가) 중요도</th>`;
    html += `<th colspan="${labelsExp.length + (q.hasNA ? 1 : 0)}" class="ipa-axis">(나) 체감도${q.hasNA ? ' (N=직무 상황 없음)' : ''}</th>`;
    html += '</tr><tr>';
    labelsImp.forEach((l, i) => { html += `<th class="ipa-label">${i+1}<br><small>${l}</small></th>`; });
    labelsExp.forEach((l, i) => { html += `<th class="ipa-label">${i+1}<br><small>${l}</small></th>`; });
    if (q.hasNA) html += '<th class="ipa-label">N</th>';
    html += '</tr></thead><tbody>';
    q.items.forEach((item, idx) => {
      html += `<tr data-row="${idx}"><td class="ipa-item">(${idx+1}) ${item}</td>`;
      for (let v = 1; v <= labelsImp.length; v++) html += radio(idx, 'imp', v);
      for (let v = 1; v <= labelsExp.length; v++) html += radio(idx, 'exp', v);
      if (q.hasNA) html += radio(idx, 'exp', 'N');
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  setIpaValue(qid, idx, axis, val) {
    const cur = this.responses[qid] || {};
    const item = { ...(cur[idx] || {}) };
    item[axis] = (axis === 'exp' && val === 'N') ? 'N' : Number(val);
    cur[idx] = item;
    this.responses[qid] = cur;
    this.saveResponses();
  }

  // ── Tool Matrix (현재/희망/불필요 + 기타 직접입력) ──
  renderToolMatrix(q) {
    const val = this.responses[q.id] || { current: [], future: [], none: [], other_text: '' };
    const labels = q.columnLabels || ['(가) 현재 활용', '(나) 향후 활용 희망', '(다) 필요 없음'];
    const otherIdx = q.items.length;
    const checkbox = (idx, axis) => {
      const arr = val[axis] || [];
      const checked = arr.includes(idx) ? 'checked' : '';
      return `<td class="tool-cell"><input type="checkbox" ${checked} onchange="window._engine.setToolValue('${q.id}', ${idx}, '${axis}', this.checked)"></td>`;
    };
    let html = '<div class="tool-matrix-wrap"><table class="tool-matrix" data-qid="' + q.id + '">';
    html += `<thead><tr><th class="tool-item-h">AI 도구</th><th>${labels[0]}</th><th>${labels[1]}</th><th>${labels[2] || '(다) 필요 없음'}</th></tr></thead><tbody>`;
    q.items.forEach((item, idx) => {
      html += `<tr><td class="tool-item">${item}</td>${checkbox(idx, 'current')}${checkbox(idx, 'future')}${checkbox(idx, 'none')}</tr>`;
    });
    if (q.otherLabel) {
      const otherText = (val.other_text || '').replace(/"/g, '&quot;');
      // 기타 행은 도구명 입력 시에만 의미 있는 행이라 (다) 필요 없음 컬럼은 비활성
      html += `<tr><td class="tool-item">${q.otherLabel} (직접 입력: <input type="text" class="tool-other-text" value="${otherText}" placeholder="도구명" oninput="window._engine.setToolOther('${q.id}', this.value)" />)</td>${checkbox(otherIdx, 'current')}${checkbox(otherIdx, 'future')}<td class="tool-cell tool-cell-disabled" aria-hidden="true"></td></tr>`;
    }
    html += '</tbody></table></div>';
    return html;
  }

  setToolValue(qid, idx, axis, checked) {
    const cur = this.responses[qid] || { current: [], future: [], none: [], other_text: '' };
    if (!Array.isArray(cur.none)) cur.none = [];
    const setOf = (axisName) => {
      const s = new Set(cur[axisName] || []);
      return s;
    };
    const writeBack = (axisName, set) => {
      cur[axisName] = [...set].sort((a, b) => a - b);
    };

    const target = setOf(axis);
    if (checked) target.add(idx); else target.delete(idx);
    writeBack(axis, target);

    // (다) 필요 없음 ↔ (가) 현재 / (나) 희망 상호배제 — 체크된 경우에만 반대편 해제
    if (checked) {
      const conflictAxes = axis === 'none' ? ['current', 'future'] : ['none'];
      conflictAxes.forEach(other => {
        const s = setOf(other);
        if (s.has(idx)) {
          s.delete(idx);
          writeBack(other, s);
          // 동일 행의 충돌 셀 체크박스만 직접 해제 (전체 리렌더 회피)
          const table = this.container.querySelector(`.tool-matrix[data-qid="${qid}"]`);
          const cells = table?.querySelectorAll(`tbody tr`);
          if (cells) {
            const totalRows = cells.length;
            const isOtherRow = idx === (this.findQuestion(qid)?.items.length);
            const rowIndex = isOtherRow ? totalRows - 1 : idx;
            const row = cells[rowIndex];
            if (row) {
              const colIndex = other === 'current' ? 1 : (other === 'future' ? 2 : 3);
              const cb = row.children[colIndex]?.querySelector('input[type="checkbox"]');
              if (cb) cb.checked = false;
            }
          }
        }
      });
    }

    this.responses[qid] = cur;
    this.saveResponses();

    const table = this.container.querySelector(`.tool-matrix[data-qid="${qid}"]`);
    table?.classList.remove('has-error');
    table?.closest('.question-block')?.classList.remove('has-error');
  }

  setToolOther(qid, text) {
    const cur = this.responses[qid] || { current: [], future: [], none: [], other_text: '' };
    cur.other_text = text;
    this.responses[qid] = cur;
    this.saveResponses();
  }

  renderTextInput(q) {
    const isIdCode = q.id === 'ID_CODE';
    const cls = isIdCode ? 'text-input id-code-input' : 'text-input';
    if (isIdCode) {
      return `<input type="text" class="${cls}" data-qid="${q.id}" placeholder="${q.placeholder || ''}" maxlength="10" />`;
    }
    return `<textarea class="${cls}" data-qid="${q.id}" placeholder="${q.placeholder || ''}" rows="4"></textarea>`;
  }

  renderSubQuestions(q) {
    let html = `
      <div class="question-block" data-qid="${q.id}">
        <div class="question-label">
          <span class="question-id">${q.id.replace(/([A-Z]+)(\d)/, '$1-$2')}</span>
          <span class="question-text">${q.text}</span>
        </div>
        <div class="sub-question-group">
    `;
    for (const sq of q.subQuestions) {
      const noteHtml = sq.note ? `<p class="sub-question-note">${sq.note}</p>` : '';
      let inner = '';
      if (sq.type === Q_TYPE.SINGLE) {
        inner = this.renderOptions(sq, 'radio');
      } else if (sq.type === Q_TYPE.MULTI_WITH_OTHER || sq.type === Q_TYPE.MULTI_LIMIT_OTHER) {
        inner = this.renderOptions(sq, 'checkbox', true);
      } else if (sq.type === Q_TYPE.MULTI) {
        inner = this.renderOptions(sq, 'checkbox');
      }
      html += `
        <div class="sub-question" data-qid="${sq.id}">
          <div class="sub-question-label">${sq.label}</div>
          ${noteHtml}
          ${inner}
          <p class="question-error" data-error="${sq.id}"></p>
        </div>
      `;
      if (this.isReviewer()) {
        html += this.renderReviewerThread(sq.id);
      }
    }
    html += '</div></div>';
    return html;
  }

  // ── Event Binding ──
  bindEvents(section) {
    this.container.querySelector('#btn-prev')?.addEventListener('click', () => {
      this.currentPage--;
      if (this.currentPage < 0) this.currentPage = 0;
      this.render();
    });

    this.container.querySelector('#btn-next')?.addEventListener('click', () => {
      if (this.validateSection(section)) {
        this.currentPage++;
        this.updateVisibleSections();
        this.render();
      }
    });

    this.container.querySelector('#btn-skip')?.addEventListener('click', () => {
      this.currentPage++;
      this.updateVisibleSections();
      this.render();
    });

    this.container.querySelector('#btn-force-submit')?.addEventListener('click', () => {
      if (!confirm('검증 없이 현재 상태로 제출합니다. 계속할까요?')) return;
      this.currentPage++;
      this.render();
    });

    this.bindThreadEvents();

    this.container.querySelectorAll('.option-list').forEach(list => {
      const qid = list.dataset.qid;
      const type = list.dataset.type;
      const q = this.findQuestion(qid);

      list.querySelectorAll('.option-item').forEach(item => {
        item.addEventListener('click', (e) => {
          if (e.target.classList.contains('other-text')) return;
          const input = item.querySelector('input[type="radio"], input[type="checkbox"]');
          if (!input || input.disabled) return;

          // label+input 구조에서는 브라우저가 click 시 input.checked를 이미 토글한다.
          // 수동 토글은 원상복구 버그를 유발하므로, requestAnimationFrame 뒤에 확정 상태만 읽어 UI 동기화한다.
          requestAnimationFrame(() => {
            if (type === 'radio') {
              list.querySelectorAll('.option-item').forEach(oi => oi.classList.remove('selected'));
              item.classList.add('selected');
              this.setResponse(qid, parseInt(input.value) || input.value);
            } else {
              item.classList.toggle('selected', input.checked);
              this.collectMultiResponse(qid, list, q);
            }

            if (q && q.exclusive !== undefined && input.checked) {
              const idx = parseInt(item.dataset.index);
              if (idx === q.exclusive) {
                list.querySelectorAll('.option-item').forEach(oi => {
                  if (oi !== item) {
                    const cb = oi.querySelector('input[type="checkbox"]');
                    if (cb) { cb.checked = false; oi.classList.remove('selected'); }
                  }
                });
              } else {
                const exItem = list.querySelector(`[data-index="${q.exclusive}"]`);
                if (exItem) {
                  const cb = exItem.querySelector('input[type="checkbox"]');
                  if (cb) { cb.checked = false; exItem.classList.remove('selected'); }
                }
              }
              this.collectMultiResponse(qid, list, q);
            }

            if (q && q.maxSelect) {
              this.enforceMaxSelect(qid, list, q);
            }

            const block = item.closest('.question-block, .sub-question');
            if (block) block.classList.remove('has-error');
          });
        });
      });
    });

    this.container.querySelectorAll('.likert-radio').forEach(radio => {
      radio.addEventListener('change', () => {
        const name = radio.name;
        const [qid, rowStr] = name.split(/_(\d+)$/);
        const row = parseInt(rowStr);
        const val = parseInt(radio.value);
        let resp = this.getResponse(qid) || {};
        resp[row] = val;
        this.setResponse(qid, resp);

        const table = radio.closest('.likert-table');
        if (table) table.classList.remove('has-error');
      });
    });

    this.container.querySelectorAll('.text-input').forEach(el => {
      const qid = el.dataset.qid;
      el.addEventListener('input', () => {
        this.setResponse(qid, el.value);
        el.closest('.question-block')?.classList.remove('has-error');
      });
    });

    this.container.querySelectorAll('.other-text').forEach(el => {
      el.addEventListener('input', () => {
        const qid = el.dataset.qid;
        this.setResponse(qid, el.value);
      });
      el.addEventListener('click', (e) => e.stopPropagation());
    });
  }

  collectMultiResponse(qid, list) {
    const checked = [];
    list.querySelectorAll('input:checked').forEach(cb => {
      checked.push(cb.value === 'other' ? 'other' : parseInt(cb.value));
    });
    this.setResponse(qid, checked);
  }

  enforceMaxSelect(qid, list, q) {
    const checked = list.querySelectorAll('input:checked');
    const unchecked = list.querySelectorAll('input:not(:checked)');
    if (checked.length >= q.maxSelect) {
      unchecked.forEach(cb => {
        cb.disabled = true;
        cb.closest('.option-item')?.classList.add('disabled');
      });
    } else {
      list.querySelectorAll('input').forEach(cb => {
        cb.disabled = false;
        cb.closest('.option-item')?.classList.remove('disabled');
      });
    }
  }

  // ── Restore Saved Values ──
  restoreValues(section) {
    const allQuestions = this.getAllQuestions(section);
    for (const q of allQuestions) {
      const val = this.getResponse(q.id);
      if (val === undefined) continue;

      if (q.type === Q_TYPE.LIKERT_TABLE) {
        if (typeof val === 'object') {
          for (const [row, v] of Object.entries(val)) {
            const radio = this.container.querySelector(`input[name="${q.id}_${row}"][value="${v}"]`);
            if (radio) radio.checked = true;
          }
        }
      } else if (q.type === Q_TYPE.TEXT) {
        const el = this.container.querySelector(`[data-qid="${q.id}"]`);
        if (el) el.value = val;
      } else if (q.type === Q_TYPE.SINGLE || q.type === Q_TYPE.SINGLE_WITH_OTHER) {
        const list = this.container.querySelector(`.option-list[data-qid="${q.id}"]`);
        if (list) {
          const input = list.querySelector(`input[value="${val}"]`);
          if (input) {
            input.checked = true;
            input.closest('.option-item')?.classList.add('selected');
          }
        }
        if (val === 'other') {
          const otherText = this.getResponse(q.id + '_other');
          const otherInput = this.container.querySelector(`.other-text[data-qid="${q.id}_other"]`);
          if (otherInput && otherText) otherInput.value = otherText;
        }
      } else if (Array.isArray(val)) {
        const list = this.container.querySelector(`.option-list[data-qid="${q.id}"]`);
        if (list) {
          val.forEach(v => {
            const input = list.querySelector(`input[value="${v}"]`);
            if (input) {
              input.checked = true;
              input.closest('.option-item')?.classList.add('selected');
            }
          });
          if (q.maxSelect) this.enforceMaxSelect(q.id, list, q);
        }
        if (val.includes('other')) {
          const otherText = this.getResponse(q.id + '_other');
          const otherInput = this.container.querySelector(`.other-text[data-qid="${q.id}_other"]`);
          if (otherInput && otherText) otherInput.value = otherText;
        }
      }
    }
  }

  // ── Validation ──
  validateSection(section) {
    let valid = true;
    const allQuestions = this.getAllQuestions(section);

    for (const q of allQuestions) {
      if (q.optional) continue;

      const val = this.getResponse(q.id);
      let ok = true;

      if (q.type === Q_TYPE.LIKERT_TABLE) {
        const expected = q.items.length;
        ok = val && typeof val === 'object' && Object.keys(val).length === expected;
        if (!ok) {
          const table = this.container.querySelector(`.likert-table[data-qid="${q.id}"]`);
          table?.classList.add('has-error');
          this.showError(q.id, '모든 항목에 응답해 주십시오.');
        }
      } else if (q.type === Q_TYPE.IPA_MATRIX) {
        ok = val && typeof val === 'object';
        if (ok) {
          for (let i = 0; i < q.items.length; i++) {
            const cell = val[i];
            const impOk = cell && (cell.imp >= 1 && cell.imp <= (q.scaleLabelsImportance?.length || 5));
            const expOk = cell && (cell.exp === 'N' && q.hasNA ? true : (cell.exp >= 1 && cell.exp <= (q.scaleLabelsExperience?.length || 5)));
            if (!impOk || !expOk) { ok = false; break; }
          }
        }
        if (!ok) {
          const table = this.container.querySelector(`.ipa-matrix[data-qid="${q.id}"]`);
          table?.classList.add('has-error');
          this.showError(q.id, '모든 항목의 중요도와 체감도를 평가해 주십시오.');
        }
      } else if (q.type === Q_TYPE.TOOL_MATRIX) {
        const cur = (val && Array.isArray(val.current)) ? val.current : [];
        const fut = (val && Array.isArray(val.future)) ? val.future : [];
        const none = (val && Array.isArray(val.none)) ? val.none : [];
        const otherText = (val && val.other_text) ? val.other_text.trim() : '';
        const otherIdx = q.items.length;
        // 일반 도구 행: (가)·(나)·(다) 중 하나 이상
        const baseRowsCovered = q.items.every((_, idx) =>
          cur.includes(idx) || fut.includes(idx) || none.includes(idx)
        );
        // 기타 행: 도구명을 적었을 때만 (가) 또는 (나) 중 하나 이상이 필요
        const otherRowOk = q.otherLabel && otherText
          ? (cur.includes(otherIdx) || fut.includes(otherIdx))
          : true;
        ok = baseRowsCovered && otherRowOk;
        if (!ok) {
          const table = this.container.querySelector(`.tool-matrix[data-qid="${q.id}"]`);
          table?.classList.add('has-error');
          if (!baseRowsCovered) {
            this.showError(q.id, '각 도구마다 (가) 현재 활용 / (나) 향후 활용 희망 / (다) 필요 없음 중 하나 이상을 표시해 주십시오.');
          } else {
            this.showError(q.id, '"기타"에 도구명을 입력하셨다면 (가) 또는 (나) 중 하나 이상을 표시해 주십시오.');
          }
        }
      } else if (q.type === Q_TYPE.TEXT) {
        ok = val && val.trim().length > 0;
        if (!ok) this.showError(q.id, '응답을 입력해 주십시오.');
        if (ok && q.pattern) {
          const re = new RegExp(q.pattern);
          if (!re.test(val.trim())) {
            ok = false;
            this.showError(q.id, q.patternMessage || '올바른 형식으로 입력해 주십시오.');
          }
        }
      } else if (q.type === Q_TYPE.SINGLE || q.type === Q_TYPE.SINGLE_WITH_OTHER) {
        ok = val !== undefined;
        if (!ok) this.showError(q.id, '하나를 선택해 주십시오.');
      } else if (Array.isArray(val)) {
        ok = val.length > 0;
        if (!ok) this.showError(q.id, '하나 이상 선택해 주십시오.');
      } else {
        ok = val !== undefined;
        if (!ok) this.showError(q.id, '응답해 주십시오.');
      }

      if (!ok) {
        valid = false;
        const block = this.container.querySelector(`[data-qid="${q.id}"]`);
        block?.classList.add('has-error');
      }
    }

    if (!valid) {
      const firstError = this.container.querySelector('.has-error');
      firstError?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    return valid;
  }

  showError(qid, msg) {
    const el = this.container.querySelector(`[data-error="${qid}"]`);
    if (el) el.textContent = msg;
  }

  // ── Helpers ──
  getAllQuestions(section) {
    const result = [];
    for (const q of section.questions) {
      if (q.type === Q_TYPE.SUB_QUESTIONS) {
        for (const sq of q.subQuestions) result.push(sq);
      } else {
        result.push(q);
      }
    }
    return result;
  }

  findQuestion(qid) {
    for (const s of sections) {
      for (const q of s.questions) {
        if (q.id === qid) return q;
        if (q.type === Q_TYPE.SUB_QUESTIONS) {
          for (const sq of q.subQuestions) {
            if (sq.id === qid) return sq;
          }
        }
      }
    }
    return null;
  }

  escape(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  formatDateTime(isoStr) {
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
    } catch { return isoStr; }
  }

  // ── Completion ──
  renderCompletion() {
    if (this.token && (!this.submitted || this.editMode === EDIT_MODE.EDIT)) {
      this.submitToServer();
      return;
    }

    const statusBar = this.renderStatusBar();
    const alreadyMsg = this.submitted
      ? '<p class="resubmit-note">이전 응답이 업데이트되었습니다.</p>'
      : '';

    this.container.innerHTML = `
      ${statusBar}
      <div class="survey-container with-status-bar">
        <div class="completion">
          <div class="completion-icon">
            <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
          </div>
          <h2>설문이 완료되었습니다</h2>
          <p>1차 설문에 응해 주셔서 진심으로 감사드립니다.<br/>
          수집된 결과는 2차 설문(IPA·AHP) 문항 설계에 반영되며, 최종적으로 건축 분야 AI 정책 수립의 핵심 근거자료로 활용됩니다.</p>
          ${alreadyMsg}
          <button class="btn btn-next" id="btn-download" style="margin-top:32px">응답 데이터 다운로드 (JSON)</button>
        </div>
      </div>
    `;
    this.container.querySelector('#btn-download')?.addEventListener('click', () => {
      this.downloadResponses();
    });
  }

  async submitToServer() {
    const statusBar = this.renderStatusBar();
    this.container.innerHTML = `
      ${statusBar}
      <div class="survey-container with-status-bar">
        <div class="completion" style="padding:120px 20px">
          <div class="spinner" style="width:40px;height:40px;border:3px solid #e0e0e0;border-top:3px solid #2c2c2c;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 24px"></div>
          <style>@keyframes spin{to{transform:rotate(360deg)}}</style>
          <h2>응답을 제출하고 있습니다…</h2>
        </div>
      </div>
    `;

    try {
      // 사례품 동의·휴대폰은 응답 dict 에서 분리해 별도 필드로 전송 (PII 분리, small-housing 패턴)
      const responsesPayload = { ...this.responses };
      const consentReward = !!responsesPayload['CONSENT_REWARD'];
      const rewardPhone = consentReward ? (responsesPayload['PHONE'] || '').trim() : '';
      delete responsesPayload['CONSENT_REWARD'];
      delete responsesPayload['PHONE'];

      const res = await fetch(`${API_BASE}/ai/api/responses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: this.token,
          survey_version: SURVEY_VERSION,
          responses: responsesPayload,
          consent_reward: consentReward,
          reward_phone: rewardPhone,
        }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();

      this.submitted = true;
      const now = new Date().toISOString();
      if (data.status === 'created') this.submittedAt = now;
      else this.updatedAt = now;
      this.editMode = EDIT_MODE.NEW;
      this.renderCompletion();
    } catch (err) {
      const statusBar = this.renderStatusBar();
      this.container.innerHTML = `
        ${statusBar}
        <div class="survey-container with-status-bar">
          <div class="completion">
            <h2 style="color:var(--c-error)">제출 중 오류가 발생했습니다</h2>
            <p style="margin:16px 0">${err.message}<br/>응답은 브라우저에 저장되어 있습니다. 다시 시도하거나 JSON을 다운로드해 주십시오.</p>
            <button class="btn btn-next" id="btn-retry" style="margin:8px">다시 시도</button>
            <button class="btn btn-prev" id="btn-fallback" style="margin:8px">JSON 다운로드</button>
          </div>
        </div>
      `;
      this.container.querySelector('#btn-retry')?.addEventListener('click', () => this.submitToServer());
      this.container.querySelector('#btn-fallback')?.addEventListener('click', () => this.downloadResponses());
    }
  }

  // 미동의자가 intro 페이지에서 [🎁 사례품 동의만 저장하고 끝내기] 버튼을 누른 경우.
  // 응답은 건드리지 않고 participants.consent_reward + reward_phone 만 PATCH.
  async submitRewardConsentOnly() {
    const phone = this.responses['PHONE'] || '';
    this.container.innerHTML = `
      ${this.renderStatusBar()}
      <div class="survey-container with-status-bar">
        <div class="completion" style="padding:120px 20px">
          <div class="spinner" style="width:40px;height:40px;border:3px solid #e0e0e0;border-top:3px solid #2c2c2c;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 24px"></div>
          <style>@keyframes spin{to{transform:rotate(360deg)}}</style>
          <h2>사례품 동의를 저장하고 있습니다…</h2>
        </div>
      </div>
    `;

    try {
      const res = await fetch(`${API_BASE}/ai/api/responses/${this.token}/reward-consent`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          consent_reward: true,
          reward_phone: phone,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Server error: ${res.status}`);
      }

      // 동의 정보 갱신 — 새로고침 시 미동의 배너가 다시 안 뜨도록 클라이언트 상태 동기화
      if (this.participant) this.participant.consent_reward = true;
      this.updatedAt = new Date().toISOString();

      this.container.innerHTML = `
        ${this.renderStatusBar()}
        <div class="survey-container with-status-bar">
          <div class="completion">
            <div class="completion-icon">
              <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
            </div>
            <h2>사례품 동의가 저장되었습니다</h2>
            <p>입력해 주신 휴대전화 번호로 사례품(2만원 모바일 상품권)이 발송됩니다.<br/>
            정성 어린 응답에 다시 한번 진심으로 감사드립니다.</p>
            <p style="margin-top:24px;color:#666;font-size:13px">
              사례품 발송용 정보는 발송 완료 후 즉시 파기됩니다.
            </p>
          </div>
        </div>
      `;
    } catch (err) {
      this.container.innerHTML = `
        ${this.renderStatusBar()}
        <div class="survey-container with-status-bar">
          <div class="completion">
            <h2 style="color:var(--c-error)">저장 중 오류가 발생했습니다</h2>
            <p style="margin:16px 0">${this.escape(err.message)}</p>
            <button class="btn btn-next" id="btn-retry-reward" style="margin:8px">다시 시도</button>
          </div>
        </div>
      `;
      this.container.querySelector('#btn-retry-reward')?.addEventListener('click',
        () => this.submitRewardConsentOnly());
    }
  }

  downloadResponses() {
    const data = {
      meta: {
        survey: SURVEY_META.title,
        version: SURVEY_VERSION,
        submittedAt: new Date().toISOString(),
        idCode: this.responses['ID_CODE'] || '',
        token: this.token || '',
      },
      responses: { ...this.responses },
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `survey_${data.meta.idCode || 'anon'}_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
}
