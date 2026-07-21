# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document
from frappe.utils import random_string


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
		commission_rate: DF.Percent
		date_of_birth: DF.Date | None
		document_id: DF.Attach | None
		email_id: DF.Data
		full_name: DF.Data | None
		gender: DF.Literal[None]
		national_id: DF.Data | None
		phone: DF.Phone | None
		referral_code: DF.Data | None
		sales_partner: DF.Link | None
		status: DF.Literal["Pending Verification", "Verified"]
		total_commission: DF.Currency
		total_sales: DF.Currency
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

	def on_update(self):
		just_verified = (
			self._previous_status == "Pending Verification"
			and self.status == "Verified"
		)

		if just_verified and not self.referral_code:
			self.db_set("referral_code", self._generate_unique_referral_code())

		if just_verified and not self.sales_partner:
			sales_partner_name = self._create_sales_partner()
			self.db_set("sales_partner", sales_partner_name)

		self._sync_commission_rate_to_sales_partner()

	def _generate_unique_referral_code(self) -> str:
		settings = frappe.get_cached_doc("Affiliate Settings")
		prefix = settings.referral_code_prefix or "RC"
		name_length = settings.referral_code_name_length or 6

		base_letters = re.sub(r"[^A-Z]", "", (self.full_name or "").upper())
		base_letters = base_letters[:name_length].ljust(name_length, "0")
		base_code = f"{prefix}{base_letters}"

		candidate = base_code
		suffix_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
		suffix_index = 0
		while frappe.db.exists("Affiliate Profile", {"referral_code": candidate}):
			# Collision: bump only the last character through 0-9 then A-Z.
			suffix_index += 1
			if suffix_index >= len(suffix_chars):
				# Astronomically unlikely (36 collisions on the same base) -
				# fall back to a random suffix rather than looping forever.
				candidate = f"{base_code[:-1]}{random_string(1).upper()}"
				break
			candidate = f"{base_code[:-1]}{suffix_chars[suffix_index]}"

		return candidate

	def _create_sales_partner(self) -> str:
		sales_partner = frappe.get_doc(
			{
				"doctype": "Sales Partner",
				"partner_name": self.full_name or self.email_id,
				"commission_rate": self.commission_rate or 0,
			}
		)
		sales_partner.insert(ignore_permissions=True)
		return sales_partner.name

	def _sync_commission_rate_to_sales_partner(self):
		if not self.sales_partner:
			return
		current_rate = frappe.db.get_value(
			"Sales Partner", self.sales_partner, "commission_rate"
		)
		if current_rate != self.commission_rate:
			frappe.db.set_value(
				"Sales Partner", self.sales_partner, "commission_rate", self.commission_rate
			)
