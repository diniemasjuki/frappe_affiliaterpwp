from datetime import date

import frappe
from frappe import _
from frappe.utils import add_months, cint, get_first_day, get_last_day, getdate, nowdate, today

from affiliate.api.commission_sync import generate_unique_bill_no, get_unpaid_out_commissions_by_currency
from affiliate.api.currency_exchange import get_company_currency

# How a payout came into existence - stored on Affiliate Payout.source so
# admins can filter the monthly batch's output from the affiliates' own
# portal requests.
PAYOUT_SOURCE_MANUAL = "Manual Claim"
PAYOUT_SOURCE_MONTHLY = "Monthly Batch"

PAYOUT_MODE_MANUAL = "Manual Claim"
PAYOUT_MODE_MONTHLY = "Monthly"


def monthly_affiliates() -> set[str]:
	"""Affiliates whose payout mode is Monthly - the only ones the
	monthly batch generates payouts for. Everyone else claims manually
	from the portal.
	"""
	return set(
		frappe.get_all(
			"Affiliate Profile",
			filters={"payout_mode": PAYOUT_MODE_MONTHLY},
			pluck="name",
		)
	)


def resolve_payout_mode(payout_mode: str | None) -> str:
	"""Normalizes a possibly-empty payout mode to a valid one. Profiles
	created before the payout mode existed (and any future bad value)
	treat as Manual Claim - the pre-monthly-batch behaviour.
	"""
	return payout_mode if payout_mode in (PAYOUT_MODE_MANUAL, PAYOUT_MODE_MONTHLY) else PAYOUT_MODE_MANUAL


def affiliates_with_approved_commissions() -> list[str]:
	"""Affiliates holding at least one Approved, unpaid-out commission -
	the candidate pool a payout batch sweeps. Module-level so tests can
	stub the pool instead of sweeping a live site's real affiliates.
	"""
	return frappe.get_all(
		"Affiliate Commission",
		filters={"status": "Approved"},
		fields=["affiliate"],
		distinct=True,
		pluck="affiliate",
	)


def generate_payout_batch(
	period_start: str | None = None,
	period_end: str | None = None,
	source: str | None = None,
	monthly_mode_only: bool = False,
):
	"""Groups all unpaid-out Approved commissions (across all eligible
	affiliates) into Payouts - one per affiliate PER CURRENCY, since a
	payout can only ever combine commissions of a single currency (an
	affiliate with Approved commissions in both MYR and SGD gets two
	payouts, not one mixed-currency number). Safe to run repeatedly -
	an affiliate with no eligible commissions, or below the minimum
	cashout threshold, simply gets no payout this run. Also safe to run
	alongside affiliates self-requesting payouts from the portal -
	commissions already claimed by a self-request are excluded here.

	`monthly_mode_only` restricts the run to affiliates whose payout
	mode is Monthly (the scheduled monthly batch) - such a run is always
	stamped with the Monthly Batch source unless `source` overrides it;
	an unrestricted run stamps Manual Claim.
	"""
	minimum_cashout = frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0
	source = source or (PAYOUT_SOURCE_MONTHLY if monthly_mode_only else PAYOUT_SOURCE_MANUAL)
	eligible_affiliates = monthly_affiliates() if monthly_mode_only else None

	created = []
	for affiliate_name in affiliates_with_approved_commissions():
		if eligible_affiliates is not None and affiliate_name not in eligible_affiliates:
			continue
		for currency in sorted(get_unpaid_out_commissions_by_currency(affiliate_name)):
			payout_name = generate_payout_batch_for_affiliate(
				affiliate_name,
				period_start=period_start,
				period_end=period_end,
				minimum_cashout=minimum_cashout,
				currency=currency,
				source=source,
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
	source: str = PAYOUT_SOURCE_MANUAL,
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
			"source": source,
			"commissions": [{"commission": c.name} for c in commissions],
		}
	)
	payout.insert(ignore_permissions=True)

	return payout.name


def scheduled_payout_date(reference: str | date, payout_day: int) -> date:
	"""The date `payout_day` falls on in `reference`'s month, clamped to
	the month's last day (a configured day of 28+ must not skip short
	months - validate() caps the setting at 28, this clamp keeps the
	math safe anyway).
	"""
	reference = getdate(reference)
	return reference.replace(day=min(payout_day, get_last_day(reference).day))


