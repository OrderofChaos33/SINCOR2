/**
 * SINCOR Friendly Help System
 * Makes complex features feel simple with contextual hints
 */

class FriendlyHelper {
    constructor() {
        this.helpEnabled = true;
        this.currentTour = null;
        this.hints = {
            // Admin Dashboard Hints
            'cortex-chat-btn': {
                title: 'Meet CORTEX 🧠',
                text: 'Your AI assistant. Just talk to it like a person!',
                position: 'bottom'
            },
            'metrics-section': {
                title: 'Your Business at a Glance 📊',
                text: 'See your key numbers - leads, revenue, system health',
                position: 'top'
            },
            'agent-network': {
                title: '42 AI Agents Working for You 🤖',
                text: 'Each agent has a special job - like having 42 employees!',
                position: 'left'
            },
            'media-studio-btn': {
                title: 'Create Videos & Voiceovers 🎬',
                text: 'Make professional videos for your business automatically',
                position: 'bottom'
            },
            
            // Media Studio Hints
            'create-video-btn': {
                title: 'Easy Video Creation 🎬',
                text: 'Just enter your company name - we handle everything else!',
                position: 'bottom'
            },
            'progress-bar': {
                title: 'Your Video is Being Made ⏱️',
                text: 'Our AI agents are creating your personalized video',
                position: 'top'
            }
        };
        
        this.tourSteps = {
            'first-visit': [
                {
                    target: '#welcome-message',
                    title: '👋 Welcome to SINCOR!',
                    text: 'This is your business command center. Everything is designed to be simple and powerful.',
                    position: 'center'
                },
                {
                    target: '.metrics-grid',
                    title: '📊 Your Business Numbers',
                    text: 'Here you can see your leads, revenue, and how well your AI agents are working.',
                    position: 'bottom'
                },
                {
                    target: '[onclick="openCortexChat()"]',
                    title: '🧠 Your AI Assistant',
                    text: 'Click here to chat with CORTEX. Ask questions like "How\'s my business doing?" or "Find me new customers."',
                    position: 'bottom'
                },
                {
                    target: '.agent-network',
                    title: '🤖 Your AI Team',
                    text: 'These are your 42 AI agents. Each one has a special job - marketing, sales, research, and more!',
                    position: 'left'
                }
            ]
        };
        
        this.init();
    }

    init() {
        this.createHelpWidget();
        this.addTooltipListeners();
        this.checkForFirstVisit();
    }

