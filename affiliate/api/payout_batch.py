from datetime import date

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, get_first_day, get_last_day, getdate, nowdate, today

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


@frappe.whitelist()
def check_and_regenerate_payout(commission: str) -> dict:
	"""Desk action behind the "Check & Regenerate Payout" button on the
	Affiliate Commission form: bring ONE affiliate's commission statuses
	and payouts fully up to date, in two steps.

	1. Re-check: re-runs the same SO/SI status sync the doc_events hooks
	   perform (commission_sync.resync_commissions_for_affiliate) over
	   every non-reversal commission of the commission's affiliate - so
	   approval thresholds, cancellations/returns and clawbacks are all
	   re-evaluated against the LIVE Sales Order / Sales Invoice statuses
	   even if a hook firing was missed.
	2. Regenerate: sweeps the freshly-approved commissions that are not
	   attached to any payout into payouts - per currency, one payout per
	   currency. Commissions already attached to a payout are excluded by
	   the shared eligibility helper, and Affiliate Payout.validate()
	   hard-blocks a commission landing in two payouts, so this can never
	   double-pay. If the affiliate already has an open (Pending/
	   Processing) payout in a currency, its not-yet-attached eligible
	   commissions are TOPPED UP into that payout instead of creating a
	   parallel one - keeping at most one open payout per affiliate per
	   currency.

	Returns a summary dict for the confirmation dialog: status changes
	made, payouts created/topped up, currencies still below the minimum
	cashout, and the final no-double-payout audit.
	"""
	frappe.only_for("System Manager")

	affiliate = frappe.db.get_value("Affiliate Commission", commission, "affiliate")
	if not affiliate:
		frappe.throw(_("Commission {0} not found.").format(commission))

	# 1) Re-check linked Sales Order / Sales Invoice statuses first - the
	#    regen below must sweep based on freshly-resolved statuses, not
	#    whatever the hooks last left behind.
	from affiliate.api.commission_sync import resync_commissions_for_affiliate

	status_changes = resync_commissions_for_affiliate(affiliate)

	# 2) Regenerate payouts from everything that is now eligible.
	created, updated, below_minimum_cashout = _regenerate_payouts_for_affiliate(
		affiliate,
		minimum_cashout=frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0,
	)

	return {
		"affiliate": affiliate,
		"status_changes": status_changes,
		"payouts_created": created,
		"payouts_updated": updated,
		"below_minimum_cashout": below_minimum_cashout,
		"still_eligible": len(get_unpaid_out_commissions_by_currency(affiliate)),
		"double_payout_check": _audit_no_double_payout(affiliate),
	}


@frappe.whitelist()
def check_and_regenerate_all_payouts() -> dict:
	"""Desk action behind the "Check & Regenerate All Payouts" button on
	Affiliate Settings - the site-wide version of
	check_and_regenerate_payout.

	Re-checks EVERY affiliate's commission statuses against their live
	Sales Orders / Sales Invoices, then regenerates payouts from every
	not-yet-paid-out eligible commission across all affiliates (Monthly
	and Manual Claim alike) - one payout per affiliate per currency,
	open payouts topped up rather than paralleled.

	The same anti-double-payout guarantees apply: attached commissions
	are never swept again, and Affiliate Payout.validate() hard-blocks
	a commission landing in two payouts.
	"""
	frappe.only_for("System Manager")

	from affiliate.api.commission_sync import resync_commissions_for_affiliate

	affiliates = _affiliates_with_commissions()

	# 1) Site-wide status re-check first, for the same reason as the
	#    per-commission action: sweep on freshly-resolved statuses.
	status_changes = []
	for affiliate in affiliates:
		status_changes.extend(resync_commissions_for_affiliate(affiliate))

	# 2) Regenerate for everyone actually holding eligible commissions.
	minimum_cashout = frappe.db.get_single_value("Affiliate Settings", "minimum_cashout") or 0
	created = []
	updated = []
	below_minimum_cashout = []
	for affiliate in affiliates_with_approved_commissions():
		a_created, a_updated, a_below = _regenerate_payouts_for_affiliate(
			affiliate, minimum_cashout=minimum_cashout
		)
		created.extend(a_created)
		updated.extend(a_updated)
		# Site-wide summary: identify whose currency fell short.
		below_minimum_cashout.extend(f"{affiliate} ({currency})" for currency in a_below)

	return {
		"affiliates_processed": len(affiliates),
		"status_changes": status_changes,
		"payouts_created": created,
		"payouts_updated": updated,
		"below_minimum_cashout": below_minimum_cashout,
		"still_eligible": _count_eligible_commissions_site_wide(),
		"double_payout_check": _audit_no_double_payout(),
	}


