"""Payout-mode tests: Monthly vs Manual Claim.

Covers the monthly batch's affiliate filter and source stamping, the
scheduler's once-per-period idempotency (including catch-up after a
missed day), the next-payout-date helper, the Monthly guard on
request_payout, and the Monthly Payout Day validation.

Runs against the configured site DB. Each test cleans up the records it
creates (keyed off the _EMAIL markers) and restores the Affiliate
Settings values it touched, so it can run repeatedly.
"""

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_months, getdate, nowdate
from unittest.mock import patch

# The batch functions sweep the site's whole affiliate pool - these
# tests stub that pool (and the Monthly-mode set) down to the profiles
# created here, so a run against the live site can never create payouts
# for real affiliates.
_BATCH_SEAMS = (
	"affiliate.api.payout_batch.affiliates_with_approved_commissions",
	"affiliate.api.payout_batch.monthly_affiliates",
)


def _batch_patch(affiliates, monthly=None):
	"""patch() for the batch seams: every affiliate in `affiliates` has
	approved commissions; only `monthly` (default: all of them) is on
	the Monthly payout mode.
	"""
	return patch(
		"affiliate.api.payout_batch.affiliates_with_approved_commissions",
		return_value=list(affiliates),
	), patch(
		"affiliate.api.payout_batch.monthly_affiliates",
		return_value=set(monthly if monthly is not None else affiliates),
	)

_EMAIL = "payout-mode-test@example.com"
_SESSION_EMAIL = "payout-mode-session@example.com"


