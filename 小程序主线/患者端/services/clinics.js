const demoClinics = [
  { id: 1, code: 'DEMO-01', name: '示例诊所', address: '演示地址', active: true },
];

const { clinicApiUrl } = require('../settings');

async function listClinics() {
  if (!clinicApiUrl) return demoClinics;
  return new Promise((resolve, reject) => {
    wx.request({
      url: clinicApiUrl,
      timeout: 8000,
      success({ statusCode, data }) {
        if (statusCode === 200 && Array.isArray(data && data.data)) {
          resolve(data.data);
        } else {
          reject(new Error(`Clinic API returned HTTP ${statusCode} or an invalid response`));
        }
      },
      fail: reject,
    });
  });
}

module.exports = { listClinics };
