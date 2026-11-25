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
      const clinics = (await listClinics()).map((clinic) => ({
        ...clinic,
        address: /^演示地址\d+（非真实地址）$/.test(clinic.address) ? '地址未公开' : clinic.address,
        displayPhone: clinic.phone && !clinic.phone.startsWith('TEST-CLINIC-') ? clinic.phone : '',
      }));
      this.setData({ clinics, status: clinics.length ? 'ready' : 'empty' });
    } catch (error) {
      console.error('诊所列表查询失败', error);
      this.setData({ clinics: [], status: 'error' });
    }
  },
});
