import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-not-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///cad.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    MAIL_SENDER = os.getenv("MAIL_SENDER", "no-reply@example.com")
