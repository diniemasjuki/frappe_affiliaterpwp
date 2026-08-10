var PORTAL_DATA = null;
var itiWiz1 = null;
var itiSettings = null;

function initPhoneInput(inputEl) {
  if (typeof window.intlTelInput !== 'function') {
    // Library failed to load (blocked network, CDN down, etc). Don't let
    // this crash the whole wizard/dashboard flow - fall back to a plain
    // text input so registration can still proceed.
    console.error('intlTelInput failed to load; falling back to plain phone input.');
    return null;
  }
  return window.intlTelInput(inputEl, {
    initialCountry: 'my',
    preferredCountries: ['my', 'sg', 'id'],
    separateDialCode: true,
  });
}

function getPhoneValue(iti, inputEl) {
  if (!iti) return inputEl.value.trim();
  var full = iti.getNumber();
  if (full) return full;
  // No country code entered yet, or getNumber() returned empty - fall
  // back to whatever raw text is in the input (better than silently
  // dropping the phone number the person typed).
  return inputEl.value.trim();
}

function setPhoneValue(iti, inputEl, value) {
  if (iti && value) {
    iti.setNumber(value);
  } else {
    inputEl.value = value || '';
  }
}

function sw(id) {
  document.querySelectorAll('.sc').forEach(function(s) { s.classList.remove('on'); });
  var el = document.getElementById(id);
  if (el) el.classList.add('on');
  window.scrollTo(0, 0);

  if (id === 'S-wizard-1' && !itiWiz1) {
    itiWiz1 = initPhoneInput(document.getElementById('wiz1-phone'));
  }
}

function fmt(n) {
  return 'RM' + (parseFloat(n) || 0).toFixed(2);
}

// Token dibaca dari pageData (rujuk www/affiliate-portal.py) — bukan dari
// window.frappe.csrf_token, sebab page ni standalone www page yang tak
// semestinya load bundle Desk yang set global tu, dan cookie 'csrftoken'
// browser tak reliable disegerakkan lepas login_manager.login_as() dalam
// flow login/register/Google OAuth kita — baca terus dari cookie punca
// semua POST lepas login gagal "Invalid Request" walaupun session sah.
// Sama pattern macam travel_booking/public/js/portal.js.
var _pageData = (function() {
  try {
    var el = document.getElementById('pageData');
    return el ? JSON.parse(el.textContent) : {};
  } catch (e) {
    return {};
  }
})();
var CSRF_TOKEN = _pageData.csrf_token || '';

function getCsrfToken() {
  if (CSRF_TOKEN) return CSRF_TOKEN;
  var match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : '';
}

async function API(method, args) {
  args = args || {};
  var res = await fetch('/api/method/' + method, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Frappe-CSRF-Token': getCsrfToken(),
    },
    credentials: 'include',
    body: JSON.stringify(args),
  });
  var data = await res.json();
  if (!res.ok) {
    throw new Error(extractErrorMessage(data));
  }
  return data.message;
}

function extractErrorMessage(data) {
  // Frappe puts the real frappe.throw() text in _server_messages (a
  // JSON-encoded array of JSON-encoded {message, title, ...} objects,
  // or sometimes plain strings) - NOT in data.message, which is only
  // populated on success. Fall back to exc_type only if we truly can't
  // find anything readable.
  if (data && data._server_messages) {
    try {
      var messages = JSON.parse(data._server_messages);
      var texts = messages.map(function(m) {
        try {
          var parsed = JSON.parse(m);
          return parsed.message || m;
        } catch (e) {
          return m;
        }
      }).filter(Boolean);
      if (texts.length) {
        // Strip any HTML tags Frappe may have wrapped the message in.
        return texts.join(' ').replace(/<[^>]*>/g, '');
      }
    } catch (e) {}
  }
  if (data && data.message && typeof data.message === 'string') return data.message;
  if (data && data.exc_type) return data.exc_type;
  return 'Request failed.';
}

function showError(elId, msg) {
  document.getElementById(elId + '-msg').textContent = msg;
  document.getElementById(elId).style.display = 'block';
}
function hideError(elId) {
  document.getElementById(elId).style.display = 'none';
}

async function signInWithGoogle() {
  hideError('login-error');
  try {
    var authUrl = await API('affiliate.api.portal_api.get_google_login_url', {
      redirect_to: '/affiliate-portal',
    });
    window.location.href = authUrl;
  } catch (e) {
    showError('login-error', e.message || 'Could not start Google sign-in. Please try again.');
  }
}

