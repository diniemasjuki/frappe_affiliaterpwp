# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class AffiliateSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		approve_commission_on_si_status: DF.Literal["Draft", "Submitted", "Unpaid", "Partly Paid", "Overdue", "Unpaid and Discounted", "Overdue and Discounted", "Paid"]
		approve_commission_on_so_status: DF.Literal["Draft", "On Hold", "To Deliver and Bill", "To Bill", "To Deliver", "Completed"]
		clawback_on_refund: DF.Check
		commission_base: DF.Literal["Gross", "Nett"]
		commission_on_addons: DF.Check
		default_commission_percent: DF.Percent
		minimum_cashout: DF.Currency
		referral_code_name_length: DF.Int
		referral_code_prefix: DF.Data
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Settings"
