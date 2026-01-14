SINCOR RAILWAY DEPLOYMENT - SUPER SIMPLE!
==========================================

STEP 1: Copy these 4 files to your sincor-clean folder:
- simple.py (the main app)
- Procfile (Railway startup)
- runtime.txt (Python version)
- requirements.txt (dependencies)

STEP 2: In your sincor-clean folder terminal, run:
git add -A
git commit -m "SINCOR Railway deployment ready"
git push origin main

STEP 3: Railway will auto-deploy!

STEP 4: Add getsincor.com in Railway settings:
1. Go to Railway → Your Project → Settings → Domains
2. Click "Add Domain" → Enter "getsincor.com" 
3. Railway gives you a CNAME target (like abc123.up.railway.app)
4. In Namecheap DNS settings:
   - Add CNAME Record: "www" → [Railway's CNAME target]  
   - Add ALIAS/ANAME Record: "@" → [Railway's CNAME target]
   - OR if no ALIAS support: Add URL Redirect: getsincor.com → www.getsincor.com

DONE! 🚀 getsincor.com will work!

Need help? The files are tested and ready to go.