async function doLogin() {
  hideError('login-error');
  var email = document.getElementById('login-em').value.trim();
  var password = document.getElementById('login-pw').value;
  if (!email || !password) { showError('login-error', 'Please enter your email and password.'); return; }

  var btn = document.getElementById('login-btn');
  btn.textContent = 'Signing in...';
  btn.disabled = true;

  try {
    var formData = new URLSearchParams();
    formData.append('usr', email);
    formData.append('pwd', password);
    var loginRes = await fetch('/api/method/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Frappe-CSRF-Token': getCsrfToken() },
      credentials: 'include',
      body: formData,
    });
    if (!loginRes.ok) throw new Error('Invalid email or password.');
    window.location.reload();
  } catch (e) {
    showError('login-error', e.message || 'Invalid email or password.');
  } finally {
    btn.textContent = 'Sign in';
    btn.disabled = false;
  }
}

async function doLogout() {
  try {
    await fetch('/api/method/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Frappe-CSRF-Token': getCsrfToken() },
      credentials: 'include',
    });
  } catch (e) {}
  window.location.reload();
}

async function doRegister() {
  hideError('register-error');
  var firstName = document.getElementById('reg-first-name').value.trim();
  var lastName = document.getElementById('reg-last-name').value.trim();
  var email = document.getElementById('reg-email').value.trim();
  var password = document.getElementById('reg-password').value;
  var passwordConfirm = document.getElementById('reg-password-confirm').value;

  if (!firstName || !lastName || !email || !password) {
    showError('register-error', 'Please fill in all required fields.');
    return;
  }
  if (password !== passwordConfirm) {
    showError('register-error', 'Passwords do not match.');
    return;
  }

  var btn = document.getElementById('register-btn');
  btn.textContent = 'Creating account...';
  btn.disabled = true;

  try {
    await API('affiliate.api.registration.register_affiliate', {
      first_name: firstName,
      last_name: lastName,
      email: email,
      password: password,
    });
    window.location.reload();
  } catch (e) {
    showError('register-error', e.message || 'Could not create your account.');
  } finally {
    btn.textContent = 'Create account';
    btn.disabled = false;
  }
}

async function submitWizard1() {
  hideError('wiz1-error');
  var fullName = document.getElementById('wiz1-full-name').value.trim();
  var phone = getPhoneValue(itiWiz1, document.getElementById('wiz1-phone'));
  var gender = document.getElementById('wiz1-gender').value;
  var dob = document.getElementById('wiz1-dob').value;
  var address = document.getElementById('wiz1-address').value.trim();

  if (!fullName || !phone) {
    showError('wiz1-error', 'Please fill in your name and phone number.');
    return;
  }

  try {
    await API('affiliate.api.portal_api.submit_wizard_step1', {
      full_name: fullName, phone: phone, gender: gender, date_of_birth: dob, address: address,
    });
    sw('S-wizard-2');
  } catch (e) {
    showError('wiz1-error', e.message || 'Could not save. Please try again.');
  }
}

async function uploadWizardDocument(file) {
  var formData = new FormData();
  formData.append('file', file);
  formData.append('is_private', 1);

  var res = await fetch('/api/method/upload_file', {
    method: 'POST',
    headers: { 'X-Frappe-CSRF-Token': getCsrfToken() },
    credentials: 'include',
    body: formData,
  });
  var data = await res.json();
  if (!res.ok || !data.message) throw new Error('File upload failed.');
  return data.message.file_url;
}

async function submitWizard2() {
  hideError('wiz2-error');
  var nationalId = document.getElementById('wiz2-national-id').value.trim();
  var fileInput = document.getElementById('wiz2-document');

  if (!nationalId || !fileInput.files.length) {
    showError('wiz2-error', 'Please enter your ID number and upload a document.');
    return;
  }

  var btn = document.getElementById('wiz2-btn');
  btn.textContent = 'Uploading...';
  btn.disabled = true;

  try {
    var fileUrl = await uploadWizardDocument(fileInput.files[0]);
    await API('affiliate.api.portal_api.submit_wizard_step2', {
      national_id: nationalId, document_id: fileUrl,
    });
    sw('S-wizard-3');
  } catch (e) {
    showError('wiz2-error', e.message || 'Could not save. Please try again.');
  } finally {
    btn.textContent = 'Continue';
    btn.disabled = false;
  }
}

