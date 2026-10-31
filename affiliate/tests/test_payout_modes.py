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
		# A live site may already have run this period's batch (the marker
		# blocks re-runs) - force the "batch due" state under test here;
		# tearDown restores the site's real marker.
		frappe.db.set_single_value("Affiliate Settings", "last_monthly_batch_run", "1900-01-01")

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


class TestCheckAndRegeneratePayout(UnitTestCase):
	"""The Affiliate Commission desk action: status re-check + payout
	regeneration, with the no-double-payout guarantees.

	Same live-site-DB hygiene as TestPayoutModes: everything created here
	is keyed off _EMAIL and removed in dependency order; commissions are
	Created without Sales Orders so the resync pass is a no-op and no
	ERPNext documents are needed.
	"""

	def setUp(self):
		self._settings_backup = {
			key: frappe.db.get_single_value("Affiliate Settings", key)
			for key in ("minimum_cashout",)
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

	def _make_profile(self, email=_EMAIL):
		prof = frappe.get_doc(
			{
				"doctype": "Affiliate Profile",
				"email_id": email,
				"full_name": "Regenerate Payout Test",
				"status": "Pending Verification",
				"payout_mode": "Monthly",
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

	def test_regenerate_creates_payout_once_and_is_idempotent(self):
		from affiliate.api.payout_batch import check_and_regenerate_payout

		prof = self._make_profile()
		c1 = self._make_commission(prof, 100)
		self._make_commission(prof, 50)

		result = check_and_regenerate_payout(c1.name)

		self.assertEqual(len(result["payouts_created"]), 1)
		payout = frappe.get_doc("Affiliate Payout", result["payouts_created"][0])
		self.assertEqual(flt(payout.amount), 150.0)
		self.assertEqual(len(payout.commissions), 2)
		self.assertEqual(result["double_payout_check"], [])

		# Second click: everything is already attached - nothing new may
		# be created and no commission may move anywhere.
		again = check_and_regenerate_payout(c1.name)
		self.assertEqual(again["payouts_created"], [])
		self.assertEqual(again["payouts_updated"], [])
		self.assertEqual(frappe.db.count("Affiliate Payout", {"affiliate": prof.name}), 1)
		self.assertEqual(len(payout.commissions), 2)

	def test_regenerate_tops_up_open_payout_instead_of_second_payout(self):
		from affiliate.api.commission_sync import generate_unique_bill_no
		from affiliate.api.payout_batch import check_and_regenerate_payout

		prof = self._make_profile()
		c1 = self._make_commission(prof, 100)
		open_payout = frappe.get_doc(
			{
				"doctype": "Affiliate Payout",
				"affiliate": prof.name,
				"bill_no": generate_unique_bill_no(),
				"generated_date": frappe.utils.nowdate(),
				"currency": "MYR",
				"amount": 100,
				"status": "Pending",
				"payment_method": "Bank Transfer",
				"source": "Manual Claim",
				"commissions": [{"commission": c1.name}],
			}
		).insert(ignore_permissions=True)

		# A commission approved AFTER the open payout was raised: the
		# regenerate action must fold it into the open payout, not create
		# a parallel one.
		c2 = self._make_commission(prof, 50)
		result = check_and_regenerate_payout(c2.name)

		self.assertEqual(result["payouts_created"], [])
		self.assertEqual(result["payouts_updated"], [open_payout.name])
		self.assertEqual(frappe.db.count("Affiliate Payout", {"affiliate": prof.name}), 1)
		self.assertEqual(
			flt(frappe.db.get_value("Affiliate Payout", open_payout.name, "amount")), 150.0
		)

	def test_payout_rejects_commission_already_in_another_payout(self):
		"""The hard guarantee behind "never doubled": validate() refuses a
		payout that would attach a commission a payout already holds.
		"""
		from affiliate.api.commission_sync import generate_unique_bill_no

		prof = self._make_profile()
		c1 = self._make_commission(prof, 100)
		frappe.get_doc(
			{
				"doctype": "Affiliate Payout",
				"affiliate": prof.name,
				"bill_no": generate_unique_bill_no(),
				"generated_date": frappe.utils.nowdate(),
				"currency": "MYR",
				"amount": 100,
				"status": "Pending",
				"payment_method": "Bank Transfer",
				"source": "Manual Claim",
				"commissions": [{"commission": c1.name}],
			}
		).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Affiliate Payout",
					"affiliate": prof.name,
					"bill_no": generate_unique_bill_no(),
					"generated_date": frappe.utils.nowdate(),
					"currency": "MYR",
					"amount": 100,
					"status": "Pending",
					"payment_method": "Bank Transfer",
					"source": "Monthly Batch",
					"commissions": [{"commission": c1.name}],
				}
			).insert(ignore_permissions=True)

	def test_regenerate_reports_currencies_below_minimum_cashout(self):
		from affiliate.api.payout_batch import check_and_regenerate_payout

		prof = self._make_profile()
		c1 = self._make_commission(prof, 10)
		frappe.db.set_single_value("Affiliate Settings", "minimum_cashout", 100)

		result = check_and_regenerate_payout(c1.name)

		self.assertEqual(result["payouts_created"], [])
		self.assertEqual(result["below_minimum_cashout"], ["MYR"])
		self.assertEqual(result["still_eligible"], 1)
		self.assertEqual(
			frappe.db.count("Affiliate Payout", {"affiliate": prof.name}), 0
		)

	def test_regenerate_all_sweeps_all_affiliates_and_is_idempotent(self):
		from affiliate.api.payout_batch import check_and_regenerate_all_payouts

		prof_a = self._make_profile()
		prof_b = self._make_profile(email=_SESSION_EMAIL)
		self._make_commission(prof_a, 100)
		self._make_commission(prof_a, 50)
		self._make_commission(prof_b, 200, currency="SGD")

		# The site-wide action sweeps the whole site's affiliates by
		# design - stub both population seams (and the site-wide eligible
		# counter) down to the fixtures here, so a live site's real
		# commissions can never be touched by this test.
		seams = (
			patch(
				"affiliate.api.payout_batch._affiliates_with_commissions",
				return_value=[prof_a.name, prof_b.name],
			),
			patch(
				"affiliate.api.payout_batch.affiliates_with_approved_commissions",
				return_value=[prof_a.name, prof_b.name],
			),
			patch(
				"affiliate.api.payout_batch._count_eligible_commissions_site_wide",
				return_value=0,
			),
		)
		with seams[0] as _, seams[1] as _, seams[2] as _:
			result = check_and_regenerate_all_payouts()

		self.assertEqual(result["affiliates_processed"], 2)
		self.assertEqual(len(result["payouts_created"]), 2)
		self.assertEqual(result["double_payout_check"], [])
		self.assertEqual(result["status_changes"], [])
		self.assertEqual(
			flt(frappe.db.get_value("Affiliate Payout", {"affiliate": prof_a.name}, "amount")), 150.0
		)

		# Second click: everything is already attached - a full no-op.
		with seams[0] as _, seams[1] as _, seams[2] as _:
			again = check_and_regenerate_all_payouts()

		self.assertEqual(again["payouts_created"], [])
		self.assertEqual(again["payouts_updated"], [])
		self.assertEqual(frappe.db.count("Affiliate Payout", {"affiliate": prof_a.name}), 1)
		self.assertEqual(frappe.db.count("Affiliate Payout", {"affiliate": prof_b.name}), 1)
		# The first commission's payout holds both of A's commissions.
		self.assertEqual(
			frappe.db.count(
				"Affiliate Payout Commission",
				{"parent": ["in", frappe.get_all("Affiliate Payout", filters={"affiliate": prof_a.name}, pluck="name")]},
			),
			2,
		)


def flt(value):
	return frappe.utils.flt(value, 2)
