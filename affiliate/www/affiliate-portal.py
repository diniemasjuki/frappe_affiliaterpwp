# affiliate/www/affiliate-portal.py
import frappe

no_cache = 1
allow_guest = True

def get_context(context):
	context.no_cache = 1
    
	context.csrf_token = (
		frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	)