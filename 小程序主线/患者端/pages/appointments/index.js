const api = require('../../services/booking');

Page({
  data: {
    signedIn: false, username: '', password: '', busy: false, items: [], nextPage: null,
    cancellingId: '', reason: '', error: '', notice: '',
  },

  onShow() {
    this.setData({ signedIn: api.hasSession() });
    if (this.data.signedIn) return this.loadAppointments();
  },

  changePassword({ detail }) {
    this.setData({ password: detail.value });
  },

  async signIn({ detail }) {
    if (this.data.busy) return;
    const username = detail.value.username.trim();
    this.setData({ busy: true, error: '', username });
    try {
      await api.signIn(username, detail.value.password);
      this.setData({ signedIn: true });
    } catch (error) {
      this.showError(error);
    } finally {
      this.setData({ busy: false, password: '' });
    }
    if (this.data.signedIn) await this.loadAppointments();
  },

  signOut() {
    if (this.data.busy) return;
    api.clearSession();
    this.pendingCancellation = null;
    this.setData({ signedIn: false, username: '', items: [], nextPage: null, cancellingId: '', reason: '', error: '', notice: '' });
  },

  showError(error) {
    this.setData({ error: error.message, signedIn: api.hasSession() });
    if (error.statusCode === 401) {
      this.pendingCancellation = null;
      this.setData({ items: [], nextPage: null, cancellingId: '', reason: '', notice: '' });
    }
  },

  async loadAppointments(page = 1) {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      const result = await api.listAppointments(page);
      const seen = new Set(page === 1 ? [] : this.data.items.map((item) => item.id));
      const fresh = result.items.filter((item) => !seen.has(item.id));
      this.setData({ items: page === 1 ? fresh : this.data.items.concat(fresh), nextPage: result.next_page });
    } catch (error) {
      this.showError(error);
    } finally {
      this.setData({ busy: false });
    }
  },

  refresh() {
    if (this.data.busy) return;
    this.setData({ cancellingId: '', reason: '', notice: '' });
    return this.loadAppointments();
  },

  loadMore() {
    if (this.data.nextPage) return this.loadAppointments(this.data.nextPage);
  },

  openVisit({ currentTarget }) {
    const id = currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/visit/index?appointment=${encodeURIComponent(id)}` });
  },

  openCancel({ currentTarget }) {
    if (this.data.busy) return;
    this.setData({ cancellingId: currentTarget.dataset.id, reason: '', error: '', notice: '' });
  },

  closeCancel() {
    if (this.data.busy) return;
    this.setData({ cancellingId: '', reason: '', error: '' });
  },

  changeReason({ detail }) {
    this.setData({ reason: detail.value });
  },

  async confirmCancel() {
    if (this.data.busy) return;
    const id = this.data.cancellingId;
    const item = this.data.items.find((appointment) => appointment.id === id);
    if (!item || !item.can_cancel) return;
    const reason = this.data.reason.trim();
    if (!reason) {
      this.setData({ error: '请填写取消原因。' });
      return;
    }
    this.setData({ busy: true, error: '' });
    try {
      const answer = await wx.showModal({ title: '取消预约', content: `确认取消 ${item.date} ${item.time} 的预约？` });
      if (!answer.confirm) return;
      const previous = this.pendingCancellation;
      if (!previous || previous.id !== id || previous.reason !== reason) {
        this.pendingCancellation = { id, reason, key: api.newRequestKey() };
      }
      const updated = await api.cancelAppointment(id, reason, this.pendingCancellation.key);
      this.pendingCancellation = null;
      this.setData({ items: this.data.items.map((row) => (row.id === id ? updated : row)),
        cancellingId: '', reason: '', notice: `已取消 ${updated.number}` });
    } catch (error) {
      this.showError(error);
    } finally {
      this.setData({ busy: false });
    }
  },
});
