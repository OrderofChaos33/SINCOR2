"""
SINCOR Interactive Onboarding System
Makes complex AI system feel simple and welcoming for non-tech users
"""

from flask import render_template_string, jsonify, session, request
import json
from pathlib import Path
from datetime import datetime

def add_onboarding_routes(app):
    """Add friendly onboarding routes for non-tech users."""
    
    @app.route("/welcome")
    def welcome_tour():
        """Welcome page with friendly getting started tour."""
        return render_template_string(WELCOME_TEMPLATE)
    
    @app.route("/api/onboarding-progress", methods=["POST"])
    def update_onboarding_progress():
        """Track user's onboarding progress."""
        try:
            data = request.get_json()
            step = data.get("step")
            completed = data.get("completed", True)
            
            if "onboarding_progress" not in session:
                session["onboarding_progress"] = {}
            
            session["onboarding_progress"][step] = {
                "completed": completed,
                "timestamp": datetime.now().isoformat()
            }
            
            return jsonify({"success": True})
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/api/onboarding-status")
    def get_onboarding_status():
        """Get current onboarding progress."""
        progress = session.get("onboarding_progress", {})
        
        steps = [
            "welcome_video_watched",
            "admin_dashboard_visited", 
            "cortex_chat_tried",
            "first_agent_command",
            "business_info_added"
        ]
        
        completed_steps = sum(1 for step in steps if progress.get(step, {}).get("completed"))
        total_steps = len(steps)
        
        return jsonify({
            "progress": progress,
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "completion_percentage": (completed_steps / total_steps) * 100
        })


# Friendly Welcome Template
WELCOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to SINCOR! Let's Get Started</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        .glow { box-shadow: 0 0 20px rgba(34, 197, 94, 0.5); }
        .pulse-gentle { animation: pulse-gentle 2s infinite; }
        @keyframes pulse-gentle { 0%, 100% { opacity: 1; } 50% { opacity: 0.8; } }
        .tooltip { position: relative; }
        .tooltip-content {
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background: #1f2937;
            color: white;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 14px;
            white-space: nowrap;
            opacity: 0;
            transition: opacity 0.3s;
            pointer-events: none;
            z-index: 1000;
        }
        .tooltip:hover .tooltip-content { opacity: 1; }
    </style>
