# Copyright (c) 2026, Warga Prihatin and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import random_string

from affiliate.install import after_install
from affiliate.www.affiliate import get_registration_context


class TestRegistrationContext(UnitTestCase):
	"""Tests for the portal page's registration-prefill/eligibility context.

	get_registration_context() decides whether a logged-in visitor goes
	straight to the read-only "confirm your details" screen (skipping the
	signup form) or falls back to the old login/signup flow. The rules
	under test: enabled account, no Affiliate role, no Affiliate Profile,
	and a complete (first + last) name — taken from the linked Contact
	when one exists, falling back per field to the User doc.

	Base class is UnitTestCase (not IntegrationTestCase) deliberately —
	same reason as test_affiliate_profile.py: IntegrationTestCase's test-
	record dependency bootstrap is broken on this site. Each test builds
	and cleans up its own User/Contact/Profile.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	def setUp(self):
		after_install()  # idempotent - guarantees the Affiliate role exists
		self._original_user = frappe.session.user
		self._cleanup = []

	def tearDown(self):
		for doctype, name in reversed(self._cleanup):
			if doctype == "User":
				# Frappe core auto-creates a mirror Contact for every new
				# User (user.py's create_contact, run inline in tests) -
				# not tracked in _cleanup, so sweep it up by link first:
				# deleting the User only nulls the link, leaving the row.
				for c in frappe.get_all("Contact", filters={"user": name}, pluck="name"):
					frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.set_user(self._original_user)
		frappe.clear_cache()

	def _unique_email(self) -> str:
		return f"test_reg_ctx_{random_string(8).lower()}@example.test"

	def _new_user(self, first_name: str, last_name: str = "", enabled: int = 1) -> str:
		email = self._unique_email()
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"enabled": enabled,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("User", email))
		return email

	def _new_contact(self, user: str, first_name: str, last_name: str = "", phone: str = "") -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": first_name,
				"last_name": last_name,
				"user": user,
			}
		)
		# Telefon Contact hidup dalam child table Contact Phone — medan
		# phone level dokumen dibuang masa validate, jadi mesti melalui
		# add_phone().
		if phone:
			doc.add_phone(phone, is_primary_phone=True)
		name = doc.insert(ignore_permissions=True).name
		self._cleanup.append(("Contact", name))
		return name

	def _as(self, user: str) -> dict:
		frappe.set_user(user)
		frappe.clear_cache()
		return get_registration_context()

	def test_guest_is_not_eligible(self):
		ctx = self._as("Guest")
		self.assertFalse(ctx["logged_in"])
		self.assertFalse(ctx["eligible"])

	def test_active_user_with_full_name_is_eligible(self):
		email = self._new_user("Ahmad", "Bin Ali")
		ctx = self._as(email)
		self.assertTrue(ctx["logged_in"])
		self.assertTrue(ctx["active"])
		self.assertFalse(ctx["has_role"])
		self.assertFalse(ctx["has_profile"])
		self.assertTrue(ctx["eligible"])
		self.assertEqual(ctx["first_name"], "Ahmad")
		self.assertEqual(ctx["last_name"], "Bin Ali")
		self.assertEqual(ctx["email"], email)

	def test_contact_names_win_over_user_names(self):
		email = self._new_user("Ahmad", "Bin Ali")
		self._new_contact(email, "Iskandar", "Bin Omar")
		ctx = self._as(email)
		self.assertTrue(ctx["eligible"])
		self.assertEqual(ctx["first_name"], "Iskandar")
		self.assertEqual(ctx["last_name"], "Bin Omar")

	def test_contact_falls_back_per_field_to_user(self):
		email = self._new_user("Ahmad", "Bin Ali")
		self._new_contact(email, "Iskandar")
		ctx = self._as(email)
		self.assertTrue(ctx["eligible"])
		self.assertEqual(ctx["first_name"], "Iskandar")
		self.assertEqual(ctx["last_name"], "Bin Ali")

	def test_phone_falls_back_to_customer_chain(self):
		# User hanya ada Contact cermin auto-created (tanpa telefon) —
		# telefon diambil dari sebelah Customer melalui rantau yang sama
		# dengan portal booking: Contact Email → Dynamic Link → Customer.
		group = frappe.db.get_single_value("Selling Settings", "customer_group")
		if not group or frappe.db.get_value("Customer Group", group, "is_group"):
			groups = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", limit=1)
			if not groups:
				self.skipTest("No non-group Customer Group configured on this site")
			group = groups[0]
		territory = frappe.db.get_single_value("Selling Settings", "territory")
		if not territory or frappe.db.get_value("Territory", territory, "is_group"):
			territories = frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", limit=1)
			if not territories:
				self.skipTest("No non-group Territory configured on this site")
			territory = territories[0]

		email = self._new_user("Ahmad", "Bin Ali")
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Zack Trading {random_string(6)}",
				"customer_type": "Company",
				"customer_group": group,
				"territory": territory,
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("Customer", customer.name))
		booking_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Booking",
				"last_name": "Guest",
			}
		)
		booking_contact.add_phone("+60129990001", is_primary_phone=True)
		booking_contact.append("email_ids", {"email_id": email, "is_primary": 1})
		booking_contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		booking_contact.insert(ignore_permissions=True)
		self._cleanup.append(("Contact", booking_contact.name))

		ctx = self._as(email)
		# Contact booking auto-link ke user (primary email sama dengan
		# akaun — Contact.set_user) dan sebagai Contact terbaru, nama
		# DAN telefon datang daripadanya.
		self.assertEqual(ctx["full_name"], "Booking Guest")
		self.assertEqual(ctx["phone"], "+60129990001")

	def test_full_name_and_phone_prefill_from_linked_contact(self):
		email = self._new_user("Ahmad", "Bin Ali")
		self._new_contact(email, "Iskandar", "Bin Omar", phone="+60123456789")
		ctx = self._as(email)
		self.assertEqual(ctx["full_name"], "Iskandar Bin Omar")
		self.assertEqual(ctx["phone"], "+60123456789")

	def test_missing_last_name_is_not_eligible(self):
		# register_affiliate() rejects a blank last name, so a confirm
		# screen that can't be confirmed must not be offered.
		email = self._new_user("Ahmad")
		self.assertFalse(self._as(email)["eligible"])

	def test_disabled_user_is_not_eligible(self):
		email = self._new_user("Ahmad", "Bin Ali")
		frappe.db.set_value("User", email, "enabled", 0)
		self.assertFalse(self._as(email)["eligible"])

	def test_user_with_affiliate_role_is_not_eligible(self):
		email = self._new_user("Ahmad", "Bin Ali")
		user = frappe.get_doc("User", email)
		user.append("roles", {"role": "Affiliate"})
		user.save(ignore_permissions=True)
		frappe.clear_cache()
		ctx = self._as(email)
		self.assertTrue(ctx["has_role"])
		self.assertFalse(ctx["eligible"])

	def test_user_with_profile_is_not_eligible(self):
		email = self._new_user("Ahmad", "Bin Ali")
		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"user": email,
				"email_id": email,
				"full_name": "Ahmad Bin Ali",
				"status": "Pending Verification",
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("Affiliate Profile", profile.name))
		ctx = self._as(email)
		self.assertTrue(ctx["has_profile"])
		self.assertFalse(ctx["eligible"])


class TestPersonalInfoNameUppercase(UnitTestCase):
	"""The personal-info forms force full_name to uppercase: the inputs
	transform as the user types (client side) and the two save endpoints
	normalize server side, so whatever slips past the browser (API call,
	autofill, old client) still lands uniformly uppercase in the profile.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	def setUp(self):
		self._original_user = frappe.session.user
		self._cleanup = []

	def tearDown(self):
		for doctype, name in reversed(self._cleanup):
			if doctype == "User":
				for c in frappe.get_all("Contact", filters={"user": name}, pluck="name"):
					frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.set_user(self._original_user)
		frappe.clear_cache()

	def _affiliate_session(self) -> str:
		email = f"test_upper_{random_string(8).lower()}@example.test"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Ahmad",
				"last_name": "Bin Ali",
				"enabled": 1,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("User", email))
		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"user": email,
				"email_id": email,
				"full_name": "Ahmad Bin Ali",
				"status": "Pending Verification",
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("Affiliate Profile", profile.name))
		frappe.set_user(email)
		return profile.name

	def test_wizard_step1_saves_name_uppercase(self):
		from affiliate.api.portal_api import submit_wizard_step1

		profile_name = self._affiliate_session()
		submit_wizard_step1(
			full_name="ahmad bin ali",
			phone="+60123456789",
			gender="Male",
			date_of_birth="1990-01-01",
			address="Jalan Test",
		)
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", profile_name, "full_name"),
			"AHMAD BIN ALI",
		)

	def test_update_settings_saves_name_uppercase(self):
		from affiliate.api.portal_api import update_settings

		profile_name = self._affiliate_session()
		update_settings(full_name="ahmad bin ali")
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", profile_name, "full_name"),
			"AHMAD BIN ALI",
		)
