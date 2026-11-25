const { listClinics } = require('../../services/clinics');
const { clinicApiUrl } = require('../../settings');

Page({
  data: { clinics: [], status: 'loading', demoMode: !clinicApiUrl },

  onLoad() {
    return this.loadClinics();
  },

  openBooking({ currentTarget }) {
    const { id, name } = currentTarget.dataset;
    wx.navigateTo({ url: `/pages/booking/index?clinic=${encodeURIComponent(id)}&name=${encodeURIComponent(name)}` });
  },

  openAppointments() {
    wx.navigateTo({ url: '/pages/appointments/index' });
  },

  async loadClinics() {
    this.setData({ status: 'loading' });
    try {
      const clinics = await listClinics();
      this.setData({ clinics, status: clinics.length ? 'ready' : 'empty' });
    } catch (error) {
      console.error('诊所列表查询失败', error);
      this.setData({ clinics: [], status: 'error' });
    }
  },
});
