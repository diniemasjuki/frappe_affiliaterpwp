import frappe

from affiliate.api.portal_api import get_logged_in_profile


@frappe.whitelist()
def get_products() -> dict:
	
	get_logged_in_profile()

	rows = frappe.db.sql(
		"""
		SELECT
			tp.name        AS package_name,
			tp.package_title,
			tp.package_type,
			t.name         AS trip_id,
			t.trip_name,
			t.trip_image
		FROM `tabTrip Package` tp
		JOIN `tabTrip` t ON t.name = tp.trip_link
		WHERE tp.status = 'Active'
		  AND t.status = 'Active'
		  AND EXISTS (
			  SELECT 1
			  FROM `tabTrip Package Group Date Select` sel
			  JOIN `tabTrip Group Date` td ON td.name = sel.trip_group_date
			  WHERE sel.parent = tp.name
				AND td.status = 'Active'
				AND td.departure_date >= CURDATE()
		  )
		ORDER BY t.trip_name ASC, tp.package_title ASC
		""",
		as_dict=True,
	)

	products = [
		{
			"name":          r.package_name,
			"trip_id":       r.trip_id,
			"trip_name":     r.trip_name or "",
			"trip_image":    r.trip_image or "",
			"package_title": r.package_title or "",
			"package_type":  r.package_type or "",
		}
		for r in rows
	]

	return {
		"base_url": frappe.utils.get_url(),
		"products": products,
	}