async function submitWizard3() {
  hideError('wiz3-error');
  var bankName = document.getElementById('wiz3-bank-name').value.trim();
  var accountName = document.getElementById('wiz3-account-name').value.trim();
  var accountNumber = document.getElementById('wiz3-account-number').value.trim();

  try {
    await API('affiliate.api.portal_api.submit_wizard_step3', {
      bank_name: bankName, account_name: accountName, account_number: accountNumber,
    });
    sw('S-wizard-4');
  } catch (e) {
    showError('wiz3-error', e.message || 'Could not save. Please try again.');
  }
}

var wizardReferralCheckTimer = null;

function onWizardReferralCodeInput() {
  var input = document.getElementById('wiz4-referral-code');
  input.value = input.value.toUpperCase();
  var el = document.getElementById('wiz4-availability');

  clearTimeout(wizardReferralCheckTimer);
  var code = input.value.trim();

  if (!code) { el.textContent = ''; return; }
  if (code.length !== 8) { el.textContent = ''; return; }

  el.textContent = 'Checking...';
  el.style.color = '#9B9B9E';

  wizardReferralCheckTimer = setTimeout(async function() {
    try {
      var result = await API('affiliate.api.portal_api.check_referral_code_availability', { code: code });
      if (result.available) {
        el.textContent = 'Available';
        el.style.color = '#2E7D46';
      } else {
        el.textContent = result.reason;
        el.style.color = '#B23A26';
      }
    } catch (e) {
      el.textContent = '';
    }
  }, 400);
}

async function submitWizard4() {
  hideError('wiz4-error');
  var code = document.getElementById('wiz4-referral-code').value.trim();

  if (code && code.length !== 8) {
    showError('wiz4-error', 'Referral code must be exactly 8 characters.');
    return;
  }

  try {
    await API('affiliate.api.portal_api.submit_wizard_step4', { referral_code: code });
    window.location.reload();
  } catch (e) {
    showError('wiz4-error', e.message || 'Could not save. Please try again.');
  }
}

async function skipWizard4() {
  hideError('wiz4-error');
  try {
    await API('affiliate.api.portal_api.submit_wizard_step4', { referral_code: '' });
    window.location.reload();
  } catch (e) {
    showError('wiz4-error', e.message || 'Could not skip. Please try again.');
  }
}

async function loadDashboard() {
  try {
    var data = await API('affiliate.api.portal_api.get_dashboard_data', {});
    PORTAL_DATA = data;

    if (data.newly_registered) {
      sw('S-need-register');
      return;
    }

    if (!data.wizard_complete) {
      sw('S-wizard-1');
      return;
    }

    renderDashboard();
    sw('S-dashboard');
  } catch (e) {
    sw('S-login');
  }
}

function renderDashboard() {
  if (!PORTAL_DATA) return;
  var p = PORTAL_DATA.profile;

  document.getElementById('ap-affiliate-name').textContent = p.full_name;
  document.getElementById('ap-greeting').textContent = 'Welcome back, ' + (p.full_name || '').split(' ')[0];
  document.getElementById('ap-avatar').textContent = apInitials(p.full_name);

  var pill = document.getElementById('ap-status-pill');
  if (p.status === 'Verified') {
    pill.textContent = 'Verified';
    pill.className = 'ap-status-pill verified';
  } else {
    pill.textContent = 'Pending verification';
    pill.className = 'ap-status-pill pending';
  }

  if (p.referral_code) {
    document.getElementById('ap-referral-code').textContent = p.referral_code;
    document.getElementById('ap-referral-code').style.display = 'block';
    document.getElementById('ap-referral-pending').style.display = 'none';
    document.getElementById('ap-copy-btn').style.display = 'inline-block';
  } else {
    document.getElementById('ap-referral-code').style.display = 'none';
    document.getElementById('ap-referral-pending').style.display = 'block';
    document.getElementById('ap-copy-btn').style.display = 'none';
  }

  document.getElementById('ap-total-sales').textContent = fmt(PORTAL_DATA.total_sales);
  document.getElementById('ap-total-commission').textContent = fmt(PORTAL_DATA.total_commission);
  document.getElementById('ap-available-balance').textContent = fmt(PORTAL_DATA.available_balance);

  loadLeaderboard();
  loadCommissionStatus();
  loadPerformance();

  populateProfileForm(p);
  populateReferralCodeForm(p);
  populatePaymentMethodForm(p);
  document.getElementById('ap-login-email').textContent = p.email_id || '-';
}

function apInitials(fullName) {
  if (!fullName) return 'A';
  var parts = fullName.trim().split(/\s+/);
  var first = parts[0] ? parts[0][0] : '';
  var last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (first + last).toUpperCase() || 'A';
}

