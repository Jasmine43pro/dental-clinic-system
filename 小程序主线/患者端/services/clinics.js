const demoClinics = [
  { id: 1, code: 'DEMO-01', name: '云杉路店', address: '云杉路18号 云杉邻里中心2层', active: true },
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
