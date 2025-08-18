# email_utils.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, Content, Attachment
import base64

def send_booking_confirmation_with_pdf(to_emails, subject, html_body, pdf_bytes, filename="cmr_consignment_note.pdf", cc_emails=None):
    """
    to_emails: lista med mottagare (str)
    cc_emails: lista eller None
    """
    sg_api = os.environ.get("SENDGRID_API_KEY")
    if not sg_api:
        raise RuntimeError("SENDGRID_API_KEY is not set")

    message = Mail(
        from_email=os.environ.get("FROM_EMAIL", "no-reply@easyfreightbooking.com"),
        to_emails=to_emails,
        subject=subject,
        html_content=html_body
    )
    if cc_emails:
        for cc in cc_emails:
            message.add_cc(cc)

    encoded = base64.b64encode(pdf_bytes).decode()
    attachment = Attachment()
    attachment.file_content = encoded
    attachment.file_type = "application/pdf"
    attachment.file_name = filename
    attachment.disposition = "attachment"
    message.attachment = attachment

    sg = SendGridAPIClient(sg_api)
    resp = sg.send(message)
    return resp.status_code
