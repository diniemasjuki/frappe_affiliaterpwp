# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AffiliateCurrencyBalance(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		available_balance: DF.Currency
		currency: DF.Link
		total_commission: DF.Currency
		total_sales: DF.Currency
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Currency Balance"
