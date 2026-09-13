# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# The referral_code field on Affiliate Profile is varchar(8), and custom
# referral codes are validated to be exactly 8 characters (see
# portal_api._validate_custom_referral_code). Auto-generated codes must
# honour the same ceiling, so this is the single source of truth for the
# maximum total length of a generated referral code.
REFERRAL_CODE_MAX_LENGTH = 8


class AffiliateSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		approve_commission_on_si_status: DF.Literal["Draft", "Submitted", "Unpaid", "Partly Paid", "Overdue", "Unpaid and Discounted", "Overdue and Discounted", "Paid"]
		approve_commission_on_so_status: DF.Literal["Draft", "On Hold", "To Deliver and Bill", "To Bill", "To Deliver", "Completed"]
		ai_api_key: DF.Password | None
		ai_base_url: DF.Data | None
		ai_model: DF.Data | None
		ai_provider: DF.Literal["OpenAI", "GLM"]
		clawback_on_refund: DF.Check
		commission_base: DF.Literal["Gross", "Nett"]
		commission_on_addons: DF.Check
		default_commission_percent: DF.Percent
		enable_ai_verification: DF.Check
		enable_id_extraction: DF.Check
		minimum_cashout: DF.Currency
		referral_code_name_length: DF.Int
		referral_code_prefix: DF.Data
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Settings"

	def validate(self):
		# An auto-generated referral code is built as prefix + name_length
		# characters, then written via db_set() (which bypasses field
		# validation). If the configured prefix + name_length exceed the
		# referral_code field's 8-character length, the code gets silently
		# truncated by the DB (or rejected in strict mode) — corrupting the
		# code the affiliate is assigned. Reject such a configuration here,
		# at the point a human actually saves it, so a bad setting can never
		# reach the generation path. (affiliate_profile's generator also
		# clamps defensively as a last resort, but this is the real guard.)
		prefix = (self.referral_code_prefix or "").strip()
		if not prefix:
			frappe.throw(_("Referral Code Prefix is required."))

		name_length = self.referral_code_name_length or 0
		if name_length < 1:
			frappe.throw(_("Referral Code Name Length must be at least 1."))

		total = len(prefix) + name_length
		if total > REFERRAL_CODE_MAX_LENGTH:
			frappe.throw(
				_(
					"Referral code prefix length ({0}) + name length ({1}) = {2} characters, "
					"which exceeds the {3}-character maximum for a referral code. "
					"Reduce the prefix or the name length so the total is at most {3}."
				).format(len(prefix), name_length, total, REFERRAL_CODE_MAX_LENGTH)
			)

		# The AI ID features are the whole point of the AI Integration
		# section - silently saving them checked with no API key would
		# only surface later as a vague "AI analysis failed" in the
		# affiliate's browser. Fail here, at the point a human saves the
		# setting, so a broken configuration can never be stored. (The
		# portal itself still degrades gracefully: an unconfigured or
		# unreachable provider disables the AI panels without breaking
		# the wizard - this check is about honest configuration, not a
		# runtime gate.)
		if self.enable_id_extraction or self.enable_ai_verification:
			if not self.ai_provider:
				frappe.throw(_("AI Provider is required when an AI feature is enabled."))
			if not self.get_password("ai_api_key", raise_exception=False):
				frappe.throw(
					_("AI API Key is required when an AI feature is enabled.")
				)
