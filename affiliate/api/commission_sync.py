"""
Keeps Affiliate Commission.status in sync with the status of its linked
Sales Order / Sales Invoice - fully automatic, no manual admin approval
step. Wired from hooks.py as doc_events on Sales Order and Sales Invoice
(on_update, whichever fires a status change).

Priority rule: if an Affiliate Commission has a sales_invoice set, the
Sales Invoice's status mapping wins over the Sales Order's - an invoice
reflects more progress than the order alone.

Lifecycle (five stages, matching Affiliate Commission's status options):
  Pending   - Sales Order exists, no Sales Invoice yet, and the SO
              hasn't reached the admin-configured approval status.
  Invoiced  - Sales Invoice exists, but hasn't reached the
              admin-configured approval status yet (e.g. still unpaid).
              A confirmed sale, but not yet money RareCruise has
              actually collected.
  Approved  - the Sales Order (before any invoice exists) or Sales
              Invoice has reached the status an admin configured in
              Affiliate Settings (approve_commission_on_so_status /
              approve_commission_on_si_status) as the point commission
              is considered earned. This is the first point at which
              commission is eligible for the affiliate to request
              payout (see portal_api.request_payout /
              get_unpaid_out_commissions).
  Paid      - the affiliate has actually been paid out for this
              commission (via payout_batch.mark_payout_paid). Never set
              by this module - only by the payout flow itself.
  Denied    - the underlying Sales Order/Invoice was cancelled,
              returned, or otherwise voided. This is ALWAYS Denied
              regardless of any admin setting - there's no configurable
              threshold for it, because there's no legitimate business
              case where a cancelled or returned sale should count as
              earned commission. Can be reached from any earlier stage.
"""

import frappe

# These are hardcoded, not admin-configurable, deliberately: a cancelled
# or returned sale should never count as earned commission no matter
# what approval threshold an admin has configured elsewhere.
SO_ALWAYS_DENIED = {"Cancelled", "Closed"}
SI_ALWAYS_DENIED = {"Cancelled", "Return", "Credit Note Issued"}

# Ordered so we can tell whether a given status has "reached" the
# admin-configured approval threshold yet, not just whether it exactly
# matches it (e.g. if the admin sets the SO threshold to "To Bill", a
# later status like "Completed" should still count as approved, not
# silently stay Pending because it doesn't match verbatim).
SO_STATUS_ORDER = ["Draft", "On Hold", "To Deliver and Bill", "To Bill", "To Deliver", "Completed"]
SI_STATUS_ORDER = [
	"Draft",
	"Submitted",
	"Unpaid",
	"Partly Paid",
	"Overdue",
	"Unpaid and Discounted",
	"Overdue and Discounted",
	"Paid",
]


def _resolve_so_status(so_status: str) -> str | None:
	"""Maps a Sales Order status to a commission status, using the
	admin-configured threshold from Affiliate Settings rather than a
	hardcoded value. Returns None if the status isn't recognized at all
	(so the caller can leave the commission's status untouched).
	"""
	if so_status in SO_ALWAYS_DENIED:
		return "Denied"

	if so_status not in SO_STATUS_ORDER:
		return None

	threshold = frappe.db.get_single_value(
		"Affiliate Settings", "approve_commission_on_so_status"
	) or "Completed"

	if threshold not in SO_STATUS_ORDER:
		threshold = "Completed"

	if SO_STATUS_ORDER.index(so_status) >= SO_STATUS_ORDER.index(threshold):
		return "Approved"

	return "Pending"


def _resolve_si_status(si_status: str) -> str | None:
	"""Same idea as _resolve_so_status, but for Sales Invoice status
	against Affiliate Settings.approve_commission_on_si_status.
	"""
	if si_status in SI_ALWAYS_DENIED:
		return "Denied"

	if si_status not in SI_STATUS_ORDER:
		return None

	threshold = frappe.db.get_single_value(
		"Affiliate Settings", "approve_commission_on_si_status"
	) or "Paid"

	if threshold not in SI_STATUS_ORDER:
		threshold = "Paid"

	if SI_STATUS_ORDER.index(si_status) >= SI_STATUS_ORDER.index(threshold):
		return "Approved"

	return "Invoiced"


