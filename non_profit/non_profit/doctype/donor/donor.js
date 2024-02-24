// Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Donor', {
	refresh: function(frm) {
		frappe.dynamic_link = {doc: frm.doc, fieldname: 'name', doctype: 'Donor'};

		frm.toggle_display(['address_html','contact_html'], !frm.doc.__islocal);

		if(!frm.doc.__islocal) {
			frappe.contacts.render_address_and_contact(frm);
			if (!frm.doc.customer) {
				frm.add_custom_button(__('Create Customer'), () => {
					frm.call('make_customer_and_link').then(() => {
						frm.reload_doc();
					});
				});
			}
		} else {
			frappe.contacts.clear_address_and_contact(frm);
		}
		

	}
});
