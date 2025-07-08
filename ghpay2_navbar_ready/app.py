from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv
import requests
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import smtplib
from email.message import EmailMessage

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# ENV Variables
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")  # Add to your .env
EMAIL_ADDRESS = os.getenv("EMAIL_USER")                 # Gmail
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")                # Gmail App Password

DATABASE = 'payments.db'
ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

RECEIPT_FOLDER = 'receipts'
os.makedirs(RECEIPT_FOLDER, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT,
        name TEXT,
        email TEXT,
        amount INTEGER,
        reference TEXT,
        status TEXT,
        timestamp TEXT
    )''')
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/payment')
def payment():
    return render_template('payment.html')

@app.route('/admin', methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USER and password == ADMIN_PASS:
            session["admin_logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid credentials"
    return render_template("admin_login.html", error=error)

@app.route('/dashboard')
def dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT * FROM payments ORDER BY id DESC")
    records = c.fetchall()
    conn.close()
    return render_template('dashboard.html', records=records)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route('/thank_you')
def thank_you():
    student_id = request.args.get('student_id')
    return render_template('thank_you.html', student_id=student_id)

@app.route('/save_payment', methods=['POST'])
def save_payment():
    data = request.get_json()
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''INSERT INTO payments (student_id, name, email, amount, reference, status, timestamp)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (data['student_id'], data['name'], data['email'], data['amount'],
               data['reference'], data['status'], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return {'message': 'Payment saved'}

# ✅ NEW: Verify Paystack Payment, Generate & Email Receipt
@app.route('/verify_payment', methods=['POST'])
def verify_payment():
    data = request.get_json()
    reference = data.get('reference')

    if not reference:
        return jsonify({'error': 'Missing reference'}), 400

    headers = {
        'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'
    }
    response = requests.get(f'https://api.paystack.co/transaction/verify/{reference}', headers=headers)

    if response.status_code != 200:
        return jsonify({'error': 'Verification failed'}), 500

    result = response.json().get('data')

    if result['status'] == 'success':
        # Save to DB
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute('''INSERT INTO payments (student_id, name, email, amount, reference, status, timestamp)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (data.get('student_id'), result['customer']['first_name'], result['customer']['email'],
                   result['amount'], reference, result['status'],
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()

        # Generate receipt
        receipt_name = f"receipt-{reference}.pdf"
        receipt_path = os.path.join(RECEIPT_FOLDER, receipt_name)
        generate_pdf_receipt(result, receipt_path)

        # Send email
        send_email_with_receipt(result['customer']['email'], receipt_path)

        return jsonify({
            'status': 'success',
            'receipt_url': f'/receipts/{receipt_name}'
        })

    return jsonify({'status': 'failed'}), 400

# ✅ PDF Generator
def generate_pdf_receipt(data, path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 12)
    y = 750

    c.drawString(200, y, "SCHOOL PAYMENT RECEIPT")
    y -= 40

    items = [
        ("Reference", data['reference']),
        ("Student Email", data['customer']['email']),
        ("Amount Paid", f"₦{data['amount'] / 100:.2f}"),
        ("Date", datetime.strptime(data['paid_at'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%d %b %Y, %I:%M %p')),
        ("Status", data['status']),
        ("Channel", data['channel']),
        ("Bank", data['authorization']['bank']),
    ]

    for label, value in items:
        c.drawString(50, y, f"{label}: {value}")
        y -= 25

    c.drawString(200, y - 30, "Thank you for your payment!")
    c.save()

# ✅ Email Sender
def send_email_with_receipt(to_email, receipt_path):
    msg = EmailMessage()
    msg['Subject'] = 'Your School Fee Payment Receipt'
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = to_email
    msg.set_content('Thank you for your payment. Please find your receipt attached.')

    with open(receipt_path, 'rb') as f:
        file_data = f.read()
        file_name = os.path.basename(receipt_path)
        msg.add_attachment(file_data, maintype='application', subtype='pdf', filename=file_name)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

# ✅ Serve PDF Receipt
@app.route('/receipts/<filename>')
def get_receipt(filename):
    return send_from_directory(RECEIPT_FOLDER, filename)

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