def create_commission_if_eligible(doc, method=None):
	"""Creates the initial Affiliate Commission row the first time a
	submitted Sales Order with a sales_partner is saved. Guarded
	against duplicates - safe to call on every save. This is the
	create-side counterpart to sync_from_sales_order/
	sync_from_sales_invoice below, which only ever UPDATE an
	already-existing row.

	Requires docstatus == 1 (submitted) - NOT just the presence of
	sales_partner - because on_update fires on every save, including
	the very first save of a brand new Draft (docstatus=0) order. A
	Sales Order can be created, edited multiple times, and abandoned or
	deleted entirely without ever being submitted (e.g. a quotation
	being worked out with a customer) - creating a commission that
	early would leave an orphaned record blocking that draft's deletion
	via Frappe's Link integrity check, for a "sale" that never actually
	happened.
	"""
	if doc.docstatus != 1:
		return

	if not doc.sales_partner:
		return

	if frappe.db.exists("Affiliate Commission", {"sales_order": doc.name}):
		return

	affiliate_name = frappe.db.get_value(
		"Affiliate Profile", {"sales_partner": doc.sales_partner}, "name"
	)
	if not affiliate_name:
		# A Sales Order can have a sales_partner that isn't one of ours
		# (e.g. set directly in Desk, unrelated to this affiliate system)
		# - nothing to create in that case.
		return

	initial_status = _resolve_so_status(doc.status) or "Pending"

	frappe.get_doc(
		{
			"doctype": "Affiliate Commission",
			"affiliate": affiliate_name,
			"sales_order": doc.name,
			"commission_amount": doc.total_commission or 0,
			"status": initial_status,
		}
	).insert(ignore_permissions=True)

	if initial_status in ("Approved", "Paid"):
		# Only Approved/Paid count towards the cached totals - most new
		# commissions start Pending, so this is usually a no-op, but a
		# Sales Order that's already past the approval threshold the
		# first time it's seen (e.g. imported/backfilled data) should
		# reflect immediately.
		update_affiliate_cached_totals(affiliate_name)


def sync_from_sales_order(doc, method=None):
	commission_name = frappe.db.get_value(
		"Affiliate Commission", {"sales_order": doc.name}, "name"
	)
	if not commission_name:
		return

	# If this commission already has a Sales Invoice linked, the SI status
	# takes priority - don't let a Sales Order status change downgrade it.
	sales_invoice = frappe.db.get_value(
		"Affiliate Commission", commission_name, "sales_invoice"
	)
	if sales_invoice:
		return

	new_status = _resolve_so_status(doc.status)
	if not new_status:
		return

	_update_commission_status(commission_name, new_status)


def sync_from_sales_invoice(doc, method=None):
	sales_order_name = _linked_sales_order(doc)
	if not sales_order_name:
		return

	commission_name = frappe.db.get_value(
		"Affiliate Commission", {"sales_order": sales_order_name}, "name"
	)
	if not commission_name:
		return

	_upsert_commission_invoice_row(commission_name, doc)

	new_status = _resolve_commission_invoice_status(commission_name)
	if not new_status:
		return

	_update_commission_status(commission_name, new_status)


def _upsert_commission_invoice_row(commission_name: str, sales_invoice_doc):
	"""Adds this Sales Invoice to the commission's sales_invoices child
	table if it isn't already there, or refreshes its recorded
	amount/status if it is. One Sales Order can be billed across
	several Sales Invoices (e.g. a deposit invoice and a balance
	invoice) - this table is what tracks all of them, rather than the
	single sales_invoice Link field (kept only for backward-compatible
	display of the first invoice) which could only ever point at one.
	"""
	commission = frappe.get_doc("Affiliate Commission", commission_name)

	existing_row = next(
		(r for r in commission.sales_invoices if r.sales_invoice == sales_invoice_doc.name),
		None,
	)

	if existing_row:
		existing_row.invoice_amount = sales_invoice_doc.grand_total
		existing_row.invoice_status = sales_invoice_doc.status
	else:
		commission.append(
			"sales_invoices",
			{
				"sales_invoice": sales_invoice_doc.name,
				"invoice_amount": sales_invoice_doc.grand_total,
				"invoice_status": sales_invoice_doc.status,
			},
		)
		if not commission.sales_invoice:
			# Backward-compat display field - only set on the first
			# invoice, never overwritten by later ones.
			commission.sales_invoice = sales_invoice_doc.name

	commission.save(ignore_permissions=True)


