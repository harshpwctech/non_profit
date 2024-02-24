// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Donation', {
	refresh: function(frm) {
		!frm.doc.invoice && frm.doc.docstatus ===1 && frm.add_custom_button("Generate Invoice", () => {
			frm.call({
				doc: frm.doc,
				method: "generate_invoice",
				args: {save: true},
				freeze: true,
				freeze_message: __("Creating Donation Invoice"),
				callback: function(r) {
					if (r.invoice)
						frm.reload_doc();
				}
			});
		});
	},
});
