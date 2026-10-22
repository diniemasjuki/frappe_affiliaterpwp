import frappe


def execute():
	"""Profiles created before the payout mode existed get the old
	behaviour made explicit: Manual Claim. (Default Payout Mode in
	Affiliate Settings only ever affects registrations from this release
	onwards - existing affiliates keep exactly what they had before, so
	nobody is silently moved onto the monthly schedule.)
	"""
	frappe.db.sql(
		"""
		update `tabAffiliate Profile`
		set payout_mode = 'Manual Claim'
		where payout_mode is null or payout_mode = ''
		"""
	)