def _resolve_commission_invoice_status(commission_name: str) -> str | None:
	"""Resolves overall commission status from ALL Sales Invoices
	attached to it (not just the most recently updated one), for the
	installment billing case where a single Sales Order is billed
	across multiple invoices:

	- If ANY attached invoice is Cancelled/Returned/Credit-noted, the
	  whole commission is Denied - the safest choice, since a partial
	  refund on one installment calls the entire sale's completeness
	  into question, and there's no partial-approval scheme here (see
	  the "all invoices must be fully Paid" decision below).
	- Approved only once EVERY attached invoice has reached the
	  admin-configured approval threshold (normally "Paid") AND the
	  invoices attached so far add up to the full Sales Order value.
	  The amount check matters because, with installment billing (e.g.
	  a deposit invoice now, a balance invoice later), the system has
	  no way to know a further invoice is still expected just from
	  looking at the invoices that exist right now - without this
	  check, a fully-paid deposit alone would incorrectly mark the
	  whole commission Approved before the balance invoice has even
	  been raised.
	- Otherwise, Invoiced - at least one invoice exists but the order
	  isn't fully invoiced-and-paid yet.
	"""
	commission = frappe.get_doc("Affiliate Commission", commission_name)
	if not commission.sales_invoices:
		return None

	resolved_statuses = [
		_resolve_si_status(row.invoice_status) for row in commission.sales_invoices
	]

	if any(status == "Denied" for status in resolved_statuses):
		return "Denied"

	all_reached_threshold = all(status == "Approved" for status in resolved_statuses)
	if all_reached_threshold and _invoices_cover_full_order(commission):
		return "Approved"

	return "Invoiced"


def _invoices_cover_full_order(commission) -> bool:
	"""True once the invoice amounts attached to this commission add up
	to at least the linked Sales Order's grand_total - i.e. nothing is
	still awaiting a further invoice (e.g. a balance payment yet to be
	billed). Comparison is rounded to 2dp to avoid floating point
	amounts (e.g. from tax rounding) causing an endless "just barely
	short" state that never resolves.
	"""
	if not commission.sales_order:
		return True

	order_total = frappe.db.get_value("Sales Order", commission.sales_order, "grand_total") or 0
	invoiced_total = sum(row.invoice_amount or 0 for row in commission.sales_invoices)

	return round(invoiced_total, 2) >= round(order_total, 2)


def sync_from_payment_entry(doc, method=None):
	"""Re-checks the status of every Sales Invoice a Payment Entry
	references, the moment that Payment Entry is submitted or
	cancelled. Needed because ERPNext updates Sales Invoice.status
	internally as a side effect of submitting/cancelling a payment
	(via erpnext's status_updater, not a normal document save) - that
	internal update doesn't reliably re-fire Sales Invoice's own
	on_update the same way an ordinary save does, so a commission could
	otherwise stay stuck on "Invoiced" even after the customer has
	actually paid in full.

	We re-fetch each invoice's status fresh from the DB (rather than
	trusting anything on the Payment Entry doc itself, which doesn't
	carry the invoice's status) and reuse the exact same
	sync_from_sales_invoice logic a direct invoice update would trigger.
	"""
	for reference in doc.get("references") or []:
		if reference.reference_doctype != "Sales Invoice":
			continue

		sales_invoice = frappe.get_doc("Sales Invoice", reference.reference_name)
		sync_from_sales_invoice(sales_invoice)


def _linked_sales_order(sales_invoice_doc) -> str | None:
	# A Sales Invoice can originate from a Sales Order via its items'
	# `sales_order` field (standard ERPNext "Items" table reference).
	for item in sales_invoice_doc.get("items") or []:
		if item.get("sales_order"):
			return item.sales_order
	return None


def _update_commission_status(commission_name: str, new_status: str):
	current_status = frappe.db.get_value(
		"Affiliate Commission", commission_name, "status"
	)

	if current_status == "Paid":
		# Once a commission is Paid, the affiliate has actually received
		# this money into their bank account via a completed Payout -
		# that's a real-world fact, not just a status label, and sync
		# logic must never silently overwrite it. If the underlying
		# Sales Order/Invoice is later cancelled or returned (e.g. a
		# late chargeback or refund), that's a clawback situation an
		# admin needs to consciously handle themselves (e.g. deducting
		# it from a future payout, or contacting the affiliate directly)
		# - not something this sync should quietly erase the record of
		# by flipping "Paid" back to "Denied" as if the payment never
		# happened.
		return

	affiliate_name = frappe.db.get_value(
		"Affiliate Commission", commission_name, "affiliate"
	)

	if current_status != new_status:
		frappe.db.set_value("Affiliate Commission", commission_name, "status", new_status)

	if affiliate_name:
		update_affiliate_cached_totals(affiliate_name)


