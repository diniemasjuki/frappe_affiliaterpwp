# affiliate/www/affiliate.py
#
# Companion Python module for the affiliate portal page (affiliate.html).
# Frappe's www renderer loads this automatically and calls get_context(),
# which exposes csrf_token to the Jinja template — required for every POST
# the portal makes (get_google_login_url, login, register, etc.).
#
# Also exposes the session state to the page's pageData JSON so the JS can
# route on load WITHOUT probing the dashboard API — the old probe produced
# a guaranteed 403 in the browser console for every non-affiliate visitor.
# A logged-in user is never landed on a sign-in screen: eligible users get
# the read-only "confirm your details" screen (skipping the signup form),
# users with an incomplete name get the same screen with editable name
# fields, and everything else gets a neutral "no affiliate access" notice.
# Registration still only ever happens through an explicit submit —
# visiting the page while logged in never enrolls anyone.
import os

import frappe

no_cache = 1
allow_guest = True


def _phone_from_contact(contact_name: str) -> str:
	"""Primary phone/mobile bagi satu Contact. Sumber canonical ialah
	child table Contact Phone (row is_primary_phone dulu, kemudian
	is_primary_mobile_no) — medan phone/mobile_no level dokumen hanyalah
	sinkronan daripada child table, jadi dibaca sebagai fallback sahaja
	untuk data lama."""
	if not contact_name:
		return ""
	rows = frappe.get_all(
		"Contact Phone",
		filters={"parent": contact_name, "parenttype": "Contact"},
		fields=["phone"],
		order_by="is_primary_phone desc, is_primary_mobile_no desc",
		limit=1,
	)
	phone = (rows[0].phone if rows else "") or ""
	if not phone:
		row = frappe.db.get_value("Contact", contact_name, ["phone", "mobile_no"], as_dict=True)
		phone = (row and (row.phone or row.mobile_no)) or ""
	return (phone or "").strip()


def _phone_from_customer(user: str) -> str:
	"""Telefon dari sebelah Customer, dicapai melalui rantau yang sama
	dengan portal booking resolve customer (alamat email akaun →
	Contact Email → Dynamic Link → Customer): medan mobile_no Customer
	dulu, kemudian telefon Contact utama Customer. Kosong kalau tiada
	Customer / tiada telefon."""
	customer = frappe.db.sql(
		"""
		SELECT dl.link_name
		FROM `tabContact Email` ce
		JOIN `tabContact` c ON c.name = ce.parent
		JOIN `tabDynamic Link` dl ON dl.parent = c.name
		WHERE ce.email_id = %s AND dl.link_doctype = 'Customer'
		LIMIT 1
		""",
		(user,),
	)
	if not customer:
		return ""
	customer_name = customer[0][0]
	phone = frappe.db.get_value("Customer", customer_name, "mobile_no") or ""
	if not phone:
		primary_contact = frappe.db.get_value(
			"Customer", customer_name, "customer_primary_contact"
		)
		phone = _phone_from_contact(primary_contact)
	if not phone:
		# Contact yang dipautkan ke Customer melalui Dynamic Link — inilah
		# bentuk data yang ditinggalkan checkout portal booking.
		for linked in frappe.get_all(
			"Dynamic Link",
			filters={
				"parenttype": "Contact",
				"link_doctype": "Customer",
				"link_name": customer_name,
			},
			pluck="parent",
		):
			phone = _phone_from_contact(linked)
			if phone:
				break
	return (phone or "").strip()


