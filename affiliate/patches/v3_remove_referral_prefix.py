import frappe


def execute():
	"""referral_code_prefix was removed from Affiliate Settings - drop
	its orphaned value from the Singles table so it doesn't linger as
	invisible data. Existing codes that already start with the old
	prefix (e.g. "RC...") stay exactly as they are; only newly
	generated codes are prefix-free.
	"""
	frappe.db.delete(
		"Singles",
		{"doctype": "Affiliate Settings", "field": "referral_code_prefix"},
	)
