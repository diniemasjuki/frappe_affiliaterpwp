# Copyright (c) 2026, Warga Prihatin and Contributors
# See license.txt

import frappe
from frappe import _
from frappe.tests import UnitTestCase
from frappe.utils import random_string

from affiliate.affiliate.doctype.affiliate_settings.affiliate_settings import (
	REFERRAL_CODE_MAX_LENGTH,
)
from affiliate.api.portal_api import (
	_validate_custom_referral_code,
	check_referral_code_availability,
	set_custom_referral_code,
	submit_wizard_step4,
	upload_affiliate_document,
)


def _unique_email() -> str:
	return f"test_aff_{random_string(8).lower()}@example.test"


class TestAffiliateProfile(UnitTestCase):
	"""Tests for the affiliate referral-code autogeneration path.

	The hard rule these tests enforce: an auto-generated referral code
	must NEVER exceed the 8-character referral_code field length, no
	matter how Affiliate Settings is (mis)configured — the validate()
	guard on settings stops bad configs at save time, and the generator
	clamps defensively as a last resort.

	Base class is UnitTestCase (not IntegrationTestCase) deliberately:
	IntegrationTestCase auto-loads test-record dependencies for every
	link field, which for Affiliate Profile recurses into ERPNext's
	Sales Partner test module — and importing ERPNext's test utils runs
	its module-level BootStrapTestData(), which crashes on this site's
	already-existing "Standard Buying" Price List. None of these tests
	need those fixtures (each builds and cleans up its own data), so
	UnitTestCase avoids the broken bootstrap entirely.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
	IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]

	def setUp(self):
		self._original_settings = frappe.db.get_value(
			"Affiliate Settings",
			"Affiliate Settings",
			["referral_code_prefix", "referral_code_name_length"],
			as_dict=True,
		)
		# Reset to the known-good default config before each test so a
		# bad value a previous test wrote straight to the DB can't leak
		# into the next one.
		frappe.db.set_value(
			"Affiliate Settings",
			"Affiliate Settings",
			{"referral_code_prefix": "RC", "referral_code_name_length": 6},
		)
		frappe.clear_cache()
		self._cleanup = []

	def tearDown(self):
		for name in self._cleanup:
			frappe.delete_doc("Affiliate Profile", name, force=True, ignore_permissions=True)
		# Restore the real settings last so a test that wrote a bad value
		# directly to the DB can't leave it behind for the live site.
		frappe.db.set_value(
			"Affiliate Settings",
			"Affiliate Settings",
			{
				"referral_code_prefix": self._original_settings.referral_code_prefix,
				"referral_code_name_length": self._original_settings.referral_code_name_length,
			},
		)
		frappe.clear_cache()

	# --- generator: length invariant under every config ---

	def _new_profile(self, full_name: str):
		doc = frappe.new_doc("Affiliate Profile")
		doc.full_name = full_name
		return doc

	def test_generate_default_produces_exactly_8_chars(self):
		code = self._new_profile("Ahmad Bin Ali")._generate_unique_referral_code()
		self.assertEqual(len(code), 8)
		self.assertTrue(code.startswith("RC"))

	def test_generate_clamps_oversize_settings_via_db(self):
		# Simulate a bad config that was written straight to the DB
		# (bypassing settings.validate()): prefix "RC" (2) + name_length 8
		# would naively produce a 10-char code. The generator must clamp
		# it to 8 regardless, because the value is written via db_set()
		# which skips field validation.
		frappe.db.set_value(
			"Affiliate Settings", "Affiliate Settings", "referral_code_name_length", 8
		)
		frappe.clear_cache()
		code = self._new_profile("Ahmad Bin Ali")._generate_unique_referral_code()
		self.assertLessEqual(len(code), REFERRAL_CODE_MAX_LENGTH)

	def test_generate_clamps_very_long_prefix_via_db(self):
		frappe.db.set_value(
			"Affiliate Settings",
			"Affiliate Settings",
			{"referral_code_prefix": "AFFILIATE", "referral_code_name_length": 4},
		)
		frappe.clear_cache()
		code = self._new_profile("Ahmad")._generate_unique_referral_code()
		self.assertLessEqual(len(code), REFERRAL_CODE_MAX_LENGTH)

	def test_generate_pads_short_name_to_8(self):
		# "Abu" -> base letters "ABU" right-padded with 0 to name_length 6
		# -> "ABU000" -> "RCABU000" (exactly 8).
		code = self._new_profile("Abu")._generate_unique_referral_code()
		self.assertEqual(len(code), 8)
		self.assertEqual(code, "RCABU000")

	def test_generate_handles_empty_name(self):
		code = self._new_profile("")._generate_unique_referral_code()
		self.assertEqual(len(code), 8)
		self.assertEqual(code, "RC000000")

	def test_generate_strips_non_alpha_from_name(self):
		# Digits, spaces and punctuation in the name must not leak into
		# the code (only A-Z is kept before slicing/padding).
		code = self._new_profile("Ahmad 123 Ali!")._generate_unique_referral_code()
		self.assertEqual(len(code), 8)
		self.assertNotIn(" ", code)
		self.assertNotIn("1", code)

	# --- generator: uniqueness ---

	def test_generate_avoids_collision_with_sales_partner(self):
		# Pre-create a Sales Partner holding the exact base code the
		# generator would otherwise produce for this name, so the first
		# candidate collides and the generator must move to a suffix.
		base_code = "RCAHMADB"  # "Ahmad Bin Ali" -> AHMADB -> RCAHMADB
		sp_name = f"_test_collision_{random_string(6).lower()}"
		territory = (
			frappe.db.exists("Territory", "All Territories")
			or frappe.db.get_value("Territory", {}, "name")
		)
		sp = frappe.get_doc(
			{
				"doctype": "Sales Partner",
				"partner_name": sp_name,
				"commission_rate": 0,
				"territory": territory,
				"referral_code": base_code,
			}
		)
		sp.insert(ignore_permissions=True)
		try:
			code = self._new_profile("Ahmad Bin Ali")._generate_unique_referral_code()
			self.assertEqual(len(code), 8)
			self.assertNotEqual(code, base_code)
		finally:
			frappe.delete_doc("Sales Partner", sp.name, force=True, ignore_permissions=True)

	# --- custom-code validation stays consistent with auto-gen ---

	def test_custom_validation_requires_exactly_8(self):
		with self.assertRaises(frappe.ValidationError):
			_validate_custom_referral_code("RC123")  # too short
		with self.assertRaises(frappe.ValidationError):
			_validate_custom_referral_code("RCAHMADALI")  # too long

	def test_custom_validation_rejects_non_alnum(self):
		with self.assertRaises(frappe.ValidationError):
			_validate_custom_referral_code("RC-HMADB")

	def test_custom_validation_accepts_8_alnum(self):
		# Must not raise.
		_validate_custom_referral_code("RCAHMADB")

	# --- end-to-end: the verification flow that actually assigns a code ---

	def test_verification_flow_generates_8_char_code(self):
		# The real path: a Pending profile is flipped to Verified, which
		# fires on_update -> _generate_unique_referral_code (via db_set)
		# and creates the linked Sales Partner. Assert the assigned code
		# is exactly 8 chars and the Sales Partner exists afterwards.
		email = _unique_email()
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Ahmad",
				"last_name": "Bin Ali",
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)
		self._cleanup.append(email)  # not used directly but keeps a record

		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"user": email,
				"email_id": email,
				"full_name": "Ahmad Bin Ali",
				"status": "Pending Verification",
			}
		)
		profile.insert(ignore_permissions=True)
		profile_name = profile.name

		try:
			profile.status = "Verified"
			profile.save(ignore_permissions=True)
			profile.reload()

			self.assertEqual(len(profile.referral_code), 8)
			self.assertTrue(profile.sales_partner)
			# The code is mirrored onto the linked Sales Partner too.
			sp_code = frappe.db.get_value(
				"Sales Partner", profile.sales_partner, "referral_code"
			)
			self.assertEqual(sp_code, profile.referral_code)
		finally:
			if profile.sales_partner:
				frappe.delete_doc(
					"Sales Partner", profile.sales_partner, force=True, ignore_permissions=True
				)
			frappe.delete_doc("Affiliate Profile", profile_name, force=True, ignore_permissions=True)
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)


class TestReferralCodePortalAPI(UnitTestCase):
	"""Black-box tests of the affiliate-portal endpoints that touch a
	referral code — the same calls affiliate-portal.js makes. These
	verify the BACKEND half of the UI/backend consistency contract: the
	portal JS guards the 8-char rule client-side, but each of these
	whitelisted methods must also enforce it server-side, so a request
	that bypasses the JS (curl, a script, a future client) can never
	persist an over-length or under-length code either.

	(There is no browser backend available in this environment to drive
	the actual portal page, so these cover the endpoint behaviour the
	page would exercise instead of a GUI walkthrough.)
	"""

	def setUp(self):
		self.email = _unique_email()
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": self.email,
				"first_name": "Test",
				"last_name": "Affiliate",
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)
		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"user": self.email,
				"email_id": self.email,
				"full_name": "Test Affiliate",
				"status": "Pending Verification",
			}
		)
		profile.insert(ignore_permissions=True)
		self.profile_name = profile.name
		self._prev_user = frappe.session.user
		# Drive the endpoints as this affiliate, exactly as the portal's
		# own fetch() calls would (they rely on the session user).
		frappe.set_user(self.email)

	def tearDown(self):
		frappe.set_user(self._prev_user)
		frappe.delete_doc("Affiliate Profile", self.profile_name, force=True, ignore_permissions=True)
		frappe.delete_doc("User", self.email, force=True, ignore_permissions=True)

	def test_check_availability_free_8_char_code(self):
		r = check_referral_code_availability("RCFREE01", exclude_self=False)
		self.assertTrue(r["available"])

	def test_check_availability_rejects_short_code(self):
		# The availability indicator must never say "Available" for a
		# code that fails the 8-char rule.
		r = check_referral_code_availability("RC123", exclude_self=False)
		self.assertFalse(r["available"])
		self.assertIn("8 characters", r["reason"])

	def test_check_availability_rejects_long_code(self):
		r = check_referral_code_availability("RCAHMADALI", exclude_self=False)
		self.assertFalse(r["available"])

	def test_check_availability_taken_by_existing_profile(self):
		frappe.db.set_value("Affiliate Profile", self.profile_name, "referral_code", "RCTAKEN1")
		# exclude_self=False so this profile itself counts as a clash.
		r = check_referral_code_availability("RCTAKEN1", exclude_self=False)
		self.assertFalse(r["available"])

	def test_submit_wizard_step4_saves_custom_8_char_code(self):
		r = submit_wizard_step4(referral_code="RCMYCODE")
		self.assertTrue(r["saved"])
		self.assertFalse(r.get("skipped"))
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", self.profile_name, "referral_code"),
			"RCMYCODE",
		)

	def test_submit_wizard_step4_can_skip(self):
		r = submit_wizard_step4(referral_code="")
		self.assertTrue(r["saved"])
		self.assertTrue(r.get("skipped"))
		# Skipped means no code is assigned yet.
		self.assertFalse(
			frappe.db.get_value("Affiliate Profile", self.profile_name, "referral_code")
		)

	def test_submit_wizard_step4_rejects_non_8(self):
		with self.assertRaises(frappe.ValidationError):
			submit_wizard_step4(referral_code="RC123")
		with self.assertRaises(frappe.ValidationError):
			submit_wizard_step4(referral_code="RCAHMADALI")

	def test_set_custom_referral_code_enforces_8(self):
		with self.assertRaises(frappe.ValidationError):
			set_custom_referral_code("RC123")

	def test_set_custom_referral_code_saves_and_cooldowns(self):
		set_custom_referral_code("RCCODE01")
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", self.profile_name, "referral_code"),
			"RCCODE01",
		)
		# Changing it set the cooldown timestamp.
		self.assertTrue(
			frappe.db.get_value(
				"Affiliate Profile", self.profile_name, "last_referral_code_change"
			)
		)
		# A second change inside the 30-day window is blocked, even though
		# the new code is itself a valid 8-char code.
		with self.assertRaises(frappe.ValidationError):
			set_custom_referral_code("RCCODE02")

	# --- ID document upload (wizard step 2 backing endpoint) ---
	#
	# upload_affiliate_document replaces a bare POST to /api/method/
	# upload_file, which 403-blocked every non-Administrator affiliate at
	# step 2 (the File doctype's has_permission gate denies create on an
	# unattached private file whose owner isn't set yet). These cover the
	# behaviour of the replacement endpoint: it stores a private File
	# attached to the caller's own profile, rejects non-image/PDF uploads,
	# and requires a file at all.

	def _set_request_files(self, files):
		# In the test process there is no real HTTP request, so stand up a
		# minimal frappe.local.request with the .files dict the endpoint
		# reads. Other request attributes the File save or notification
		# machinery may touch (host, url, remote_addr, ...) are answered
		# with an empty string rather than AttributeError. Restore the
		# original afterwards so nothing leaks across tests.
		self._orig_request = getattr(frappe.local, "request", None)

		class _RequestStub:
			def __init__(self, files):
				self.files = files

			def __getattr__(self, _name):
				# Any attribute we didn't set explicitly is absent - return
				# "" (falsy) instead of raising, matching a request-less
				# test context.
				return ""

		frappe.local.request = _RequestStub(files)

	def _restore_request(self):
		frappe.local.request = self._orig_request

	def test_upload_affiliate_document_creates_private_file(self):
		from io import BytesIO

		from werkzeug.datastructures import FileStorage

		upload = FileStorage(
			stream=BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
			filename="id.png",
			content_type="image/png",
		)
		self._set_request_files({"file": upload})
		try:
			url = upload_affiliate_document()
		finally:
			self._restore_request()

		self.assertTrue(url.startswith("/private/files/"))
		file_row = frappe.db.get_value(
			"File",
			{"file_url": url},
			["name", "is_private", "attached_to_doctype", "attached_to_name"],
			as_dict=True,
		)
		self.assertIsNotNone(file_row, "a File row should exist for the uploaded url")
		self.assertEqual(int(file_row.is_private), 1)
		self.assertEqual(file_row.attached_to_doctype, "Affiliate Profile")
		self.assertEqual(file_row.attached_to_name, self.profile_name)
		frappe.delete_doc("File", file_row.name, force=True, ignore_permissions=True)

	def test_upload_affiliate_document_rejects_non_image(self):
		from io import BytesIO

		from werkzeug.datastructures import FileStorage

		upload = FileStorage(
			stream=BytesIO(b"MZ\x00\x00"),
			filename="bad.exe",
			content_type="application/octet-stream",
		)
		self._set_request_files({"file": upload})
		try:
			with self.assertRaises(frappe.ValidationError):
				upload_affiliate_document()
		finally:
			self._restore_request()

	def test_upload_affiliate_document_requires_file(self):
		self._set_request_files({})
		try:
			with self.assertRaises(frappe.ValidationError):
				upload_affiliate_document()
		finally:
			self._restore_request()
