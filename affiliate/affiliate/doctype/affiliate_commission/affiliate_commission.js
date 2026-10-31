// Copyright (c) 2026, Warga Prihatin and contributors
// For license information, please see license.txt

frappe.ui.form.on("Affiliate Commission", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Check & Regenerate Payout"), () => {
			frappe.confirm(
				__(
					"Re-check every linked Sales Order / Sales Invoice status for <b>{0}</b>'s commissions, then regenerate payouts from all eligible commissions?<br><br>Commissions already attached to a payout are never paid twice - anything new is added to the affiliate's open payout, or bundled into a new one.",
					[frm.doc.affiliate]
				),
				() => {
					frappe.call({
						method: "affiliate.api.payout_batch.check_and_regenerate_payout",
						args: { commission: frm.doc.name },
						freeze: true,
						freeze_message: __("Checking statuses and regenerating payouts..."),
					}).then((r) => {
						frm.reload_doc();
						frappe.msgprint({
							title: __("Payout Check Result"),
							indicator: "green",
							message: render_result(r.message || {}),
						});
					});
				}
			);
		});
	},
});

function render_result(d) {
	const rows = [];

	const changes = d.status_changes || [];
	if (changes.length) {
		rows.push(
			"<p><b>" +
				__("Commission statuses updated ({0})", [changes.length]) +
				"</b></p><ul>" +
				changes
					.map(
						(c) =>
							"<li>" +
							__("{0}: {1} → {2}", [c.commission, c.from, c.to]) +
							"</li>"
					)
					.join("") +
				"</ul>"
		);
	} else {
		rows.push("<p>" + __("Commission statuses: all already in sync with their Sales Order / Sales Invoice.") + "</p>");
	}

	if ((d.payouts_created || []).length) {
		rows.push(
			"<p>" + __("New payout(s) created: <b>{0}</b>", [d.payouts_created.join(", ")]) + "</p>"
		);
	}
	if ((d.payouts_updated || []).length) {
		rows.push(
			"<p>" +
				__("Open payout(s) topped up with newly eligible commissions: <b>{0}</b>", [
					d.payouts_updated.join(", "),
				]) +
				"</p>"
		);
	}
	if (!d.payouts_created?.length && !d.payouts_updated?.length) {
		rows.push("<p>" + __("No new payout needed.") + "</p>");
	}
	if ((d.below_minimum_cashout || []).length) {
		rows.push(
			"<p class='text-muted'>" +
				__("Not paid out - below the minimum cashout in: {0}", [d.below_minimum_cashout.join(", ")]) +
				"</p>"
		);
	}
	if (d.still_eligible > 0) {
		rows.push(
			"<p class='text-muted'>" +
				__("{0} commission(s) still unattached (below minimum cashout, or in a currency with an open payout).", [
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
