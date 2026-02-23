import os

# Railway sets PORT env var dynamically
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 2
timeout = 120
loglevel = "info"
accesslog = "-"
errorlog = "-"