function populateProfileForm(p) {
  document.getElementById('set-full-name').value = p.full_name || '';
  if (!itiSettings) {
    itiSettings = initPhoneInput(document.getElementById('set-phone'));
  }
  setPhoneValue(itiSettings, document.getElementById('set-phone'), p.phone);
  document.getElementById('set-gender').value = p.gender || '';
  document.getElementById('set-dob').value = p.date_of_birth || '';
  document.getElementById('set-address').value = p.address || '';
}

function populateReferralCodeForm(p) {
  document.getElementById('set-referral-code').value = p.referral_code || '';
  document.getElementById('set-referral-availability').textContent = '';

  var hint = document.getElementById('referral-code-cooldown-hint');
  if (!p.last_referral_code_change) {
    hint.textContent = '';
    return;
  }

  var lastChange = new Date(p.last_referral_code_change.replace(' ', 'T'));
  var daysSince = Math.floor((Date.now() - lastChange.getTime()) / (1000 * 60 * 60 * 24));
  var daysRemaining = 30 - daysSince;

  if (daysRemaining > 0) {
    hint.textContent = 'You can change your referral code again in ' + daysRemaining + ' day(s).';
  } else {
    hint.textContent = 'Last changed ' + daysSince + ' day(s) ago.';
  }
}

function populatePaymentMethodForm(p) {
  document.getElementById('set-bank-name').value = p.bank_name || '';
  document.getElementById('set-account-name').value = p.account_name || '';
  document.getElementById('set-account-number').value = p.account_number || '';
}

function badgeClass(status) {
  return 'ap-badge-' + status.toLowerCase();
}

function formatStatusLabel(status) {
  if (status === 'Invoiced') return 'Order in Progress';
  return status;
}

var CURRENT_REFERRAL_FILTER = 'all';

async function apSetReferralFilter(filter) {
  CURRENT_REFERRAL_FILTER = filter;
  document.querySelectorAll('.ap-tab-btn[data-refsub]').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.refsub === filter);
  });
  await loadReferrals();
}

