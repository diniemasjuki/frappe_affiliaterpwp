# affiliate/www/affiliate.py
#
# Companion Python module for the affiliate portal page (affiliate.html).
# Frappe's www renderer loads this automatically and calls get_context(),
# which exposes csrf_token to the Jinja template — required for every POST
# the portal makes (get_google_login_url, login, register, etc.).
import frappe

no_cache = 1
allow_guest = True

def get_context(context):
	context.no_cache = 1

	# CSRF token diperlukan untuk SEMUA pengguna (termasuk Guest) kerana
	# portal membuat POST API calls yang Frappe sahkan CSRF walaupun untuk
	# endpoint allow_guest. Token ini rawak dan terikat ke sesi, bukan
	# kredensial — selamat diberi kepada guest sama seperti cookie csrftoken
	# yang Frappe tetapkan.
	context.csrf_token = frappe.sessions.get_csrf_token()
