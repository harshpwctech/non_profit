# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.contacts.address_and_contact import load_address_and_contact
from frappe.model.document import Document


class Donor(Document):
	def onload(self):
		"""Load address and contacts in `__onload`"""
		load_address_and_contact(self)

	def validate(self):
		from frappe.utils import validate_email_address
		if self.email:
			validate_email_address(self.email.strip(), True)
	
	@frappe.whitelist()
	def make_customer_and_link(self):
		if self.customer:
			frappe.msgprint(_("A customer is already linked to this Member"))

		customer = create_customer(frappe._dict({
			'fullname': self.donor_name,
			'email': self.email,
			'mobile': self.mobile
		}))

		self.customer = customer
		self.save()
		frappe.msgprint(_("Customer {0} has been created succesfully.").format(self.customer))


def create_customer(user_details, member=None):
	customer = frappe.new_doc("Customer")
	customer.customer_name = user_details.fullname
	customer.customer_type = "Individual"
	customer.customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
	customer.territory = frappe.db.get_single_value("Selling Settings", "territory")
	customer.flags.ignore_mandatory = True
	customer.insert(ignore_permissions=True)

	try:
		frappe.db.savepoint("contact_creation")
		contact = frappe.new_doc("Contact")
		contact.first_name = user_details.fullname
		if user_details.mobile:
			contact.add_phone(user_details.mobile, is_primary_phone=1, is_primary_mobile_no=1)
		if user_details.email:
			contact.add_email(user_details.email, is_primary=1)
		contact.insert(ignore_permissions=True)

		contact.append("links", {
			"link_doctype": "Customer",
			"link_name": customer.name
		})

		if member:
			contact.append("links", {
				"link_doctype": "Member",
				"link_name": member
			})

		contact.save(ignore_permissions=True)

	except frappe.DuplicateEntryError:
		return customer.name

	except Exception as e:
		frappe.db.rollback(save_point="contact_creation")
		frappe.log_error(frappe.get_traceback(), _("Contact Creation Failed"))
		pass

	return customer.name
