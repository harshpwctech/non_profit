# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import json

import frappe
from frappe import _
from frappe.email import sendmail_to_system_managers
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form, getdate

from non_profit.non_profit.doctype.membership.membership import verify_signature


class Donation(Document):
	def validate(self):
		if not self.donor or not frappe.db.exists('Donor', self.donor):
			# for web forms
			user_type = frappe.db.get_value('User', frappe.session.user, 'user_type')
			if user_type == 'Website User':
				self.create_donor_for_website_user()
			else:
				frappe.throw(_('Please select a Donor'))

	def create_donor_for_website_user(self):
		donor_name = frappe.get_value('Donor', dict(email=frappe.session.user))

		if not donor_name:
			user = frappe.get_doc('User', frappe.session.user)
			donor = frappe.get_doc(dict(
				doctype='Donor',
				donor_type=self.get('donor_type'),
				email=frappe.session.user,
				member_name=user.get_fullname()
			)).insert(ignore_permissions=True)
			donor_name = donor.name

		if self.get('__islocal'):
			self.donor = donor_name

	def on_payment_authorized(self, status_changed_to=None):
		if status_changed_to not in ("Completed", "Authorized"):
			return
		self.load_from_db()
		self.db_set("paid", 1)
		settings = frappe.get_doc('Non Profit Settings')
		if settings.allow_donation_invoicing and settings.automate_donation_invoicing:
			self.generate_invoice(with_payment_entry=settings.automate_donation_payment_entries, save=True)
	
	@frappe.whitelist()
	def generate_invoice(self, save=True, with_payment_entry=False):
		if not (self.paid or self.currency or self.amount):
			frappe.throw(_("The payment for this donation is not paid. To generate invoice fill the payment details"))

		if self.invoice:
			frappe.throw(_("An invoice is already linked to this document"))

		donor = frappe.get_cached_doc("Donor", self.donor)
		if not donor.customer:
			frappe.throw(_("No customer linked to donor {0}").format(frappe.bold(self.donor)))

		donor_type = frappe.get_cached_doc("Donor Type", self.donor_type)
		settings = frappe.get_cached_doc("Non Profit Settings")
		self.validate_donor_type_and_settings(donor_type, settings)

		invoice = make_invoice(self, donor, donor_type, settings)
		self.reload()
		self.invoice = invoice.name

		if with_payment_entry:
			self.make_payment_entry(settings, invoice)

		if save:
			self.save()

		return invoice
	
	def validate_donor_type_and_settings(self, donor_type, settings):
		settings_link = get_link_to_form("Non Profit Settings", "Non Profit Settings")
		
		if not settings.donation_debit_account:
			frappe.throw(_("You need to set <b>Donor Debit Account</b> in {0}").format(settings_link))

		if not settings.company:
			frappe.throw(_("You need to set <b>Default Company</b> for invoicing in {0}").format(settings_link))

		if not donor_type.linked_item:
			frappe.throw(_("Please set a Linked Item for the Donor Type {0}").format(
				get_link_to_form("Donor Type", donor_type.name)))

	def make_payment_entry(self, settings, invoice):
		if not settings.donation_payment_account:
			frappe.throw(_('You need to set <b>Payment Account</b> for Donation in {0}').format(
				get_link_to_form('Non Profit Settings', 'Non Profit Settings')))

		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
		frappe.flags.ignore_account_permission = True
		pe = get_payment_entry(dt="Sales Invoice", dn=invoice.name, bank_amount=invoice.grand_total)
		frappe.flags.ignore_account_permission = False
		pe.paid_to = settings.donation_payment_account
		pe.posting_date = getdate()
		pe.reference_no = self.name
		pe.reference_date = getdate()
		pe.flags.ignore_mandatory = True
		pe.save()
		pe.submit()

def make_invoice(donation, donor, donor_type, settings):
	invoice = frappe.get_doc({
		"doctype": "Sales Invoice",
		"customer": donor.customer,
		"debit_to": settings.donation_debit_account,
		"currency": donation.currency,
		"company": settings.company,
		"is_pos": 0,
		"items": [
			{
				"item_code": donor_type.linked_item,
				"rate": donation.amount,
				"qty": 1
			}
		]
	})
	invoice.set_missing_values()
	invoice.insert()
	invoice.submit()

	frappe.msgprint(_("Sales Invoice created successfully"))

	return invoice


