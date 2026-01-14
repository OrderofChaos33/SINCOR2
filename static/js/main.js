// SINCOR Website JavaScript
// Optimized for performance and accessibility

(function() {
    'use strict';

    // Performance optimization: Cache DOM queries
    let mobileMenuButton, mobileMenu;

    document.addEventListener('DOMContentLoaded', function() {

        // Initialize cached elements
        mobileMenuButton = document.getElementById('mobile-menu-button');
        mobileMenu = document.getElementById('mobile-menu');

        // Enhanced mobile menu toggle with accessibility
        if (mobileMenuButton && mobileMenu) {
            mobileMenuButton.addEventListener('click', function(e) {
                e.preventDefault();
                const isHidden = mobileMenu.classList.contains('hidden');

                mobileMenu.classList.toggle('hidden');

                // Update ARIA attributes for accessibility
                mobileMenuButton.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
                mobileMenu.setAttribute('aria-hidden', isHidden ? 'false' : 'true');

                // Focus management
                if (isHidden) {
                    const firstMenuItem = mobileMenu.querySelector('a');
                    if (firstMenuItem) firstMenuItem.focus();
                }
            });

            // Close mobile menu on escape key
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && !mobileMenu.classList.contains('hidden')) {
                    mobileMenu.classList.add('hidden');
                    mobileMenuButton.setAttribute('aria-expanded', 'false');
                    mobileMenu.setAttribute('aria-hidden', 'true');
                    mobileMenuButton.focus();
                }
            });

            // Close mobile menu when clicking outside
            document.addEventListener('click', function(e) {
                if (!mobileMenuButton.contains(e.target) && !mobileMenu.contains(e.target)) {
                    if (!mobileMenu.classList.contains('hidden')) {
                        mobileMenu.classList.add('hidden');
                        mobileMenuButton.setAttribute('aria-expanded', 'false');
                        mobileMenu.setAttribute('aria-hidden', 'true');
                    }
                }
            });
        }

    // Smooth scrolling for anchor links
    const anchorLinks = document.querySelectorAll('a[href^="#"]');
    anchorLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const targetId = this.getAttribute('href');
            const targetElement = document.querySelector(targetId);
            
            if (targetElement) {
                targetElement.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });

    // Form handling for signup/contact
    const signupForms = document.querySelectorAll('.signup-form');
    signupForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            
            // Basic form validation
            const email = form.querySelector('input[type="email"]');
            if (email && !isValidEmail(email.value)) {
                showNotification('Please enter a valid email address', 'error');
                return;
            }
            
            // Collect form data
            const formData = new FormData(form);
            const data = Object.fromEntries(formData.entries());
            
            // Submit contact form
            submitContactForm(data);
        });
    });

    // Demo video handling
    const demoButton = document.querySelector('.demo-button');
    const videoModal = document.querySelector('#video-modal');
    
    if (demoButton && videoModal) {
        demoButton.addEventListener('click', function(e) {
            e.preventDefault();
            videoModal.classList.remove('hidden');
        });
        
        // Close modal when clicking outside
        videoModal.addEventListener('click', function(e) {
            if (e.target === videoModal) {
                videoModal.classList.add('hidden');
            }
        });
    }

    // Pricing table interactions
    const pricingCards = document.querySelectorAll('.pricing-card');
    pricingCards.forEach(card => {
        card.addEventListener('mouseenter', function() {
            this.classList.add('scale-105');
        });
        
        card.addEventListener('mouseleave', function() {
            this.classList.remove('scale-105');
        });
    });

    // Dashboard chart initialization (when on dashboard pages)
    if (window.location.pathname.includes('/dashboard')) {
        initializeDashboardCharts();
    }

    // Analytics tracking (placeholder)
    trackPageView();
    
    // Performance monitoring
    measurePerformance();
});

