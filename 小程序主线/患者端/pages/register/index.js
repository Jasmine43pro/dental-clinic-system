const api = require('../../services/booking');

Page({
  data: { birthDate: '', busy: false, error: '' },

  changeBirthDate({ detail }) {
    this.setData({ birthDate: detail.value });
  },

  returnToLogin() {
    wx.navigateBack({ fail: () => wx.redirectTo({ url: '/pages/appointments/index' }) });
  },

  async register({ detail }) {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    try {
      await api.registerPatient({ ...detail.value, birth_date: this.data.birthDate });
      wx.showToast({ title: '注册成功', icon: 'success' });
      this.returnToLogin();
    } catch (error) {
      this.setData({ error: error.message });
    } finally {
      this.setData({ busy: false });
    }
  },
});
