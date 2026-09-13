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
    prefillPersonalInfo();
  }
}

// Personal info form (wizard step 1): full name di-prefill dari info
// login (first + last) dan phone dari Contact/Customer yang dipautkan
// ke akaun (pageData.registration). Medan tetap boleh diedit — prefill
// sekali sahaja dan tak menulis-ganti apa yang user dah taip.
function prefillPersonalInfo() {
  var r = getRegistrationPrefill();
  var nameEl = document.getElementById('wiz1-full-name');
  if (nameEl && !nameEl.value && r.full_name) {
    nameEl.value = r.full_name.toUpperCase();
  }
  var phoneEl = document.getElementById('wiz1-phone');
  if (phoneEl && !phoneEl.value && r.phone) {
    setPhoneValue(itiWiz1, phoneEl, r.phone);
  }
}

// Session state di-inject dari server (rujuk www/affiliate.py pageData).
function isGuestSession() {
  return !_pageData.session_user || _pageData.session_user === 'Guest';
}

// Maklumat sedia ada pengguna login (dari Contact/User, disediakan di
// www/affiliate.py) untuk skrin pengesahan pendaftaran.
function getRegistrationPrefill() {
  return (_pageData && _pageData.registration) || {};
}

// editable=false: nama dipaparkan read-only (maklumat lengkap sedia ada).
// editable=true: nama tak lengkap — medan nama dibolehkan untuk dilengkap
//kan, email tetap terkunci pada akaun session.
function showConfirmRegistration(editable) {
  var r = getRegistrationPrefill();
  var first = document.getElementById('confirm-first-name');
  var last = document.getElementById('confirm-last-name');
  first.value = r.first_name || '';
  last.value = r.last_name || '';
  document.getElementById('confirm-email').value = r.email || _pageData.session_user;
  [first, last].forEach(function(el) {
    el.readOnly = !editable;
    el.style.background = editable ? '' : '#FBF9F4';
  });
  document.getElementById('confirm-title').textContent = editable
    ? 'Complete your details'
    : 'Confirm your details';
  document.getElementById('confirm-sub').textContent = editable
    ? 'Add your name to join the affiliate program - no new password needed'
    : 'These details are already on your account - confirm them to join the affiliate program';
  sw('S-confirm');
}

async function doConfirmRegister() {
  hideError('confirm-error');
  var r = getRegistrationPrefill();
  var firstName = document.getElementById('confirm-first-name').value.trim();
  var lastName = document.getElementById('confirm-last-name').value.trim();

  if (!firstName || !lastName) {
    showError('confirm-error', 'Please fill in your first and last name.');
    return;
  }

  var btn = document.getElementById('confirm-btn');
  btn.textContent = 'Registering...';
  btn.disabled = true;
  try {
    await API('affiliate.api.registration.register_affiliate', {
      first_name: firstName,
      last_name: lastName,
      email: r.email || _pageData.session_user,
    });
    window.location.reload();
  } catch (e) {
    showError('confirm-error', e.message || 'Could not register. Please try again.');
    btn.textContent = 'Confirm & register';
    btn.disabled = false;
  }
}

// Guest sahaja yang nampak sign-in form — pengguna berlogin tidak pernah
// dilandingkan ke sini (mereka ke confirm screen / no-access).
function showLoginForSession() {
  sw('S-login');
}

// Pengguna berlogin yang tak boleh didaftarkan (role/profile dalam
// keadaan pelik, akaun dimatikan): notis neutral, bukan sign-in form.
function showNoAccess() {
  var emailEl = document.getElementById('noaccess-email');
  if (emailEl) emailEl.textContent = _pageData.session_user || '';
  sw('S-no-access');
}

// Currency symbols reported by the API (never hardcoded here - the
// server derives them from the Currency doctype). Populated by
// mergeCurrencySymbols() from every API response that mentions money.
var CURRENCY_SYMBOLS = {};

function mergeCurrencySymbols(map) {
  if (map) {
    for (var code in map) {
      if (map.hasOwnProperty(code)) CURRENCY_SYMBOLS[code] = map[code];
    }
  }
}

function curSymbol(currency) {
  return CURRENCY_SYMBOLS[currency] || currency || 'RM';
}

