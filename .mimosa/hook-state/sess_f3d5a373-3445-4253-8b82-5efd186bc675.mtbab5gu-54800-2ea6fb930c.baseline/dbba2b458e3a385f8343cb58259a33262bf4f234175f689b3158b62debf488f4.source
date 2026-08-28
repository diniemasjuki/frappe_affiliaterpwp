# Copyright (c) 2026, Warga Prihatin and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AffiliateCommissionInvoice(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		invoice_amount: DF.Currency
		invoice_status: DF.Literal["Draft", "Submitted", "Unpaid", "Partly Paid", "Overdue", "Unpaid and Discounted", "Overdue and Discounted", "Paid", "Cancelled", "Return", "Credit Note Issued"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		sales_invoice: DF.Link | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Affiliate Commission Invoice"
