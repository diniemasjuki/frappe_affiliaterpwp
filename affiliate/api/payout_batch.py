import frappe
from frappe import _
from frappe.utils import today

from affiliate.api.commission_sync import get_unpaid_out_commissions, generate_unique_bill_no


def generate_payout_batch(period_start: str | None = None, period_end: str | None = None):
	"""Groups all unpaid-out Approved commissions (across all eligible
	affiliates) into one Payout per affiliate. Safe to run repeatedly -
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
		payout_name = generate_payout_batch_for_affiliate(
			affiliate_name,
			period_start=period_start,
			period_end=period_end,
			minimum_cashout=minimum_cashout,
		)
		if payout_name:
			created.append(payout_name)

	return created


def generate_payout_batch_for_affiliate(
	affiliate_name: str,
	period_start: str | None = None,
	period_end: str | None = None,
	minimum_cashout: float | None = None,
):
	"""Creates a single Affiliate Payout for one affiliate's currently
	unpaid-out Approved commissions (i.e. Approved and not already
	attached to some other Payout - whether that other Payout came from
	a previous batch run or an affiliate's own self-request). Returns
	the new Payout's name, or None if there was nothing eligible to pay
	out or the affiliate hasn't reached the minimum cashout threshold.
	"""
	if minimum_cashout is None:
		minimum_cashout = frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0

	commissions = get_unpaid_out_commissions(affiliate_name)
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