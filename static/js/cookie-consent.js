/**
 * SINCOR Cookie Consent Banner
 * GDPR/CCPA Compliant Cookie Management
 */

(function() {
    'use strict';

    const COOKIE_CONSENT_KEY = 'sincor_cookie_consent';
    const COOKIE_EXPIRY_DAYS = 365;

    // Cookie categories
    const COOKIE_CATEGORIES = {
        essential: { name: 'Essential', required: true },
        analytics: { name: 'Analytics', required: false },
        functional: { name: 'Functional', required: false },
        marketing: { name: 'Marketing', required: false }
    };

    // Check if consent was already given
    function hasConsent() {
        return localStorage.getItem(COOKIE_CONSENT_KEY) !== null;
    }

    // Get consent preferences
    function getConsentPreferences() {
        const stored = localStorage.getItem(COOKIE_CONSENT_KEY);
        if (stored) {
            try {
                return JSON.parse(stored);
            } catch (e) {
                return null;
            }
        }
        return null;
    }

    // Save consent preferences
    function saveConsentPreferences(preferences) {
        localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({
            ...preferences,
            timestamp: new Date().toISOString()
        }));
        
        // Set expiry cookie
        const expires = new Date();
        expires.setDate(expires.getDate() + COOKIE_EXPIRY_DAYS);
        document.cookie = `${COOKIE_CONSENT_KEY}=true; expires=${expires.toUTCString()}; path=/; SameSite=Lax`;
    }

    // Apply consent preferences
    function applyConsent(preferences) {
        // Load analytics if consented
        if (preferences.analytics) {
            loadAnalytics();
        }
        
        // Load marketing if consented
        if (preferences.marketing) {
            loadMarketing();
        }
        
        // Functional cookies always active for better UX
        if (preferences.functional) {
            enableFunctionalCookies();
        }
    }

    // Load Google Analytics (only if consented)
    function loadAnalytics() {
        if (window.gtag) return; // Already loaded
        
        // Set your Google Analytics 4 Measurement ID here
        const GA_MEASUREMENT_ID = ''; // e.g., 'G-XXXXXXXXXX'
        
        if (!GA_MEASUREMENT_ID) {
            console.log('Analytics consent granted but GA_MEASUREMENT_ID not configured');
            return;
        }
        
        const script = document.createElement('script');
        script.async = true;
        script.src = `https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`;
        document.head.appendChild(script);
        
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', GA_MEASUREMENT_ID, {
            'anonymize_ip': true,
            'cookie_flags': 'SameSite=None;Secure',
            'cookie_domain': 'auto',
            'cookie_expires': 63072000 // 2 years
        });
    }

    // Load marketing scripts
    function loadMarketing() {
        // Add Facebook Pixel, LinkedIn Insight, etc. here
        console.log('Marketing cookies enabled');
    }

    // Enable functional cookies
    function enableFunctionalCookies() {
        console.log('Functional cookies enabled');
    }

    // Create cookie banner HTML
    function createBanner() {
        const banner = document.createElement('div');
        banner.id = 'cookie-consent-banner';
        banner.innerHTML = `
            <div style="
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                color: white;
                padding: 24px;
                box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.3);
                z-index: 9999;
                font-family: 'Inter', system-ui, sans-serif;
            ">
                <div style="max-width: 1200px; margin: 0 auto;">
                    <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 24px;">
                        <div style="flex: 1; min-width: 300px;">
                            <h3 style="font-size: 18px; font-weight: 600; margin: 0 0 8px 0;">
                                🍪 We Value Your Privacy
                            </h3>
                            <p style="margin: 0; opacity: 0.9; font-size: 14px; line-height: 1.6;">
                                We use cookies to enhance your experience, analyze site traffic, and personalize content. 
                                By clicking "Accept All", you consent to our use of cookies. 
                                <a href="/cookies" style="color: #60a5fa; text-decoration: underline;" target="_blank">Learn more</a>
                            </p>
                        </div>
                        <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                            <button id="cookie-settings-btn" style="
                                background: transparent;
                                color: white;
                                border: 1px solid rgba(255, 255, 255, 0.3);
                                padding: 12px 24px;
                                border-radius: 8px;
                                cursor: pointer;
                                font-weight: 500;
                                font-size: 14px;
                                transition: all 0.3s;
                            " onmouseover="this.style.background='rgba(255,255,255,0.1)'" onmouseout="this.style.background='transparent'">
                                ⚙️ Customize
                            </button>
                            <button id="cookie-reject-btn" style="
                                background: rgba(239, 68, 68, 0.2);
                                color: #fca5a5;
                                border: 1px solid #ef4444;
                                padding: 12px 24px;
                                border-radius: 8px;
                                cursor: pointer;
                                font-weight: 500;
                                font-size: 14px;
                                transition: all 0.3s;
                            " onmouseover="this.style.background='rgba(239, 68, 68, 0.3)'" onmouseout="this.style.background='rgba(239, 68, 68, 0.2)'">
                                ❌ Reject All
                            </button>
                            <button id="cookie-accept-btn" style="
                                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                color: white;
                                border: none;
                                padding: 12px 32px;
                                border-radius: 8px;
                                cursor: pointer;
                                font-weight: 600;
                                font-size: 14px;
                                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
                                transition: all 0.3s;
                            " onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 6px 16px rgba(102, 126, 234, 0.5)'" onmouseout="this.style.transform=''; this.style.boxShadow='0 4px 12px rgba(102, 126, 234, 0.4)'">
                                ✅ Accept All
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(banner);
        
        // Add event listeners
        document.getElementById('cookie-accept-btn').addEventListener('click', acceptAll);
        document.getElementById('cookie-reject-btn').addEventListener('click', rejectAll);
        document.getElementById('cookie-settings-btn').addEventListener('click', showSettings);
    }

    // Accept all cookies
    function acceptAll() {
        const preferences = {
            essential: true,
            analytics: true,
            functional: true,
            marketing: true
        };
        saveConsentPreferences(preferences);
        applyConsent(preferences);
        removeBanner();
    }

    // Reject all non-essential cookies
    function rejectAll() {
        const preferences = {
            essential: true,
            analytics: false,
            functional: false,
            marketing: false
        };
        saveConsentPreferences(preferences);
        applyConsent(preferences);
        removeBanner();
    }

    // Show settings modal
    function showSettings() {
        const modal = document.createElement('div');
        modal.id = 'cookie-settings-modal';
        modal.innerHTML = `
            <div style="
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.7);
                z-index: 10000;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            ">
                <div style="
                    background: white;
                    border-radius: 16px;
                    max-width: 600px;
                    width: 100%;
                    max-height: 90vh;
                    overflow-y: auto;
                    padding: 32px;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                ">
                    <h2 style="margin: 0 0 16px 0; font-size: 24px; color: #1a1a2e;">Cookie Preferences</h2>
                    <p style="color: #666; margin: 0 0 24px 0;">Choose which cookies you want to accept.</p>
                    
                    <div style="space-y: 16px;">
                        ${Object.keys(COOKIE_CATEGORIES).map(key => {
                            const cat = COOKIE_CATEGORIES[key];
                            return `
                                <div style="
                                    padding: 16px;
                                    border: 1px solid #e5e7eb;
                                    border-radius: 8px;
                                    margin-bottom: 12px;
                                ">
                                    <div style="display: flex; justify-content: space-between; align-items: center;">
                                        <div style="flex: 1;">
                                            <h3 style="margin: 0 0 4px 0; font-size: 16px; color: #1a1a2e;">${cat.name}</h3>
                                            <p style="margin: 0; font-size: 14px; color: #666;">
                                                ${key === 'essential' ? 'Required for basic website functionality' : ''}
                                                ${key === 'analytics' ? 'Help us understand how you use our website' : ''}
                                                ${key === 'functional' ? 'Enable personalization and enhanced features' : ''}
                                                ${key === 'marketing' ? 'Used for targeted advertising and tracking' : ''}
                                            </p>
                                        </div>
                                        <label style="position: relative; display: inline-block; width: 50px; height: 24px;">
                                            <input type="checkbox" id="cookie-${key}" ${cat.required ? 'checked disabled' : ''} style="opacity: 0; width: 0; height: 0;">
                                            <span style="
                                                position: absolute;
                                                cursor: ${cat.required ? 'not-allowed' : 'pointer'};
                                                top: 0;
                                                left: 0;
                                                right: 0;
                                                bottom: 0;
                                                background-color: ${cat.required ? '#667eea' : '#ccc'};
                                                transition: 0.4s;
                                                border-radius: 24px;
                                            ">
                                                <span style="
                                                    position: absolute;
                                                    content: '';
                                                    height: 18px;
                                                    width: 18px;
                                                    left: 3px;
                                                    bottom: 3px;
                                                    background-color: white;
                                                    transition: 0.4s;
                                                    border-radius: 50%;
                                                "></span>
                                            </span>
                                        </label>
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                    
                    <div style="display: flex; gap: 12px; margin-top: 24px;">
                        <button id="cookie-save-settings-btn" style="
                            flex: 1;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            border: none;
                            padding: 14px;
                            border-radius: 8px;
                            cursor: pointer;
                            font-weight: 600;
                            font-size: 14px;
                        ">
                            Save Preferences
                        </button>
                        <button id="cookie-cancel-settings-btn" style="
                            flex: 1;
                            background: #e5e7eb;
                            color: #1a1a2e;
                            border: none;
                            padding: 14px;
                            border-radius: 8px;
                            cursor: pointer;
                            font-weight: 600;
                            font-size: 14px;
                        ">
                            Cancel
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        
        // Add toggle behavior for checkboxes
        Object.keys(COOKIE_CATEGORIES).forEach(key => {
            const checkbox = document.getElementById(`cookie-${key}`);
            if (!COOKIE_CATEGORIES[key].required) {
                const toggle = checkbox.nextElementSibling;
                const slider = toggle.querySelector('span:last-child');
                
                checkbox.addEventListener('change', function() {
                    if (this.checked) {
                        toggle.style.backgroundColor = '#667eea';
                        slider.style.transform = 'translateX(26px)';
                    } else {
                        toggle.style.backgroundColor = '#ccc';
                        slider.style.transform = 'translateX(0)';
                    }
                });
            }
        });
        
        document.getElementById('cookie-save-settings-btn').addEventListener('click', saveSettings);
        document.getElementById('cookie-cancel-settings-btn').addEventListener('click', () => modal.remove());
    }

    // Save custom settings
    function saveSettings() {
        const preferences = {
            essential: true,
            analytics: document.getElementById('cookie-analytics')?.checked || false,
            functional: document.getElementById('cookie-functional')?.checked || false,
            marketing: document.getElementById('cookie-marketing')?.checked || false
        };
        
        saveConsentPreferences(preferences);
        applyConsent(preferences);
        document.getElementById('cookie-settings-modal')?.remove();
        removeBanner();
    }

    // Remove banner
    function removeBanner() {
        const banner = document.getElementById('cookie-consent-banner');
        if (banner) {
            banner.style.opacity = '0';
            banner.style.transform = 'translateY(100%)';
            banner.style.transition = 'all 0.3s ease-out';
            setTimeout(() => banner.remove(), 300);
        }
    }

    // Initialize on page load
    function init() {
        // Check if consent exists
        const existingConsent = getConsentPreferences();
        
        if (existingConsent) {
            // Apply existing preferences
            applyConsent(existingConsent);
        } else {
            // Show banner for new visitors
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', createBanner);
            } else {
                createBanner();
            }
        }
    }

    // Expose function to manually show settings
    window.showCookieSettings = showSettings;
    
    // Start
    init();
})();
