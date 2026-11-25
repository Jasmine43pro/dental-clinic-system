const api = require('../../services/booking');

Page({
  data: {
    clinicId: '', clinicName: '', signedIn: false, username: '', password: '',
    date: '', slots: [], selectedId: '', reason: '', allergyStatus: '', busy: false, error: '', result: null,
  },

  onLoad({ clinic, name }) {
    let clinicName = name || '';
    try {
      clinicName = decodeURIComponent(clinicName);
    } catch (error) {
      // A launcher may already have decoded a name containing a literal percent sign.
    }
    this.setData({ clinicId: clinic, clinicName, signedIn: api.hasSession() });
    if (this.data.signedIn) return this.loadSlots();
  },

  onShow() {
    if (!this.data.signedIn && api.hasSession()) {
      this.setData({ signedIn: true, allergyStatus: '' });
      return this.loadSlots();
    }
  },

  changePassword({ detail }) {
    this.setData({ password: detail.value });
  },

  async signIn({ detail }) {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '', username: detail.value.username.trim() });
    try {
      await api.signIn(detail.value.username.trim(), detail.value.password);
      this.setData({ signedIn: true, allergyStatus: '' });
    } catch (error) {
      this.setData({ error: error.message });
    } finally {
      this.setData({ password: '', busy: false });
    }
    if (this.data.signedIn) await this.loadSlots();
  },

  signOut() {
    if (this.data.busy) return;
    api.clearSession();
    this.pendingBooking = null;
    this.setData({ signedIn: false, username: '', slots: [], selectedId: '', reason: '', allergyStatus: '', result: null, error: '' });
  },

  changeDate({ detail }) {
    if (this.data.busy) return;
    this.setData({ date: detail.value });
    return this.loadSlots();
  },

  clearDate() {
    if (this.data.busy) return;
    this.setData({ date: '' });
    return this.loadSlots();
  },

  async loadSlots() {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '', slots: [], selectedId: '' });
    try {
      const slots = await api.listSlots(this.data.clinicId, this.data.date);
      this.setData({ slots });
    } catch (error) {
      this.setData({ error: error.message, signedIn: api.hasSession() });
    } finally {
      this.setData({ busy: false });
    }
  },

  chooseSlot({ detail }) {
    this.setData({ selectedId: detail.value });
  },

  changeReason({ detail }) {
    this.setData({ reason: detail.value });
  },

  chooseAllergyStatus({ detail }) {
    this.setData({ allergyStatus: detail.value });
  },

  async confirmBooking() {
    if (this.data.busy) return;
    const slot = this.data.slots.find((item) => item.id === this.data.selectedId);
    if (!slot) return;
    const reason = this.data.reason.trim();
    const allergyStatus = this.data.allergyStatus;
    if (!allergyStatus) {
      this.setData({ error: '请先核实并选择过敏状态。' });
      return;
    }
    this.setData({ busy: true, error: '' });
    try {
      const answer = await wx.showModal({
        title: '确认预约',
        content: `${this.data.clinicName}\n${slot.date} ${slot.time} · ${slot.dentist}\n过敏核对：${allergyStatus === 'known' ? '有已知过敏' : '已核实无已知过敏'}`,
      });
      if (!answer.confirm) return;
      const previous = this.pendingBooking;
      if (!previous || previous.id !== slot.id || previous.reason !== reason || previous.allergyStatus !== allergyStatus) {
        this.pendingBooking = { id: slot.id, reason, allergyStatus, key: api.newRequestKey() };
      }
      const result = await api.book(slot.id, reason, this.pendingBooking.key, allergyStatus);
      this.pendingBooking = null;
      this.setData({ result, selectedId: '', reason: '', allergyStatus: '' });
    } catch (error) {
      this.setData({ error: error.message || '预约未完成，请重试。', signedIn: api.hasSession() });
    } finally {
      this.setData({ busy: false });
    }
  },

  anotherBooking() {
    this.setData({ result: null });
    return this.loadSlots();
  },
});