async function loadReferrals() {
  var el = document.getElementById('ap-referrals-list');
  try {
    var data = await API('affiliate.api.portal_api.get_referrals', { filter: CURRENT_REFERRAL_FILTER });
    renderReferrals(data.referrals || []);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load referrals</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function renderReferrals(rows) {
  var el = document.getElementById('ap-referrals-list');
  if (!rows.length) {
    el.innerHTML = '<div class="ap-empty">' +
      '<p class="ap-empty-title">No referrals here yet</p>' +
      '<p class="ap-empty-sub">Share your referral code to see sales appear here.</p>' +
      '</div>';
    return;
  }
  el.innerHTML = rows.map(function(c) {
    var rowClass = c.status === 'Denied' ? 'ap-row-card ap-row-denied' : 'ap-row-card';
    var date = (c.creation || '').split(' ')[0];
    return '<div class="' + rowClass + '">' +
      '<div><p class="ap-row-title">' + c.sales_order + '</p>' +
      '<p class="ap-row-sub">' + date + ' &middot; ' + fmt(c.sales_order_amount) + ' sale</p></div>' +
      '<div style="display:flex;align-items:center;gap:12px;">' +
      '<span style="font-size:13px;font-weight:700;color:#111111;">' + fmt(c.commission_amount) + '</span>' +
      '<span class="ap-badge ' + badgeClass(c.status) + '">' + formatStatusLabel(c.status) + '</span>' +
      '</div></div>';
  }).join('');
}

async function loadPayouts() {
  var el = document.getElementById('ap-payouts-list');
  try {
    var data = await API('affiliate.api.portal_api.get_payouts_summary', {});
    renderPayoutsSummary(data);
    renderPayoutsList(data.payouts || []);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load payouts</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function renderPayoutsSummary(data) {
  document.getElementById('ap-payout-available').textContent = fmt(data.available_balance);
  document.getElementById('ap-payout-total-paid').textContent = fmt(data.total_paid_out);
  document.getElementById('ap-payout-pending').textContent = fmt(data.pending_payout);

  var btn = document.getElementById('ap-request-payout-btn');
  var hint = document.getElementById('ap-payout-request-hint');

  if (data.has_open_payout) {
    btn.disabled = true;
    hint.textContent = 'You already have a payout in progress.';
  } else if (data.available_balance <= 0) {
    btn.disabled = true;
    hint.textContent = 'No approved commissions available to cash out yet.';
  } else if (data.available_balance < data.minimum_cashout) {
    btn.disabled = true;
    var shortfall = data.minimum_cashout - data.available_balance;
    hint.textContent = 'You need ' + fmt(shortfall) + ' more to reach the ' + fmt(data.minimum_cashout) + ' minimum.';
  } else {
    btn.disabled = false;
    hint.textContent = fmt(data.available_balance) + ' is ready to request.';
  }
}

async function apRequestPayout() {
  var btn = document.getElementById('ap-request-payout-btn');
  btn.disabled = true;
  btn.textContent = 'Requesting...';
  try {
    await API('affiliate.api.portal_api.request_payout', {});
    await loadPayouts();
  } catch (e) {
    alert(e.message || 'Could not request payout.');
    await loadPayouts();
  } finally {
    btn.textContent = 'Request payout';
  }
}

function renderPayoutsList(rows) {
  var el = document.getElementById('ap-payouts-list');
  if (!rows.length) {
    el.innerHTML = '<div class="ap-empty">' +
      '<p class="ap-empty-title">No payouts yet</p>' +
      '<p class="ap-empty-sub">Once your commission is processed, it\'ll show up here.</p>' +
      '</div>';
    return;
  }
  el.innerHTML = rows.map(function(p) {
    var period = (p.period_start && p.period_end) ? (p.period_start + ' - ' + p.period_end) : '-';
    return '<div class="ap-row-card">' +
      '<div><p class="ap-row-title">' + (p.bill_no || p.name) + '</p>' +
      '<p class="ap-row-sub">' + (p.generated_date || '-') + ' &middot; ' + period + ' &middot; ' + (p.payment_method || 'Bank Transfer') + '</p></div>' +
      '<div style="display:flex;align-items:center;gap:12px;">' +
      '<span style="font-size:13px;font-weight:700;color:#111111;">' + fmt(p.amount) + '</span>' +
      '<span class="ap-badge ' + badgeClass(p.status || 'Pending') + '">' + (p.status || 'Pending') + '</span>' +
      '</div></div>';
  }).join('');
}

async function loadLeaderboard() {
  var el = document.getElementById('ap-leaderboard');
  try {
    var data = await API('affiliate.api.portal_api.get_leaderboard', {});
    renderLeaderboard(data);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load the leaderboard</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function rankBadgeClass(rank) {
  if (rank === 1) return 'ap-board-rank gold';
  if (rank === 2) return 'ap-board-rank silver';
  if (rank === 3) return 'ap-board-rank bronze';
  return 'ap-board-rank';
}

function renderLeaderboard(data) {
  var el = document.getElementById('ap-leaderboard');
  var rows = data.leaderboard || [];

  if (!rows.length) {
    el.innerHTML = '<div class="ap-empty">' +
      '<div class="ap-empty-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none"><path d="M8 21H16M12 17V21M6 3H18V8C18 11.31 15.31 14 12 14C8.69 14 6 11.31 6 8V3Z" stroke="#A6ADB4" stroke-width="1.6"/><path d="M6 5H3V7C3 8.66 4.34 10 6 10M18 5H21V7C21 8.66 19.66 10 18 10" stroke="#A6ADB4" stroke-width="1.6"/></svg></div>' +
      '<p class="ap-empty-title">No ranked affiliates yet</p>' +
      '<p class="ap-empty-sub">Be the first to refer a sale and claim the top spot.</p>' +
      '</div>';
    return;
  }

  var html = rows.map(function(r) {
    var rowClass = r.is_you ? 'ap-board-row you' : 'ap-board-row';
    var youTag = r.is_you ? '<span class="you-tag">You</span>' : '';
    return '<div class="' + rowClass + '">' +
      '<div class="' + rankBadgeClass(r.rank) + '">' + r.rank + '</div>' +
      '<div class="ap-board-name">' + r.first_name + youTag + '</div>' +
      '<div class="ap-board-sales">' + fmt(r.total_sales) + '</div>' +
      '</div>';
  }).join('');

  var alreadyShown = rows.some(function(r) { return r.is_you; });
  if (!alreadyShown && data.your_rank) {
    html += '<div class="ap-board-your-rank ap-board-row you">' +
      '<div class="ap-board-rank">' + data.your_rank + '</div>' +
      '<div class="ap-board-name">You<span class="you-tag">You</span></div>' +
      '<div class="ap-board-sales">' + fmt(data.your_total_sales) + '</div>' +
      '</div>';
  }

  el.innerHTML = html;
}

var CURRENT_PERF_PERIOD = 'week';

async function loadCommissionStatus() {
  var el = document.getElementById('ap-commission-status');
  try {
    var data = await API('affiliate.api.portal_api.get_commission_status', {});
    renderCommissionStatus(data);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load commission status</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function renderCommissionStatus(data) {
  var el = document.getElementById('ap-commission-status');
  var unpaid = data.unpaid_amount || 0;
  var paid = data.paid_amount || 0;
  var total = unpaid + paid;

  if (total === 0) {
    el.innerHTML = '<div class="ap-empty">' +
      '<p class="ap-empty-title">No commissions yet</p>' +
      '<p class="ap-empty-sub">Share your referral code to start earning.</p>' +
      '</div>';
    return;
  }

  var unpaidPct = (unpaid / total) * 100;
  var paidPct = 100 - unpaidPct;

  el.innerHTML =
    '<div class="ap-status-bar">' +
      '<div class="ap-status-seg-unpaid" style="width:' + unpaidPct + '%"></div>' +
      '<div class="ap-status-seg-paid" style="width:' + paidPct + '%"></div>' +
    '</div>' +
    '<div class="ap-status-legend-row">' +
      '<div>' +
        '<span class="ap-status-dot" style="background:#E8A33D"></span>' +
        '<span class="ap-status-label">Unpaid</span>' +
        '<p class="ap-status-value">' + fmt(unpaid) + '</p>' +
        '<p class="ap-status-count">' + data.unpaid_count + ' commission' + (data.unpaid_count === 1 ? '' : 's') + '</p>' +
      '</div>' +
      '<div>' +
        '<span class="ap-status-dot" style="background:#E4DECF"></span>' +
        '<span class="ap-status-label">Paid</span>' +
        '<p class="ap-status-value">' + fmt(paid) + '</p>' +
        '<p class="ap-status-count">' + data.paid_count + ' commission' + (data.paid_count === 1 ? '' : 's') + '</p>' +
      '</div>' +
    '</div>';
}

async function apSetPerfPeriod(period) {
  CURRENT_PERF_PERIOD = period;
  document.querySelectorAll('.ap-perf-period-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.period === period);
  });
  await loadPerformance();
}

async function loadPerformance() {
  var el = document.getElementById('ap-performance');
  try {
    var data = await API('affiliate.api.portal_api.get_performance', { period: CURRENT_PERF_PERIOD });
    renderPerformance(data);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load performance</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function renderPerformance(data) {
  var el = document.getElementById('ap-performance');
  var c = data.confirmed || {};
  var p = data.pending || {};

  el.innerHTML =
    '<p class="ap-perf-section-label">Confirmed</p>' +
    '<div class="ap-perf-grid" style="margin-bottom:16px;">' +
      '<div class="ap-perf-metric">' +
        '<p class="ap-perf-metric-label">Orders</p>' +
        '<p class="ap-perf-metric-value">' + (c.orders || 0) + '</p>' +
      '</div>' +
      '<div class="ap-perf-metric">' +
        '<p class="ap-perf-metric-label">Total sales</p>' +
        '<p class="ap-perf-metric-value">' + fmt(c.total_sales) + '</p>' +
      '</div>' +
      '<div class="ap-perf-metric">' +
        '<p class="ap-perf-metric-label">Commission</p>' +
        '<p class="ap-perf-metric-value success">' + fmt(c.total_commission) + '</p>' +
      '</div>' +
    '</div>' +
    '<p class="ap-perf-section-label">Pending review <span class="note">- not yet confirmed, may change</span></p>' +
    '<div class="ap-perf-grid">' +
      '<div class="ap-perf-metric pending">' +
        '<p class="ap-perf-metric-label">Orders</p>' +
        '<p class="ap-perf-metric-value">' + (p.orders || 0) + '</p>' +
      '</div>' +
      '<div class="ap-perf-metric pending">' +
        '<p class="ap-perf-metric-label">Total sales</p>' +
        '<p class="ap-perf-metric-value">' + fmt(p.total_sales) + '</p>' +
      '</div>' +
      '<div class="ap-perf-metric pending">' +
        '<p class="ap-perf-metric-label">Commission</p>' +
        '<p class="ap-perf-metric-value">' + fmt(p.total_commission) + '</p>' +
      '</div>' +
    '</div>';
}

var TABS_LOADED = {};

var TAB_TITLES = {
  overview: 'Dashboard',
  referrals: 'My referrals',
  payouts: 'Payouts',
  products: 'Products',
  profile: 'Profile',
  'referral-code': 'Referral code',
  'payment-method': 'Payment method',
  'login-method': 'Login method',
};

function apShowTab(tab) {
  document.querySelectorAll('.ap-tab-panel').forEach(function(p) { p.classList.remove('active'); });
  document.getElementById('tab-' + tab).classList.add('active');
  document.querySelectorAll('.ap-nav-item[data-tab]').forEach(function(el) {
    el.classList.toggle('active', el.dataset.tab === tab);
  });

  var titleEl = document.getElementById('ap-mobile-title');
  if (titleEl) titleEl.textContent = TAB_TITLES[tab] || 'Dashboard';
  apCloseSidebar();

  if (!TABS_LOADED[tab]) {
    TABS_LOADED[tab] = true;
    if (tab === 'referrals') loadReferrals();
    if (tab === 'payouts') loadPayouts();
    if (tab === 'products') loadProducts();
  }
}

function apToggleSidebar() {
  document.getElementById('ap-sidebar').classList.toggle('open');
  document.getElementById('ap-sidebar-overlay').classList.toggle('open');
}

function apCloseSidebar() {
  document.getElementById('ap-sidebar').classList.remove('open');
  document.getElementById('ap-sidebar-overlay').classList.remove('open');
}

function apCopyCode() {
  var text = document.getElementById('ap-referral-code').textContent;
  navigator.clipboard.writeText(text);
}

function apShowSaved(elId) {
  var el = document.getElementById(elId);
  el.style.display = 'inline-block';
  setTimeout(function() { el.style.display = 'none'; }, 2000);
}

async function apSaveProfile() {
  try {
    await API('affiliate.api.portal_api.update_settings', {
      full_name: document.getElementById('set-full-name').value,
      phone: getPhoneValue(itiSettings, document.getElementById('set-phone')),
      gender: document.getElementById('set-gender').value,
      date_of_birth: document.getElementById('set-dob').value,
      address: document.getElementById('set-address').value,
    });
    apShowSaved('profile-saved');
  } catch (e) {
    alert(e.message || 'Could not save profile.');
  }
}

var settingsReferralCheckTimer = null;

function onSettingsReferralCodeInput() {
  var input = document.getElementById('set-referral-code');
  input.value = input.value.toUpperCase();
  var el = document.getElementById('set-referral-availability');

  clearTimeout(settingsReferralCheckTimer);
  var code = input.value.trim();
  var currentCode = (PORTAL_DATA && PORTAL_DATA.profile.referral_code) || '';

  if (!code || code.length !== 8 || code === currentCode) { el.textContent = ''; return; }

  el.textContent = 'Checking...';
  el.style.color = '#9B9B9E';

  settingsReferralCheckTimer = setTimeout(async function() {
    try {
      var result = await API('affiliate.api.portal_api.check_referral_code_availability', { code: code });
      if (result.available) {
        el.textContent = 'Available';
        el.style.color = '#2E7D46';
      } else {
        el.textContent = result.reason;
        el.style.color = '#B23A26';
      }
    } catch (e) {
      el.textContent = '';
    }
  }, 400);
}

async function apSaveReferralCode() {
  hideError('referral-code-error');
  var code = document.getElementById('set-referral-code').value.trim();

  if (code.length !== 8) {
    showError('referral-code-error', 'Referral code must be exactly 8 characters.');
    return;
  }

  try {
    await API('affiliate.api.portal_api.set_custom_referral_code', { referral_code: code });
    apShowSaved('referral-code-saved');
    await loadDashboard();
  } catch (e) {
    showError('referral-code-error', e.message || 'Could not save referral code.');
  }
}

async function apSavePaymentMethod() {
  try {
    await API('affiliate.api.portal_api.update_settings', {
      bank_name: document.getElementById('set-bank-name').value,
      account_name: document.getElementById('set-account-name').value,
      account_number: document.getElementById('set-account-number').value,
    });
    apShowSaved('payment-saved');
  } catch (e) {
    alert(e.message || 'Could not save payment method.');
  }
}

async function apChangePassword() {
  hideError('password-error');
  var current = document.getElementById('pw-current').value;
  var newPw = document.getElementById('pw-new').value;
  var confirm = document.getElementById('pw-confirm').value;

  if (!current || !newPw || !confirm) {
    showError('password-error', 'Please fill in all password fields.');
    return;
  }
  if (newPw !== confirm) {
    showError('password-error', 'New password and confirmation don\'t match.');
    return;
  }

  try {
    await API('affiliate.api.portal_api.change_password', {
      current_password: current,
      new_password: newPw,
      confirm_password: confirm,
    });
    document.getElementById('pw-current').value = '';
    document.getElementById('pw-new').value = '';
    document.getElementById('pw-confirm').value = '';
    apShowSaved('password-saved');
  } catch (e) {
    showError('password-error', e.message || 'Could not change password.');
  }
}

var PRODUCTS_BASE_URL = '';
var PRODUCTS_LIST = [];

async function loadProducts() {
  var el = document.getElementById('ap-products-grid');
  try {
    var data = await API('affiliate.api.products.get_products', {});
    PRODUCTS_BASE_URL = (data.base_url || '').replace(/\/+$/, '');
    PRODUCTS_LIST = data.products || [];
    renderProducts(PRODUCTS_LIST);
  } catch (e) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">Couldn\'t load products</p><p class="ap-empty-sub">Try refreshing the page.</p></div>';
  }
}

function buildProductUrl(packageId) {
  var code = (PORTAL_DATA && PORTAL_DATA.profile.referral_code) || '';
  var url = PRODUCTS_BASE_URL + '/booking/?trip=' + encodeURIComponent(packageId);
  if (code) url += '&sp=' + encodeURIComponent(code);
  return url;
}

function groupByTrip(list) {
  var groups = [];
  var byTrip = {};
  list.forEach(function(p, idx) {
    if (!byTrip[p.trip_id]) {
      byTrip[p.trip_id] = { trip_id: p.trip_id, trip_name: p.trip_name, trip_image: p.trip_image, rows: [] };
      groups.push(byTrip[p.trip_id]);
    }
    byTrip[p.trip_id].rows.push(idx);
  });
  return groups;
}

function renderProducts(list) {
  var el = document.getElementById('ap-products-grid');
  var hasCode = !!(PORTAL_DATA && PORTAL_DATA.profile.referral_code);

  if (!list.length) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">No products available right now</p><p class="ap-empty-sub">Check back once a trip is open for booking.</p></div>';
    return;
  }

  var groups = groupByTrip(list);

  el.innerHTML = groups.map(function(g, gi) {
    var img = g.trip_image
      ? '<img src="' + g.trip_image + '" alt="">'
      : '<svg width="30" height="30" viewBox="0 0 24 24" fill="none"><path d="M3 8L12 3L21 8V16L12 21L3 16V8Z" stroke="#B0AC9F" stroke-width="1.6"/></svg>';

    var rowsHtml = g.rows.map(function(idx) {
      var p = list[idx];
      var copyLabel = hasCode ? 'Copy link' : 'Code pending';
      return '<div class="ap-product-row">' +
        '<div class="ap-product-row-main">' +
          '<div>' +
            '<p class="ap-product-row-title">' + p.package_title + '</p>' +
            (p.package_type ? '<span class="ap-badge ap-badge-neutral">' + p.package_type + '</span>' : '') +
          '</div>' +
          '<button class="btn btn-outline ap-product-copy-btn" data-copy="' + idx + '"' + (hasCode ? '' : ' disabled') + '>' + copyLabel + '</button>' +
        '</div>' +
      '</div>';
    }).join('');

    var pkgWord = g.rows.length === 1 ? 'package' : 'packages';

    return '<div class="ap-trip-group">' +
      '<div class="ap-trip-group-image">' + img + '</div>' +
      '<div class="ap-trip-group-body">' +
        '<p class="ap-trip-group-title">' + g.trip_name + '</p>' +
        '<button class="ap-trip-group-toggle" data-toggle="' + gi + '">' +
          '<span>' + g.rows.length + ' ' + pkgWord + '</span>' +
          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        '</button>' +
        '<div class="ap-trip-group-packages" data-packages="' + gi + '">' + rowsHtml + '</div>' +
      '</div>' +
    '</div>';
  }).join('');

  el.querySelectorAll('[data-toggle]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var gi = btn.getAttribute('data-toggle');
      var panel = el.querySelector('[data-packages="' + gi + '"]');
      var open = panel.classList.contains('open');
      panel.classList.toggle('open', !open);
      btn.classList.toggle('open', !open);
    });
  });

  el.querySelectorAll('[data-copy]').forEach(function(btn) {
    if (btn.disabled) return;
    btn.addEventListener('click', function() {
      var idx = btn.getAttribute('data-copy');
      navigator.clipboard.writeText(buildProductUrl(list[idx].name));
      var original = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(function() { btn.textContent = original; }, 1400);
    });
  });
}

function apFilterProducts(query) {
  query = (query || '').toLowerCase();
  renderProducts(PRODUCTS_LIST.filter(function(p) {
    return !query || p.trip_name.toLowerCase().indexOf(query) > -1;
  }));
}

loadDashboard();