def _affiliates_with_commissions() -> list[str]:
	"""Every affiliate holding at least one non-reversal commission - the
	population the site-wide status re-check sweeps. Module-level so
	tests can stub it down to their own fixtures instead of touching a
	live site's real commissions.
	"""
	return frappe.get_all(
		"Affiliate Commission",
		filters={"is_reversal": 0},
		fields=["affiliate"],
		distinct=True,
		pluck="affiliate",
	)


def _regenerate_payouts_for_affiliate(
	affiliate: str, minimum_cashout: float
) -> tuple[list[str], list[str], list[str]]:
	"""Sweeps one affiliate's currently-eligible commissions into
	payouts, per currency: an open (Pending/Processing) payout in a
	currency is topped up with the not-yet-attached commissions, a
	currency with none gets a new payout when it clears the minimum
	cashout. Returns (created, updated, below_minimum_cashout).
	"""
	source = (
		PAYOUT_SOURCE_MONTHLY
		if resolve_payout_mode(frappe.db.get_value("Affiliate Profile", affiliate, "payout_mode"))
		== PAYOUT_MODE_MONTHLY
		else PAYOUT_SOURCE_MANUAL
	)

	created = []
	updated = []
	below_minimum_cashout = []
	for currency in sorted(get_unpaid_out_commissions_by_currency(affiliate)):
		open_payout = frappe.get_all(
			"Affiliate Payout",
			filters={
				"affiliate": affiliate,
				"currency": currency,
				"status": ["in", ["Pending", "Processing"]],
			},
			limit=1,
		)
		if open_payout:
			if _top_up_payout(open_payout[0].name):
				updated.append(open_payout[0].name)
			continue

		payout_name = generate_payout_batch_for_affiliate(
			affiliate,
			minimum_cashout=minimum_cashout,
			currency=currency,
			source=source,
		)
		if payout_name:
			created.append(payout_name)
		else:
			# Eligibility said there WERE commissions in this currency, so
			# the only reason generate_... returns None is the threshold.
			below_minimum_cashout.append(currency)

	return created, updated, below_minimum_cashout


def _count_eligible_commissions_site_wide() -> int:
	"""Approved, non-reversal commissions still not attached to any
	payout, across all affiliates - what a further run could still act
	on (below-threshold currencies, currencies with an open payout).
	"""
	return cint(
		frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabAffiliate Commission` c
			   WHERE c.status = 'Approved' AND c.is_reversal = 0
			     AND NOT EXISTS (
			         SELECT 1 FROM `tabAffiliate Payout Commission` pc
			         WHERE pc.commission = c.name
			     )"""
		)[0][0]
	)


def _top_up_payout(payout_name: str) -> bool:
	"""Adds every of the payout's affiliate's not-yet-attached eligible
	commissions IN THE PAYOUT'S CURRENCY to its open (Pending/Processing)
	payout and refreshes the amount, instead of creating a second
	concurrent payout for the same money pool. Returns True when rows
	were added.

	Eligibility already excludes commissions attached to ANY payout, and
	the payout's validate() would reject a duplicate anyway, so this can
	never attach the same commission twice.
	"""
	payout = frappe.get_doc("Affiliate Payout", payout_name)
	attached = {row.commission for row in payout.commissions}
	new_commissions = [
		c.name
		for c in get_unpaid_out_commissions_by_currency(payout.affiliate).get(payout.currency, [])
		if c.name not in attached
	]
	if not new_commissions:
		return False

	for commission_name in new_commissions:
		payout.append("commissions", {"commission": commission_name})

	# The amount is always the sum of the child rows' commissions - the
	# child table stores links only, so the amounts come from the DB.
	amounts = frappe.get_all(
		"Affiliate Commission",
		filters={"name": ["in", [row.commission for row in payout.commissions]]},
		pluck="commission_amount",
	)
	payout.amount = flt(sum(amounts or []))

	payout.save(ignore_permissions=True)
	return True


def _audit_no_double_payout(affiliate: str | None = None) -> list[str]:
	"""Final integrity check for the regenerate action: any commission
	that ended up attached to more than one payout row would be listed
	here. Empty list = no commission can be paid twice. Pass an
	affiliate to scope the check to their commissions; None audits the
	whole site.
	(A second line of defence - Affiliate Payout.validate() already
	blocks the duplicates this would look for.)
	"""
	if affiliate:
		return frappe.db.sql(
			"""SELECT pc.commission
			   FROM `tabAffiliate Payout Commission` pc
			   JOIN `tabAffiliate Commission` c ON c.name = pc.commission
			   WHERE c.affiliate = %s
			   GROUP BY pc.commission HAVING COUNT(*) > 1""",
			(affiliate,),
			pluck=True,
		)
	return frappe.db.sql(
		"""SELECT pc.commission
		   FROM `tabAffiliate Payout Commission` pc
		   GROUP BY pc.commission HAVING COUNT(*) > 1""",
		pluck=True,
	)
