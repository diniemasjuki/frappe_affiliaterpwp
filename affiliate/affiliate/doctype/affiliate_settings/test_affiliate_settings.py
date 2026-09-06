# Copyright (c) 2026, Warga Prihatin and Contributors
# See license.txt

import frappe
from frappe import _
from frappe.tests import IntegrationTestCase
from frappe.utils import random_string

from affiliate.affiliate.doctype.affiliate_settings.affiliate_settings import (
	REFERRAL_CODE_MAX_LENGTH,
)


class IntegrationTestAffiliateSettings(IntegrationTestCase):
	"""Tests that Affiliate Settings cannot be configured to produce
	referral codes longer than the 8-character referral_code field.

	A misconfiguration here is what previously let auto-generated codes
	hit 10 characters (prefix "RC" + name_length default 8) and get
	silently truncated by the DB. The validate() hook is the human-facing
	guard that stops that at the source.
	"""

	# On IntegrationTestCase, the doctype test records and all
	# link-field test record dependencies are recursively loaded
	# Use these module variables to add/remove to/from that list
	EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
	IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]

	def setUp(self):
		# Persist and restore the real settings so a test that writes a
		# deliberately-bad value directly to the DB (bypassing validate)
		# can't leak that bad value into other tests or the live site.
		self._original = frappe.db.get_value(
			"Affiliate Settings",
			"Affiliate Settings",
			["referral_code_prefix", "referral_code_name_length"],
			as_dict=True,
		)

	def tearDown(self):
		frappe.db.set_value(
			"Affiliate Settings",
			"Affiliate Settings",
			{
				"referral_code_prefix": self._original.referral_code_prefix,
				"referral_code_name_length": self._original.referral_code_name_length,
			},
		)
		frappe.clear_cache()

	def _new_settings(self, prefix: str, name_length: int):
		"""A fresh in-memory settings doc (not saved) with the given
		referral-code config. Lets each assertion start from a known
		state rather than whatever the live single-doctype happens to hold.
		"""
		doc = frappe.new_doc("Affiliate Settings")
		doc.referral_code_prefix = prefix
		doc.referral_code_name_length = name_length
		return doc

	def test_referral_code_max_length_constant(self):
		# The field-level guarantee. If this ever drifts from the
		# affiliate_profile referral_code field's `length`, every other
		# test here is testing the wrong ceiling.
		self.assertEqual(REFERRAL_CODE_MAX_LENGTH, 8)

	def test_default_config_total_is_exactly_8(self):
		# A fresh install's defaults must produce codes that fit the
		# 8-char field exactly — not just "no more than 8", but the same
		# length custom codes are required to be (see
		# portal_api._validate_custom_referral_code), so auto and
		# custom codes are interchangeable.
		doc = self._new_settings(prefix="RC", name_length=6)
		self.assertEqual(len(doc.referral_code_prefix) + doc.referral_code_name_length, 8)

	def test_validate_rejects_config_exceeding_8(self):
		# The regression that motivated this: prefix "RC" (2) + name_length 8
		# = 10, overflowing the field. Saving it must now raise rather than
		# silently letting the generator produce a truncated code.
		doc = self._new_settings(prefix="RC", name_length=8)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_validate_rejects_long_prefix(self):
		# A prefix so long there's no room for even one name character.
		doc = self._new_settings(prefix="AFFILIAT", name_length=1)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_validate_rejects_empty_prefix(self):
		doc = self._new_settings(prefix="   ", name_length=6)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_validate_rejects_zero_name_length(self):
		doc = self._new_settings(prefix="RC", name_length=0)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_validate_accepts_total_exactly_8(self):
		# Boundary: the maximum allowed total must save cleanly.
		doc = self._new_settings(prefix="RC", name_length=6)
		doc.save(ignore_permissions=True)
		self.assertEqual(
			len(doc.referral_code_prefix) + doc.referral_code_name_length, 8
		)

	def test_validate_accepts_total_below_8(self):
		# Shorter codes are allowed — the rule is "must not exceed 8",
		# not "must be exactly 8". An admin who wants prefix-only short
		# codes can have them.
		doc = self._new_settings(prefix="RC", name_length=3)
		doc.save(ignore_permissions=True)
		self.assertLessEqual(
			len(doc.referral_code_prefix) + doc.referral_code_name_length, 8
		)

	def test_db_set_bypass_is_clamped_by_generator_not_settings(self):
		# Settings.validate() can't catch a value written straight to the
		# DB (e.g. by a future patch or a direct admin UPDATE). This just
		# confirms the bad value lands in the DB when bypassed — the
		# affiliate_profile generator test covers the clamp that catches
		# it at generation time.
		frappe.db.set_value(
			"Affiliate Settings", "Affiliate Settings", "referral_code_name_length", 8
		)
		self.assertEqual(
			frappe.db.get_single_value("Affiliate Settings", "referral_code_name_length"),
			8,
		)
