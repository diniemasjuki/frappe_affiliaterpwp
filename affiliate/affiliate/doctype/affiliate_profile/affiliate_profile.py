# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document
from frappe.utils import random_string

from affiliate.affiliate.doctype.affiliate_settings.affiliate_settings import (
	REFERRAL_CODE_MAX_LENGTH,
)


class AffiliateProfile(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_name: DF.Data | None
		account_number: DF.Data | None
		address: DF.SmallText | None
		available_balance: DF.Currency
		bank_name: DF.Data | None
		commission_base: DF.Literal["", "Gross", "Nett"] | None
		commission_rate: DF.Percent
		date_of_birth: DF.Date | None
		document_id: DF.Attach | None
		email_id: DF.Data
		full_name: DF.Data | None
		gender: DF.Literal["Male", "Female"]
		last_referral_code_change: DF.Datetime | None
		national_id: DF.Data | None
		phone: DF.Phone | None
		referral_code: DF.Data | None
		sales_partner: DF.Link | None
		status: DF.Literal["Pending Verification", "Verified"]
		total_commission: DF.Currency
		total_sales: DF.Currency
		user: DF.Link | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Profile"

	def validate(self):
		# Track the previous status so on_update can tell whether this save
		# is the moment verification just happened (Pending Verification -> Verified),
		# which is the only time referral_code/sales_partner get created.
		self._previous_status = None
		if not self.is_new():
			self._previous_status = frappe.db.get_value(
				"Affiliate Profile", self.name, "status"
			)

		self._normalize_phone()

	def _normalize_phone(self):
		"""Store phone numbers as "+ISD-number" (e.g. "+60-123456788")
		rather than plain E.164 ("+60123456789").

		This runs in validate() - not in each API endpoint - so it's
		applied on every save no matter how it happens (the affiliate
		portal, Desk manual edits, data import, future code that saves
		this doctype), rather than depending on every caller to
		remember to call a conversion helper themselves.

		Why this format specifically: Frappe's own backend validation
		(validate_phone_number_with_country_code, in
		frappe/utils/__init__.py) parses phone field values with the
		`phonenumbers` library, which requires a leading "+" to
		determine the country - a bare "60-123456789" fails with
		"Please select a country code". But the Desk widget for
		fieldtype Phone (frappe/public/js/frappe/form/controls/phone.js)
		only renders the flag + split input correctly when the stored
		value contains a dash - a plain "+60123456789" gets misread and
		the widget re-prepends a dial code on top of it, corrupting the
		on-screen display (though never the underlying data). Only
		"+ISD-number" satisfies both at once - confirmed by testing
		phonenumbers.parse() directly against all three candidate
		formats.
		"""
		if not self.phone:
			return

		phone = self.phone.strip()
		if "-" in phone:
			# Already in the expected format (e.g. entered directly at
			# the Desk, or already normalized on a previous save) -
			# leave it alone rather than risk mangling it further.
			return

		try:
			from phonenumbers import parse as phonenumbers_parse

			parsed = phonenumbers_parse(phone)
			dial_code = str(parsed.country_code)
			national_number = str(parsed.national_number)
			self.phone = f"+{dial_code}-{national_number}"
		except Exception:
			# Couldn't parse - leave it as given. Frappe's own
			# validate_phone_number_with_country_code (which runs later
			# in the same save) will raise a clear error if the number
			# is genuinely invalid, so we don't need to duplicate that
			# check here.
			pass

	def on_update(self):
		just_verified = (
			self._previous_status == "Pending Verification"
			and self.status == "Verified"
		)

		if just_verified and not self.commission_rate:
			# Affiliate Settings.default_commission_percent exists to give
			# every newly-verified affiliate a sensible starting rate
			# without an admin having to manually set it each time - but
			# only fills in if nothing's been set already, so it never
			# clobbers a rate an admin deliberately configured before
			# verifying (e.g. a negotiated rate for a specific partner).
			# This runs before referral_code/sales_partner creation below
			# so the new Sales Partner record is created with the correct
			# rate from the start, rather than 0 and a second sync.
			default_rate = frappe.db.get_single_value(
				"Affiliate Settings", "default_commission_percent"
			)
			if default_rate:
				self.db_set("commission_rate", default_rate)

		if just_verified and not self.referral_code:
			self.db_set("referral_code", self._generate_unique_referral_code())

		if just_verified and not self.sales_partner:
			sales_partner_name = self._create_sales_partner()
			self.db_set("sales_partner", sales_partner_name)

		self._sync_to_sales_partner()

		if just_verified:
			# db_set() above writes commission_rate/referral_code/sales_partner
			# AND bumps the DB row's `modified` timestamp - but does not
			# refresh self.modified in memory. Re-sync just the timestamp
			# (not a full reload, which risks re-reading before the
			# db_set() writes are visible) so a later .save() on this same
			# in-memory object does not incorrectly raise
			# TimestampMismatchError.
			self.modified = frappe.db.get_value(self.doctype, self.name, "modified")
			self._original_modified = self.modified

	def _generate_unique_referral_code(self) -> str:
		settings = frappe.get_cached_doc("Affiliate Settings")
		prefix = settings.referral_code_prefix or "RC"

		# Defensive ceiling: referral_code is a varchar(8) field and this
		# value is written via db_set() (which skips field validation), so
		# a misconfigured prefix + name_length that overshoots would either
		# be silently truncated by the DB or rejected in strict mode — both
		# corrupt the code the affiliate is assigned. Affiliate Settings
		# .validate() rejects such configs at save time, but clamp here too
		# so the guarantee holds even if a bad value was written directly to
		# the single-doctype row (bypassing that validation). At least one
		# name character is always kept so the code isn't prefix-only.
		max_name_length = max(1, REFERRAL_CODE_MAX_LENGTH - len(prefix))
		name_length = settings.referral_code_name_length or max_name_length
		name_length = min(name_length, max_name_length)

		base_letters = re.sub(r"[^A-Z]", "", (self.full_name or "").upper())
		base_letters = base_letters[:name_length].ljust(name_length, "0")
		base_code = f"{prefix}{base_letters}"

		# Final invariant: never hand back a code longer than the field can
		# hold. Truncate from the right (keep the prefix intact, drop the
		# tail name characters) so the prefix — the part every code from
		# this site shares — always survives.
		if len(base_code) > REFERRAL_CODE_MAX_LENGTH:
			base_code = base_code[:REFERRAL_CODE_MAX_LENGTH]

		candidate = base_code
		suffix_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
		suffix_index = 0
		while frappe.db.exists("Affiliate Profile", {"referral_code": candidate}) or frappe.db.exists(
			"Sales Partner", {"referral_code": candidate}
		):
			# Collision: bump only the last character through 0-9 then A-Z.
			# Checked against both Affiliate Profile and Sales Partner since
			# this same code ends up copied onto the linked Sales Partner
			# record (which also enforces uniqueness on referral_code) once
			# this affiliate gets verified.
			suffix_index += 1
			if suffix_index >= len(suffix_chars):
				# Astronomically unlikely (36 collisions on the same base) -
				# fall back to a random suffix rather than looping forever.
				candidate = f"{base_code[:-1]}{random_string(1).upper()}"
				break
			candidate = f"{base_code[:-1]}{suffix_chars[suffix_index]}"

		return candidate

	def _create_sales_partner(self) -> str:
		"""Creates the linked Sales Partner record the first time this
		Affiliate Profile is verified. Two ERPNext constraints we have
		to satisfy here:

		- Sales Partner.territory is mandatory. We use "All Territories"
		  (the root Territory node that always exists on every ERPNext
		  site - created by the setup wizard) as a sensible default,
		  since our affiliates aren't organized by geography. Falls back
		  to whatever Territory happens to exist if that's somehow been
		  renamed or removed, rather than hard-failing verification.
		- Sales Partner.partner_name is unique. Two affiliates can
		  easily share a full name (e.g. two "Ahmad Bin Ali"), so we
		  disambiguate with this profile's own name if the plain
		  full_name is already taken.

		Also carries over:
		- referral_code: Sales Partner has its OWN referral_code field
		  (separate from Affiliate Profile.referral_code, even though
		  we generate ours first and copy it across here) which is what
		  ERPNext's native e-commerce checkout uses to auto-attach a
		  Sales Partner to a Sales Order via a "?sp=<code>" URL
		  parameter. Leaving this blank means that native mechanism can
		  never find our affiliates - copying our own code across here
		  is what actually wires that up. This method runs after
		  on_update() has already generated self.referral_code via
		  db_set(), so it's available here.
		- partner_type: ERPNext ships an "Affiliate" Sales Partner Type
		  by default, which is exactly the right classification for
		  every partner this app creates.
		"""
		territory = (
			frappe.db.exists("Territory", "All Territories")
			or frappe.db.get_value("Territory", {}, "name")
		)
		if not territory:
			frappe.throw(
				"Cannot verify this affiliate: no Territory exists in the system. "
				"Sales Partner requires at least one Territory record."
			)

		partner_name = self.full_name or self.email_id
		if frappe.db.exists("Sales Partner", partner_name):
			partner_name = f"{partner_name} ({self.name})"

		partner_type = (
			"Affiliate" if frappe.db.exists("Sales Partner Type", "Affiliate") else None
		)

		sales_partner = frappe.get_doc(
			{
				"doctype": "Sales Partner",
				"partner_name": partner_name,
				"commission_rate": self.commission_rate or 0,
				"territory": territory,
				"partner_type": partner_type,
				"referral_code": self.referral_code,
			}
		)
		sales_partner.insert(ignore_permissions=True)
		return sales_partner.name

	def _sync_to_sales_partner(self):
		"""Keeps the linked Sales Partner's partner_name, commission_rate,
		and referral_code in step with this Affiliate Profile any time
		either changes after verification - not just commission_rate as
		before. Runs on every save via on_update(), same as the
		commission-rate-only version this replaces.

		Only touches fields that actually differ, to avoid unnecessary
		writes and unnecessary Sales Partner modification timestamps.
		"""
		if not self.sales_partner:
			return

		current = frappe.db.get_value(
			"Sales Partner",
			self.sales_partner,
			["partner_name", "commission_rate", "referral_code"],
			as_dict=True,
		)
		if not current:
			return

		updates = {}

		if current.commission_rate != self.commission_rate:
			updates["commission_rate"] = self.commission_rate

		desired_name = self.full_name or self.email_id
		if current.partner_name != desired_name:
			# partner_name is unique - if the new name collides with a
			# DIFFERENT Sales Partner record, disambiguate the same way
			# _create_sales_partner() does, rather than letting the
			# save fail outright.
			clash = frappe.db.exists("Sales Partner", desired_name)
			if clash and clash != self.sales_partner:
				desired_name = f"{desired_name} ({self.name})"
			updates["partner_name"] = desired_name

		if self.referral_code and current.referral_code != self.referral_code:
			updates["referral_code"] = self.referral_code

		if updates:
			frappe.db.set_value("Sales Partner", self.sales_partner, updates)