import { sections, Q_TYPE, SURVEY_META } from './questions.js';

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
};

const EDIT_MODE = {
  NEW: 'new',
  EDIT: 'edit',
};

export class SurveyEngine {
  constructor(container) {
    this.container = container;
    this.token = new URLSearchParams(window.location.search).get('token');
    this.participant = null;
    this.submitted = false;
    this.submittedAt = null;
    this.updatedAt = null;
    this.editMode = EDIT_MODE.NEW;
    this.gate = this.token ? GATE.LOADING : GATE.DENIED;
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

    if (this.token) {
      this.verifyToken().then(async () => {
        if (this.isReviewer()) {
          await this.fetchThreads();
        }
        this.render();
      });
    } else {
      this.render();
    }
  }

  async verifyToken() {
    try {
      const res = await fetch(`${API_BASE}/ai/api/survey/${this.token}`);
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
      } else {
        this.gate = GATE.OPEN;
      }
    } catch {
      this.gate = GATE.DENIED;
    }
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
    if (this.gate === GATE.DENIED) {
      this.renderAccessDenied();
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
            <dt>연구책임</dt><dd>${m.researcher} (${m.contact})</dd>
          </dl>
        </div>

        <button class="btn-start" id="btn-start">${startLabel}</button>
      </div>
    `;
    this.bindParticipantEvents();
    this.container.querySelector('#btn-start')?.addEventListener('click', () => {
      this.currentPage = 1;
      this.render();
    });
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
        html += `<td><input type="radio" class="likert-radio" name="${q.id}_${idx}" value="${v}" /></td>`;
      }
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
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
      const res = await fetch(`${API_BASE}/ai/api/responses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: this.token,
          survey_version: 'v7',
          responses: { ...this.responses },
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

  downloadResponses() {
    const data = {
      meta: {
        survey: SURVEY_META.title,
        version: 'v7',
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