def scheduled_payout_date_on_or_before(reference: str | date, payout_day: int) -> date:
	"""The most recent scheduled payout date on or before `reference`:
	this month's payout day when it has arrived, otherwise last month's.
	This (not simply "day matches") is what makes the monthly batch
	catch up after missed days - downtime on the 5th still produces the
	batch on the 6th.
	"""
	reference = getdate(reference)
	candidate = scheduled_payout_date(reference, payout_day)
	if reference < candidate:
		candidate = scheduled_payout_date(add_months(reference, -1), payout_day)
	return candidate


def get_monthly_payout_day() -> int:
	"""The configured Monthly Payout Day, or 0 when monthly payouts are
	disabled (unset, or out of the 1-28 range).
	"""
	payout_day = cint(frappe.db.get_single_value("Affiliate Settings", "monthly_payout_day"))
	return payout_day if 1 <= payout_day <= 28 else 0


def next_monthly_payout_date(reference: str | date | None = None) -> str | None:
	"""When an affiliate on Monthly mode can expect their payout:
	the current period's scheduled date if its batch hasn't run yet
	(including pending catch-up after downtime), otherwise next month's
	date. None when monthly payouts are disabled.
	"""
	payout_day = get_monthly_payout_day()
	if not payout_day:
		return None

	reference = getdate(reference or nowdate())
	due = scheduled_payout_date_on_or_before(reference, payout_day)
	last_run = getdate(frappe.db.get_single_value("Affiliate Settings", "last_monthly_batch_run") or "1900-01-01")

	if last_run < due:
		return due.isoformat()

	return scheduled_payout_date(add_months(due, 1), payout_day).isoformat()


def run_monthly_payout_batch_if_due() -> list[str]:
	"""Daily scheduler entry: generates the monthly payout batch for
	affiliates on the Monthly payout mode.

	Runs when the scheduled date has arrived AND this period's batch
	hasn't run yet - tracked via Affiliate Settings.last_monthly_batch_run
	so each period only ever produces one batch, even when the batch
	creates zero payouts (no eligible commissions) or runs as a catch-up
	days after the scheduled date. Affiliates on Manual Claim are never
	touched; they request payouts themselves from the portal.

	Period is labelled as the calendar month before the scheduled date's
	month - a payout on the 5th of October pays September's commissions.
	The payout's commission rows are the actual truth; the period fields
	are informational only.
	"""
	payout_day = get_monthly_payout_day()
	if not payout_day:
		return []

	d = getdate(nowdate())
	due = scheduled_payout_date_on_or_before(d, payout_day)
	last_run = getdate(frappe.db.get_single_value("Affiliate Settings", "last_monthly_batch_run") or "1900-01-01")
	if last_run >= due:
		return []

	previous_month = add_months(due, -1)
	created = generate_payout_batch(
		period_start=get_first_day(previous_month),
		period_end=get_last_day(previous_month),
		source=PAYOUT_SOURCE_MONTHLY,
		monthly_mode_only=True,
	)

	# Marked even when the batch created nothing - the point is "this
	# period has been processed", not "payouts exist".
	frappe.db.set_single_value("Affiliate Settings", "last_monthly_batch_run", d)

	if created:
		_notify_admins_of_batch(created)

	return created


def _notify_admins_of_batch(payout_names: list[str]):
	"""Pings System Managers with a Notification Log when the monthly
	batch creates payouts - the generated list needs an admin to move it
	through Processing -> Paid, and nothing else points them at it.
	Notification failures must never break the batch itself.
	"""
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		admins = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			pluck="parent",
			distinct=True,
		)
		admins = [u for u in admins if u != "Administrator" and frappe.db.get_value("User", u, "enabled")]
		admins = admins or ["Administrator"]

		total = frappe.db.get_all(
			"Affiliate Payout",
			filters={"name": ["in", payout_names]},
			fields=["sum(amount) as amount"],
		)[0].amount or 0

		enqueue_create_notification(
			admins,
			{
				"type": "Alert",
				"subject": _("Monthly payout batch: {0} payout(s) totalling {1} awaiting processing").format(
					len(payout_names), frappe.utils.fmt_money(total)
				),
				"document_type": "Affiliate Payout",
				"document_name": payout_names[0],
			},
		)
	except Exception:
		frappe.log_error(
			title=_("Monthly payout batch notification failed"),
			message=frappe.get_traceback(),
		)


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