def get_registration_context() -> dict:
	"""Portal session state for pageData, consumed by the portal JS.

	- logged_in / active: whether there is a real session and the User
	  account behind it is still enabled (a stale session can outlive
	  the account being disabled).
	- has_role / has_profile: dashboard access, mirroring
	  get_dashboard_data's own gate exactly — the JS only calls the API
	  when both are true, so the call never 403s in practice.
	- eligible: an active user with neither role nor profile AND a
	  complete first + last name on file — they get the read-only
	  confirm screen, skipping the signup form. An incomplete name keeps
	  them out of eligible (register_affiliate() rejects blank names,
	  and a confirm screen that can't be confirmed is a dead end) but
	  they still get the confirm screen with editable name fields.
	- first_name / last_name / full_name / email: the details shown for
	  confirmation and the personal-info form prefill. Names come from
	  the user's linked Contact, falling back per field to the User doc;
	  the email is always the account email itself, since
	  register_affiliate() only ever attaches a profile to the current
	  session account.
	- phone: prefill for the personal-info form, extracted from (in
	  order) the linked Contact's phone/mobile fields, then the Customer
	  reached the same way the booking portal reaches it (account email
	  → Contact Email → Dynamic Link → Customer, then the Customer's own
	  mobile_no or its primary contact's phone).
	"""
	state = {
		"logged_in": False,
		"active": False,
		"has_role": False,
		"has_profile": False,
		"eligible": False,
		"first_name": "",
		"last_name": "",
		"full_name": "",
		"phone": "",
		"email": "",
	}
	if frappe.session.user == "Guest":
		return state

	state["logged_in"] = True
	state["email"] = frappe.session.user

	user = frappe.db.get_value(
		"User",
		frappe.session.user,
		["enabled", "first_name", "last_name"],
		as_dict=True,
	)
	if not user:
		return state
	state["active"] = bool(user.enabled)

	state["has_role"] = "Affiliate" in frappe.get_roles(frappe.session.user)
	state["has_profile"] = bool(
		frappe.db.exists("Affiliate Profile", {"user": frappe.session.user})
	)

	# A user can carry several linked Contacts: Frappe core auto-creates a
	# mirror Contact at user creation (names copied from the User doc and
	# re-synced on every user save) alongside any business-maintained ones.
	# Names come from the newest linked Contact — a business Contact
	# created after the mirror wins; a fresh user with only the mirror
	# simply gets the User doc's own names.
	linked_contacts = frappe.get_all(
		"Contact",
		filters={"user": frappe.session.user},
		fields=["name", "first_name", "last_name"],
		order_by="creation desc",
	)
	contact = linked_contacts[0] if linked_contacts else None

	# Fallback adalah per medan — Contact mungkin ada nama depan sahaja.
	first_name = ((contact and contact.first_name) or user.first_name or "").strip()
	last_name = ((contact and contact.last_name) or user.last_name or "").strip()
	state["first_name"] = first_name
	state["last_name"] = last_name
	state["full_name"] = f"{first_name} {last_name}".strip()

	# Telefon: cari contact pautan user yang BETUL-BETUL ada telefon
	# (termuda dulu) — contact cermin tak pernah ada telefon, dan
	# background job boleh menjadikannya termuda, jadi jangan bergantung
	# pada kontak terbaru semata-mata. Kalau semua kosong, barulah
	# sebelah Customer (rantau email → Dynamic Link, sama seperti portal
	# booking) dicuba.
	phone = ""
	for linked in linked_contacts:
		phone = _phone_from_contact(linked.name)
		if phone:
			break
	if not phone:
		phone = _phone_from_customer(frappe.session.user)
	state["phone"] = phone

	state["eligible"] = bool(
		state["active"]
		and not state["has_role"]
		and not state["has_profile"]
		and first_name
		and last_name
	)
	return state


def get_context(context):
	context.no_cache = 1

	# CSRF token diperlukan untuk SEMUA pengguna (termasuk Guest) kerana
	# portal membuat POST API calls yang Frappe sahkan CSRF walaupun untuk
	# endpoint allow_guest. Token ini rawak dan terikat ke sesi, bukan
	# kredensial — selamat diberi kepada guest sama seperti cookie csrftoken
	# yang Frappe tetapkan.
	context.csrf_token = frappe.sessions.get_csrf_token()

	context.session_user = frappe.session.user
	if frappe.session.user != "Guest":
		context.user_fullname = frappe.utils.get_fullname(frappe.session.user)
	else:
		context.user_fullname = ""

	context.registration = get_registration_context()

	# Kadar komisyen lalai (Affiliate Settings) untuk subtitle skrin
	# pendaftaran — nombor pemasaran umum, selak diberi kepada Guest.
	context.default_commission_percent = (
		frappe.get_cached_value("Affiliate Settings", None, "default_commission_percent") or 0
	)

	# Cache-buster untuk portal JS: fail ini dirujuk terus (luar bundler
	# Frappe), jadi tanpa ?v= browser boleh pegang salinan lama lama
	# selepas deploy. Mtime berubah pada setiap edit — cukup untuk bust.
	context.portal_js_ver = int(
		os.path.getmtime(
			os.path.join(
				os.path.dirname(__file__), "..", "public", "js", "affiliate-portal.js"
			)
		)
	)
