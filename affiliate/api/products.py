import frappe

from affiliate.api.portal_api import get_logged_in_profile


@frappe.whitelist()
def get_products() -> dict:
	"""Senarai Trip yang sah dijual sekarang, SATU baris per Trip
	(bukan per package) — link yang dijana peringkat Trip sahaja,
	customer pilih package + tarikh sailing sendiri dalam booking
	wizard selepas klik link.

	base_url dipulangkan sekali (bukan per-trip) — guna
	frappe.utils.get_url() di sisi server (bukan window.location.origin
	di frontend), supaya link yang dijana tetap betul walaupun affiliate
	portal ni dihoskan di subdomain lain dari booking wizard pada masa
	depan.
	"""
	get_logged_in_profile()

	rows = frappe.db.sql(
		"""
		SELECT DISTINCT
			t.name  AS trip_id,
			t.trip_name,
			t.trip_image
		FROM `tabTrip` t
		WHERE t.status = 'Active'
		  AND EXISTS (
			  SELECT 1
			  FROM `tabTrip Group Date` td
			  WHERE td.trip = t.name
				AND td.status = 'Active'
				AND td.departure_date >= CURDATE()
				AND EXISTS (
					SELECT 1
					FROM `tabTrip Package Group Date Select` sel
					JOIN `tabTrip Package` tp ON tp.name = sel.parent
					WHERE sel.trip_group_date = td.name
					  AND tp.status = 'Active'
				)
		  )
		ORDER BY t.trip_name ASC
		""",
		as_dict=True,
	)

	products = [
		{
			"name":       r.trip_id,
			"trip_name":  r.trip_name or "",
			"trip_image": r.trip_image or "",
		}
		for r in rows
	]

	return {
		"base_url": frappe.utils.get_url(),
		"products": products,
	}