function fmt(n, currency) {
  return curSymbol(currency) + (parseFloat(n) || 0).toFixed(2);
}

// "5%" / "5.5%" - rates are stored as Percent and may carry decimals;
// display without trailing zeros.
function fmtRate(rate) {
  return parseFloat(rate || 0) + '%';
}

// "≈ RM1,250.00" - display-only conversion into the affiliate's chosen
// currency; shown with a visible approx marker, never as an exact figure.
function fmtEst(n, currency) {
  return '\u2248 ' + fmt(n, currency);
}

// "MYR100.00 · SGD50.00" - exact per-currency breakdown.
function fmtBreakdown(rows, key) {
  return (rows || []).map(function(r) { return fmt(r[key], r.currency); }).join(' \u00b7 ');
}

// Card helper: main line = estimate in the chosen currency (or the
// breakdown when no estimate is available), sub line = exact breakdown.
function renderMoneyCard(valueElId, subElId, est, estCurrency, breakdownRows, breakdownKey) {
  var valueEl = document.getElementById(valueElId);
  var subEl = subElId ? document.getElementById(subElId) : null;
  var breakdown = fmtBreakdown(breakdownRows, breakdownKey);

  if (est !== null && est !== undefined) {
    valueEl.textContent = fmtEst(est, estCurrency);
  } else {
    valueEl.textContent = breakdown || fmt(0, estCurrency);
  }
  if (subEl) {
    subEl.textContent = (est !== null && est !== undefined && breakdown) ? breakdown : '';
    subEl.style.display = subEl.textContent ? 'block' : 'none';
  }
}

// Token dibaca dari pageData (rujuk www/affiliate.py) — bukan dari
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
      redirect_to: '/affiliate',
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
  var payload = { first_name: firstName, last_name: lastName, email: email };

  if (!firstName || !lastName || !email) {
    showError('register-error', 'Please fill in all required fields.');
    return;
  }

  // Guest sahaja yang perlu password — logged-in user daftar guna akaun
  // sedia ada (server abaikan/buang field password untuk path itu).
  if (isGuestSession()) {
    var password = document.getElementById('reg-password').value;
    var passwordConfirm = document.getElementById('reg-password-confirm').value;
    if (!password) {
      showError('register-error', 'Please fill in all required fields.');
      return;
    }
    if (password !== passwordConfirm) {
      showError('register-error', 'Passwords do not match.');
      return;
    }
    payload.password = password;
  }

  var btn = document.getElementById('register-btn');
  btn.textContent = 'Creating account...';
  btn.disabled = true;

  try {
    await API('affiliate.api.registration.register_affiliate', payload);
    window.location.reload();
  } catch (e) {
    showError('register-error', e.message || 'Could not create your account.');
  } finally {
    btn.textContent = isGuestSession() ? 'Create account' : 'Register as Affiliate';
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
  // Posts the file to our own whitelisted endpoint
  // (affiliate.api.portal_api.upload_affiliate_document) rather than
  // /api/method/upload_file. The generic endpoint enforces Frappe's
  // File-specific has_permission gate, which denies create on an
  // unattached private file to any non-Administrator (owner is only set
  // during db_insert, after the create check) - so affiliates were
  // 403-blocked at step 2. Our endpoint saves the File with
  // ignore_permissions, scoped to the caller's own profile. Returns the
  // file_url string directly as `message`.
  var formData = new FormData();
  formData.append('file', file);

  var res = await fetch('/api/method/affiliate.api.portal_api.upload_affiliate_document', {
    method: 'POST',
    headers: { 'X-Frappe-CSRF-Token': getCsrfToken() },
    credentials: 'include',
    body: formData,
  });
  var data = await res.json();
  if (!res.ok || !data.message) throw new Error(extractErrorMessage(data) || 'File upload failed.');
  return data.message;
}

