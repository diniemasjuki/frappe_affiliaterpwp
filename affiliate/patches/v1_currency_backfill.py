"""Backfills currency tags for the multi-currency model (see
affiliate/api/commission_sync.py and the currency axis in travel_booking):

- Affiliate Commission.currency <- the linked Sales Order's transaction
  currency (blank -> site company currency as a last resort).
- Affiliate Commission Invoice (child rows).currency <- the Sales
  Invoice's currency, falling back to its commission's currency.
- Affiliate Payout.currency <- the currency of the commissions inside it
  (all payouts pre-dating currency tagging were necessarily
  single-currency, since mixing was impossible before this feature).
- Affiliate Profile.default_currency <- site company currency (only when
  blank; an affiliate's own choice is never overwritten).

Only touches rows where the currency is blank, so it is idempotent and
safe to re-run. Afterwards, cached totals are recomputed for every
affected profile so the per-currency balance rows and the *_est
conversion fields are populated immediately.
"""

import frappe

from affiliate.api.commission_sync import update_affiliate_cached_totals
from affiliate.api.currency_exchange import get_company_currency


def execute():
	company_currency = get_company_currency()

	for row in frappe.get_all(
		"Affiliate Commission",
		filters={"currency": ["is", "not set"]},
		fields=["name", "sales_order"],
	):
		currency = None
		if row.sales_order:
			currency = frappe.db.get_value("Sales Order", row.sales_order, "currency")
		frappe.db.set_value(
			"Affiliate Commission", row.name, "currency", currency or company_currency
		)

	for row in frappe.get_all(
		"Affiliate Commission Invoice",
		filters={"currency": ["is", "not set"]},
		fields=["name", "parent", "sales_invoice"],
	):
		currency = None
		if row.sales_invoice:
			currency = frappe.db.get_value("Sales Invoice", row.sales_invoice, "currency")
		if not currency:
			currency = frappe.db.get_value(
				"Affiliate Commission", row.parent, "currency"
			)
		frappe.db.set_value(
			"Affiliate Commission Invoice", row.name, "currency", currency or company_currency
		)

	for row in frappe.get_all(
		"Affiliate Payout",
		filters={"currency": ["is", "not set"]},
		fields=["name"],
	):
		currency = frappe.db.sql(
			"""SELECT ac.currency
			   FROM `tabAffiliate Payout Commission` pc
			   JOIN `tabAffiliate Commission` ac ON ac.name = pc.commission
			   WHERE pc.parent = %s AND pc.parenttype = 'Affiliate Payout'
			   LIMIT 1""",
			(row.name,),
		)
		frappe.db.set_value(
			"Affiliate Payout", row.name, "currency", (currency[0][0] if currency else None) or company_currency
		)

	frappe.db.sql(
		"""UPDATE `tabAffiliate Profile`
		   SET default_currency = %s
		   WHERE default_currency IS NULL OR default_currency = ''""",
		(company_currency,),
	)

	affected = frappe.get_all(
		"Affiliate Commission",
		filters={"currency": ["is", "set"]},
		fields=["affiliate"],
		distinct=True,
		pluck="affiliate",
	)
	for affiliate_name in affected:
		update_affiliate_cached_totals(affiliate_name)
