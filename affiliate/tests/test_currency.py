"""Currency-sensitivity tests for the affiliate app (multi-currency
model): per-currency balances, display-currency estimates, and the
single-currency payout rule.

Runs against the configured site DB. Each test cleans up the records it
creates (keyed off the shared _EMAIL marker) so it can run repeatedly.
"""

import frappe
from frappe.tests import UnitTestCase

_EMAIL = "currency-test@example.com"


class TestCurrencySensitive(UnitTestCase):
	def setUp(self):
		self._cleanup()

	def tearDown(self):
		self._cleanup()

	def _cleanup(self):
		"""Deletes everything this test class ever created, in
		dependency order: payouts (own the commission child rows), then
		commissions (link the profile - would block profile deletion),
		then the profiles themselves.
		"""
		for profile_name in frappe.get_all(
			"Affiliate Profile", filters={"email_id": _EMAIL}, pluck="name"
		):
			for payout in frappe.get_all(
				"Affiliate Payout", filters={"affiliate": profile_name}, pluck="name"
			):
				frappe.delete_doc("Affiliate Payout", payout, force=True, ignore_permissions=True)
			for commission in frappe.get_all(
				"Affiliate Commission", filters={"affiliate": profile_name}, pluck="name"
			):
				frappe.delete_doc("Affiliate Commission", commission, force=True, ignore_permissions=True)
			frappe.delete_doc("Affiliate Profile", profile_name, force=True, ignore_permissions=True)

	def _make_profile(self):
		prof = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"email_id": _EMAIL,
				"full_name": "Currency Test",
				"status": "Pending Verification",
			}
		)
		prof.insert(ignore_permissions=True)
		return prof

	def _make_commission(self, prof, amount, currency, status="Approved"):
		doc = frappe.get_doc(
			{
				"doctype": "Affiliate Commission",
				"affiliate": prof.name,
				"currency": currency,
				"commission_amount": amount,
				"commission_rate": 2,
				"commission_base": "Gross",
				"status": status,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_totals_are_split_per_currency_with_company_estimate(self):
		from affiliate.api.commission_sync import update_affiliate_cached_totals

		prof = self._make_profile()
		self._make_commission(prof, 100, "MYR")
		self._make_commission(prof, 50, "SGD")
		# Pending must NOT count; Paid counts (same as Approved here).
		self._make_commission(prof, 999, "MYR", status="Pending")
		self._make_commission(prof, 10, "MYR", status="Paid")

		update_affiliate_cached_totals(prof.name)
		prof.reload()

		balances = {b.currency: b for b in prof.balances}
		self.assertIn("MYR", balances)
		self.assertIn("SGD", balances)
		self.assertEqual(flt(balances["MYR"].total_commission), 110.0)
		self.assertEqual(flt(balances["SGD"].total_commission), 50.0)
		self.assertEqual(flt(balances["MYR"].available_balance), 110.0)
		self.assertEqual(flt(balances["SGD"].available_balance), 50.0)

		# Scalar totals: company-currency approximation. With the site at
		# MYR, the MYR bucket passes through unchanged; the SGD bucket is
		# converted via Currency Exchange (must exist on this site).
		from affiliate.api.currency_exchange import get_exchange_rate

		company_currency = frappe.db.get_single_value("Global Defaults", "default_currency")
		sgd_rate = get_exchange_rate("SGD", company_currency)
		self.assertIsNotNone(sgd_rate)
		self.assertEqual(flt(prof.total_commission), flt(110.0 + 50.0 * sgd_rate))

	def test_estimate_fields_blank_when_rate_missing(self):
		from affiliate.api.commission_sync import (
			get_company_currency,
			update_affiliate_cached_totals,
		)

		prof = self._make_profile()
		self._make_commission(prof, 100, "MYR")
		self._make_commission(prof, 50, "SGD")

		# Point the display currency at something with no exchange-rate
		# path. Currency columns are NOT NULL, so an unavailable estimate
		# is stored as 0 - the portal hides it by recomputing live.
		frappe.db.set_value("Affiliate Profile", prof.name, "default_currency", "ZWL")
		try:
			if get_company_currency() != "ZWL":
				update_affiliate_cached_totals(prof.name)
				prof.reload()
				self.assertEqual(flt(prof.total_commission_est), 0.0)
				self.assertEqual(flt(prof.available_balance_est), 0.0)
				# Exact balances still maintained.
				self.assertEqual(len(prof.balances), 2)
		finally:
			frappe.db.set_value("Affiliate Profile", prof.name, "default_currency", None)

	def test_eligible_commissions_are_grouped_by_currency(self):
		from affiliate.api.commission_sync import get_unpaid_out_commissions_by_currency

		prof = self._make_profile()
		self._make_commission(prof, 100, "MYR")
		self._make_commission(prof, 50, "SGD")
		# Paid and Pending commissions are not payout-eligible.
		self._make_commission(prof, 10, "MYR", status="Paid")

		grouped = get_unpaid_out_commissions_by_currency(prof.name)

		self.assertEqual(set(grouped), {"MYR", "SGD"})
		self.assertEqual(len(grouped["MYR"]), 1)
		self.assertEqual(flt(grouped["MYR"][0].commission_amount), 100.0)
		self.assertEqual(flt(sum(c.commission_amount for c in grouped["SGD"])), 50.0)

	def test_batch_payout_is_single_currency(self):
		from affiliate.api.payout_batch import generate_payout_batch_for_affiliate
		from affiliate.api.commission_sync import get_unpaid_out_commissions_by_currency

		prof = self._make_profile()
		self._make_commission(prof, 100, "MYR")
		self._make_commission(prof, 50, "SGD")

		payout_name = generate_payout_batch_for_affiliate(prof.name, currency="MYR", minimum_cashout=0)
		self.assertTrue(payout_name)

		payout = frappe.get_doc("Affiliate Payout", payout_name)
		self.assertEqual(payout.currency, "MYR")
		self.assertEqual(flt(payout.amount), 100.0)
		self.assertEqual(len(payout.commissions), 1)

		# The SGD commission was NOT swept into the MYR payout...
		grouped = get_unpaid_out_commissions_by_currency(prof.name)
		self.assertEqual(set(grouped), {"SGD"})

		# ...and is still available as its own single-currency payout.
		sgd_payout_name = generate_payout_batch_for_affiliate(prof.name, currency="SGD", minimum_cashout=0)
		self.assertTrue(sgd_payout_name)
		sgd_payout = frappe.get_doc("Affiliate Payout", sgd_payout_name)
		self.assertEqual(sgd_payout.currency, "SGD")
		self.assertEqual(flt(sgd_payout.amount), 50.0)


def flt(value):
	return frappe.utils.flt(value, 2)
