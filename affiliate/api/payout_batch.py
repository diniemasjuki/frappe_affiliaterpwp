import frappe
from frappe import _
from frappe.utils import today

from affiliate.api.commission_sync import generate_unique_bill_no, get_unpaid_out_commissions_by_currency
from affiliate.api.currency_exchange import get_company_currency


def generate_payout_batch(period_start: str | None = None, period_end: str | None = None):
	"""Groups all unpaid-out Approved commissions (across all eligible
	affiliates) into Payouts - one per affiliate PER CURRENCY, since a
	payout can only ever combine commissions of a single currency (an
	affiliate with Approved commissions in both MYR and SGD gets two
	payouts, not one mixed-currency number). Safe to run repeatedly -
	an affiliate with no eligible commissions, or below the minimum
	cashout threshold, simply gets no payout this run. Also safe to run
	alongside affiliates self-requesting payouts from the portal -
	commissions already claimed by a self-request are excluded here.
	"""
	minimum_cashout = frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0

	affiliates_with_approved = frappe.get_all(
		"Affiliate Commission",
		filters={"status": "Approved"},
		fields=["affiliate"],
		distinct=True,
		pluck="affiliate",
	)

	created = []
	for affiliate_name in affiliates_with_approved:
		for currency in sorted(get_unpaid_out_commissions_by_currency(affiliate_name)):
			payout_name = generate_payout_batch_for_affiliate(
				affiliate_name,
				period_start=period_start,
				period_end=period_end,
				minimum_cashout=minimum_cashout,
				currency=currency,
			)
			if payout_name:
				created.append(payout_name)

	return created


def generate_payout_batch_for_affiliate(
	affiliate_name: str,
	period_start: str | None = None,
	period_end: str | None = None,
	minimum_cashout: float | None = None,
	currency: str | None = None,
):
	"""Creates a single Affiliate Payout for one affiliate's currently
	unpaid-out Approved commissions IN ONE CURRENCY (i.e. Approved, not
	already attached to some other Payout, and matching `currency`).
	Returns the new Payout's name, or None if there was nothing eligible
	to pay out in that currency or the currency's balance hasn't reached
	the minimum cashout threshold. `currency` defaults to the site's
	company currency - use generate_payout_batch (which iterates all
	currency groups) rather than calling this directly for affiliates
	that earn in several currencies.
	"""
	if minimum_cashout is None:
		minimum_cashout = frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0

	currency = currency or get_company_currency()
	commissions = get_unpaid_out_commissions_by_currency(affiliate_name).get(currency, [])
	if not commissions:
		return None

	total_amount = sum(c.commission_amount for c in commissions)
	if total_amount < minimum_cashout:
		return None

	payout = frappe.get_doc(
		{
			"doctype": "Affiliate Payout",
			"affiliate": affiliate_name,
			"bill_no": generate_unique_bill_no(),
			"generated_date": today(),
			"period_start": period_start,
			"period_end": period_end,
			"currency": currency,
			"amount": total_amount,
			"status": "Pending",
			"payment_method": "Bank Transfer",
			"commissions": [{"commission": c.name} for c in commissions],
		}
	)
	payout.insert(ignore_permissions=True)

	return payout.name


@frappe.whitelist()
def mark_payout_paid(payout_name: str) -> dict:
	"""Admin-only action confirming a payout has actually been
	transferred. Just flips the status and saves - Affiliate Payout's
	own on_update() takes care of marking every linked Commission as
	Paid and refreshing the affiliate's cached totals, the same as it
	would if an admin instead made this change by editing the Status
	field directly on the Payout form in Desk.
	"""
	payout = frappe.get_doc("Affiliate Payout", payout_name)

	if payout.status == "Paid":
		frappe.throw(_("This payout is already marked as paid."))

	commission_count = len(payout.commissions)

	payout.status = "Paid"
	payout.save(ignore_permissions=True)

	return {"paid": True, "payout": payout.name, "commissions_updated": commission_count}