def update_affiliate_cached_totals(affiliate_name: str):
	"""Recalculates and stores total_sales/total_commission/available_balance
	on the given Affiliate Profile. Only Approved and Paid commissions count
	towards these totals - Pending (not yet earned) and Denied (rejected)
	are excluded, so the figures only reflect commission the affiliate is
	actually entitled to.
	"""
	rows = frappe.get_all(
		"Affiliate Commission",
		filters={"affiliate": affiliate_name, "status": ["in", ["Approved", "Paid"]]},
		fields=["sales_order", "commission_amount"],
	)

	total_commission = sum(r.commission_amount or 0 for r in rows)

	total_sales = 0
	for r in rows:
		if r.sales_order:
			total_sales += frappe.db.get_value("Sales Order", r.sales_order, "grand_total") or 0

	paid_out = frappe.db.sql(
		"""SELECT COALESCE(SUM(amount), 0)
		   FROM `tabAffiliate Payout`
		   WHERE affiliate = %s AND status = 'Paid'""",
		(affiliate_name,),
	)[0][0]

	available_balance = total_commission - paid_out

	frappe.db.set_value(
		"Affiliate Profile",
		affiliate_name,
		{
			"total_sales": total_sales,
			"total_commission": total_commission,
			"available_balance": available_balance,
		},
	)


def generate_unique_bill_no() -> str:
	"""Generate a "PAY-YYYY-NNNN" bill number.

	Affiliate Payout's autoname is "field:bill_no" - the document's
	name IS its bill_no, rather than the two being separate (previously
	a payout had an opaque hash name like "9b67vpjgne" and a
	human-readable bill_no like "PAY-2026-0009" that were easy to
	confuse when looking a specific payout up). That means bill_no must
	be set to something BEFORE insert() is called, on every code path
	that creates a new Payout - both the affiliate self-request flow
	(portal_api.request_payout) and the admin/scheduled batch flow
	(payout_batch.generate_payout_batch_for_affiliate) call this same
	function, so a payout can never be created with an empty bill_no
	regardless of which flow created it.
	"""
	year = frappe.utils.nowdate().split("-")[0]
	prefix = f"PAY-{year}-"

	last = frappe.db.sql(
		"""
		SELECT name FROM `tabAffiliate Payout`
		WHERE name LIKE %s
		ORDER BY name DESC LIMIT 1
		""",
		(prefix + "%",),
	)

	if last and last[0][0]:
		try:
			last_seq = int(last[0][0].split("-")[-1])
		except ValueError:
			last_seq = 0
	else:
		last_seq = 0

	return f"{prefix}{last_seq + 1:04d}"


def get_unpaid_out_commissions(affiliate: str) -> list:
	"""Approved commissions for this affiliate that haven't been
	attached to any Affiliate Payout yet (via the Affiliate Payout
	Commission child table).

	This is the SINGLE SOURCE OF TRUTH for what "eligible for payout"
	means - both the affiliate-initiated self-request flow
	(portal_api.request_payout) and the admin/scheduled batch flow
	(payout_batch.generate_payout_batch) call this same function.
	Previously they each had their own logic for this: the portal only
	counted commissions not already in a Payout, while the batch job
	counted ALL Approved commissions regardless of Payout membership.
	That mismatch meant an affiliate who self-requested a payout could
	have the *same* commissions swept into a second payout by the next
	admin batch run - a real double-payment risk. Having one function
	that both call means there's exactly one definition of "already
	spoken for" and it can't drift out of sync between the two flows.

	Pending commissions aren't included here because they haven't been
	approved yet, and commissions already sitting in a Payout
	(Pending/Processing/Paid) are excluded so the same money can't be
	requested or batched twice.
	"""
	approved = frappe.get_all(
		"Affiliate Commission",
		filters={"affiliate": affiliate, "status": "Approved"},
		fields=["name", "commission_amount"],
	)
	if not approved:
		return []

	already_in_payout = set(
		frappe.get_all(
			"Affiliate Payout Commission",
			filters={"commission": ["in", [c.name for c in approved]]},
			pluck="commission",
		)
	)

	return [c for c in approved if c.name not in already_in_payout]