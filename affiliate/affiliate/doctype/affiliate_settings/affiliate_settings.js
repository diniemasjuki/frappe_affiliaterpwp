// Copyright (c) 2026, Warga Prihatin and contributors
// For license information, please see license.txt

frappe.ui.form.on("Affiliate Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Check & Regenerate All Payouts"), () => {
			frappe.confirm(
				__(
					"Re-check <b>every affiliate's</b> commission statuses against their Sales Orders / Sales Invoices, then regenerate payouts for <b>all not-yet-paid-out commissions site-wide</b>?<br><br>Commissions already in a payout are never paid twice - newly eligible ones are added to the affiliate's open payout, or bundled into a new one."
				),
				() => {
					frappe.call({
						method: "affiliate.api.payout_batch.check_and_regenerate_all_payouts",
						freeze: true,
						freeze_message: __("Checking all commissions and regenerating payouts..."),
					}).then((r) => {
						frappe.msgprint({
							title: __("Site-wide Payout Check Result"),
							indicator: "green",
							message: render_regenerate_summary(r.message || {}),
						});
					});
				}
			);
		});
	},
});

function render_regenerate_summary(d) {
	const rows = [];

	const changes = d.status_changes || [];
	if (changes.length) {
		const shown = changes.slice(0, 10);
		const more = changes.length - shown.length;
		rows.push(
			"<p><b>" +
				__("Commission statuses updated ({0})", [changes.length]) +
				"</b></p><ul>" +
				shown
					.map((c) => "<li>" + __("{0}: {1} → {2}", [c.commission, c.from, c.to]) + "</li>")
					.join("") +
				(more > 0 ? "<li>" + __("...and {0} more", [more]) + "</li>" : "") +
				"</ul>"
		);
	} else {
		rows.push("<p>" + __("Commission statuses: all already in sync with their Sales Order / Sales Invoice.") + "</p>");
	}

	if ((d.payouts_created || []).length) {
		rows.push(
			"<p>" + __("New payout(s) created ({0}): <b>{1}</b>", [d.payouts_created.length, d.payouts_created.join(", ")]) + "</p>"
		);
	}
	if ((d.payouts_updated || []).length) {
		rows.push(
			"<p>" +
				__("Open payout(s) topped up with newly eligible commissions ({0}): <b>{1}</b>", [
					d.payouts_updated.length,
					d.payouts_updated.join(", "),
				]) +
				"</p>"
		);
	}
	if (!d.payouts_created?.length && !d.payouts_updated?.length) {
		rows.push("<p>" + __("No new payout needed - every eligible commission is already in a payout.") + "</p>");
	}
	if ((d.below_minimum_cashout || []).length) {
		rows.push(
			"<p class='text-muted'>" +
				__("Not paid out - below the minimum cashout: {0}", [d.below_minimum_cashout.join(", ")]) +
				"</p>"
		);
	}
	if (d.still_eligible > 0) {
		rows.push(
			"<p class='text-muted'>" +
				__("{0} commission(s) site-wide still unattached (below minimum cashout, or in a currency with an open payout).", [
					d.still_eligible,
				]) +
				"</p>"
		);
	}

	const ok = !(d.double_payout_check || []).length;
	rows.push(
		"<p class='" + (ok ? "text-success" : "text-danger") + "'><b>" +
			(ok
				? __("✓ Double-payout check: OK - no commission is attached to more than one payout.")
				: __("⚠ Double-payout check FAILED for: {0}", [d.double_payout_check.join(", ")])) +
			"</b></p>"
	);

	return rows.join("");
}