    createHelpWidget() {
        const helpWidget = document.createElement('div');
        helpWidget.innerHTML = `
            <div id="friendly-help-widget" class="fixed bottom-4 right-4 z-50">
                <!-- Help Toggle Button -->
                <button id="help-toggle" class="bg-blue-600 hover:bg-blue-500 text-white p-3 rounded-full shadow-lg transition-all duration-300 pulse-gentle" 
                        onclick="friendlyHelper.toggleHelp()">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                </button>

                <!-- Help Panel -->
                <div id="help-panel" class="hidden absolute bottom-16 right-0 bg-gray-800 text-white p-4 rounded-lg shadow-xl w-80 border border-gray-600">
                    <div class="mb-4">
                        <h3 class="font-bold text-lg text-blue-400 mb-2">🤔 Need Help?</h3>
                        <p class="text-sm text-gray-300">Click on things to get helpful tips!</p>
                    </div>
                    
                    <div class="space-y-2">
                        <button onclick="friendlyHelper.startTour('first-visit')" 
                                class="w-full bg-green-600 hover:bg-green-500 px-3 py-2 rounded text-sm font-semibold">
                            📍 Take a Quick Tour
                        </button>
                        <button onclick="friendlyHelper.showQuickTips()" 
                                class="w-full bg-purple-600 hover:bg-purple-500 px-3 py-2 rounded text-sm font-semibold">
                            💡 Quick Tips
                        </button>
                        <button onclick="friendlyHelper.openVideoHelp()" 
                                class="w-full bg-red-600 hover:bg-red-500 px-3 py-2 rounded text-sm font-semibold">
                            🎥 Video Tutorials
                        </button>
                        <button onclick="friendlyHelper.contactSupport()" 
                                class="w-full bg-yellow-600 hover:bg-yellow-500 px-3 py-2 rounded text-sm">
                            📞 Get Support
                        </button>
                    </div>
                    
                    <div class="mt-4 pt-3 border-t border-gray-600">
                        <label class="flex items-center text-sm">
                            <input type="checkbox" id="help-enabled" ${this.helpEnabled ? 'checked' : ''} 
                                   onchange="friendlyHelper.toggleHelpMode(this.checked)" class="mr-2">
                            Show helpful hints
                        </label>
                    </div>
                </div>
            </div>

            <!-- Tooltip Container -->
            <div id="help-tooltip" class="hidden fixed z-50 bg-gray-900 text-white p-3 rounded-lg shadow-xl max-w-xs border border-blue-400">
                <div class="font-semibold text-blue-400 mb-1" id="tooltip-title"></div>
                <div class="text-sm" id="tooltip-text"></div>
            </div>

            <!-- Tour Overlay -->
            <div id="tour-overlay" class="hidden fixed inset-0 bg-black bg-opacity-50 z-40"></div>
            <div id="tour-tooltip" class="hidden fixed z-50 bg-blue-900 text-white p-4 rounded-lg shadow-xl max-w-sm border-2 border-blue-400">
                <div class="font-bold text-lg text-blue-300 mb-2" id="tour-title"></div>
                <div class="text-sm mb-4" id="tour-text"></div>
                <div class="flex justify-between items-center">
                    <span class="text-xs text-gray-300" id="tour-progress"></span>
                    <div class="space-x-2">
                        <button onclick="friendlyHelper.skipTour()" class="bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-sm">
                            Skip
                        </button>
                        <button onclick="friendlyHelper.nextTourStep()" class="bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded text-sm font-semibold">
                            Next →
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(helpWidget);
    }

    addTooltipListeners() {
        // Add hover listeners for contextual hints
        Object.keys(this.hints).forEach(selector => {
            const elements = document.querySelectorAll(`[data-help="${selector}"], .${selector}, #${selector}`);
            
            elements.forEach(element => {
                element.addEventListener('mouseenter', (e) => {
                    if (this.helpEnabled && !this.currentTour) {
                        this.showTooltip(e.target, this.hints[selector]);
                    }
                });
                
                element.addEventListener('mouseleave', () => {
                    this.hideTooltip();
                });
            });
        });
    }

    showTooltip(element, hint) {
        const tooltip = document.getElementById('help-tooltip');
        const titleEl = document.getElementById('tooltip-title');
        const textEl = document.getElementById('tooltip-text');
        
        titleEl.textContent = hint.title;
        textEl.textContent = hint.text;
        
        tooltip.classList.remove('hidden');
        
        // Position tooltip
        const rect = element.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();
        
        let top, left;
        
        switch(hint.position) {
            case 'top':
                top = rect.top - tooltipRect.height - 10;
                left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
                break;
            case 'bottom':
                top = rect.bottom + 10;
                left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
                break;
            case 'left':
                top = rect.top + (rect.height / 2) - (tooltipRect.height / 2);
                left = rect.left - tooltipRect.width - 10;
                break;
            case 'right':
                top = rect.top + (rect.height / 2) - (tooltipRect.height / 2);
                left = rect.right + 10;
                break;
            default:
                top = rect.bottom + 10;
                left = rect.left;
        }
        
        // Keep tooltip on screen
        top = Math.max(10, Math.min(top, window.innerHeight - tooltipRect.height - 10));
        left = Math.max(10, Math.min(left, window.innerWidth - tooltipRect.width - 10));
        
        tooltip.style.top = top + 'px';
        tooltip.style.left = left + 'px';
    }

    hideTooltip() {
        document.getElementById('help-tooltip').classList.add('hidden');
    }

    toggleHelp() {
        const panel = document.getElementById('help-panel');
        panel.classList.toggle('hidden');
    }

    toggleHelpMode(enabled) {
        this.helpEnabled = enabled;
        localStorage.setItem('sincor-help-enabled', enabled);
        
        if (enabled) {
            this.showBriefMessage('✅ Helpful hints turned ON');
        } else {
            this.showBriefMessage('💤 Helpful hints turned OFF');
            this.hideTooltip();
        }
    }

    startTour(tourName) {
        this.currentTour = {
            name: tourName,
            steps: this.tourSteps[tourName],
            currentStep: 0
        };
        
        document.getElementById('help-panel').classList.add('hidden');
        document.getElementById('tour-overlay').classList.remove('hidden');
        
        this.showTourStep();
    }

    showTourStep() {
        if (!this.currentTour) return;
        
        const step = this.currentTour.steps[this.currentTour.currentStep];
        const tooltip = document.getElementById('tour-tooltip');
        
        document.getElementById('tour-title').textContent = step.title;
        document.getElementById('tour-text').textContent = step.text;
        document.getElementById('tour-progress').textContent = 
            `Step ${this.currentTour.currentStep + 1} of ${this.currentTour.steps.length}`;
        
        tooltip.classList.remove('hidden');
        
        // Position near target element
        if (step.target !== 'center') {
            const target = document.querySelector(step.target);
            if (target) {
                const rect = target.getBoundingClientRect();
                tooltip.style.top = (rect.bottom + 20) + 'px';
                tooltip.style.left = rect.left + 'px';
                
                // Highlight target
                target.style.boxShadow = '0 0 20px rgba(59, 130, 246, 0.8)';
                target.style.position = 'relative';
                target.style.zIndex = '45';
            }
        } else {
            // Center the tooltip
            tooltip.style.top = '50%';
            tooltip.style.left = '50%';
            tooltip.style.transform = 'translate(-50%, -50%)';
        }
    }

    nextTourStep() {
        if (!this.currentTour) return;
        
        // Remove highlight from current step
        const currentStep = this.currentTour.steps[this.currentTour.currentStep];
        if (currentStep.target !== 'center') {
            const target = document.querySelector(currentStep.target);
            if (target) {
                target.style.boxShadow = '';
                target.style.zIndex = '';
            }
        }
        
        this.currentTour.currentStep++;
        
        if (this.currentTour.currentStep >= this.currentTour.steps.length) {
            this.endTour();
        } else {
            this.showTourStep();
        }
    }

    skipTour() {
        this.endTour();
    }

    endTour() {
        // Clean up highlights
        if (this.currentTour) {
            this.currentTour.steps.forEach(step => {
                if (step.target !== 'center') {
                    const target = document.querySelector(step.target);
                    if (target) {
                        target.style.boxShadow = '';
                        target.style.zIndex = '';
                    }
                }
            });
        }
        
        document.getElementById('tour-overlay').classList.add('hidden');
        document.getElementById('tour-tooltip').classList.add('hidden');
        this.currentTour = null;
        
        this.showBriefMessage('🎉 Tour completed! You\'re ready to use SINCOR!');
    }

    checkForFirstVisit() {
        const hasVisited = localStorage.getItem('sincor-visited');
        if (!hasVisited) {
            setTimeout(() => {
                this.startTour('first-visit');
            }, 2000);
            localStorage.setItem('sincor-visited', 'true');
        }
    }

    showQuickTips() {
        const tips = [
            '💬 Talk to CORTEX like you\'re texting a friend',
            '📊 Your dashboard shows everything important at a glance', 
            '🤖 Each agent specializes in one thing - like having experts on staff',
            '🎯 Start with simple commands like "show me my data"',
            '📈 SINCOR learns your business and gets smarter over time'
        ];
        
        const tipText = tips.map(tip => `${tip}`).join('\\n\\n');
        
        alert(`💡 Quick Tips for Using SINCOR:\\n\\n${tipText}\\n\\nRemember: If you can think it, just ask CORTEX! 🚀`);
    }

    openVideoHelp() {
        alert('🎥 Video Tutorials\\n\\n• "SINCOR Basics" (3 min)\\n• "Talking to CORTEX" (5 min)\\n• "Understanding Your Agents" (7 min)\\n• "Getting Results Fast" (4 min)\\n\\nTutorial library coming soon! 📚');
    }

    contactSupport() {
        alert('📞 Get Support\\n\\nWe\'re here to help!\\n\\n📧 Email: support@sincor.com\\n💬 Live Chat: Available 9-5 EST\\n📱 Text: 1-800-SINCOR\\n\\nDon\'t be shy - we love helping people succeed! 😊');
    }

    showBriefMessage(message) {
        // Create temporary message
        const messageEl = document.createElement('div');
        messageEl.className = 'fixed top-4 right-4 bg-green-600 text-white px-4 py-2 rounded-lg shadow-lg z-50 transition-opacity duration-300';
        messageEl.textContent = message;
        
        document.body.appendChild(messageEl);
        
        setTimeout(() => {
            messageEl.style.opacity = '0';
            setTimeout(() => {
                document.body.removeChild(messageEl);
            }, 300);
        }, 2000);
    }

    // Add hint to any element
    addHint(selector, title, text, position = 'bottom') {
        this.hints[selector] = { title, text, position };
        
        const elements = document.querySelectorAll(selector);
        elements.forEach(element => {
            element.setAttribute('data-help', selector);
        });
        
        this.addTooltipListeners();
    }
}

// Initialize the friendly helper
let friendlyHelper;
document.addEventListener('DOMContentLoaded', () => {
    friendlyHelper = new FriendlyHelper();
});

// Add CSS for animations
const helpStyles = document.createElement('style');
helpStyles.textContent = `
    .pulse-gentle {
        animation: pulse-gentle 2s infinite;
    }
    
    @keyframes pulse-gentle {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.05); }
    }
    
    .help-highlight {
        position: relative;
        z-index: 45;
        box-shadow: 0 0 20px rgba(59, 130, 246, 0.8) !important;
    }
`;
document.head.appendChild(helpStyles);