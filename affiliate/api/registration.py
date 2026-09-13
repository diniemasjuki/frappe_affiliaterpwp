"""
affiliate/api/registration.py

Self-registration for the affiliate program, called from the portal
signup form. Creates the frappe.User and Affiliate Profile as one
logical unit, assigns the Affiliate role, and logs the new user in
immediately.

Two entry paths share this endpoint:

- Guest (no session): the classic signup - a new User is created from
  the submitted name/email/password, tagged with the Affiliate role,
  and logged in.

- Already-authenticated session (e.g. an existing customer): no new
  User is ever created. The Affiliate Profile is attached to the
  CURRENT session account (the submitted email, if any, must match
  it), and only the role + profile are added - the rest of the
  signup form (password) is hidden in the UI for this path. Submitting
  the signup form is the explicit act of becoming an affiliate; merely
  visiting the portal while logged in never enrolls anyone.

The new profile starts at status="Pending Verification" with no
referral_code - that only gets generated once an admin approves the
profile (see affiliate_profile.py's on_update), which normally happens
after the affiliate has also completed the mandatory profile wizard
(see wizard.py) with their IC/bank details.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def register_affiliate(
	first_name: str = "", last_name: str = "", email: str = "", password: str = ""
) -> dict:
	if frappe.session.user != "Guest":
		return _register_current_user(first_name, last_name, email)

	_validate_registration_input(first_name, last_name, email, password)

	if frappe.db.exists("User", email):
		frappe.throw(_("An account with this email already exists. Please sign in instead."))

	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"send_welcome_email": 0,
			"new_password": password,
		}
	)
	user.insert(ignore_permissions=True)

	try:
		if "Affiliate" not in {r.role for r in user.roles}:
			user.append("roles", {"role": "Affiliate"})
			user.save(ignore_permissions=True)

		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"user": user.name,
				"email_id": email,
				"full_name": f"{first_name} {last_name}",
				"status": "Pending Verification",
			}
		)
		profile.insert(ignore_permissions=True)
	except Exception:
		# Something after the User was created failed (role assignment,
		# Profile validation, etc). Don't leave a half-registered User
		# behind with no matching Affiliate Profile - clean it up so the
		# person can simply try registering again with the same email.
		frappe.delete_doc("User", user.name, ignore_permissions=True, force=True)
		raise

	frappe.local.login_manager.login_as(user.name)

	return {"success": True, "profile": profile.name}


def _register_current_user(first_name: str, last_name: str, email: str) -> dict:
	"""Signup path for an already-authenticated visitor: attach an
	Affiliate Profile (and the Affiliate role) to the CURRENT session
	account. Never creates a User - the account already exists, so the
	submitted email (when the form still sends one) must be the account
	's own.
	"""
	user_doc = frappe.get_doc("User", frappe.session.user)

	if email and email.strip().lower() != frappe.session.user.lower():
		frappe.throw(
			_("You are signed in as {0}. Please use that email, or sign out to register a different account.").format(
				frappe.session.user
			)
		)

	# The form pre-fills the account's own names; fall back to them if
	# the submitted values are blank (e.g. a single-word display name).
	first_name = (first_name or "").strip() or (user_doc.first_name or "").strip()
	last_name = (last_name or "").strip() or (user_doc.last_name or "").strip()
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))

	if "Affiliate" not in {r.role for r in user_doc.roles}:
		user_doc.append("roles", {"role": "Affiliate"})
		user_doc.save(ignore_permissions=True)

	profile_name = frappe.db.get_value(
		"Affiliate Profile", {"user": frappe.session.user}, "name"
	)
	if profile_name:
		# Already registered (e.g. role was revoked manually) - just
		# make sure the role is back and return the existing profile.
		return {"success": True, "profile": profile_name, "existing_profile": True}

	profile = frappe.get_doc(
		{
			"doctype": "Affiliate Profile",
			"user": frappe.session.user,
			"email_id": frappe.session.user,
			"full_name": f"{first_name} {last_name}",
			"status": "Pending Verification",
		}
	)
	profile.insert(ignore_permissions=True)

	return {"success": True, "profile": profile.name}


def _validate_registration_input(first_name: str, last_name: str, email: str, password: str):
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))

	if not email or "@" not in email:
		frappe.throw(_("Please enter a valid email address."))

	if not password or len(password) < 8:
		frappe.throw(_("Password must be at least 8 characters."))