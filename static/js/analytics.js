/**
 * SINCOR Analytics Module
 * Privacy-first analytics tracking with consent management
 *
 * Usage:
 *   SINCOR.analytics.trackEvent('button_click', { button: 'cta' });
 *   SINCOR.analytics.trackPageView();
 *   SINCOR.analytics.trackConversion('purchase', { value: 297 });
 */

(function(window, document) {
  'use strict';

  // Create SINCOR namespace if it doesn't exist
  window.SINCOR = window.SINCOR || {};

  /**
   * Analytics Module
   */
  const Analytics = {
    // Configuration
    config: {
      enabled: true,
      debug: false,
      consentRequired: true,
      hasConsent: false,
      providers: {
        ga4: false,          // Google Analytics 4
        plausible: false,    // Plausible Analytics
        posthog: false,      // PostHog
        custom: false        // Custom backend tracking
      }
    },

    // Event queue for events tracked before consent
    eventQueue: [],

    /**
     * Initialize analytics
     */
    init: function(options) {
      this.config = { ...this.config, ...options };

      // Check for existing consent
      this.config.hasConsent = this.checkConsent();

      // Load analytics providers if consent given
      if (this.config.hasConsent) {
        this.loadProviders();
        this.flushEventQueue();
      }

      // Track initial page view
      this.trackPageView();

      this.log('Analytics initialized', this.config);
    },

    /**
     * Check if user has given analytics consent
     */
    checkConsent: function() {
      try {
        const consent = localStorage.getItem('sincor_analytics_consent');
        return consent === 'granted';
      } catch (e) {
        return false;
      }
    },

    /**
     * Grant analytics consent
     */
    grantConsent: function() {
      try {
        localStorage.setItem('sincor_analytics_consent', 'granted');
        this.config.hasConsent = true;
        this.loadProviders();
        this.flushEventQueue();
        this.log('Consent granted');
      } catch (e) {
        this.error('Failed to grant consent', e);
      }
    },

    /**
     * Revoke analytics consent
     */
    revokeConsent: function() {
      try {
        localStorage.setItem('sincor_analytics_consent', 'denied');
        this.config.hasConsent = false;
        this.eventQueue = [];
        this.log('Consent revoked');
      } catch (e) {
        this.error('Failed to revoke consent', e);
      }
    },

    /**
     * Load analytics providers
     */
    loadProviders: function() {
      // Google Analytics 4
      if (this.config.providers.ga4 && window.gtag) {
        this.log('Loading Google Analytics 4');
        // GA4 tracking code will be loaded separately via script tag
      }

      // Plausible Analytics
      if (this.config.providers.plausible && window.plausible) {
        this.log('Loading Plausible Analytics');
        // Plausible tracking code will be loaded separately via script tag
      }

      // PostHog
      if (this.config.providers.posthog && window.posthog) {
        this.log('Loading PostHog');
        // PostHog tracking code will be loaded separately via script tag
      }

      // Custom backend tracking
      if (this.config.providers.custom) {
        this.log('Custom analytics enabled');
      }
    },

    /**
     * Flush queued events after consent granted
     */
    flushEventQueue: function() {
      if (this.eventQueue.length > 0) {
        this.log(`Flushing ${this.eventQueue.length} queued events`);
        this.eventQueue.forEach(event => {
          this.sendEvent(event.name, event.properties);
        });
        this.eventQueue = [];
      }
    },

    /**
     * Track a custom event
     */
    trackEvent: function(eventName, properties = {}) {
      if (!this.config.enabled) return;

      const event = {
        name: eventName,
        properties: {
          ...properties,
          timestamp: new Date().toISOString(),
          page: window.location.pathname,
          referrer: document.referrer
        }
      };

      // Queue event if no consent yet
      if (this.config.consentRequired && !this.config.hasConsent) {
        this.eventQueue.push(event);
        this.log('Event queued (awaiting consent):', eventName);
        return;
      }

      this.sendEvent(eventName, event.properties);
    },

    /**
     * Track page view
     */
    trackPageView: function(path) {
      const pagePath = path || window.location.pathname;
      const properties = {
        path: pagePath,
        url: window.location.href,
        title: document.title,
        referrer: document.referrer
      };

      this.trackEvent('page_view', properties);
    },

    /**
     * Track conversion event
     */
    trackConversion: function(conversionType, properties = {}) {
      this.trackEvent('conversion', {
        conversion_type: conversionType,
        ...properties
      });
    },

    /**
     * Track form submission
     */
    trackFormSubmit: function(formName, properties = {}) {
      this.trackEvent('form_submit', {
        form_name: formName,
        ...properties
      });
    },

    /**
     * Track button/link click
     */
    trackClick: function(elementType, elementName, properties = {}) {
      this.trackEvent('click', {
        element_type: elementType,
        element_name: elementName,
        ...properties
      });
    },

    /**
     * Track error/exception
     */
    trackError: function(errorMessage, properties = {}) {
      this.trackEvent('error', {
        error_message: errorMessage,
        ...properties
      });
    },

    /**
     * Send event to analytics providers
     */
    sendEvent: function(eventName, properties) {
      this.log('Tracking event:', eventName, properties);

      // Google Analytics 4
      if (this.config.providers.ga4 && window.gtag) {
        try {
          gtag('event', eventName, properties);
        } catch (e) {
          this.error('GA4 tracking failed', e);
        }
      }

      // Plausible Analytics
      if (this.config.providers.plausible && window.plausible) {
        try {
          plausible(eventName, { props: properties });
        } catch (e) {
          this.error('Plausible tracking failed', e);
        }
      }

      // PostHog
      if (this.config.providers.posthog && window.posthog) {
        try {
          posthog.capture(eventName, properties);
        } catch (e) {
          this.error('PostHog tracking failed', e);
        }
      }

      // Custom backend tracking
      if (this.config.providers.custom) {
        this.sendToBackend(eventName, properties);
      }
    },

    /**
     * Send event to custom backend
     */
    sendToBackend: function(eventName, properties) {
      const endpoint = '/api/analytics/track'; // Update with actual endpoint

      fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          event: eventName,
          properties: properties
        })
      }).catch(e => {
        this.error('Backend tracking failed', e);
      });
    },

    /**
     * Auto-track common user interactions
     */
    autoTrack: function() {
      // Track outbound links
      document.addEventListener('click', (e) => {
        const link = e.target.closest('a');
        if (link && link.hostname !== window.location.hostname) {
          this.trackClick('outbound_link', link.href, {
            text: link.textContent.trim()
          });
        }
      });

      // Track downloads
      document.addEventListener('click', (e) => {
        const link = e.target.closest('a[download]');
        if (link) {
          this.trackClick('download', link.href, {
            filename: link.download || link.href.split('/').pop()
          });
        }
      });

      // Track form submissions
      document.addEventListener('submit', (e) => {
        const form = e.target;
        if (form.id || form.name) {
          this.trackFormSubmit(form.id || form.name);
        }
      });

      // Track JavaScript errors
      window.addEventListener('error', (e) => {
        this.trackError(e.message, {
          filename: e.filename,
          lineno: e.lineno,
          colno: e.colno
        });
      });
    },

    /**
     * Get visitor session information
     */
    getSessionInfo: function() {
      return {
        sessionId: this.getSessionId(),
        userId: this.getUserId(),
        device: this.getDeviceInfo(),
        browser: this.getBrowserInfo()
      };
    },

    /**
     * Get or create session ID
     */
    getSessionId: function() {
      try {
        let sessionId = sessionStorage.getItem('sincor_session_id');
        if (!sessionId) {
          sessionId = this.generateId();
          sessionStorage.setItem('sincor_session_id', sessionId);
        }
        return sessionId;
      } catch (e) {
        return null;
      }
    },

    /**
     * Get or create user ID
     */
    getUserId: function() {
      try {
        let userId = localStorage.getItem('sincor_user_id');
        if (!userId) {
          userId = this.generateId();
          localStorage.setItem('sincor_user_id', userId);
        }
        return userId;
      } catch (e) {
        return null;
      }
    },

    /**
     * Generate unique ID
     */
    generateId: function() {
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
      });
    },

    /**
     * Get device information
     */
    getDeviceInfo: function() {
      return {
        type: this.getDeviceType(),
        screenWidth: window.screen.width,
        screenHeight: window.screen.height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight
      };
    },

    /**
     * Get device type
     */
    getDeviceType: function() {
      const ua = navigator.userAgent;
      if (/(tablet|ipad|playbook|silk)|(android(?!.*mobi))/i.test(ua)) {
        return 'tablet';
      }
      if (/Mobile|Android|iP(hone|od)|IEMobile|BlackBerry|Kindle|Silk-Accelerated|(hpw|web)OS|Opera M(obi|ini)/.test(ua)) {
        return 'mobile';
      }
      return 'desktop';
    },

    /**
     * Get browser information
     */
    getBrowserInfo: function() {
      const ua = navigator.userAgent;
      let browser = 'Unknown';

      if (ua.indexOf('Firefox') > -1) browser = 'Firefox';
      else if (ua.indexOf('SamsungBrowser') > -1) browser = 'Samsung';
      else if (ua.indexOf('Opera') > -1 || ua.indexOf('OPR') > -1) browser = 'Opera';
      else if (ua.indexOf('Trident') > -1) browser = 'IE';
      else if (ua.indexOf('Edge') > -1) browser = 'Edge';
      else if (ua.indexOf('Chrome') > -1) browser = 'Chrome';
      else if (ua.indexOf('Safari') > -1) browser = 'Safari';

      return {
        name: browser,
        language: navigator.language,
        platform: navigator.platform
      };
    },

    /**
     * Debug logging
     */
    log: function(...args) {
      if (this.config.debug) {
        console.log('[SINCOR Analytics]', ...args);
      }
    },

    /**
     * Error logging
     */
    error: function(...args) {
      console.error('[SINCOR Analytics]', ...args);
    }
  };

  // Expose Analytics module
  window.SINCOR.analytics = Analytics;

  // Auto-initialize if enabled
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      Analytics.init({
        debug: window.location.hostname === 'localhost',
        providers: {
          custom: true // Enable custom backend tracking
        }
      });
    });
  } else {
    Analytics.init({
      debug: window.location.hostname === 'localhost',
      providers: {
        custom: true
      }
    });
  }

})(window, document);
