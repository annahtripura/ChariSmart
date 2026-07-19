from datetime import datetime
import os
import smtplib
from email.message import EmailMessage
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///food_distribution.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

DONATION_STATUSES = ["pending", "approved", "rejected", "delivered", "expired"]
REQUEST_STATUSES = ["pending", "approved", "rejected", "fulfilled"]


# ---------------- Models ----------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin, donor, user
    area = db.Column(db.String(120), nullable=True)
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    donations = db.relationship("Donation", backref="donor", lazy=True)
    food_requests = db.relationship("FoodRequest", backref="requester", lazy=True)


class Donation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    food_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.String(100), nullable=False)
    area = db.Column(db.String(120), nullable=False)
    pickup_address = db.Column(db.String(255), nullable=False)
    expire_at = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default="pending")  # pending, approved, rejected, delivered, expired
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    donor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class FoodRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    food_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.String(100), nullable=False)
    area = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default="pending")  # pending, approved, rejected, fulfilled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receiver_email = db.Column(db.String(150), nullable=False)
    area = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    donation_id = db.Column(db.Integer, db.ForeignKey("donation.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------- Helpers ----------------

def normalize_area(area):
    return " ".join((area or "").strip().split()).title()


def parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


@app.template_filter("datetime_format")
def datetime_format(value):
    if not value:
        return "Not set"
    return value.strftime("%d %b %Y, %I:%M %p")


def get_admin_email():
    return os.getenv("MAIL_USERNAME", "admin@example.com") or "admin@example.com"


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        if user.is_blocked:
            session.clear()
            flash("Your account has been blocked by admin.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user or user.role != role:
                flash("You are not allowed to access this page.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


@app.context_processor
def inject_user():
    return {
        "auth_user": current_user(),
        "admin_email": get_admin_email(),
        "now": datetime.utcnow(),
    }


def send_email(to_email, subject, body):
    """Send email using SMTP. If SMTP is not configured, print email in terminal."""
    mail_server = os.getenv("MAIL_SERVER", "").strip()
    mail_port = int(os.getenv("MAIL_PORT", "587"))
    mail_username = os.getenv("MAIL_USERNAME", "").strip()
    mail_password = os.getenv("MAIL_PASSWORD", "").strip()
    mail_from = os.getenv("MAIL_FROM", mail_username or "no-reply@example.com").strip()
    use_tls = os.getenv("EMAIL_USE_TLS", "true").lower() in ["true", "1", "yes"]

    if not mail_server or not mail_username or not mail_password:
        print("\n========== EMAIL NOT CONFIGURED ==========")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("==========================================\n")
        return False

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(mail_server, mail_port) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.login(mail_username, mail_password)
        smtp.send_message(msg)

    return True


def email_donor(donation, subject, status_note):
    body = f"""
Dear {donation.donor.name},

Your food donation status has been updated.

Food: {donation.food_name}
Quantity: {donation.quantity}
Area: {donation.area}
Pickup Address: {donation.pickup_address}
Expire Time: {datetime_format(donation.expire_at)}
Current Status: {donation.status.title()}

Message:
{status_note}

ChariSmart
""".strip()
    try:
        send_email(donation.donor.email, subject, body)
    except Exception as e:
        print(f"Donor email failed for {donation.donor.email}: {e}")


def notify_area_users_for_donation(donation):
    area_users = User.query.filter_by(role="user", area=donation.area, is_blocked=False).all()

    subject = f"Food Available in {donation.area}"
    body = f"""
Dear user,

New food donation has been approved in your area.

Food: {donation.food_name}
Quantity: {donation.quantity}
Area: {donation.area}
Pickup Address: {donation.pickup_address}
Expire Time: {datetime_format(donation.expire_at)}
Donor Contact: {donation.donor.email}

Description:
{donation.description or "N/A"}

ChariSmart
""".strip()

    for receiver in area_users:
        db.session.add(Notification(
            receiver_email=receiver.email,
            area=donation.area,
            message=body,
            donation_id=donation.id,
        ))
        try:
            send_email(receiver.email, subject, body)
        except Exception as e:
            print(f"Area user email failed for {receiver.email}: {e}")

    db.session.commit()
    return len(area_users)


def auto_expire_donations():
    expired_items = Donation.query.filter(
        Donation.status == "approved",
        Donation.expire_at.isnot(None),
        Donation.expire_at < datetime.utcnow(),
    ).all()

    if not expired_items:
        return 0

    for item in expired_items:
        item.status = "expired"
        email_donor(
            item,
            "Your Food Donation Has Expired",
            "The expiry time passed, so the donation was automatically marked as expired.",
        )
    db.session.commit()
    return len(expired_items)


@app.before_request
def before_every_request():
    if request.endpoint != "static":
        try:
            auto_expire_donations()
        except Exception as e:
            print(f"Auto expiry check failed: {e}")


def admin_stats():
    return {
        "total_users": User.query.filter_by(role="user").count(),
        "total_donors": User.query.filter_by(role="donor").count(),
        "total_donations": Donation.query.count(),
        "pending_donations": Donation.query.filter_by(status="pending").count(),
        "approved_donations": Donation.query.filter_by(status="approved").count(),
        "delivered_donations": Donation.query.filter_by(status="delivered").count(),
        "expired_donations": Donation.query.filter_by(status="expired").count(),
        "food_requests": FoodRequest.query.count(),
    }


# ---------------- Routes ----------------

@app.route("/")
def home():
    approved_donations = Donation.query.filter_by(status="approved").order_by(Donation.created_at.desc()).limit(6).all()
    recent_requests = FoodRequest.query.filter_by(status="approved").order_by(FoodRequest.created_at.desc()).limit(4).all()
    return render_template("home.html", donations=approved_donations, requests=recent_requests)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        area = normalize_area(request.form.get("area", ""))

        if role not in ["user", "donor"]:
            flash("Only User or Donor can register here.", "danger")
            return redirect(url_for("register"))

        if not name or not email or not password:
            flash("Name, email and password are required.", "danger")
            return redirect(url_for("register"))

        if role == "user" and not area:
            flash("Area is required for normal users.", "danger")
            return redirect(url_for("register"))

        exists = User.query.filter_by(email=email).first()
        if exists:
            flash("Email already registered. Please login.", "warning")
            return redirect(url_for("login"))

        new_user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            area=area if role == "user" else None,
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            if user.is_blocked:
                flash("Your account is blocked. Please contact admin.", "danger")
                return redirect(url_for("login"))
            session["user_id"] = user.id
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    if user.role == "admin":
        donations = Donation.query.order_by(Donation.created_at.desc()).all()
        food_requests = FoodRequest.query.order_by(FoodRequest.created_at.desc()).limit(8).all()
        users = User.query.order_by(User.created_at.desc()).limit(6).all()
        return render_template(
            "admin_dashboard.html",
            donations=donations,
            food_requests=food_requests,
            users=users,
            stats=admin_stats(),
        )

    if user.role == "donor":
        donations = Donation.query.filter_by(donor_id=user.id).order_by(Donation.created_at.desc()).all()
        open_requests = FoodRequest.query.filter(FoodRequest.status.in_(["pending", "approved"])).order_by(FoodRequest.created_at.desc()).limit(8).all()
        stats = {
            "total": len(donations),
            "pending": sum(1 for d in donations if d.status == "pending"),
            "approved": sum(1 for d in donations if d.status == "approved"),
            "delivered": sum(1 for d in donations if d.status == "delivered"),
            "expired": sum(1 for d in donations if d.status == "expired"),
        }
        return render_template("donor_dashboard.html", donations=donations, requests=open_requests, stats=stats)

    donations = Donation.query.filter_by(status="approved", area=user.area).order_by(Donation.created_at.desc()).all()
    notifications = Notification.query.filter_by(receiver_email=user.email).order_by(Notification.created_at.desc()).all()
    my_requests = FoodRequest.query.filter_by(user_id=user.id).order_by(FoodRequest.created_at.desc()).all()
    stats = {
        "available": len(donations),
        "requests": len(my_requests),
        "notifications": len(notifications),
    }
    return render_template("user_dashboard.html", donations=donations, notifications=notifications, my_requests=my_requests, stats=stats)


@app.route("/donate", methods=["GET", "POST"])
@login_required
@role_required("donor")
def donate():
    if request.method == "POST":
        food_name = request.form.get("food_name", "").strip()
        quantity = request.form.get("quantity", "").strip()
        area = normalize_area(request.form.get("area", ""))
        pickup_address = request.form.get("pickup_address", "").strip()
        expire_at = parse_datetime(request.form.get("expire_at", ""))
        description = request.form.get("description", "").strip()

        if not food_name or not quantity or not area or not pickup_address or not expire_at:
            flash("Please fill all required fields with valid expiry time.", "danger")
            return redirect(url_for("donate"))

        donation = Donation(
            food_name=food_name,
            quantity=quantity,
            area=area,
            pickup_address=pickup_address,
            expire_at=expire_at,
            description=description,
            donor_id=current_user().id,
        )
        db.session.add(donation)
        db.session.commit()

        flash("Donation submitted. Waiting for admin approval.", "success")
        return redirect(url_for("dashboard"))

    return render_template("donate.html")


@app.route("/request-food", methods=["GET", "POST"])
@login_required
@role_required("user")
def request_food():
    user = current_user()
    if request.method == "POST":
        food_name = request.form.get("food_name", "").strip()
        quantity = request.form.get("quantity", "").strip()
        area = normalize_area(request.form.get("area", user.area))
        message = request.form.get("message", "").strip()

        if not food_name or not quantity or not area:
            flash("Food name, quantity and area are required.", "danger")
            return redirect(url_for("request_food"))

        food_request = FoodRequest(
            food_name=food_name,
            quantity=quantity,
            area=area,
            message=message,
            user_id=user.id,
        )
        db.session.add(food_request)
        db.session.commit()
        flash("Food request submitted. Admin will review it.", "success")
        return redirect(url_for("dashboard"))

    return render_template("request_food.html")


@app.route("/requests")
@login_required
def requests_list():
    user = current_user()
    if user.role == "user":
        requests_data = FoodRequest.query.filter_by(user_id=user.id).order_by(FoodRequest.created_at.desc()).all()
    else:
        requests_data = FoodRequest.query.order_by(FoodRequest.created_at.desc()).all()
    return render_template("requests.html", requests_data=requests_data)


@app.route("/admin/request/<int:request_id>/<action>", methods=["POST"])
@login_required
@role_required("admin")
def update_request_status(request_id, action):
    food_request = FoodRequest.query.get_or_404(request_id)
    if action not in REQUEST_STATUSES:
        flash("Invalid request status.", "danger")
        return redirect(url_for("requests_list"))
    food_request.status = action
    db.session.commit()
    flash(f"Food request marked as {action}.", "success")
    return redirect(url_for("requests_list"))


@app.route("/admin/donation/<int:donation_id>/<status>", methods=["POST"])
@login_required
@role_required("admin")
def update_donation_status(donation_id, status):
    donation = Donation.query.get_or_404(donation_id)
    if status not in DONATION_STATUSES:
        flash("Invalid donation status.", "danger")
        return redirect(url_for("dashboard"))

    donation.status = status
    db.session.commit()

    if status == "approved":
        total_notified = notify_area_users_for_donation(donation)
        email_donor(donation, "Your Food Donation Was Approved", "Admin approved your donation. Users in that area have been notified.")
        flash(f"Donation approved. {total_notified} user(s) from {donation.area} notified.", "success")
    elif status == "rejected":
        email_donor(donation, "Your Food Donation Was Rejected", "Admin rejected your donation request.")
        flash("Donation rejected and donor notified.", "info")
    elif status == "delivered":
        email_donor(donation, "Your Food Donation Was Marked Delivered", "Admin marked your food donation as delivered.")
        flash("Donation marked as delivered and donor notified.", "success")
    elif status == "expired":
        email_donor(donation, "Your Food Donation Was Marked Expired", "Admin marked your food donation as expired.")
        flash("Donation marked as expired and donor notified.", "warning")
    else:
        flash(f"Donation status changed to {status}.", "success")

    return redirect(url_for("dashboard"))


@app.route("/donation/<int:donation_id>/delete", methods=["POST"])
@login_required
def delete_donation(donation_id):
    user = current_user()
    donation = Donation.query.get_or_404(donation_id)

    if user.role != "admin" and not (user.role == "donor" and donation.donor_id == user.id):
        flash("You are not allowed to delete this donation.", "danger")
        return redirect(url_for("dashboard"))

    food_name = donation.food_name
    donor_email = donation.donor.email
    donor_name = donation.donor.name

    Notification.query.filter_by(donation_id=donation.id).delete()
    db.session.delete(donation)
    db.session.commit()

    if user.role == "admin":
        try:
            send_email(
                donor_email,
                "Your Food Donation Was Deleted",
                f"Dear {donor_name},\n\nYour food donation '{food_name}' was deleted by admin.\n\nChariSmart",
            )
        except Exception as e:
            print(f"Donation delete email failed for {donor_email}: {e}")
        flash("Donation deleted and donor notified.", "success")
    else:
        flash("Your donation item has been deleted.", "success")

    return redirect(url_for("dashboard"))


@app.route("/admin/users")
@login_required
@role_required("admin")
def manage_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("manage_users.html", users=users)


@app.route("/admin/user/<int:user_id>/toggle-block", methods=["POST"])
@login_required
@role_required("admin")
def toggle_block_user(user_id):
    target = User.query.get_or_404(user_id)
    if target.role == "admin":
        flash("Admin account cannot be blocked.", "danger")
        return redirect(url_for("manage_users"))
    target.is_blocked = not target.is_blocked
    db.session.commit()
    flash(f"{target.name} has been {'blocked' if target.is_blocked else 'unblocked'}.", "success")
    return redirect(url_for("manage_users"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(user_id):
    target = User.query.get_or_404(user_id)
    if target.role == "admin":
        flash("Admin account cannot be deleted.", "danger")
        return redirect(url_for("manage_users"))

    Notification.query.filter_by(receiver_email=target.email).delete()
    FoodRequest.query.filter_by(user_id=target.id).delete()

    donations = Donation.query.filter_by(donor_id=target.id).all()
    for donation in donations:
        Notification.query.filter_by(donation_id=donation.id).delete()
        db.session.delete(donation)

    db.session.delete(target)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(url_for("manage_users"))


# ---------------- Database setup ----------------

def sqlite_columns(table_name):
    result = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return [row[1] for row in result]


def ensure_database_schema():
    """Small helper for users updating from the old lab ZIP without migrations."""
    try:
        user_cols = sqlite_columns("user")
        if user_cols:
            if "is_blocked" not in user_cols:
                db.session.execute(text("ALTER TABLE user ADD COLUMN is_blocked BOOLEAN DEFAULT 0"))
            if "created_at" not in user_cols:
                db.session.execute(text("ALTER TABLE user ADD COLUMN created_at DATETIME"))

        donation_cols = sqlite_columns("donation")
        if donation_cols and "expire_at" not in donation_cols:
            db.session.execute(text("ALTER TABLE donation ADD COLUMN expire_at DATETIME"))

        notification_cols = sqlite_columns("notification")
        if notification_cols and "donation_id" not in notification_cols:
            db.session.execute(text("ALTER TABLE notification ADD COLUMN donation_id INTEGER"))

        db.session.commit()
    except Exception as e:
        print(f"Schema upgrade skipped: {e}")
        db.session.rollback()


def seed_admin():
    admin = User.query.filter_by(email="admin@example.com").first()
    if not admin:
        admin = User(
            name="Admin",
            email="admin@example.com",
            password_hash=generate_password_hash("admin123"),
            role="admin",
            area=None,
            is_blocked=False,
        )
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_database_schema()
    seed_admin()


if __name__ == "__main__":
    app.run(debug=True)
