# affiliate/install.py
"""
App install hooks for the Affiliate app.

`after_install` provisions the "Affiliate" Role that the self-service
registration flow (affiliate.api.registration.register_affiliate) and the
portal's profile-creation path (affiliate.api.portal_api.
_create_profile_for_current_user) both assign to every new affiliate user.

The role is referenced by name in both code paths but is not otherwise
created anywhere — no fixtures, no role_permissions entry, and the Role
table ships no such row. Without this hook, the very first registration on a
freshly installed site throws "Could not find Row #1: Role: Affiliate"
inside User.save() and rolls the whole registration back (the except in
register_affiliate deletes the half-created User), so no affiliate can ever
self-register until an admin manually creates the role. Provisioning it here
on install removes that footgun.

desk_access is deliberately 0: affiliates interact exclusively through the
/affiliate web portal, never the Desk. API methods they call are gated by
@frappe.whitelist() (logged-in session), not desk_access, so this doesn't
limit the portal — it only keeps the Desk UI out of their accounts.

Idempotent: re-running on a site that already has the role (e.g. invoked
manually via `bench execute affiliate.install.after_install` on an
already-installed site) is a no-op and won't clobber an admin's edits to
the role.
"""
import frappe

AFFILIATE_ROLE = "Affiliate"


def after_install(app=None):
	if frappe.db.exists("Role", AFFILIATE_ROLE):
		return

	role = frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": AFFILIATE_ROLE,
			"enabled": 1,
			"desk_access": 0,
		}
	)
	role.insert(ignore_permissions=True)
