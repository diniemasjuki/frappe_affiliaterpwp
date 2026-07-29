"""
affiliate/api/registration.py

Guest-facing self-registration for new affiliates. Creates the
frappe.User and Affiliate Profile as one logical unit, assigns the
Affiliate role, and logs the new user in immediately. Runs BEFORE a
session exists (allow_guest=True), so it cannot rely on any
session-based helper.

The new profile starts at status="Pending Verification" with no
referral_code - that only gets generated once an admin approves the
profile (see affiliate_profile.py's on_update), which normally happens
after the affiliate has also completed the mandatory profile wizard
(see wizard.py) with their IC/bank details.
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def register_affiliate(first_name: str, last_name: str, email: str, password: str) -> dict:
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


def _validate_registration_input(first_name: str, last_name: str, email: str, password: str):
	if not first_name or not last_name:
		frappe.throw(_("First name and last name are required."))

	if not email or "@" not in email:
		frappe.throw(_("Please enter a valid email address."))

	if not password or len(password) < 8:
		frappe.throw(_("Password must be at least 8 characters."))