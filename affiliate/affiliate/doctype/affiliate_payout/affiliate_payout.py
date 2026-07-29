# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import frappe
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
		bill_no: DF.Data
		commissions: DF.Table[AffiliatePayoutCommission]
		generated_date: DF.Date | None
		payment_method: DF.Data | None
		period_end: DF.Date | None
		period_start: DF.Date | None
		status: DF.Literal["Pending", "Processing", "Paid"]
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Payout"

	def validate(self):
		self._previous_status = None
		if not self.is_new():
			self._previous_status = frappe.db.get_value(
				"Affiliate Payout", self.name, "status"
			)

	def on_update(self):
		
		just_paid = self._previous_status != "Paid" and self.status == "Paid"
		if not just_paid:
			return

		for row in self.commissions:
			frappe.db.set_value("Affiliate Commission", row.commission, "status", "Paid")

		from affiliate.api.commission_sync import update_affiliate_cached_totals

		update_affiliate_cached_totals(self.affiliate)