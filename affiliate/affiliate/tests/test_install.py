# Copyright (c) 2026, Warga Prihatin and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase

from affiliate.install import AFFILIATE_ROLE, after_install


class TestInstall(UnitTestCase):
	"""Tests for the affiliate app's after_install hook.

	The hook provisions the "Affiliate" Role that both registration flows
	(affiliate.api.registration.register_affiliate and affiliate.api.
	portal_api._create_profile_for_current_user) assign to every new
	affiliate user. Before the hook existed, the role was referenced by
	name but never created anywhere - so the very first self-registration
	on a fresh install threw "Could not find Row #1: Role: Affiliate"
	inside User.save() and rolled back, blocking signups entirely until
	an admin manually created the role.

	These tests are deliberately non-destructive: they never delete the
	role (doing so mid-test would break registration on the live site
	if the test were interrupted). They assert the hook runs without
	error and that the role is present afterwards, and that calling it
	repeatedly is a safe no-op (idempotent) rather than raising on the
	duplicate or leaving extra rows behind.
	"""

	def test_after_install_ensures_role_present(self):
		after_install()
		self.assertTrue(frappe.db.exists("Role", AFFILIATE_ROLE))

	def test_after_install_is_idempotent(self):
		# The role already exists (created by install / the test above).
		# Calling the hook again must be a no-op, not raise.
		after_install()
		after_install()
		self.assertTrue(frappe.db.exists("Role", AFFILIATE_ROLE))
