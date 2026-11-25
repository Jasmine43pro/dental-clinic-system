const { clinicApiUrl } = require('../settings');

const api = clinicApiUrl.replace(/clinics\/$/, '');
let sessionToken = '';

function request(path, method = 'GET', data) {
  const header = { 'content-type': 'application/json' };
  if (sessionToken) header.Authorization = `Bearer ${sessionToken}`;
  return new Promise((resolve, reject) => {
    wx.request({
      url: api + path,
      method,
      data,
      header,
      timeout: 12000,
      success({ statusCode, data: body }) {
        if (statusCode >= 200 && statusCode < 300 && body && body.data) {
          resolve(body.data);
          return;
        }
        if (statusCode === 401) sessionToken = '';
        const error = new Error((body && body.error) || '请求失败，请稍后重试。');
        error.statusCode = statusCode;
        reject(error);
      },
      fail() {
        reject(new Error('无法连接预约服务，请检查网络后重试。'));
      },
    });
  });
}

async function signIn(username, password) {
  const result = await request('login/', 'POST', { username, password });
  sessionToken = result.token;
}

async function registerPatient(form) {
  const result = await request('register/', 'POST', form);
  sessionToken = result.token;
}

function listSlots(clinic, date) {
  const query = `clinic=${encodeURIComponent(clinic)}` + (date ? `&date=${encodeURIComponent(date)}` : '');
  return request(`slots/?${query}`);
}

function book(slot, reason, requestKey, allergyStatus) {
  return request('appointments/', 'POST', { slot, reason, request_key: requestKey, allergy_status: allergyStatus });
}

function listAppointments(page = 1) {
  return request(`appointments/?page=${page}`);
}

function cancelAppointment(id, reason, requestKey) {
  return request(`appointments/${encodeURIComponent(id)}/cancel/`, 'POST', { reason, request_key: requestKey });
}

function getVisit(appointmentId) {
  return request(`appointments/${encodeURIComponent(appointmentId)}/visit/`);
}

function getInvoice(invoiceId) {
  return request(`invoices/${encodeURIComponent(invoiceId)}/`);
}

function newRequestKey() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const digit = Math.floor(Math.random() * 16);
    return (character === 'x' ? digit : (digit & 3) | 8).toString(16);
  });
}

function clearSession() {
  sessionToken = '';
}

module.exports = {
  signIn, registerPatient, listSlots, book, listAppointments, cancelAppointment, getVisit, getInvoice, newRequestKey,
  clearSession, hasSession: () => Boolean(sessionToken),
};
