# Stripe shim removed.
# The lightweight shim that previously existed has been intentionally removed
# as Stripe has been deprioritized in this project. Do NOT import `stripe` at
# module level. If you need compatibility mode, set STRIPE_SECRET_KEY and
# install the official `stripe` package; code should then import `stripe`
# inside runtime paths guarded by that environment variable.

raise ImportError("Stripe shim removed. Install 'stripe' and set STRIPE_SECRET_KEY to enable legacy compatibility mode.")