</head>
<body class="bg-gradient-to-br from-gray-900 via-blue-900 to-gray-900 text-white min-h-screen">
    <!-- Welcome Hero Section -->
    <div class="container mx-auto px-4 py-8">
        <div class="text-center mb-12">
            <h1 class="text-5xl font-bold mb-4 bg-gradient-to-r from-green-400 to-blue-500 bg-clip-text text-transparent">
                🎉 Welcome to SINCOR!
            </h1>
            <p class="text-xl text-gray-300 mb-6">
                Your 42-agent AI business automation system is ready to transform your business.
                <br>Don't worry - we'll make this super easy! 😊
            </p>
            
            <!-- Progress Bar -->
            <div class="max-w-md mx-auto mb-8">
                <div class="bg-gray-700 rounded-full h-3">
                    <div id="progressBar" class="bg-gradient-to-r from-green-400 to-blue-500 h-3 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
                <p class="text-sm text-gray-400 mt-2">
                    <span id="progressText">Let's get started!</span> 
                    (<span id="progressCount">0</span>/5 steps completed)
                </p>
            </div>
        </div>

        <!-- Getting Started Steps -->
        <div class="max-w-4xl mx-auto">
            <h2 class="text-2xl font-bold text-center mb-8 text-green-400">📚 Your Simple 5-Step Journey</h2>
            
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
                <!-- Step 1: Watch Welcome Video -->
                <div id="step1" class="step-card bg-gray-800 p-6 rounded-xl border-2 border-gray-700 hover:border-green-400 transition-all cursor-pointer">
                    <div class="text-center">
                        <div class="text-4xl mb-4">🎬</div>
                        <h3 class="text-lg font-bold mb-2">1. Watch Your Welcome Video</h3>
                        <p class="text-sm text-gray-400 mb-4">See how SINCOR works for businesses like yours</p>
                        <button onclick="startWelcomeVideo()" class="bg-green-600 hover:bg-green-500 px-4 py-2 rounded font-semibold">
                            ▶️ Watch Video (2 min)
                        </button>
                    </div>
                </div>

                <!-- Step 2: Explore Dashboard -->
                <div id="step2" class="step-card bg-gray-800 p-6 rounded-xl border-2 border-gray-700 hover:border-blue-400 transition-all cursor-pointer">
                    <div class="text-center">
                        <div class="text-4xl mb-4">📊</div>
                        <h3 class="text-lg font-bold mb-2">2. See Your Command Center</h3>
                        <p class="text-sm text-gray-400 mb-4">Your business control panel - easier than it looks!</p>
                        <a href="/admin" target="_blank" onclick="markStepComplete('admin_dashboard_visited')" 
                           class="inline-block bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded font-semibold">
                            🎯 Open Dashboard
                        </a>
                    </div>
                </div>

                <!-- Step 3: Chat with CORTEX -->
                <div id="step3" class="step-card bg-gray-800 p-6 rounded-xl border-2 border-gray-700 hover:border-purple-400 transition-all cursor-pointer">
                    <div class="text-center">
                        <div class="text-4xl mb-4">🧠</div>
                        <h3 class="text-lg font-bold mb-2">3. Meet CORTEX (Your AI Assistant)</h3>
                        <p class="text-sm text-gray-400 mb-4">Just talk to it like you talk to me!</p>
                        <button onclick="openCortexDemo()" class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded font-semibold">
                            💬 Say Hello!
                        </button>
                    </div>
                </div>

                <!-- Step 4: Try Your First Command -->
                <div id="step4" class="step-card bg-gray-800 p-6 rounded-xl border-2 border-gray-700 hover:border-yellow-400 transition-all cursor-pointer">
                    <div class="text-center">
                        <div class="text-4xl mb-4">⚡</div>
                        <h3 class="text-lg font-bold mb-2">4. Give Your First Command</h3>
                        <p class="text-sm text-gray-400 mb-4">Try: "Show me my business data"</p>
                        <button onclick="showCommandExamples()" class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded font-semibold">
                            💡 See Examples
                        </button>
                    </div>
                </div>

                <!-- Step 5: Add Business Info -->
                <div id="step5" class="step-card bg-gray-800 p-6 rounded-xl border-2 border-gray-700 hover:border-red-400 transition-all cursor-pointer">
                    <div class="text-center">
                        <div class="text-4xl mb-4">🏢</div>
                        <h3 class="text-lg font-bold mb-2">5. Tell SINCOR About Your Business</h3>
                        <p class="text-sm text-gray-400 mb-4">So we can help you better</p>
                        <button onclick="openBusinessSetup()" class="bg-red-600 hover:bg-red-500 px-4 py-2 rounded font-semibold">
                            📝 Quick Setup
                        </button>
                    </div>
                </div>

                <!-- Completion Celebration -->
                <div id="step6" class="step-card bg-gradient-to-r from-green-600 to-blue-600 p-6 rounded-xl border-2 border-green-400 transition-all hidden">
                    <div class="text-center">
                        <div class="text-4xl mb-4">🎉</div>
                        <h3 class="text-lg font-bold mb-2">You're All Set!</h3>
                        <p class="text-sm mb-4">Your AI business automation is ready to rock!</p>
                        <a href="/admin" class="inline-block bg-white text-gray-900 px-4 py-2 rounded font-semibold hover:bg-gray-200">
                            🚀 Start Using SINCOR
                        </a>
                    </div>
                </div>
            </div>

            <!-- Help Section -->
            <div class="bg-gray-800 p-6 rounded-xl text-center">
                <h3 class="text-xl font-bold mb-4 text-yellow-400">🤔 Need Help? No Problem!</h3>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <button onclick="openHelpChat()" class="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded">
                        💬 Chat with Support
                    </button>
                    <button onclick="showVideoTutorials()" class="bg-green-600 hover:bg-green-500 px-4 py-2 rounded">
                        📹 Video Tutorials
                    </button>
                    <button onclick="openQuickGuide()" class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded">
                        📖 Quick Start Guide
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Video Modal -->
    <div id="videoModal" class="hidden fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50">
        <div class="bg-gray-800 p-6 rounded-xl max-w-2xl w-full mx-4">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-bold">🎬 Your SINCOR Welcome Video</h3>
                <button onclick="closeVideoModal()" class="text-gray-400 hover:text-white text-2xl">&times;</button>
            </div>
            <div id="videoContainer" class="bg-black rounded-lg p-8 text-center">
                <div class="text-4xl mb-4">🎥</div>
                <p class="text-lg mb-6">Your personalized welcome video is loading...</p>
                <p class="text-sm text-gray-400">This video explains SINCOR in simple terms for business owners like you!</p>
                <div class="mt-6">
                    <button onclick="simulateVideoWatched()" class="bg-green-600 hover:bg-green-500 px-6 py-2 rounded font-semibold">
                        ✅ I Watched the Video
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Command Examples Modal -->
    <div id="commandModal" class="hidden fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50">
        <div class="bg-gray-800 p-6 rounded-xl max-w-lg w-full mx-4">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-bold">💡 Easy Commands to Try</h3>
                <button onclick="closeCommandModal()" class="text-gray-400 hover:text-white text-2xl">&times;</button>
            </div>
            <div class="space-y-4">
                <div class="bg-gray-700 p-4 rounded-lg">
                    <p class="font-semibold">"Show me my business data"</p>
                    <p class="text-sm text-gray-400">See your sales, leads, and performance</p>
                </div>
                <div class="bg-gray-700 p-4 rounded-lg">
                    <p class="font-semibold">"Find me new customers"</p>
                    <p class="text-sm text-gray-400">AI agents will research prospects</p>
                </div>
                <div class="bg-gray-700 p-4 rounded-lg">
                    <p class="font-semibold">"Create a marketing plan"</p>
                    <p class="text-sm text-gray-400">Get a custom marketing strategy</p>
                </div>
                <div class="bg-gray-700 p-4 rounded-lg">
                    <p class="font-semibold">"What's my next action?"</p>
                    <p class="text-sm text-gray-400">Get recommended next steps</p>
                </div>
            </div>
            <div class="text-center mt-6">
                <button onclick="tryFirstCommand()" class="bg-green-600 hover:bg-green-500 px-6 py-2 rounded font-semibold">
                    💬 Try One Now!
                </button>
            </div>
        </div>
    </div>

    <script>
        let completedSteps = 0;
        const totalSteps = 5;

        // Load progress on page load
        window.addEventListener('load', loadProgress);

        async function loadProgress() {
            try {
                const response = await fetch('/api/onboarding-status');
                const data = await response.json();
                
                completedSteps = data.completed_steps;
                updateProgressBar();
                
                // Highlight completed steps
                Object.keys(data.progress).forEach(step => {
                    if (data.progress[step].completed) {
                        markStepVisuallyComplete(step);
                    }
                });
                
            } catch (error) {
                console.log('Could not load progress:', error);
            }
        }

        function updateProgressBar() {
            const percentage = (completedSteps / totalSteps) * 100;
            document.getElementById('progressBar').style.width = percentage + '%';
            document.getElementById('progressCount').textContent = completedSteps;
            
            if (completedSteps === 0) {
                document.getElementById('progressText').textContent = "Let's get started!";
            } else if (completedSteps < totalSteps) {
                document.getElementById('progressText').textContent = "Great progress!";
            } else {
                document.getElementById('progressText').textContent = "🎉 All done!";
                showCompletionCelebration();
            }
        }

        function markStepVisuallyComplete(stepId) {
            const stepMap = {
                'welcome_video_watched': 'step1',
                'admin_dashboard_visited': 'step2', 
                'cortex_chat_tried': 'step3',
                'first_agent_command': 'step4',
                'business_info_added': 'step5'
            };
            
            const element = document.getElementById(stepMap[stepId]);
            if (element) {
                element.classList.add('glow');
                element.style.borderColor = '#10b981';
                element.querySelector('div:first-child').innerHTML = '✅';
            }
        }

        async function markStepComplete(stepId) {
            try {
                await fetch('/api/onboarding-progress', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ step: stepId, completed: true })
                });
                
                completedSteps++;
                updateProgressBar();
                markStepVisuallyComplete(stepId);
                
            } catch (error) {
                console.log('Could not save progress:', error);
            }
        }

        function startWelcomeVideo() {
            document.getElementById('videoModal').classList.remove('hidden');
        }

        function closeVideoModal() {
            document.getElementById('videoModal').classList.add('hidden');
        }

        function simulateVideoWatched() {
            markStepComplete('welcome_video_watched');
            closeVideoModal();
            alert('🎉 Great! You watched your welcome video. Now let\\'s explore your dashboard!');
        }

        function openCortexDemo() {
            markStepComplete('cortex_chat_tried');
            window.open('/admin', '_blank');
            setTimeout(() => {
                alert('💬 In your admin dashboard, look for the "🧠 Open CORTEX Chat" button to talk with your AI assistant!');
            }, 2000);
        }

        function showCommandExamples() {
            document.getElementById('commandModal').classList.remove('hidden');
        }

        function closeCommandModal() {
            document.getElementById('commandModal').classList.add('hidden');
        }

        function tryFirstCommand() {
            markStepComplete('first_agent_command');
            closeCommandModal();
            window.open('/admin', '_blank');
            alert('🚀 Great! Now go to your admin dashboard and try one of those commands with CORTEX!');
        }

        function openBusinessSetup() {
            markStepComplete('business_info_added');
            alert('📝 Business Setup\\n\\nThis would open a simple form to collect:\\n• Your company name\\n• Your industry\\n• Your goals\\n\\nFor now, this step is marked complete! 😊');
        }

        function showCompletionCelebration() {
            document.getElementById('step6').classList.remove('hidden');
            setTimeout(() => {
                alert('🎉 CONGRATULATIONS! 🎉\\n\\nYou\\'ve completed the SINCOR onboarding!\\n\\nYour AI business automation system is ready to help grow your business. Welcome aboard! 🚀');
            }, 1000);
        }

        function openHelpChat() {
            alert('💬 Help Chat\\n\\nWe\\'re here to help! You can:\\n\\n• Email: support@sincor.com\\n• Live chat (coming soon)\\n• Call: 1-800-SINCOR\\n\\nDon\\'t hesitate to reach out! 😊');
        }

        function showVideoTutorials() {
            alert('📹 Video Tutorials\\n\\n• "SINCOR in 5 Minutes"\\n• "Your First Commands"\\n• "Understanding Your Agents"\\n• "Business Growth Tips"\\n\\nTutorials library coming soon! 🎬');
        }

        function openQuickGuide() {
            alert('📖 Quick Start Guide\\n\\n✅ Login to your dashboard\\n✅ Chat with CORTEX\\n✅ Review your business data\\n✅ Set your first goal\\n✅ Let the agents work!\\n\\nIt\\'s that simple! 🎯');
        }
    </script>
</body>
</html>
"""