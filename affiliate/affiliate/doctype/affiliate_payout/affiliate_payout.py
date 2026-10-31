# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import frappe
from frappe import _
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
		currency: DF.Link | None
		generated_date: DF.Date | None
		payment_method: DF.Data | None
		period_end: DF.Date | None
		period_start: DF.Date | None
		source: DF.Literal["Manual Claim", "Monthly Batch"]
		status: DF.Literal["Pending", "Processing", "Paid"]
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Payout"

	def validate(self):
		self._previous_status = None
		if not self.is_new():
			self._previous_status = frappe.db.get_value(
				"Affiliate Payout", self.name, "status"
			)

		self._validate_no_duplicate_commissions()

	def _validate_no_duplicate_commissions(self):
		"""Hard anti-double-payout guarantee: a commission may be attached
		to at most ONE Affiliate Payout, ever, regardless of which code
		path builds the payout (scheduled monthly batch, portal manual
		claim, admin regenerate action, or a manual Desk edit). Every
		creator is expected to consult
		commission_sync.get_unpaid_out_commissions* - which already
		excludes attached commissions - but this check catches any future
		path that forgets, plus races between two simultaneous creators.

		Payouts are not submittable, so there is no cancelled-but-live
		state that legitimately re-frees a commission: deleting a payout
		deletes its child rows with it.
		"""
		commission_names = [row.commission for row in self.commissions if row.commission]
		if not commission_names:
			return

		duplicates = frappe.get_all(
			"Affiliate Payout Commission",
			filters={"commission": ["in", commission_names], "parent": ["!=", self.name or ""]},
			fields=["parent", "commission"],
			limit=len(commission_names),
		)
		if duplicates:
			first = duplicates[0]
			frappe.throw(
				_("Commission {0} is already attached to payout {1} - it cannot be paid out twice.").format(
					frappe.bold(first.commission), frappe.bold(first.parent)
				)
			)

	def on_update(self):
		
		just_paid = self._previous_status != "Paid" and self.status == "Paid"
		if not just_paid:
			return

		for row in self.commissions:
			frappe.db.set_value("Affiliate Commission", row.commission, "status", "Paid")

		from affiliate.api.commission_sync import update_affiliate_cached_totals

		update_affiliate_cached_totals(self.affiliate)