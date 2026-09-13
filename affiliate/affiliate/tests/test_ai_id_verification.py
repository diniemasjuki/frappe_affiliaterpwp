# Copyright (c) 2026, Warga Prihatin and Contributors
# See license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import random_string

from affiliate.affiliate.doctype.affiliate_profile.affiliate_profile import (
	normalize_national_id,
)
from affiliate.api import ai_service


class TestNationalIdNormalization(UnitTestCase):
	"""The IC/National ID field keeps only uppercase letters and digits.

	Enforced at three layers on purpose: the wizard input transforms as
	the user types (client), the wizard endpoint normalizes before save,
	and the profile's validate() normalizes on EVERY save so Desk edits
	and API callers can't reintroduce symbols.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	def test_strips_symbols_and_uppercases(self):
		self.assertEqual(normalize_national_id(" 1234-5678-9012 "), "123456789012")
		self.assertEqual(normalize_national_id("a12/b3.c4"), "A12B3C4")
		self.assertEqual(normalize_national_id(None), "")
		self.assertEqual(normalize_national_id("!!!"), "")

	def test_profile_validate_normalizes_on_save(self):
		self._original_user = frappe.session.user
		profile = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"email_id": f"test_nid_{random_string(8).lower()}@example.test",
				"full_name": "Ahmad Bin Ali",
				"national_id": " 9012-34-5678 ",
				"status": "Pending Verification",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(
				frappe.db.get_value("Affiliate Profile", profile.name, "national_id"),
				"9012345678",
			)
		finally:
			frappe.delete_doc("Affiliate Profile", profile.name, force=True, ignore_permissions=True)
			frappe.set_user(self._original_user)


class TestAiResponseHandling(UnitTestCase):
	"""Pure-function tests for the model-output parsing and normalization
	- no HTTP, no DB writes.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	def test_parse_plain_json(self):
		self.assertEqual(ai_service.parse_model_json('{"a": 1}'), {"a": 1})

	def test_parse_fenced_json(self):
		text = "```json\n{\"full_name\": \"Ali\"}\n```"
		self.assertEqual(ai_service.parse_model_json(text), {"full_name": "Ali"})

	def test_parse_json_embedded_in_prose(self):
		text = 'Sure! Here is the data: {"a": 1} hope that helps.'
		self.assertEqual(ai_service.parse_model_json(text), {"a": 1})

	def test_parse_garbage_raises(self):
		self.assertRaises(frappe.ValidationError, ai_service.parse_model_json, "no json here at all")

	def test_normalize_extracted_fields(self):
		normalized = ai_service.normalize_extracted(
			{
				"full_name": " Ahmad Bin Ali ",
				"national_id": "900123-45-6789",
				"date_of_birth": "31/12/1990",
				"gender": "LELAKI",
				"address": " No 5, Jalan Test ",
				"extra_key": "dropped",
			}
		)
		self.assertEqual(normalized["full_name"], "Ahmad Bin Ali")
		self.assertEqual(normalized["national_id"], "900123456789")
		# Malaysian documents are day-first; "31/12/1990" must not become
		# an impossible month-31 date.
		self.assertEqual(normalized["date_of_birth"], "1990-12-31")
		self.assertEqual(normalized["gender"], "Male")
		self.assertEqual(normalized["address"], "No 5, Jalan Test")
		self.assertNotIn("extra_key", normalized)

	def test_normalize_extracted_gender_variants(self):
		self.assertEqual(ai_service.normalize_extracted({"gender": "Wanita"})["gender"], "Female")
		self.assertEqual(ai_service.normalize_extracted({"gender": "F"})["gender"], "Female")
		self.assertEqual(ai_service.normalize_extracted({"gender": "???"})["gender"], "")

	def test_normalize_extracted_keeps_unparseable_date_raw(self):
		normalized = ai_service.normalize_extracted({"date_of_birth": "not a date"})
		self.assertEqual(normalized["date_of_birth"], "not a date")


