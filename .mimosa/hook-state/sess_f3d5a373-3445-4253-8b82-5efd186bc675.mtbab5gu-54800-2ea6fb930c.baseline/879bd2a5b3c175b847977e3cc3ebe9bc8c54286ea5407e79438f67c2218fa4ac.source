# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class AffiliateCommission(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from affiliate.affiliate.doctype.affiliate_commission_invoice.affiliate_commission_invoice import AffiliateCommissionInvoice
		from frappe.types import DF

		affiliate: DF.Link | None
		commission_amount: DF.Currency
		sales_invoice: DF.Link | None
		sales_invoices: DF.Table[AffiliateCommissionInvoice]
		sales_order: DF.Link | None
		status: DF.Literal["Pending", "Invoiced", "Approved", "Paid", "Denied"]
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Commission"
