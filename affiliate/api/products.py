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

	today = frappe.utils.today()

	# A Trip is bookable when it has at least one active, upcoming group
	# date that is itself linked to an active Trip Package (via the
	# package's "Trip Package Group Date Select" child table). Built with
	# ORM calls rather than raw SQL so it respects field renames and
	# Frappe's permission layer instead of bypassing them.
	active_packages = frappe.get_all(
		"Trip Package", filters={"status": "Active"}, pluck="name"
	)
	if not active_packages:
		return {"base_url": frappe.utils.get_url(), "products": []}

	group_dates_with_active_package = set(
		frappe.get_all(
			"Trip Package Group Date Select",
			filters={"parent": ["in", active_packages]},
			pluck="trip_group_date",
		)
	)
	if not group_dates_with_active_package:
		return {"base_url": frappe.utils.get_url(), "products": []}

	valid_trips = {
		t
		for t in frappe.get_all(
			"Trip Group Date",
			filters={
				"name": ["in", list(group_dates_with_active_package)],
				"status": "Active",
				"departure_date": [">=", today],
			},
			pluck="trip",
		)
		if t
	}
	if not valid_trips:
		return {"base_url": frappe.utils.get_url(), "products": []}

	trips = frappe.get_all(
		"Trip",
		filters={
			"name": ["in", list(valid_trips)],
			"status": "Active",
			# Hanya Trip yang published boleh dipaut — page detail trip
			# (/<route>) melayan 404 kalau published=0, jadi pautan affiliate
			# ke trip yang belum publish akan putus. published ialah gate yang
			# betul: trip mesti boleh diakses awam sebelum affiliate boleh kongsi.
			"published": 1,
		},
		fields=["name", "trip_name", "trip_image", "route"],
		order_by="trip_name asc",
	)

	products = [
		{
			"name":       t.name,
			"trip_name":  t.trip_name or "",
			"trip_image": t.trip_image or "",
			# route (cth. "trip/percubaan-trip-cruise") — portal bina pautan
			# affiliate ke page detail trip, bukan terus ke wizard booking.
			"route":      t.route or "",
		}
		for t in trips
	]

	return {
		"base_url": frappe.utils.get_url(),
		"products": products,
	}