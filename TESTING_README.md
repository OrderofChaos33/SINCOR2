# SINCOR Testing & Validation Suite

This directory contains comprehensive testing tools to diagnose crashes and validate system functionality before deployment.

## 🚨 Quick Start - If Your App Keeps Crashing

1. **Run Quick Health Check** (Windows):
   ```bash
   quick_health_check.bat
   ```

2. **Run Comprehensive Diagnostics**:
   ```bash
   python test_crash_diagnostics.py
   ```

3. **Fix Critical Issues** shown in the diagnostics output

4. **Validate Fixes**:
   ```bash
   python run_final_checks.py
   ```

## 📋 Testing Tools Overview

### 1. `quick_health_check.bat` - Fast System Check
- ✅ Quick 30-second health check
- ✅ Verifies Python environment
- ✅ Checks critical files exist
- ✅ Tests basic syntax
- ✅ Validates directory structure

**When to use**: First thing to run when experiencing issues

### 2. `test_crash_diagnostics.py` - Deep Issue Analysis  
- 🔍 Comprehensive crash analysis
- 🔍 Environment validation
- 🔍 Module import testing
- 🔍 Database connectivity checks
- 🔍 Log analysis for error patterns
- 🔍 Configuration validation

**When to use**: When app crashes or fails to start

### 3. `test_e2e_validation.py` - End-to-End Testing
- 🧪 Full application testing
- 🧪 Web endpoint validation  
- 🧪 Lead capture flow testing
- 🧪 Agent system verification
- 🧪 Performance metrics collection
- 🧪 Database operation testing

**When to use**: Before deployment or after major changes

### 4. `run_final_checks.py` - Deployment Validation
- 🚀 Production readiness assessment
- 🚀 Security configuration checks
- 🚀 Environment variable validation
- 🚀 Runs all other test suites
- 🚀 Generates deployment checklist

**When to use**: Final check before going live

## 🔧 Common Issues & Solutions

### Issue: "Google API key not configured"
**Solution**: 
```bash
# Set environment variable or add to config/production.env
set GOOGLE_API_KEY=your_api_key_here
```

### Issue: "Module import failed"
**Solution**:
```bash
pip install -r requirements.txt
```

### Issue: "Database connection failed"
**Solution**:
- Check if `data/` directory exists
- Verify write permissions
- Run: `mkdir data` if missing

### Issue: "Flask app crashes on startup"
**Solution**:
1. Run `python test_crash_diagnostics.py`
2. Check logs/run.log for specific errors
3. Fix import errors in route modules
4. Verify all environment variables are set

### Issue: "Agents failing to start"
**Solution**:
1. Check agents/daetime/scheduler_harness.py imports
2. Verify taskpool modules exist
3. Check agents/base_agent.py for syntax errors

## 📊 Test Reports

All tests generate detailed JSON reports in the `logs/` directory:

- `crash_diagnostics_YYYYMMDD_HHMMSS.json`
- `e2e_test_report_YYYYMMDD_HHMMSS.json`  
- `final_validation_YYYYMMDD_HHMMSS.json`

## 🚀 Deployment Checklist

Before deploying to production:

1. ✅ Run `python run_final_checks.py`
2. ✅ All critical blockers resolved
3. ✅ Production environment variables set
4. ✅ Strong Flask secret key configured
5. ✅ SMTP settings configured for emails
6. ✅ Database backups created
7. ✅ Monitoring systems ready
8. ✅ Rollback plan prepared

## 📞 Emergency Troubleshooting

### App Won't Start At All:
1. `quick_health_check.bat`
2. `python test_crash_diagnostics.py`
3. Check Python version (needs 3.8+)
4. Install dependencies: `pip install -r requirements.txt`

### App Starts But Crashes Quickly:
1. Check `logs/run.log` for immediate errors
2. Run `python test_crash_diagnostics.py`
3. Look for import errors in route modules
4. Check database permissions

### Agents Keep Failing:
1. Check `logs/sincor_engine.log`
2. Verify API keys are set (Google, etc.)
3. Test agent imports manually
4. Check scheduler_harness.py for errors

### Performance Issues:
1. Run `python test_e2e_validation.py`
2. Check performance_metrics in report
3. Monitor database query times
4. Review agent execution logs

## 🔍 Log File Locations

- **Main App**: `logs/run.log`
- **Engine**: `logs/sincor_engine.log`
- **Agents**: `logs/daetime/run_*.log`
- **Compliance**: `logs/compliance/compliance_*.log`
- **Test Reports**: `logs/*_test_*.json`

## 💡 Pro Tips

1. **Run diagnostics after every major change**
2. **Keep test reports for troubleshooting history**
3. **Monitor logs in real-time during testing**
4. **Use quick_health_check.bat for daily verification**
5. **Always run final_checks.py before deployment**

## 🆘 Still Having Issues?

1. Check the generated test reports in `logs/`
2. Look for patterns in the error logs
3. Verify all file permissions are correct
4. Ensure no antivirus blocking Python execution
5. Try running with administrator privileges
6. Check Windows Firewall isn't blocking ports

---

**Remember**: These tests are your safety net. Run them frequently and trust their output!