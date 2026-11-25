const api = require('../../services/booking');

function displayTreatment(line) {
  return { ...line, name_snapshot: line.name_snapshot.replace(/（虚构价格）$/, '') };
}

Page({
  data: { appointmentId: '', visit: null, invoice: null, busy: false, error: '', loginNeeded: false },

  onLoad({ appointment }) {
    this.setData({ appointmentId: appointment || '' });
    return this.loadVisit();
  },

  showError(error) {
    if (error.statusCode === 401) {
      this.setData({ visit: null, invoice: null, loginNeeded: true });
    }
    this.setData({ error: error.message });
  },

  async loadVisit() {
    if (this.data.busy) return;
    if (!this.data.appointmentId) {
      this.setData({ error: '请从我的预约进入就诊摘要。' });
      return;
    }
    this.setData({ busy: true, error: '', loginNeeded: false });
    try {
      const visit = await api.getVisit(this.data.appointmentId);
      this.setData({ visit: { ...visit, treatments: visit.treatments.map(displayTreatment) } });
    } catch (error) {
      this.showError(error);
    } finally {
      this.setData({ busy: false });
    }
  },

  async openInvoice({ currentTarget }) {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      const invoice = await api.getInvoice(currentTarget.dataset.id);
      this.setData({ invoice: { ...invoice, lines: invoice.lines.map(displayTreatment) } });
    } catch (error) {
      this.showError(error);
    } finally {
      this.setData({ busy: false });
    }
  },

  closeInvoice() {
    this.setData({ invoice: null, error: '' });
    return this.loadVisit();
  },

  returnToAppointments() {
    wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/appointments/index' }) });
  },
});