// National ID / IC: huruf + nombor sahaja, uppercase - simbol dibuang
// semasa menaip, dan diseragamkan semula di server (normalize_national_id).
function sanitizeNationalIdValue(el) {
  el.value = el.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function onWizardNationalIdInput() {
  sanitizeNationalIdValue(document.getElementById('wiz2-national-id'));
}

// Auto-upload + OCR: bila fail dipilih, teruskan ke server dan jalankan
// pengesanan AI supaya panel "details detected" siap sebelum user tekan
// Continue. URL yang dah naik disimpan - submitWizard2 guna semula dan
// tidak memuat-naik fail yang sama dua kali. WIZ2_SELECTION_SEQ memastikan
// rantaian upload fail lama (yang resolve lewat) tidak menulis-ganti
// keadaan fail baharu yang baru dipilih.
var WIZ2_UPLOADED_URL = '';
var WIZ2_EXTRACTED = null;
var WIZ2_SELECTION_SEQ = 0;

function escapeHtml(text) {
  var div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

function onWizardDocumentSelected(input) {
  hideError('wiz2-error');
  var status = document.getElementById('wiz2-upload-status');
  var panel = document.getElementById('wiz2-extracted');
  WIZ2_SELECTION_SEQ += 1;
  var seq = WIZ2_SELECTION_SEQ;
  var isStale = function() { return seq !== WIZ2_SELECTION_SEQ; };

  WIZ2_UPLOADED_URL = '';
  WIZ2_EXTRACTED = null;
  panel.style.display = 'none';
  document.getElementById('wiz2-verify-result').style.display = 'none';

  if (!input.files.length) { status.textContent = ''; return; }

  status.textContent = 'Uploading...';
  uploadWizardDocument(input.files[0]).then(function(fileUrl) {
    if (isStale()) return null;
    WIZ2_UPLOADED_URL = fileUrl;
    status.textContent = 'Uploaded. Reading your ID with AI...';
    return API('affiliate.api.portal_api.extract_affiliate_id_document', { document_id: fileUrl });
  }).then(function(res) {
    if (isStale()) return;
    if (!res || res.enabled === false) {
      status.textContent = 'Uploaded.';
      return;
    }
    if (res.error) {
      status.textContent = 'Uploaded, but AI reading failed: ' + res.error;
      return;
    }
    WIZ2_EXTRACTED = res.extracted || {};
    renderExtractedPanel(WIZ2_EXTRACTED);
    status.textContent = 'Uploaded and read. Check the details detected below.';
    var icEl = document.getElementById('wiz2-national-id');
    if (!icEl.value && WIZ2_EXTRACTED.national_id) {
      icEl.value = WIZ2_EXTRACTED.national_id;
      sanitizeNationalIdValue(icEl);
    }
  }).catch(function(e) {
    if (isStale()) return;
    // Upload mungkin sudah berjaya sebelum OCR gagal - jangan buang
    // URL yang sah; submit boleh teruskan tanpa OCR.
    if (WIZ2_UPLOADED_URL) {
      status.textContent = 'Uploaded, but AI reading failed: ' + (e.message || 'unknown error.');
    } else {
      status.textContent = '';
      showError('wiz2-error', e.message || 'File upload failed. Please try again.');
    }
  });
}

var EXTRACTED_FIELD_LABELS = {
  full_name: 'Full name',
  national_id: 'National ID / IC',
  date_of_birth: 'Date of birth',
  gender: 'Gender',
  address: 'Address',
};

function renderExtractedPanel(extracted) {
  var fieldsEl = document.getElementById('wiz2-extracted-fields');
  var rows = [];
  Object.keys(EXTRACTED_FIELD_LABELS).forEach(function(key) {
    var value = (extracted[key] || '').toString().trim();
    if (!value) return;
    rows.push(
      '<div style="display:flex;justify-content:space-between;gap:12px;font-size:13px;">' +
        '<span style="color:#7D7A70;white-space:nowrap;">' + EXTRACTED_FIELD_LABELS[key] + '</span>' +
        '<span style="font-weight:600;color:#111111;text-align:right;word-break:break-word;">' + escapeHtml(value) + '</span>' +
      '</div>'
    );
  });

  if (!rows.length) {
    document.getElementById('wiz2-extracted').style.display = 'none';
    return;
  }

  fieldsEl.innerHTML = rows.join('');
  var applyBtn = document.getElementById('wiz2-apply-btn');
  applyBtn.textContent = 'Use these details';
  applyBtn.disabled = false;
  document.getElementById('wiz2-extracted').style.display = 'block';
}

// "Use these details": tulis nilai yang AI baca dari dokumen terus ke
// profil (server akan uppercase-kan nama) dan isi ruang IC yang masih
// kosong. Medan yang kosong pada dokumen tidak disentuh.
async function applyExtractedIdDetails() {
  var btn = document.getElementById('wiz2-apply-btn');
  var extracted = WIZ2_EXTRACTED || {};
  var payload = {};

  if (extracted.full_name) payload.full_name = extracted.full_name;
  if (extracted.date_of_birth) payload.date_of_birth = extracted.date_of_birth;
  if (extracted.gender) payload.gender = extracted.gender;
  if (extracted.address) payload.address = extracted.address;

  if (!Object.keys(payload).length) return;

  btn.disabled = true;
  btn.textContent = 'Applying...';
  try {
    await API('affiliate.api.portal_api.update_settings', payload);
    if (extracted.national_id) {
      var icEl = document.getElementById('wiz2-national-id');
      if (!icEl.value) {
        icEl.value = extracted.national_id;
        sanitizeNationalIdValue(icEl);
      }
    }
    btn.textContent = 'Applied to your details';
  } catch (e) {
    btn.textContent = 'Use these details';
    btn.disabled = false;
    showError('wiz2-error', e.message || 'Could not apply the detected details.');
  }
}

function showVerifyResult(verification) {
  var el = document.getElementById('wiz2-verify-result');
  var issues = verification.issues || [];
  var html = '<p style="margin:0 0 6px;font-weight:700;">We could not confirm some details against your ID</p>';
  issues.forEach(function(issue) {
    var line = '<p style="margin:2px 0;">' + escapeHtml(issue.field || 'Field') + ': on your ID "' +
      escapeHtml(issue.document_value || '-') + '", you entered "' + escapeHtml(issue.entered_value || '-') + '"';
    if (issue.note) line += ' - ' + escapeHtml(issue.note);
    html += line + '</p>';
  });
  html += '<p style="margin:6px 0 0;">Fix your details above and press Continue again, or continue anyway - our team does the final check.</p>';
  html += '<button type="button" class="btn btn-outline" onclick="sw(\'S-wizard-3\')" style="margin-top:10px;">Continue anyway</button>';
  el.innerHTML = html;
  el.style.display = 'block';
}

async function submitWizard2() {
  hideError('wiz2-error');
  var nationalId = document.getElementById('wiz2-national-id').value.trim();
  var fileInput = document.getElementById('wiz2-document');

  if (!nationalId || (!fileInput.files.length && !WIZ2_UPLOADED_URL)) {
    showError('wiz2-error', 'Please enter your ID number and upload a document.');
    return;
  }

  var btn = document.getElementById('wiz2-btn');
  btn.textContent = 'Checking...';
  btn.disabled = true;
  document.getElementById('wiz2-verify-result').style.display = 'none';

  try {
    var fileUrl = WIZ2_UPLOADED_URL || await uploadWizardDocument(fileInput.files[0]);
    var res = await API('affiliate.api.portal_api.submit_wizard_step2', {
      national_id: nationalId, document_id: fileUrl,
    });
    var verification = res && res.verification;
    if (verification && verification.status === 'Mismatch') {
      // Nasihat, bukan pagar: biar pengguna betulkan atau teruskan -
      // admin tetap buat keputusan akhir semasa approve.
      btn.textContent = 'Continue';
      btn.disabled = false;
      showVerifyResult(verification);
      return;
    }
    sw('S-wizard-3');
  } catch (e) {
    showError('wiz2-error', e.message || 'Could not save. Please try again.');
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
  var reg = getRegistrationPrefill();

  // Route dari state yang server render masa page load — dashboard API
  // hanya dipanggil bila server tahu ia akan lulus, jadi bukan-affiliate
  // tak lagi menghasilkan 403 dalam console.
  if (!reg.logged_in) {
    showLoginForSession();
    return;
  }

  if (reg.has_role && reg.has_profile) {
    try {
      var data = await API('affiliate.api.portal_api.get_dashboard_data', {});
      PORTAL_DATA = data;

      if (!data.wizard_complete) {
        sw('S-wizard-1');
        return;
      }

      renderDashboard();
      sw('S-dashboard');
    } catch (e) {
      // Access berubah antara render dengan call (jarang) — jangan
      // tunjuk sign-in form kepada sesiapa yang sudah berlogin.
      showNoAccess();
    }
    return;
  }

  if (reg.eligible) {
    showConfirmRegistration(false);
    return;
  }
  if (reg.active) {
    // Aktif tapi nama tak lengkap — skrin pengesahan versi boleh-edit.
    showConfirmRegistration(true);
    return;
  }
  showNoAccess();
}

function renderDashboard() {
  if (!PORTAL_DATA) return;
  var p = PORTAL_DATA.profile;

  mergeCurrencySymbols(PORTAL_DATA.currency_symbols);

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

  // Kadar komisyen semasa (override sendiri atau lalai laman - Selesai
  // diselesaikan di pelayan dengan peraturan yang sama seperti enjin
  // komisyen), di atas kad kod rujukan dan dalam tab Profil.
  var rateEl = document.getElementById('ap-code-rate');
  var profileRateEl = document.getElementById('ap-profile-commission-rate');
  if (PORTAL_DATA.commission_rate > 0) {
    rateEl.textContent = 'You earn ' + fmtRate(PORTAL_DATA.commission_rate) + ' commission on every sale';
    rateEl.style.display = 'block';
    profileRateEl.textContent = fmtRate(PORTAL_DATA.commission_rate) +
      (PORTAL_DATA.commission_rate_is_custom ? ' (your rate)' : ' (standard rate)');
  } else {
    rateEl.style.display = 'none';
    profileRateEl.textContent = '-';
  }

  // Summary cards: approx. total in the affiliate's chosen display
  // currency, with the exact per-currency figures as the sub line.
  var estCur = PORTAL_DATA.default_currency;
  renderMoneyCard('ap-total-sales', 'ap-total-sales-sub', PORTAL_DATA.total_sales_est, estCur, PORTAL_DATA.balances, 'total_sales');
  renderMoneyCard('ap-total-commission', 'ap-total-commission-sub', PORTAL_DATA.total_commission_est, estCur, PORTAL_DATA.balances, 'total_commission');
  renderMoneyCard('ap-available-balance', 'ap-available-balance-sub', PORTAL_DATA.available_balance_est, estCur, PORTAL_DATA.balances, 'available_balance');

  loadLeaderboard();
  loadCommissionStatus();
  loadPerformance();

  populateProfileForm(p);
  populateReferralCodeForm(p);
  populatePaymentMethodForm(p);
  populateCurrencyPreferenceForm(p);
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

function populateCurrencyPreferenceForm(p) {
  var select = document.getElementById('set-default-currency');
  if (!select) return;

  var options = (PORTAL_DATA.available_currencies || []).filter(function(code) {
    return code !== (p.default_currency || '');
  });
  // Keep the current choice first so it's visible even when the list is long.
  select.innerHTML = '<option value="">' + (p.default_currency || '-') + ' (current)</option>' +
    options.map(function(code) {
      return '<option value="' + code + '">' + code + (CURRENCY_SYMBOLS[code] ? ' (' + CURRENCY_SYMBOLS[code] + ')' : '') + '</option>';
    }).join('');
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
    // Kadar sebenar yang direkodkan pada komisyen itu; fallback kepada
    // kadar semasa dashboard untuk baris lama tanpa kadar tersimpan.
    var rate = c.commission_rate > 0 ? c.commission_rate : (PORTAL_DATA && PORTAL_DATA.commission_rate) || 0;
    var ratePart = rate > 0 ? ' &middot; ' + fmtRate(rate) + ' commission' : '';
    return '<div class="' + rowClass + '">' +
      '<div><p class="ap-row-title">' + c.sales_order + '</p>' +
      '<p class="ap-row-sub">' + date + ' &middot; ' + fmt(c.sales_order_amount, c.sales_order_currency) + ' sale' + ratePart + '</p></div>' +
      '<div style="display:flex;align-items:center;gap:12px;">' +
      '<span style="font-size:13px;font-weight:700;color:#111111;">' + fmt(c.commission_amount, c.currency) + '</span>' +
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

var CURRENT_PAYOUT_CURRENCY = '';

function renderPayoutsSummary(data) {
  mergeCurrencySymbols(data.currency_symbols);
  var estCur = data.default_currency;

  // Main lines: approx. totals in the affiliate's chosen currency;
  // sub lines: exact per-currency figures.
  renderMoneyCard('ap-payout-available', 'ap-payout-available-sub', data.available_balance_est, estCur, data.available_by_currency, 'amount');
  renderMoneyCard('ap-payout-total-paid', 'ap-payout-total-paid-sub', data.total_paid_out_est, estCur, data.total_paid_out_by_currency, 'amount');
  renderMoneyCard('ap-payout-pending', 'ap-payout-pending-sub', data.pending_payout_est, estCur, data.pending_payout_by_currency, 'amount');

  var btn = document.getElementById('ap-request-payout-btn');
  var hint = document.getElementById('ap-payout-request-hint');
  CURRENT_PAYOUT_CURRENCY = '';

  if (data.has_open_payout) {
    btn.disabled = true;
    hint.textContent = 'You already have a payout in progress.';
  } else if (!data.available_by_currency.length) {
    btn.disabled = true;
    hint.textContent = 'No approved commissions available to cash out yet.';
  } else if (!data.cashable_currencies.length) {
    btn.disabled = true;
    // Every currency is below the threshold on its own - a mixed total
    // is not cashable, so show exactly what each currency is short of.
    hint.textContent = data.available_by_currency.map(function(row) {
      var shortfall = data.minimum_cashout - row.amount;
      return row.currency + ': need ' + fmt(shortfall, row.currency) + ' more to reach the ' + fmt(data.minimum_cashout, row.currency) + ' minimum.';
    }).join(' ');
  } else {
    // Request the first cashable currency; once its payout completes,
    // the next one becomes requestable. One currency per payout.
    CURRENT_PAYOUT_CURRENCY = data.cashable_currencies[0];
    var ready = data.available_by_currency.filter(function(row) { return row.meets_minimum; });
    btn.disabled = false;
    var readyText = ready.map(function(row) { return fmt(row.amount, row.currency); }).join(', ');
    hint.textContent = readyText + ' is ready to request' + (ready.length > 1 ? ' (one currency at a time - ' + CURRENT_PAYOUT_CURRENCY + ' first).' : '.');
  }
}

async function apRequestPayout() {
  var btn = document.getElementById('ap-request-payout-btn');
  btn.disabled = true;
  btn.textContent = 'Requesting...';
  try {
    await API('affiliate.api.portal_api.request_payout', { currency: CURRENT_PAYOUT_CURRENCY || '' });
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
      '<span style="font-size:13px;font-weight:700;color:#111111;">' + fmt(p.amount, p.currency) + '</span>' +
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
  CURRENCY_SYMBOLS[data.currency] = data.symbol || data.currency;

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
      '<div class="ap-board-sales">' + fmt(r.total_sales, data.currency) + '</div>' +
      '</div>';
  }).join('');

  var alreadyShown = rows.some(function(r) { return r.is_you; });
  if (!alreadyShown && data.your_rank) {
    html += '<div class="ap-board-your-rank ap-board-row you">' +
      '<div class="ap-board-rank">' + data.your_rank + '</div>' +
      '<div class="ap-board-name">You<span class="you-tag">You</span></div>' +
      '<div class="ap-board-sales">' + fmt(data.your_total_sales, data.currency) + '</div>' +
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
  mergeCurrencySymbols(data.currency_symbols);
  var perCurrency = data.per_currency || [];

  var hasAny = perCurrency.some(function(row) {
    return (row.unpaid_amount || 0) > 0 || (row.paid_amount || 0) > 0;
  });
  if (!hasAny) {
    el.innerHTML = '<div class="ap-empty">' +
      '<p class="ap-empty-title">No commissions yet</p>' +
      '<p class="ap-empty-sub">Share your referral code to start earning.</p>' +
      '</div>';
    return;
  }

  var estCur = data.default_currency;
  var unpaidEst = data.unpaid_amount_est;
  var paidEst = data.paid_amount_est;
  var html = '';

  // The proportion bar only renders when a full estimate exists - with
  // a missing rate the ratio between exact currencies would be guesswork.
  if (unpaidEst !== null && unpaidEst !== undefined && paidEst !== null && paidEst !== undefined && (unpaidEst + paidEst) > 0) {
    var unpaidPct = (unpaidEst / (unpaidEst + paidEst)) * 100;
    var paidPct = 100 - unpaidPct;
    html +=
      '<div class="ap-status-bar">' +
        '<div class="ap-status-seg-unpaid" style="width:' + unpaidPct + '%"></div>' +
        '<div class="ap-status-seg-paid" style="width:' + paidPct + '%"></div>' +
      '</div>';
  }

  html += '<div class="ap-status-legend-row">' + perCurrency.map(function(row) {
    var parts = [];
    parts.push('<span class="ap-status-dot" style="background:#E8A33D"></span>' +
      '<span class="ap-status-label">Unpaid (' + row.currency + ')</span>' +
      '<p class="ap-status-value">' + fmt(row.unpaid_amount, row.currency) + '</p>' +
      '<p class="ap-status-count">' + row.unpaid_count + ' commission' + (row.unpaid_count === 1 ? '' : 's') + '</p>');
    parts.push('<span class="ap-status-dot" style="background:#E4DECF"></span>' +
      '<span class="ap-status-label">Paid (' + row.currency + ')</span>' +
      '<p class="ap-status-value">' + fmt(row.paid_amount, row.currency) + '</p>' +
      '<p class="ap-status-count">' + row.paid_count + ' commission' + (row.paid_count === 1 ? '' : 's') + '</p>');
    return '<div>' + parts.join('') + '</div>';
  }).join('');

  if (unpaidEst !== null && unpaidEst !== undefined && paidEst !== null && paidEst !== undefined) {
    html += '<div style="flex:1;min-width:140px;">' +
      '<span class="ap-status-label" style="color:#9B9B9E">Total (' + estCur + ', est.)</span>' +
      '<p class="ap-status-value">' + fmtEst(unpaidEst + paidEst, estCur) + '</p>' +
      '<p class="ap-status-count">approx. exchange rate</p>' +
      '</div>';
  }

  html += '</div>';
  el.innerHTML = html;
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

  var symbols = {};
  (c.per_currency || []).concat(p.per_currency || []).forEach(function(row) {
    symbols[row.currency] = row.symbol || row.currency;
  });
  mergeCurrencySymbols(symbols);

  function section(rows, s, isPending) {
    // Exact per-currency lines, plus the approx. total in the
    // affiliate's chosen display currency when a full rate set exists.
    var pendingClass = isPending ? ' pending' : '';
    var lines = (rows || []).map(function(row) {
      return '<div class="ap-perf-metric' + pendingClass + '">' +
        '<p class="ap-perf-metric-label">' + row.currency + '</p>' +
        '<p class="ap-perf-metric-value">' + fmt(row.total_sales, row.currency) +
        ' <span style="color:#9B9B9E;font-weight:400;">/</span> ' +
        '<span class="' + (isPending ? '' : 'success') + '">' + fmt(row.total_commission, row.currency) + '</span></p>' +
        '</div>';
    }).join('');

    var estLine = '';
    if (s.total_sales_est !== null && s.total_sales_est !== undefined) {
      estLine = '<div class="ap-perf-metric' + pendingClass + '">' +
        '<p class="ap-perf-metric-label">Total (' + s.estimate_currency + ', est.)</p>' +
        '<p class="ap-perf-metric-value">' + fmtEst(s.total_sales_est, s.estimate_currency) +
        ' <span style="color:#9B9B9E;font-weight:400;">/</span> ' +
        '<span class="' + (isPending ? '' : 'success') + '">' + fmtEst(s.total_commission_est, s.estimate_currency) + '</span></p>' +
        '</div>';
    }
    return lines + estLine;
  }

  el.innerHTML =
    '<p class="ap-perf-section-label">Confirmed</p>' +
    '<div class="ap-perf-grid" style="margin-bottom:16px;">' +
      '<div class="ap-perf-metric">' +
        '<p class="ap-perf-metric-label">Orders</p>' +
        '<p class="ap-perf-metric-value">' + (c.orders || 0) + '</p>' +
      '</div>' +
      section(c.per_currency, c, false) +
    '</div>' +
    '<p class="ap-perf-section-label">Pending review <span class="note">- not yet confirmed, may change</span></p>' +
    '<div class="ap-perf-grid">' +
      '<div class="ap-perf-metric pending">' +
        '<p class="ap-perf-metric-label">Orders</p>' +
        '<p class="ap-perf-metric-value">' + (p.orders || 0) + '</p>' +
      '</div>' +
      section(p.per_currency, p, true) +
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
    var payload = {
      full_name: document.getElementById('set-full-name').value,
      phone: getPhoneValue(itiSettings, document.getElementById('set-phone')),
      gender: document.getElementById('set-gender').value,
      date_of_birth: document.getElementById('set-dob').value,
      address: document.getElementById('set-address').value,
    };
    var currencySelect = document.getElementById('set-default-currency');
    if (currencySelect && currencySelect.value) {
      payload.default_currency = currencySelect.value;
    }
    await API('affiliate.api.portal_api.update_settings', payload);
    apShowSaved('profile-saved');
    await loadDashboard();
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

function buildProductUrl(route) {
  // Pautan affiliate menuju ke page detail trip (/<route>) — BUKAN terus ke
  // wizard booking. Dari page detail, customer pilih date + package, klik
  // [Book Now], dan trip_detail.js rambat ?sp= ke /booknow (via bnw_cart +
  // URL param) supaya kod affiliate tersimpan pada booking. Sebelum ni link
  // menuju ke /booking/?trip=<ID> (halaman legacy) yang bypass page detail.
  var code = (PORTAL_DATA && PORTAL_DATA.profile.referral_code) || '';
  if (!route) return '';
  var url = PRODUCTS_BASE_URL + '/' + route.replace(/^\/+/, '');
  if (code) url += (url.indexOf('?') > -1 ? '&' : '?') + 'sp=' + encodeURIComponent(code);
  return url;
}

function renderProducts(list) {
  var el = document.getElementById('ap-products-grid');
  var hasCode = !!(PORTAL_DATA && PORTAL_DATA.profile.referral_code);

  if (!list.length) {
    el.innerHTML = '<div class="ap-empty"><p class="ap-empty-title">No products available right now</p><p class="ap-empty-sub">Check back once a trip is open for booking.</p></div>';
    return;
  }

  el.innerHTML = list.map(function(p, idx) {
    var img = p.trip_image
      ? '<img src="' + p.trip_image + '" alt="">'
      : '<svg width="30" height="30" viewBox="0 0 24 24" fill="none"><path d="M3 8L12 3L21 8V16L12 21L3 16V8Z" stroke="#B0AC9F" stroke-width="1.6"/></svg>';
    var copyLabel = hasCode ? 'Copy link' : 'Code pending';

    return '<div class="ap-trip-group">' +
      '<div class="ap-trip-group-image">' + img + '</div>' +
      '<div class="ap-trip-group-body">' +
        '<p class="ap-trip-group-title">' + p.trip_name + '</p>' +
        '<button class="btn btn-outline ap-product-copy-btn" data-copy="' + idx + '"' + (hasCode ? '' : ' disabled') + '>' + copyLabel + '</button>' +
      '</div>' +
    '</div>';
  }).join('');

  el.querySelectorAll('[data-copy]').forEach(function(btn) {
    if (btn.disabled) return;
    btn.addEventListener('click', function() {
      var idx = btn.getAttribute('data-copy');
      navigator.clipboard.writeText(buildProductUrl(list[idx].route));
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

// Kadar komisyen lalai (disuntik server melalui pageData) pada subtitle
// skrin pendaftaran — prospekt nampak angka sebelum mendaftar.
(function() {
  var defaultRate = parseFloat(_pageData.default_commission_percent) || 0;
  if (defaultRate > 0) {
    var el = document.getElementById('reg-sub');
    if (el) el.textContent = 'Earn ' + fmtRate(defaultRate) + ' commission by referring customers';
  }
})();

loadDashboard();