// Utility functions
function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
}

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${
        type === 'success' ? 'bg-green-500' : 
        type === 'error' ? 'bg-red-500' : 
        'bg-blue-500'
    } text-white`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 5000);
}

function initializeDashboardCharts() {
    // Placeholder for dashboard chart initialization
    // In production, this would integrate with Chart.js or similar
    console.log('Initializing dashboard charts...');
    
    // Example: Initialize agent constellation chart
    const constellationChart = document.getElementById('constellation-chart');
    if (constellationChart) {
        // Chart.js or D3.js initialization would go here
        constellationChart.innerHTML = '<div class="text-center text-gray-500 py-8">Chart will load here</div>';
    }
    
    // Example: Initialize BI reporting charts
    const biCharts = document.querySelectorAll('.bi-chart');
    biCharts.forEach(chart => {
        chart.innerHTML = '<div class="text-center text-gray-500 py-8">BI Chart will load here</div>';
    });
}

function trackPageView() {
    // Placeholder for analytics tracking
    // In production, this would integrate with Google Analytics, Mixpanel, etc.
    console.log('Page view tracked:', window.location.pathname);
}

function measurePerformance() {
    // Performance monitoring
    if ('performance' in window) {
        window.addEventListener('load', function() {
            setTimeout(function() {
                const perfData = performance.getEntriesByType('navigation')[0];
                if (perfData) {
                    console.log('Page load time:', perfData.loadEventEnd - perfData.fetchStart, 'ms');
                }
            }, 0);
        });
    }
}

// Authentication helper for dashboard pages
function checkAuth() {
    // In production, this would make an API call to verify auth status
    const isAuthenticated = sessionStorage.getItem('user_authenticated');
    if (!isAuthenticated && window.location.pathname.includes('/dashboard')) {
        window.location.href = '/signup';
    }
}

// Professional signup handler
function submitContactForm(formData) {
    // Contact form submission API call
    fetch('/api/contact', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            showNotification('Thank you! Our team will contact you within 24 hours.', 'success');
            setTimeout(() => {
                window.location.href = '/demo';
            }, 3000);
        } else {
            showNotification('Something went wrong. Please try again.', 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showNotification('Something went wrong. Please try again.', 'error');
    });
}

// Demo authentication handler
function authenticateDemo() {
    fetch('/api/authenticate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            demo: true,
            user_level: 'member'
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            sessionStorage.setItem('user_authenticated', 'true');
            sessionStorage.setItem('user_level', 'member');
            window.location.href = data.redirect || '/dashboard';
        }
    });
}

// Admin login handler
function authenticateAdmin() {
    fetch('/api/authenticate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            admin: true,
            user_level: 'admin'
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            sessionStorage.setItem('user_authenticated', 'true');
            sessionStorage.setItem('user_level', 'admin');
            window.location.href = '/admin';
        }
    });
}

    // Lazy loading implementation for performance
    function initLazyLoading() {
        if ('IntersectionObserver' in window) {
            const imageObserver = new IntersectionObserver((entries, observer) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const img = entry.target;
                        if (img.dataset.src) {
                            img.src = img.dataset.src;
                            img.classList.remove('lazy-load');
                            img.classList.add('loaded');
                            observer.unobserve(img);
                        }
                    }
                });
            });

            document.querySelectorAll('img[data-src]').forEach(img => {
                imageObserver.observe(img);
            });
        }
    }

    // Initialize lazy loading
    initLazyLoading();

    // Service worker registration for caching
    if ('serviceWorker' in navigator && window.location.protocol === 'https:') {
        window.addEventListener('load', function() {
            navigator.serviceWorker.register('/sw.js')
                .then(function(registration) {
                    console.log('ServiceWorker registration successful');
                })
                .catch(function(error) {
                    console.log('ServiceWorker registration failed');
                });
        });
    }

    })(); // Close IIFE

// Export functions for use in other scripts
window.SINCOR = {
    showNotification,
    submitContactForm,
    authenticateDemo,
    authenticateAdmin,
    checkAuth
};