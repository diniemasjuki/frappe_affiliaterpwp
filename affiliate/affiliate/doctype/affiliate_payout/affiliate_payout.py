# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class AffiliatePayout(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from affiliate.affiliate.doctype.affiliate_payout_commission.affiliate_payout_commission import AffiliatePayoutCommission
		from frappe.types import DF

		affiliate: DF.Link | None
		amount: DF.Currency
		bill_no: DF.Data | None
		commissions: DF.Table[AffiliatePayoutCommission]
		generated_date: DF.Date | None
		payment_method: DF.Data | None
		period_end: DF.Date | None
		period_start: DF.Date | None
		status: DF.Literal[None]
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Payout"
