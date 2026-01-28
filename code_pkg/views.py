from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from extensions import db, login_manager
from cad.models import User, Lead

admin_bp = Blueprint("admin", __name__)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class SimpleUser(User):
    # Flask-Login requires is_authenticated etc.; User inherits nothing
    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)

@admin_bp.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        u = User.query.filter_by(email=email).first()
        if u and u.check_password(password):
            # Wrap into SimpleUser for Flask-Login compatibility
            su = SimpleUser()
            su.id = u.id
            su.email = u.email
            su.password_hash = u.password_hash
            login_user(su)
            return redirect(url_for("admin.dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("admin/login.html")

@admin_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("admin.login"))

@admin_bp.route("/")
@login_required
def dashboard():
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return render_template("admin/dashboard.html", leads=leads)
