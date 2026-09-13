import re

import frappe
from frappe import _

from affiliate.api.commission_sync import (
	generate_unique_bill_no,
	get_unpaid_out_commissions_by_currency,
	update_affiliate_cached_totals,
)
from affiliate.api.currency_exchange import (
	currency_symbol,
	get_company_currency,
	get_exchange_rate,
)


def _currency_symbols(currencies: list) -> dict:
	"""Symbol map for every currency an API response mentions, so the
	portal can prefix amounts with the right symbol instead of a
	hardcoded "RM".
	"""
	out = {}
	for currency in currencies:
		if currency and currency not in out:
			out[currency] = currency_symbol(currency)
	return out


def _default_display_currency(profile) -> str:
	"""The affiliate's chosen display currency, falling back to the
	site's company currency when they haven't picked one.
	"""
	return profile.default_currency or get_company_currency()


def _estimate(by_currency: dict, target: str):
	"""Approximate total of {currency: amount} converted into `target`
	using display-only exchange rates. Returns None when ANY component
	rate is missing - a partially-converted number presented as an
	estimate would be a wrong number, so the whole estimate is hidden
	instead.
	"""
	total = 0.0
	for currency, amount in by_currency.items():
		rate = 1.0 if currency == target else get_exchange_rate(currency, target)
		if rate is None:
			return None
		total += amount * rate
	return frappe.utils.flt(total, 2)


@frappe.whitelist(allow_guest=True)
def get_google_login_url(redirect_to: str = "/affiliate") -> str:
	"""Returns the fully-formed Google OAuth authorize URL (client_id,
	redirect_uri, scope, and a CSRF state token all included), so the
	portal's "Sign in with Google" button can redirect straight to
	Google - instead of stopping at Frappe's own generic /login page
	first (which is otherwise required, since the state token this URL
	needs is generated server-side and can't be safely hardcoded on the
	frontend).

	Runs as Guest (not logged in yet) since this is called before
	authentication happens - that's the whole point of Sign in with
	Google.
	"""
	from frappe.utils.oauth import get_oauth2_authorize_url

	return get_oauth2_authorize_url("google", redirect_to)


def _is_phone_validation_error(exc: Exception) -> bool:
	"""Frappe's validate_phone_number_with_country_code (in
	frappe/utils/__init__.py) raises frappe.ValidationError with one of
	two different messages depending on the failure:

	- "Phone Number {number} set in field {field} is not valid." -
	  wrong length, non-numeric characters, etc.
	- "Please select a country code for field {field}." - missing
	  leading "+", or a country code that doesn't exist (e.g. "+999").

	Checking only for "Phone Number" (as we used to) misses the second
	message entirely, since it doesn't contain that phrase - so those
	failures fell through to the generic error path instead of our
	friendly one. Check for a substring common to both instead.
	"""
	msg = str(exc)
	return "Phone Number" in msg or "country code" in msg