class TestPayoutModes(UnitTestCase):
	def setUp(self):
		self._settings_backup = {
			key: frappe.db.get_single_value("Affiliate Settings", key)
			for key in ("minimum_cashout", "monthly_payout_day", "last_monthly_batch_run")
		}
		frappe.db.set_single_value("Affiliate Settings", "minimum_cashout", 0)
		self._cleanup()
		frappe.set_user("Administrator")

	def tearDown(self):
		self._cleanup()
		for key, value in self._settings_backup.items():
			frappe.db.set_single_value("Affiliate Settings", key, value)
		frappe.set_user("Administrator")

	def _cleanup(self):
		"""Deletes everything this test class ever created, in dependency
		order: payouts (own the commission child rows), then commissions
		and profiles (would block each other's deletion), then the test
		users.
		"""
		for profile_name in frappe.get_all(
			"Affiliate Profile", filters={"email_id": ["in", (_EMAIL, _SESSION_EMAIL)]}, pluck="name"
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
		for user in frappe.get_all("User", filters={"name": _SESSION_EMAIL}, pluck="name"):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	def _make_profile(self, payout_mode=None, user=None):
		prof = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"email_id": _EMAIL if not user else _SESSION_EMAIL,
				"full_name": "Payout Mode Test",
				"status": "Pending Verification",
				"user": user,
				"payout_mode": payout_mode,
			}
		)
		prof.insert(ignore_permissions=True)
		return prof

	def _make_commission(self, prof, amount, currency="MYR", status="Approved"):
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

	def test_monthly_batch_only_covers_monthly_affiliates(self):
		from affiliate.api.payout_batch import (
			PAYOUT_SOURCE_MONTHLY,
			generate_payout_batch,
		)

		monthly = self._make_profile(payout_mode="Monthly")
		manual = self._make_profile(payout_mode="Manual Claim")
		self._make_commission(monthly, 100)
		self._make_commission(manual, 100)

		patch_approved, patch_monthly = _batch_patch([monthly.name, manual.name], monthly={monthly.name})
		with patch_approved as _, patch_monthly as _:
			created = generate_payout_batch(monthly_mode_only=True)

		self.assertEqual(len(created), 1)
		payout = frappe.get_doc("Affiliate Payout", created[0])
		self.assertEqual(payout.affiliate, monthly.name)
		self.assertEqual(payout.source, PAYOUT_SOURCE_MONTHLY)

		# The manual-mode affiliate keeps every commission unclaimed.
		from affiliate.api.commission_sync import get_unpaid_out_commissions_by_currency

		self.assertTrue(get_unpaid_out_commissions_by_currency(manual.name))

	def test_manual_batch_runs_for_everyone_and_stamps_manual_source(self):
		from affiliate.api.payout_batch import (
			PAYOUT_SOURCE_MANUAL,
			generate_payout_batch,
		)

		monthly = self._make_profile(payout_mode="Monthly")
		manual = self._make_profile(payout_mode="Manual Claim")
		self._make_commission(monthly, 100)
		self._make_commission(manual, 100)

		patch_approved, patch_monthly = _batch_patch([monthly.name, manual.name], monthly={monthly.name})
		with patch_approved as _, patch_monthly as _:
			created = generate_payout_batch()

		self.assertEqual(len(created), 2)
		sources = {
			frappe.db.get_value("Affiliate Payout", name, ["affiliate", "source"])[1] for name in created
		}
		self.assertEqual(sources, {PAYOUT_SOURCE_MANUAL})

	def test_monthly_batch_runs_once_per_period_and_catches_up(self):
		from affiliate.api.payout_batch import run_monthly_payout_batch_if_due

		prof = self._make_profile(payout_mode="Monthly")
		self._make_commission(prof, 100)
		frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", 1)

		try:
			patch_approved, patch_monthly = _batch_patch([prof.name])
			with patch_approved as _, patch_monthly as _:
				with patch("affiliate.api.payout_batch._notify_admins_of_batch"):
					first = run_monthly_payout_batch_if_due()
					self.assertEqual(len(first), 1)

					# Same period, minutes later: nothing - the marker blocks a
					# second batch even though new commissions could exist.
					self._make_commission(prof, 50)
					self.assertEqual(run_monthly_payout_batch_if_due(), [])

					# Next period's marker reset: the catch-up runs again - this
					# is the path a missed payout day takes.
					frappe.db.set_single_value(
						"Affiliate Settings", "last_monthly_batch_run", add_months(nowdate(), -1)
					)
					second = run_monthly_payout_batch_if_due()
					self.assertEqual(len(second), 1)

			# Only the newer commission was swept - the first batch's
			# commissions are no longer unpaid-out.
			payout = frappe.get_doc("Affiliate Payout", second[0])
			self.assertEqual(flt(payout.amount), 50.0)
		finally:
			frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", None)

	def test_monthly_batch_disabled_without_payout_day(self):
		from affiliate.api.payout_batch import run_monthly_payout_batch_if_due

		prof = self._make_profile(payout_mode="Monthly")
		self._make_commission(prof, 100)
		frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", None)

		patch_approved, patch_monthly = _batch_patch([prof.name])
		with patch_approved as _, patch_monthly as _:
			self.assertEqual(run_monthly_payout_batch_if_due(), [])

	def test_scheduled_date_helpers(self):
		from affiliate.api.payout_batch import (
			next_monthly_payout_date,
			scheduled_payout_date,
			scheduled_payout_date_on_or_before,
		)

		# Day 28 is safe in the shortest month; 29+ would not be.
		self.assertEqual(scheduled_payout_date("2026-02-10", 28), getdate("2026-02-28"))

		# Before the day: the most recent due date is last month's.
		self.assertEqual(scheduled_payout_date_on_or_before("2026-03-04", 5), getdate("2026-02-05"))
		# On/after the day: this month's.
		self.assertEqual(scheduled_payout_date_on_or_before("2026-03-05", 5), getdate("2026-03-05"))
		self.assertEqual(scheduled_payout_date_on_or_before("2026-03-31", 5), getdate("2026-03-05"))

		frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", 5)
		try:
			# Period not yet processed (last run predates the due date):
			# the pending period's own date - this is how a pending
			# catch-up still shows the imminent date.
			frappe.db.set_single_value("Affiliate Settings", "last_monthly_batch_run", "2026-03-01")
			self.assertEqual(next_monthly_payout_date("2026-03-10"), "2026-03-05")

			# Period already processed: next month's date.
			frappe.db.set_single_value("Affiliate Settings", "last_monthly_batch_run", "2026-03-10")
			self.assertEqual(next_monthly_payout_date("2026-03-10"), "2026-04-05")

			# Disabled: no date at all.
			frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", None)
			self.assertIsNone(next_monthly_payout_date("2026-03-10"))
		finally:
			frappe.db.set_single_value("Affiliate Settings", "monthly_payout_day", None)

	def test_request_payout_rejected_for_monthly_mode(self):
		from affiliate.api.portal_api import request_payout

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": _SESSION_EMAIL,
				"first_name": "Payout",
				"last_name": "Mode Test",
				"roles": [{"role": "Affiliate"}],
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)

		monthly = self._make_profile(payout_mode="Monthly", user=user.name)
		self._make_commission(monthly, 100)

		frappe.set_user(user.name)
		self.assertRaises(frappe.ValidationError, request_payout)

	def test_request_payout_stamps_manual_source(self):
		from affiliate.api.portal_api import request_payout

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": _SESSION_EMAIL,
				"first_name": "Payout",
				"last_name": "Mode Test",
				"roles": [{"role": "Affiliate"}],
				"send_welcome_email": 0,
			}
		)
		user.insert(ignore_permissions=True)

		manual = self._make_profile(payout_mode="Manual Claim", user=user.name)
		frappe.db.set_value(
			"Affiliate Profile", manual.name, {"bank_name": "Test Bank", "account_name": "Test", "account_number": "1234"}
		)
		self._make_commission(manual, 100)

		frappe.set_user(user.name)
		result = request_payout()

		payout = frappe.get_doc("Affiliate Payout", result["payout"])
		self.assertEqual(payout.source, "Manual Claim")

	def test_settings_validate_payout_day_range(self):
		# Tested through the module-level validator rather than a settings
		# save: a live site can carry unrelated config that fails other
		# parts of validate(), which would mask the assertion.
		from affiliate.affiliate.doctype.affiliate_settings.affiliate_settings import (
			validate_monthly_payout_day,
		)

		validate_monthly_payout_day(0)  # blank/disabled, allowed
		validate_monthly_payout_day(1)
		validate_monthly_payout_day(28)
		self.assertRaises(frappe.ValidationError, validate_monthly_payout_day, 29)
		self.assertRaises(frappe.ValidationError, validate_monthly_payout_day, 30)
		self.assertRaises(frappe.ValidationError, validate_monthly_payout_day, -3)


def flt(value):
	return frappe.utils.flt(value, 2)
