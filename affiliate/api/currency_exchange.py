"""Display-only currency conversion helpers for the affiliate app.

The travel_booking app issues Sales Orders natively in each company's own
currency (multi-company, conversion_rate=1) - nothing in the accounting
ever converts currencies. These helpers exist purely for the ESTIMATED
totals shown to affiliates: converting per-currency balances into an
affiliate's preferred display currency (or the site's company currency
for uniform reporting/leaderboard ranking).

Design rules:
- Rates come from ERPNext's Currency Exchange records (for_selling),
  same source the travel_booking frontend uses for its display-only
  conversions, with a short Redis cache in front.
- A missing rate is NEVER an error: callers receive None and hide the
  estimate instead of showing a wrong number.
- ERPNext's get_exchange_rate only resolves the exact from->to direction,
  so this module also tries the inverse direction and, failing that,
  cross-rate paths through the company currency and USD.
"""

import json
import time

import frappe

_FX_CACHE_TTL = 300  # seconds - matches travel_booking's display-rate cache
_CACHE_PREFIX = "affiliate:fx:"
# Staging currencies tried, in order, when no direct or inverse rate exists.
_CROSS_PATHS = None  # resolved lazily; company currency first, then USD


def get_company_currency() -> str | None:
	"""The site-wide default currency (Global Defaults.default_currency),
	falling back to the default Company's own currency. Returns None if
	the site has neither configured - callers treat that as "use whatever
	currency the row already carries".
	"""
	currency = frappe.db.get_single_value("Global Defaults", "default_currency")
	if currency:
		return currency

	default_company = frappe.db.get_single_value("Global Defaults", "default_company")
	if default_company:
		return frappe.db.get_value("Company", default_company, "default_currency")

	return None


def currency_symbol(currency: str) -> str:
	"""Display symbol for a currency (e.g. "RM"), falling back to the
	code itself when the Currency record carries no symbol (or doesn't
	exist - get_value returns None rather than raising).
	"""
	if not currency:
		return ""
	return frappe.db.get_value("Currency", currency, "symbol") or currency


def get_exchange_rate(from_currency: str, to_currency: str) -> float | None:
	"""Selling rate converting `from_currency` into `to_currency`, or None
	when no rate can be resolved. Resolution order:

	1. same currency -> 1.0
	2. Redis-cached value from a previous successful resolution
	3. ERPNext Currency Exchange, from -> to (for_selling)
	4. ERPNext Currency Exchange, to -> from (for_buying), inverted
	5. cross rate through a staging currency (company currency, then USD)

	Only successful resolutions are cached - a None result is cheap to
	re-query (a single indexed DB lookup) and must start working the
	moment an admin adds the missing Currency Exchange record.
	"""
	if not from_currency or not to_currency:
		return None
	if from_currency == to_currency:
		return 1.0

	cache_key = _CACHE_PREFIX + from_currency + ":" + to_currency
	cached = frappe.cache().get_value(cache_key)
	if cached:
		try:
			data = json.loads(cached)
			if time.time() - float(data.get("ts", 0)) < _FX_CACHE_TTL:
				return float(data["rate"])
		except (ValueError, TypeError, KeyError):
			pass  # corrupt cache entry - fall through and re-resolve

	rate = _resolve_rate(from_currency, to_currency)
	if rate:
		frappe.cache().set_value(
			cache_key, json.dumps({"rate": float(rate), "ts": time.time()})
		)
	return rate


def convert_amount(amount: float, from_currency: str, to_currency: str) -> float | None:
	"""Converts `amount` from one currency to another, rounded to 2dp.
	Returns None (not 0) when no rate exists, so callers can distinguish
	"no estimate available" from "converts to zero".
	"""
	rate = get_exchange_rate(from_currency, to_currency)
	if rate is None:
		return None
	return frappe.utils.flt(amount, 2) * rate


def _resolve_rate(from_currency: str, to_currency: str) -> float | None:
	# Direct direction first (exactly what ERPNext's own document flows use).
	direct = _erpnext_rate(from_currency, to_currency)
	if direct:
		return float(direct)

	# Inverse direction: a MYR->SGD selling rate can be derived from a
	# SGD->MYR buying rate when that's the direction the admin recorded.
	inverse = _erpnext_rate(to_currency, from_currency)
	if inverse:
		return 1.0 / float(inverse)

	# Cross rate through staging currencies: from -> staging -> to.
	# Each leg itself may be direct or inverse (e.g. no MYR->SGD selling
	# entry but a SGD->MYR buying entry still yields a usable MYR->SGD leg).
	for staging in _staging_currencies():
		if staging in (from_currency, to_currency):
			continue
		leg1 = _leg_rate(from_currency, staging)
		leg2 = _leg_rate(staging, to_currency)
		if leg1 and leg2:
			return float(leg1) * float(leg2)

	return None


def _leg_rate(from_currency: str, to_currency: str) -> float | None:
	"""One cross-path leg: direct rate if recorded, else the inverse of
	the opposite direction, else None.
	"""
	direct = _erpnext_rate(from_currency, to_currency)
	if direct:
		return float(direct)

	inverse = _erpnext_rate(to_currency, from_currency)
	if inverse:
		return 1.0 / float(inverse)

	return None


def _erpnext_rate(from_currency: str, to_currency: str) -> float | None:
	"""Thin wrapper over erpnext.setup.utils.get_exchange_rate that never
	raises - ERPNext raises when no Currency Exchange record matches, which
	here is an expected, recoverable outcome.
	"""
	try:
		from erpnext.setup.utils import get_exchange_rate

		return get_exchange_rate(
			from_currency, to_currency, frappe.utils.today(), args="for_selling"
		)
	except Exception:
		return None


def _staging_currencies() -> list[str]:
	"""Currencies to route cross rates through, company currency first so
	the common case (any currency <-> site default) needs the fewest
	Currency Exchange records to work.
	"""
	global _CROSS_PATHS
	if _CROSS_PATHS is None:
		paths = [get_company_currency() or "USD", "USD"]
		_CROSS_PATHS = [c for c in paths if c]
	return _CROSS_PATHS