def get_logged_in_profile(auto_create: bool = False):
	"""Returns the Affiliate Profile doc for the current session user.

	Raises frappe.PermissionError if there's no session (Guest) or if
	the session user has no linked Affiliate Profile - UNLESS
	auto_create=True, in which case a new Affiliate Profile is created
	on the spot for whoever is currently authenticated (see
	_create_profile_for_current_user for why).

	auto_create is opt-in per caller, not a global default: only
	get_dashboard_data() passes True, since loadDashboard() in the
	portal JS is always the very first call made right after ANY
	successful login (password or Google Sign-In) - that's the one
	natural point where "an authenticated person with no Affiliate
	Profile has reached this portal" should be treated as registration
	intent. Every other endpoint (wizard steps, settings, etc.) keeps
	the default False, since by the time those are called a profile
	should already exist from the dashboard-load step.

	When a profile IS freshly auto-created in this call, the returned
	doc has `.flags.newly_created = True` set - callers that care (only
	get_dashboard_data() does) can check this to tell "an existing
	Frappe User (e.g. a past travel_booking customer) just landed here
	for the first time and got silently enrolled" apart from "a
	returning affiliate who registered on purpose but hasn't finished
	the wizard yet" - the two cases look identical otherwise
	(wizard_complete=False) but need different portal UX: the former
	should see an explicit "you need to set up an affiliate profile"
	screen before the wizard, not get dropped straight into wizard
	step 1 with no explanation of why they're suddenly there.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to continue."), frappe.PermissionError)

	profile_name = frappe.db.get_value(
		"Affiliate Profile", {"user": frappe.session.user}, "name"
	)
	newly_created = False
	if not profile_name:
		if auto_create:
			profile_name = _create_profile_for_current_user()
			newly_created = True
		else:
			frappe.throw(
				_("Your account is not linked to an affiliate profile. Please contact support."),
				frappe.PermissionError,
			)

	doc = frappe.get_doc("Affiliate Profile", profile_name)
	doc.flags.newly_created = newly_created
	return doc


def _create_profile_for_current_user() -> str:
	"""Creates a new Affiliate Profile for whoever is currently logged
	in, whether they got there via the existing email/password
	registration flow (register_affiliate, which already creates a
	profile explicitly - so this path is really only exercised for
	Google Sign-In and any other already-authenticated session that
	reaches the portal without one) or Google Sign-In (Frappe's native
	Social Login Key handles the OAuth exchange and creates/logs in the
	frappe.User automatically - it has no idea an Affiliate Profile
	should also exist, since that's specific to this app).

	Deliberately does NOT restrict this to newly-created Users only -
	an existing Frappe User (e.g. a past travel_management customer, or
	staff) reaching this portal without an Affiliate Profile also gets
	one created here. That's a deliberate choice: the portal itself is
	the signal that someone wants to become an affiliate, regardless of
	what else their account may already be used for.
	"""
	user_doc = frappe.get_doc("User", frappe.session.user)
	full_name = user_doc.full_name or frappe.session.user

	# Matches register_affiliate()'s role assignment for the
	# password-registration path - keeps every affiliate consistently
	# tagged with this role regardless of which door they came in
	# through (password signup vs Google Sign-In vs any other
	# already-authenticated session landing here).
	if "Affiliate" not in {r.role for r in user_doc.roles}:
		user_doc.append("roles", {"role": "Affiliate"})
		user_doc.save(ignore_permissions=True)

	profile = frappe.get_doc(
		{
			"doctype": "Affiliate Profile",
			"user": frappe.session.user,
			"email_id": frappe.session.user,
			"full_name": full_name,
			"status": "Pending Verification",
		}
	)
	profile.insert(ignore_permissions=True)
	return profile.name


@frappe.whitelist()
def get_dashboard_data() -> dict:
	"""Single call returning everything the portal needs to render the
	dashboard: profile summary fields, whether the wizard is still
	incomplete, and the commission/payout tables.

	Currency-sensitive: every amount is returned together with its own
	currency (exact per-sale figures), plus the per-currency balances
	child table and the affiliate's preferred-display-currency
	estimates (approx.) for the summary cards.
	"""
	profile = get_logged_in_profile(auto_create=True)

	wizard_complete = bool(profile.national_id and profile.document_id)

	commissions = frappe.get_all(
		"Affiliate Commission",
		filters={"affiliate": profile.name},
		fields=["name", "sales_order", "sales_invoice", "currency", "commission_amount", "status", "creation", "is_reversal"],
		order_by="creation desc",
	)

	so_info = {}
	so_names = [c.sales_order for c in commissions if c.sales_order]
	if so_names:
		so_info = {
			so.name: so
			for so in frappe.get_all(
				"Sales Order",
				filters={"name": ["in", so_names]},
				fields=["name", "grand_total", "currency"],
			)
		}
	for c in commissions:
		so = so_info.get(c.sales_order)
		c["sales_order_amount"] = (so.grand_total or 0) if so else 0
		c["sales_order_currency"] = ((so.currency if so else None) or c.currency)

	payouts = frappe.get_all(
		"Affiliate Payout",
		filters={"affiliate": profile.name},
		fields=["name", "bill_no", "generated_date", "period_start", "period_end", "currency", "amount", "status", "payment_method"],
		order_by="generated_date desc",
	)

	default_currency = _default_display_currency(profile)
	company_currency = get_company_currency()
	balances = [
		{
			"currency": b.currency,
			"total_sales": b.total_sales,
			"total_commission": b.total_commission,
			"available_balance": b.available_balance,
		}
		for b in profile.balances
	]

	def _live_estimate(key):
		"""Approximate display-currency total recomputed from the exact
		per-currency balances at read time - the cached *_est fields on
		the profile go stale the moment exchange rates move, and this
		also lets us cleanly HIDE the estimate (None) when any rate is
		missing instead of showing a stored zero.
		"""
		return _estimate({b["currency"]: b[key] for b in balances}, default_currency)

	return {
		"profile": {
			"full_name": profile.full_name,
			"email_id": profile.email_id,
			"status": profile.status,
			"referral_code": profile.referral_code,
			"last_referral_code_change": profile.last_referral_code_change,
			"phone": profile.phone,
			"national_id": profile.national_id,
			"gender": profile.gender,
			"date_of_birth": profile.date_of_birth,
			"address": profile.address,
			"document_id": profile.document_id,
			"bank_name": profile.bank_name,
			"account_name": profile.account_name,
			"account_number": profile.account_number,
			"default_currency": profile.default_currency,
		},
		"wizard_complete": wizard_complete,
		"newly_registered": bool(profile.flags.newly_created),
		"company_currency": company_currency,
		"default_currency": default_currency,
		"currency_symbols": _currency_symbols(
			[b["currency"] for b in balances]
			+ [c.get("currency") for c in commissions]
			+ [c.get("sales_order_currency") for c in commissions]
			+ [p.get("currency") for p in payouts]
			+ [default_currency, company_currency]
		),
		# Exact per-currency totals (the single source of truth); scalar
		# totals below are company-currency approximations kept for
		# backward compatibility with older portal builds.
		"balances": balances,
		"total_sales": profile.total_sales,
		"total_commission": profile.total_commission,
		"available_balance": profile.available_balance,
		"total_sales_est": _live_estimate("total_sales"),
		"total_commission_est": _live_estimate("total_commission"),
		"available_balance_est": _live_estimate("available_balance"),
		"available_currencies": sorted(
			frappe.get_all("Currency", filters={"enabled": 1}, pluck="name")
		),
		"commissions": commissions,
		"payouts": payouts,
	}


@frappe.whitelist()
def get_leaderboard() -> dict:
	"""Top 5 verified affiliates ranked by all-time total_sales, plus
	the logged-in affiliate's own rank (even if they're outside the
	top 5). Only first names are exposed for other affiliates -
	that's a deliberate privacy choice, not an oversight, so never add
	full_name/email/phone/etc. to this endpoint's output.
	"""
	profile = get_logged_in_profile()

	top_affiliates = frappe.get_all(
		"Affiliate Profile",
		filters={"status": "Verified", "total_sales": [">", 0]},
		fields=["name", "full_name", "total_sales"],
		order_by="total_sales desc",
		limit=5,
	)

	leaderboard = [
		{
			"rank": idx + 1,
			"first_name": (row.full_name or "Affiliate").split(" ")[0],
			"total_sales": row.total_sales,
			"is_you": row.name == profile.name,
		}
		for idx, row in enumerate(top_affiliates)
	]

	your_rank = None
	if profile.status == "Verified" and profile.total_sales:
		your_rank = (
			frappe.db.count(
				"Affiliate Profile",
				filters={
					"status": "Verified",
					"total_sales": [">", profile.total_sales],
				},
			)
			+ 1
		)

	# total_sales is stored uniformly in the site's company currency
	# (approx., converted for comparability) - tell the portal which
	# currency that is so it formats amounts correctly.
	company_currency = get_company_currency()

	return {
		"leaderboard": leaderboard,
		"your_rank": your_rank,
		"your_total_sales": profile.total_sales,
		"currency": company_currency,
		"symbol": currency_symbol(company_currency),
	}


@frappe.whitelist()
def get_commission_status() -> dict:
	"""Breakdown of the affiliate's own commissions into unpaid
	(Pending + Invoiced + Approved - none of these have reached the
	affiliate's bank account yet) vs paid. Denied commissions are
	excluded entirely - they were never valid earnings, so counting
	them here would be misleading either way.

	Currency-sensitive: amounts are bucketed per commission currency
	(exact figures), with an approximate combined estimate in the
	affiliate's preferred display currency. The estimate is None when
	any exchange rate is missing - the portal hides it rather than
	showing a partially-converted number.
	"""
	profile = get_logged_in_profile()

	rows = frappe.get_all(
		"Affiliate Commission",
		filters={
			"affiliate": profile.name,
			"status": ["in", ["Pending", "Invoiced", "Approved", "Paid"]],
		},
		fields=["currency", "commission_amount", "status", "is_reversal"],
	)

	company_currency = get_company_currency()
	default_currency = _default_display_currency(profile)

	unpaid_amounts = {}
	unpaid_counts = {}
	paid_amounts = {}
	paid_counts = {}
	for r in rows:
		currency = r.currency or company_currency
		if r.status == "Paid":
			if not r.is_reversal:
				paid_amounts[currency] = paid_amounts.get(currency, 0.0) + (r.commission_amount or 0)
				paid_counts[currency] = paid_counts.get(currency, 0) + 1
		else:
			unpaid_amounts[currency] = unpaid_amounts.get(currency, 0.0) + (r.commission_amount or 0)
			unpaid_counts[currency] = unpaid_counts.get(currency, 0) + 1

	per_currency = [
		{
			"currency": currency,
			"symbol": currency_symbol(currency),
			"unpaid_amount": unpaid_amounts.get(currency, 0.0),
			"unpaid_count": unpaid_counts.get(currency, 0),
			"paid_amount": paid_amounts.get(currency, 0.0),
			"paid_count": paid_counts.get(currency, 0),
		}
		for currency in sorted(set(unpaid_amounts) | set(paid_amounts))
	]

	return {
		"default_currency": default_currency,
		"currency_symbols": _currency_symbols(
			list(unpaid_amounts) + list(paid_amounts) + [default_currency]
		),
		"per_currency": per_currency,
		"unpaid_amount_est": _estimate(unpaid_amounts, default_currency),
		"paid_amount_est": _estimate(paid_amounts, default_currency),
	}


@frappe.whitelist()
def get_performance(period: str = "week") -> dict:
	"""Orders, total sales, and commission for the affiliate, scoped to
	a period: "week" (last 7 days), "month" (last 30 days), or "all"
	(all-time). Counts commissions by their creation date, not the
	underlying Sales Order's date, since that's when the referral was
	actually recorded against this affiliate.

	Returns two separate breakdowns rather than one combined total:
	- confirmed: Approved + Paid commissions - money the affiliate can
	  rely on, matching the same definition used for the dashboard's
	  top-level Total Sales/Commission cards.
	- pending: commissions still awaiting approval - could still be
	  denied (e.g. order cancelled, refunded) before being confirmed.
	Showing these separately (rather than one blended number) means an
	affiliate is never shown a "Total Sales" figure that could shrink
	later because a Pending order got refunded or cancelled.
	"""
	profile = get_logged_in_profile()

	filters = {"affiliate": profile.name, "is_reversal": 0}
	if period == "week":
		filters["creation"] = [">=", frappe.utils.add_days(frappe.utils.nowdate(), -7)]
	elif period == "month":
		filters["creation"] = [">=", frappe.utils.add_days(frappe.utils.nowdate(), -30)]
	elif period != "all":
		frappe.throw(_("Invalid period. Use 'week', 'month', or 'all'."))

	commissions = frappe.get_all(
		"Affiliate Commission",
		filters=filters,
		fields=["sales_order", "commission_amount", "currency", "status"],
	)

	company_currency = get_company_currency()
	default_currency = _default_display_currency(profile)

	def _summarize(rows):
		sales_by_currency = {}
		commission_by_currency = {}
		for c in rows:
			currency = c.currency or company_currency
			commission_by_currency[currency] = (
				commission_by_currency.get(currency, 0.0) + (c.commission_amount or 0)
			)
			if c.sales_order:
				grand_total, so_currency = frappe.db.get_value(
					"Sales Order", c.sales_order, ["grand_total", "currency"]
				) or (0, None)
				sales_currency = so_currency or currency
				sales_by_currency[sales_currency] = (
					sales_by_currency.get(sales_currency, 0.0) + (grand_total or 0)
				)

		return {
			"orders": len(rows),
			"per_currency": [
				{
					"currency": currency,
					"symbol": currency_symbol(currency),
					"total_sales": sales_by_currency.get(currency, 0.0),
					"total_commission": commission_by_currency.get(currency, 0.0),
				}
				for currency in sorted(set(sales_by_currency) | set(commission_by_currency))
			],
			"total_sales_est": _estimate(sales_by_currency, default_currency),
			"total_commission_est": _estimate(commission_by_currency, default_currency),
			"estimate_currency": default_currency,
			"estimate_symbol": currency_symbol(default_currency),
		}

	confirmed_rows = [c for c in commissions if c.status in ("Approved", "Paid")]
	pending_rows = [c for c in commissions if c.status in ("Pending", "Invoiced")]

	return {
		"confirmed": _summarize(confirmed_rows),
		"pending": _summarize(pending_rows),
	}


@frappe.whitelist()
def get_referrals(filter: str = "all") -> dict:
	"""Full list of the affiliate's referred Sales Orders (via Affiliate
	Commission), for the "My referrals" page. filter is one of:
	- "all": every commission regardless of status
	- "invoiced": only commissions that have a linked Sales Invoice
	- "denied": only commissions marked Denied
	"""
	filters = {"affiliate": get_logged_in_profile().name, "is_reversal": 0}

	if filter == "invoiced":
		filters["sales_invoice"] = ["is", "set"]
	elif filter == "denied":
		filters["status"] = "Denied"
	elif filter != "all":
		frappe.throw(_("Invalid filter. Use 'all', 'invoiced', or 'denied'."))

	commissions = frappe.get_all(
		"Affiliate Commission",
		filters=filters,
		fields=["name", "sales_order", "sales_invoice", "currency", "commission_amount", "status", "creation"],
		order_by="creation desc",
	)

	so_info = {}
	so_names = [c.sales_order for c in commissions if c.sales_order]
	if so_names:
		so_info = {
			so.name: so
			for so in frappe.get_all(
				"Sales Order",
				filters={"name": ["in", so_names]},
				fields=["name", "grand_total", "currency"],
			)
		}
	for c in commissions:
		so = so_info.get(c.sales_order)
		c["sales_order_amount"] = (so.grand_total or 0) if so else 0
		c["sales_order_currency"] = ((so.currency if so else None) or c.currency)

	return {"referrals": commissions}


@frappe.whitelist()
def get_payouts_summary() -> dict:
	"""Payout history plus the summary figures the "Payouts" page needs:
	what's already paid out and still pending/processing (per payout
	currency - a payout can only ever hold ONE currency), the minimum
	cashout threshold from Affiliate Settings, the affiliate's current
	available balance per currency (computed live from unpaid-out
	Approved commissions, not the static profile fields), an approximate
	combined estimate in their preferred display currency, and whether
	they're currently allowed to request a new payout.
	"""
	profile = get_logged_in_profile()

	payouts = frappe.get_all(
		"Affiliate Payout",
		filters={"affiliate": profile.name},
		fields=["name", "bill_no", "generated_date", "period_start", "period_end", "currency", "amount", "status", "payment_method"],
		order_by="generated_date desc",
	)

	company_currency = get_company_currency()
	default_currency = _default_display_currency(profile)

	paid_by_currency = {}
	pending_by_currency = {}
	has_open_payout = False
	for p in payouts:
		currency = p.currency or company_currency
		if p.status == "Paid":
			paid_by_currency[currency] = paid_by_currency.get(currency, 0.0) + (p.amount or 0)
		elif p.status in ("Pending", "Processing"):
			has_open_payout = True
			pending_by_currency[currency] = pending_by_currency.get(currency, 0.0) + (p.amount or 0)

	minimum_cashout = frappe.get_cached_value("Affiliate Settings", None, "minimum_cashout") or 0

	grouped = get_unpaid_out_commissions_by_currency(profile.name)
	available_by_currency = []
	cashable_currencies = []
	for currency in sorted(grouped):
		amount = sum(c.commission_amount for c in grouped[currency])
		meets_minimum = amount >= minimum_cashout and amount > 0
		if meets_minimum:
			cashable_currencies.append(currency)
		available_by_currency.append(
			{
				"currency": currency,
				"symbol": currency_symbol(currency),
				"amount": amount,
				"meets_minimum": meets_minimum,
			}
		)

	return {
		"payouts": payouts,
		"minimum_cashout": minimum_cashout,
		"default_currency": default_currency,
		"company_currency": company_currency,
		"currency_symbols": _currency_symbols(
			list(paid_by_currency)
			+ list(pending_by_currency)
			+ [row["currency"] for row in available_by_currency]
			+ [default_currency, company_currency]
		),
		"available_by_currency": available_by_currency,
		"available_balance_est": _estimate(
			{row["currency"]: row["amount"] for row in available_by_currency},
			default_currency,
		),
		"total_paid_out_by_currency": [
			{"currency": currency, "symbol": currency_symbol(currency), "amount": amount}
			for currency, amount in sorted(paid_by_currency.items())
		],
		"total_paid_out_est": _estimate(paid_by_currency, default_currency),
		"pending_payout_by_currency": [
			{"currency": currency, "symbol": currency_symbol(currency), "amount": amount}
			for currency, amount in sorted(pending_by_currency.items())
		],
		"pending_payout_est": _estimate(pending_by_currency, default_currency),
		"cashable_currencies": cashable_currencies,
		"has_open_payout": has_open_payout,
		"can_request_payout": (not has_open_payout) and bool(cashable_currencies),
	}




@frappe.whitelist()
def request_payout(currency: str = "") -> dict:
	"""Lets an affiliate self-serve request a payout of the currently
	eligible Approved commissions IN ONE CURRENCY, provided that
	currency's balance meets the minimum cashout threshold and they
	don't already have an open (Pending/Processing) payout in flight.

	Payouts are strictly single-currency: an affiliate holding Approved
	commissions in MYR and SGD gets one payout per currency, never one
	mixed-currency amount. `currency` is optional - when omitted and
	exactly one currency is cashable, that currency is used; anything
	ambiguous is rejected with a per-currency balance breakdown rather
	than guessed.

	Creates the Affiliate Payout as "Pending" - an admin still handles
	the actual bank transfer and moves it through Processing -> Paid
	from the Desk, same as before. This just removes the need for the
	admin to manually go find and bundle up each affiliate's approved
	commissions themselves.
	"""
	profile = get_logged_in_profile()

	if not profile.account_number:
		frappe.throw(
			_("Please add your bank account details in Settings > Payment method before requesting a payout.")
		)

	existing_open = frappe.get_all(
		"Affiliate Payout",
		filters={"affiliate": profile.name, "status": ["in", ["Pending", "Processing"]]},
		limit=1,
	)
	if existing_open:
		frappe.throw(_("You already have a payout in progress. Please wait for it to complete before requesting another."))

	grouped = get_unpaid_out_commissions_by_currency(profile.name)
	if not grouped:
		frappe.throw(_("You don't have any approved commissions available to cash out yet."))

	def _balance(cur):
		return sum(c.commission_amount for c in grouped[cur])

	minimum_cashout = frappe.get_cached_value("Affiliate Settings", None, "minimum_cashout") or 0
	currency = (currency or "").strip()

	if currency:
		if currency not in grouped:
			frappe.throw(_("You don't have any approved {0} commissions available to cash out.").format(currency))
	else:
		cashable = [cur for cur in sorted(grouped) if _balance(cur) >= minimum_cashout and _balance(cur) > 0]
		if len(cashable) == 1:
			currency = cashable[0]
		elif not cashable:
			balances_text = ", ".join(
				frappe.utils.fmt_money(_balance(cur), currency=cur) for cur in sorted(grouped)
			)
			frappe.throw(
				_("Your available balances don't reach the minimum cashout ({0}) in any single currency. Available: {1}.").format(
					minimum_cashout,
					balances_text,
				)
			)
		else:
			balances_text = ", ".join(
				frappe.utils.fmt_money(_balance(cur), currency=cur) for cur in sorted(grouped)
			)
			frappe.throw(
				_("You have approved commissions in several currencies ({0}). Payouts are made one currency at a time - please choose which currency to cash out.").format(
					balances_text
				)
			)

	commissions = grouped[currency]
	available_balance = _balance(currency)

	if available_balance < minimum_cashout:
		frappe.throw(
			_("You need at least {0} to request a payout. Your current {1} balance is {2}.").format(
				frappe.utils.fmt_money(minimum_cashout, currency=currency),
				currency,
				frappe.utils.fmt_money(available_balance, currency=currency),
			)
		)

	today = frappe.utils.nowdate()
	payout = frappe.get_doc({
		"doctype": "Affiliate Payout",
		"affiliate": profile.name,
		"bill_no": generate_unique_bill_no(),
		"generated_date": today,
		"status": "Pending",
		"payment_method": "Bank Transfer",
		"period_start": frappe.utils.add_months(today, -1),
		"period_end": today,
		"currency": currency,
		"amount": available_balance,
		"commissions": [{"commission": c.name} for c in commissions],
	})
	payout.insert(ignore_permissions=True)

	return {"requested": True, "payout": payout.name, "amount": available_balance, "currency": currency}


@frappe.whitelist()
def change_password(current_password: str, new_password: str, confirm_password: str) -> dict:
	"""Lets an affiliate change their own login password from the
	Login method settings page. Requires the current password to be
	re-entered (standard practice - prevents someone who's grabbed an
	unlocked session from silently locking the real owner out), and
	applies the same 8-character minimum used at registration.
	"""
	from frappe.utils.password import check_password, update_password

	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to continue."), frappe.PermissionError)

	if new_password != confirm_password:
		frappe.throw(_("New password and confirmation don't match."))

	if not new_password or len(new_password) < 8:
		frappe.throw(_("New password must be at least 8 characters."))

	try:
		check_password(frappe.session.user, current_password)
	except frappe.AuthenticationError:
		frappe.throw(_("Your current password is incorrect."))

	update_password(frappe.session.user, new_password)

	return {"saved": True}


@frappe.whitelist()
def submit_wizard_step1(
	full_name: str,
	phone: str,
	gender: str,
	date_of_birth: str,
	address: str,
) -> dict:
	profile = get_logged_in_profile()
	profile.full_name = full_name
	profile.phone = phone
	profile.gender = gender
	profile.date_of_birth = date_of_birth
	profile.address = address
	try:
		profile.save(ignore_permissions=True)
	except frappe.ValidationError as e:
		if _is_phone_validation_error(e):
			frappe.throw(_("Please enter a valid phone number, including the country code."))
		raise
	return {"saved": True}


@frappe.whitelist()
def upload_affiliate_document() -> str:
	"""Receives the ID-document upload from wizard step 2 and stores it as
	a private File attached to the calling affiliate's own Affiliate
	Profile, returning its file_url for submit_wizard_step2 to persist.

	Replaces the portal's earlier bare POST to /api/method/upload_file,
	which sent only the file + is_private (no doctype/docname). That
	generic endpoint builds an *unattached* private File and then calls
	doc.save(ignore_permissions=False); Frappe's File-specific
	has_permission gate denies `create` on an unattached, private,
	not-yet-owned file to any non-Administrator (the owner field is only
	set during db_insert, AFTER the create-permission check), so every
	non-admin affiliate hit a 403 "You need the 'create' permission on
	File" and could never get past step 2. Attaching to the profile would
	not help either, because the Affiliate role is deliberately granted NO
	direct permission on Affiliate Profile - all profile mutations flow
	through these validated, ignore_permissions endpoints by design. This
	endpoint keeps that controlled-access design: it runs as the logged-in
	affiliate, resolves their own profile (a stranger's profile name can't
	be supplied - there's no parameter for it), validates the mime type,
	and saves the File with ignore_permissions so the File gate is
	satisfied while the attachment still scopes the document to the
	caller's own profile.
	"""
	if "file" not in frappe.request.files:
		frappe.throw(_("No file received."))

	upload = frappe.request.files["file"]
	content_type = getattr(upload, "mimetype", "") or ""
	if not (content_type.startswith("image/") or content_type == "application/pdf"):
		frappe.throw(_("Please upload an image or PDF file."))

	profile = get_logged_in_profile()

	doc = frappe.get_doc(
		{
			"doctype": "File",
			"attached_to_doctype": "Affiliate Profile",
			"attached_to_name": profile.name,
			"file_name": upload.filename,
			"is_private": 1,
			"content": upload.stream.read(),
		}
	)
	doc.save(ignore_permissions=True)
	return doc.file_url


@frappe.whitelist()
def submit_wizard_step2(national_id: str, document_id: str) -> dict:
	profile = get_logged_in_profile()
	profile.national_id = national_id
	profile.document_id = document_id
	profile.save(ignore_permissions=True)
	return {"saved": True}


@frappe.whitelist()
def submit_wizard_step3(bank_name: str, account_name: str, account_number: str) -> dict:
	profile = get_logged_in_profile()
	profile.bank_name = bank_name
	profile.account_name = account_name
	profile.account_number = account_number
	profile.save(ignore_permissions=True)
	return {"saved": True}


def _validate_custom_referral_code(code: str):
	"""Shared validation for a custom referral code, used by both the
	wizard (first-time pick) and Settings (later change). Raises if
	invalid - callers catch/propagate via frappe.throw as appropriate.
	"""
	if not code:
		return
	if len(code) != 8:
		frappe.throw(_("Referral code must be exactly 8 characters."))
	if not re.match(r"^[A-Za-z0-9]+$", code):
		frappe.throw(_("Referral code can only contain letters and numbers."))


@frappe.whitelist()
def check_referral_code_availability(code: str, exclude_self: bool = True) -> dict:
	"""Checks whether a candidate referral code is free to use. Used
	for the real-time "Available"/"Taken" indicator on both the wizard
	step and the Settings page. exclude_self=True (the default) means
	the check ignores the CALLING affiliate's own current code - so an
	affiliate re-submitting their own existing code (e.g. by not
	actually changing anything) isn't told it's "taken" by themselves.

	Checked against both Affiliate Profile and Sales Partner
	(mirroring _generate_unique_referral_code's own uniqueness check),
	since a verified affiliate's code is mirrored onto their linked
	Sales Partner record too.
	"""
	code = (code or "").strip().upper()

	try:
		_validate_custom_referral_code(code)
	except frappe.ValidationError as e:
		return {"available": False, "reason": str(e)}

	own_profile_name = None
	if exclude_self and frappe.session.user != "Guest":
		own_profile_name = frappe.db.get_value(
			"Affiliate Profile", {"user": frappe.session.user}, "name"
		)

	profile_filters = {"referral_code": code}
	if own_profile_name:
		profile_filters["name"] = ["!=", own_profile_name]

	taken = frappe.db.exists("Affiliate Profile", profile_filters) or frappe.db.exists(
		"Sales Partner", {"referral_code": code}
	)

	if taken:
		return {"available": False, "reason": _("This code is already taken. Try another.")}

	return {"available": True, "reason": ""}


@frappe.whitelist()
def submit_wizard_step4(referral_code: str = "") -> dict:
	"""Final wizard step: lets a newly-registered affiliate pick their
	own 8-character referral code, or leave it blank to skip (a code
	gets auto-generated later when an admin verifies them, same as
	before this step existed - see affiliate_profile.py's on_update).

	Saved immediately (not deferred until verification) so the
	uniqueness guarantee applies from the moment it's chosen - two
	affiliates can never simultaneously reserve the same code. The
	affiliate portal's dashboard already shows a clear "Pending
	verification" state for the referral code card until they're
	actually verified, so there's no risk of them thinking a reserved
	code is already live for customers to use.
	"""
	referral_code = (referral_code or "").strip().upper()

	profile = get_logged_in_profile()

	if not referral_code:
		return {"saved": True, "skipped": True}

	_validate_custom_referral_code(referral_code)

	availability = check_referral_code_availability(referral_code, exclude_self=True)
	if not availability["available"]:
		frappe.throw(availability["reason"])

	profile.referral_code = referral_code
	profile.save(ignore_permissions=True)
	return {"saved": True, "skipped": False}


@frappe.whitelist()
def set_custom_referral_code(referral_code: str) -> dict:
	"""Lets an already-onboarded affiliate change their referral code
	from Settings, subject to a 30-day cooldown between changes (see
	last_referral_code_change) - prevents an affiliate from repeatedly
	switching codes (e.g. to "hijack" a code that just freed up, or to
	confuse customers who'd bookmarked a previous code).
	"""
	referral_code = (referral_code or "").strip().upper()
	if not referral_code:
		frappe.throw(_("Please enter a referral code."))

	profile = get_logged_in_profile()

	if profile.last_referral_code_change:
		days_since_change = frappe.utils.date_diff(
			frappe.utils.now(), profile.last_referral_code_change
		)
		if days_since_change < 30:
			days_remaining = 30 - days_since_change
			frappe.throw(
				_("You can change your referral code again in {0} day(s).").format(days_remaining)
			)

	if referral_code == profile.referral_code:
		# Not actually a change (e.g. re-submitting the same value) -
		# no need to touch the cooldown timer for a no-op.
		return {"saved": True}

	_validate_custom_referral_code(referral_code)

	availability = check_referral_code_availability(referral_code, exclude_self=True)
	if not availability["available"]:
		frappe.throw(availability["reason"])

	profile.referral_code = referral_code
	profile.last_referral_code_change = frappe.utils.now()
	profile.save(ignore_permissions=True)
	return {"saved": True}


@frappe.whitelist()
def update_settings(**fields) -> dict:
	"""Used after the wizard is complete, from the Settings tab, to edit
	any profile field the affiliate is allowed to change themselves.
	Includes default_currency (their preferred display currency) -
	changing it re-computes the cached per-currency totals and display-
	currency estimates.
	"""
	profile = get_logged_in_profile()

	editable_fields = {
		"full_name",
		"phone",
		"gender",
		"date_of_birth",
		"address",
		"bank_name",
		"account_name",
		"account_number",
		"default_currency",
	}

	previous_default_currency = profile.default_currency

	for fieldname, value in fields.items():
		if fieldname in editable_fields:
			if fieldname == "default_currency" and value:
				if not frappe.db.get_value("Currency", value, "enabled"):
					frappe.throw(_("Invalid currency."))
			profile.set(fieldname, value)

	try:
		profile.save(ignore_permissions=True)
	except frappe.ValidationError as e:
		if _is_phone_validation_error(e):
			frappe.throw(_("Please enter a valid phone number, including the country code."))
		raise

	if profile.default_currency != previous_default_currency:
		# The *_est fields are expressed in the display currency, so a
		# change of display currency invalidates them.
		update_affiliate_cached_totals(profile.name)

	return {"saved": True}