@frappe.whitelist(allow_guest=True)
def capture_razorpay_donations(*args, **kwargs):
	"""
		Creates Donation from Razorpay Webhook Request Data on payment.captured event
		Creates Donor from email if not found
	"""
	data = frappe.request.get_data(as_text=True)

	try:
		verify_signature(data, endpoint='Donation')
	except Exception as e:
		log = frappe.log_error(e, 'Donation Webhook Verification Error')
		notify_failure(log)
		return { 'status': 'Failed', 'reason': e }

	if isinstance(data, str):
		data = json.loads(data)
	data = frappe._dict(data)

	payment = data.payload.get('payment', {}).get('entity', {})
	payment = frappe._dict(payment)

	try:
		if not data.event == 'payment.captured':
			return

		# to avoid capturing subscription payments as donations
		if payment.invoice_id or (
			payment.description and "subscription" in str(payment.description).lower()
		):
			return

		donor = get_donor(payment.email)
		if not donor:
			donor = create_donor(payment)

		donation = create_razorpay_donation(donor, payment)
		donation.run_method('create_payment_entry')

	except Exception as e:
		message = '{0}\n\n{1}\n\n{2}: {3}'.format(e, frappe.get_traceback(), _('Payment ID'), payment.id)
		log = frappe.log_error(message, _('Error creating donation entry for {0}').format(donor.name))
		notify_failure(log)
		return { 'status': 'Failed', 'reason': e }

	return { 'status': 'Success' }


def create_razorpay_donation(donor, payment):
	if not frappe.db.exists('Mode of Payment', payment.method):
		create_mode_of_payment(payment.method)

	company = get_company_for_donations()
	donation = frappe.get_doc({
		'doctype': 'Donation',
		'company': company,
		'donor': donor.name,
		'donor_name': donor.donor_name,
		'email': donor.email,
		'date': getdate(),
		'amount': flt(payment.amount) / 100, # Convert to rupees from paise
		'mode_of_payment': payment.method,
		'payment_id': payment.id
	}).insert(ignore_mandatory=True)

	donation.submit()
	return donation


def get_donor(email):
	donors = frappe.get_all('Donor',
		filters={'email': email},
		order_by='creation desc')

	try:
		return frappe.get_doc('Donor', donors[0]['name'])
	except Exception:
		return None


@frappe.whitelist()
def create_donor(payment):
	donor_details = frappe._dict(payment)
	donor_type = frappe.db.get_single_value('Non Profit Settings', 'default_donor_type')

	donor = frappe.new_doc('Donor')
	donor.update({
		'donor_name': donor_details.email,
		'donor_type': donor_type,
		'email': donor_details.email,
		'mobile': donor_details.contact
	})

	if donor_details.get('notes'):
		donor = get_additional_notes(donor, donor_details)

	donor.insert(ignore_mandatory=True)
	return donor


def get_company_for_donations():
	company = frappe.db.get_single_value('Non Profit Settings', 'donation_company')
	if not company:
		from non_profit.non_profit.utils import get_company
		company = get_company()
	return company


def get_additional_notes(donor, donor_details):
	if type(donor_details.notes) == dict:
		for k, v in donor_details.notes.items():
			notes = '\n'.join('{}: {}'.format(k, v))

			# extract donor name from notes
			if 'name' in k.lower():
				donor.update({
					'donor_name': donor_details.notes.get(k)
				})

			# extract pan from notes
			if 'pan' in k.lower():
				donor.update({
					'pan_number': donor_details.notes.get(k)
				})

		donor.add_comment('Comment', notes)

	elif type(donor_details.notes) == str:
		donor.add_comment('Comment', donor_details.notes)

	return donor


def create_mode_of_payment(method):
	frappe.get_doc({
		'doctype': 'Mode of Payment',
		'mode_of_payment': method
	}).insert(ignore_mandatory=True)


def notify_failure(log):
	try:
		content = '''
			Dear System Manager,
			Razorpay webhook for creating donation failed due to some reason.
			Please check the error log linked below
			Error Log: {0}
			Regards, Administrator
		'''.format(get_link_to_form('Error Log', log.name))

		sendmail_to_system_managers(_('[Important] [ERPNext] Razorpay donation webhook failed, please check.'), content)
	except Exception:
		pass