class TestAffiliateSettingsAiValidation(UnitTestCase):
	"""AI features require a provider and an API key at save time, and
	get_ai_config fills provider defaults for blank base URL/model.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	def setUp(self):
		self._original_user = frappe.session.user
		frappe.set_user("Administrator")

	def tearDown(self):
		# Back to a disabled, key-less AI config whatever the test did.
		doc = frappe.get_doc("Affiliate Settings")
		doc.enable_id_extraction = 0
		doc.enable_ai_verification = 0
		doc.ai_api_key = None  # empties the field -> removes the stored password
		doc.save()
		frappe.set_user(self._original_user)
		frappe.clear_cache()

	def _save_settings(self, **kwargs):
		doc = frappe.get_doc("Affiliate Settings")
		for fieldname, value in kwargs.items():
			doc.set(fieldname, value)
		doc.save()
		return doc

	def test_enabling_ai_without_key_is_rejected(self):
		doc = frappe.get_doc("Affiliate Settings")
		doc.ai_provider = "OpenAI"
		doc.enable_id_extraction = 1
		doc.ai_api_key = None
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.save()
		self.assertIn("AI API Key", str(ctx.exception))

	def test_enabling_ai_with_key_saves(self):
		self._save_settings(
			ai_provider="OpenAI",
			enable_id_extraction=1,
			ai_api_key="sk-test-" + random_string(8),
		)
		config = ai_service.get_ai_config()
		self.assertTrue(config["configured"])
		self.assertTrue(config["enable_id_extraction"])

	def test_provider_defaults_fill_blank_url_and_model(self):
		self._save_settings(
			ai_provider="GLM",
			enable_ai_verification=1,
			ai_api_key="zk-test-" + random_string(8),
			ai_base_url="",
			ai_model="",
		)
		config = ai_service.get_ai_config()
		self.assertEqual(config["base_url"], "https://api.z.ai/api/paas/v4")
		self.assertEqual(config["model"], "glm-4v-flash")

	def test_unconfigured_site_reports_not_configured(self):
		# Flags on but no key stored (flag flips bypass validate(), like a
		# direct DB write would) - callers must see configured=False, not
		# crash.
		frappe.db.set_single_value("Affiliate Settings", {"enable_id_extraction": 1, "ai_provider": "OpenAI"})
		config = ai_service.get_ai_config()
		self.assertFalse(config["configured"])


class TestWizardStep2SanitizeAndAiVerification(UnitTestCase):
	"""submit_wizard_step2 sanitizes the IC, runs AI verification when
	enabled, stores the verdict on the profile, and never lets an AI
	failure break the save. The AI boundary is patched - no network.
	"""

	EXTRA_TEST_RECORD_DEPENDENCIES = []
	IGNORE_TEST_RECORD_DEPENDENCIES = []

	DOCUMENT = {
		"full_name": "AHMAD BIN ALI",
		"national_id": "900123456789",
		"date_of_birth": "1990-12-31",
		"gender": "Male",
		"address": "No 5, Jalan Test",
	}

	def setUp(self):
		self._original_user = frappe.session.user
		self._cleanup = []

	def tearDown(self):
		# Back to AI disabled with no stored test key, whatever happened.
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Affiliate Settings")
		doc.enable_id_extraction = 0
		doc.enable_ai_verification = 0
		doc.ai_api_key = None  # empties the field -> removes the stored password
		doc.save()
		for doctype, name in reversed(self._cleanup):
			if doctype == "User":
				for c in frappe.get_all("Contact", filters={"user": name}, pluck="name"):
					frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.set_user(self._original_user)
		frappe.clear_cache()

	# -- helpers -------------------------------------------------------

	def _enable_ai(self):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Affiliate Settings")
		doc.ai_provider = "OpenAI"
		doc.enable_id_extraction = 1
		doc.enable_ai_verification = 1
		doc.ai_api_key = "sk-test-" + random_string(8)
		doc.save()
		frappe.set_user(self._affiliate_email)

	def _affiliate_session(self) -> str:
		email = f"test_wiz2_{random_string(8).lower()}@example.test"
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
				"full_name": "AHMAD BIN ALI",
				"date_of_birth": "1990-12-31",
				"gender": "Male",
				"address": "No 5, Jalan Test",
				"status": "Pending Verification",
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("Affiliate Profile", profile.name))
		self._affiliate_email = email
		frappe.set_user(email)
		return profile.name

	def _patch_ai(self, verdict):
		"""Patch the whole AI boundary: file fetch, OCR and verdict."""
		return (
			patch(
				"affiliate.api.ai_service.profile_document_data_url",
				return_value="data:image/png;base64,AAAA",
			),
			patch(
				"affiliate.api.ai_service.extract_id_data",
				return_value=dict(self.DOCUMENT),
			),
			patch(
				"affiliate.api.ai_service.verify_id_details",
				return_value=verdict,
			),
		)

	# -- tests ---------------------------------------------------------

	def test_step2_sanitizes_national_id_without_ai(self):
		from affiliate.api.portal_api import submit_wizard_step2

		profile_name = self._affiliate_session()
		res = submit_wizard_step2(national_id=" 900123-45-6789 ", document_id="/private/files/id.png")
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", profile_name, "national_id"),
			"900123456789",
		)
		# AI disabled -> exactly the pre-AI response shape.
		self.assertIsNone(res["verification"])

	def test_step2_match_verdict_is_stored(self):
		from affiliate.api.portal_api import submit_wizard_step2

		profile_name = self._affiliate_session()
		self._enable_ai()
		verdict = {"status": "Match", "confidence": 97, "issues": [], "detail": "match"}
		patches = self._patch_ai(verdict)
		for p in patches:
			p.start()
		try:
			res = submit_wizard_step2(national_id="900123456789", document_id="/private/files/id.png")
		finally:
			for p in patches:
				p.stop()

		self.assertEqual(res["verification"]["status"], "Match")
		stored = frappe.db.get_value(
			"Affiliate Profile", profile_name, ["ai_verification_status", "ai_extraction"], as_dict=True
		)
		self.assertEqual(stored.ai_verification_status, "Match")
		self.assertEqual(json.loads(stored.ai_extraction)["national_id"], "900123456789")

	def test_step2_mismatch_verdict_is_stored(self):
		from affiliate.api.portal_api import submit_wizard_step2

		profile_name = self._affiliate_session()
		self._enable_ai()
		verdict = {
			"status": "Mismatch",
			"confidence": 80,
			"issues": [{"field": "national_id", "document_value": "900123456789", "entered_value": "0000", "note": "differs"}],
			"detail": "mismatch",
		}
		patches = self._patch_ai(verdict)
		for p in patches:
			p.start()
		try:
			res = submit_wizard_step2(national_id="0000", document_id="/private/files/id.png")
		finally:
			for p in patches:
				p.stop()

		# The save still succeeds - the mismatch is advisory, the admin
		# makes the final call at verification.
		self.assertTrue(res["saved"])
		self.assertEqual(res["verification"]["status"], "Mismatch")
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", profile_name, "ai_verification_status"),
			"Mismatch",
		)

	def test_ai_failure_does_not_break_the_save(self):
		from affiliate.api.portal_api import submit_wizard_step2

		profile_name = self._affiliate_session()
		self._enable_ai()
		patches = (
			patch("affiliate.api.ai_service.profile_document_data_url", return_value="data:image/png;base64,AAAA"),
			patch("affiliate.api.ai_service.extract_id_data", return_value=dict(self.DOCUMENT)),
			patch("affiliate.api.ai_service.verify_id_details", side_effect=frappe.ValidationError("AI down")),
		)
		for p in patches:
			p.start()
		try:
			res = submit_wizard_step2(national_id="900123456789", document_id="/private/files/id.png")
		finally:
			for p in patches:
				p.stop()

		self.assertTrue(res["saved"])
		self.assertEqual(res["verification"]["status"], "Error")
		# No verdict was stored - the field keeps its neutral default.
		self.assertEqual(
			frappe.db.get_value("Affiliate Profile", profile_name, "ai_verification_status"),
			"Not Verified",
		)

	def test_extract_endpoint_disabled_returns_enabled_false(self):
		from affiliate.api.portal_api import extract_affiliate_id_document

		self._affiliate_session()
		res = extract_affiliate_id_document(document_id="/private/files/id.png")
		self.assertEqual(res, {"enabled": False})

	def test_extract_endpoint_returns_and_stores_extraction(self):
		from affiliate.api.portal_api import extract_affiliate_id_document

		profile_name = self._affiliate_session()
		self._enable_ai()
		patches = self._patch_ai({"status": "Match"})
		for p in patches:
			p.start()
		try:
			res = extract_affiliate_id_document(document_id="/private/files/id.png")
		finally:
			for p in patches:
				p.stop()

		self.assertTrue(res["enabled"])
		self.assertEqual(res["extracted"]["national_id"], "900123456789")
		self.assertEqual(
			json.loads(frappe.db.get_value("Affiliate Profile", profile_name, "ai_extraction"))["full_name"],
			"AHMAD BIN ALI",
		)

	def test_document_must_belong_to_the_callers_profile(self):
		from affiliate.api.portal_api import extract_affiliate_id_document

		self._affiliate_session()
		self._enable_ai()
		other_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "someone-elses-id.png",
				"file_url": f"/private/files/other-id-{random_string(6)}.png",
				"attached_to_doctype": "Affiliate Profile",
				"attached_to_name": "AFF-somebody-else",
				"is_private": 1,
				"content": b"\x89PNG\r\n\x1a\nnot-a-real-image",
			}
		).insert(ignore_permissions=True)
		self._cleanup.append(("File", other_file.name))

		# The URL exists on the site but is attached to a DIFFERENT
		# profile - the endpoint must refuse it (PermissionError), which
		# the endpoint converts into an {"error": ...} payload rather
		# than leaking the document.
		res = extract_affiliate_id_document(document_id=other_file.file_url)
		self.assertIn("error", res)
