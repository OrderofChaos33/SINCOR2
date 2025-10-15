"""
SINCOR Media Generation Routes
API endpoints for video and voiceover creation
"""

from flask import request, jsonify, render_template_string, send_file
from pathlib import Path
import json
from datetime import datetime

from agents.media.media_orchestrator import MediaOrchestratorAgent
from agents.media.video_production_agent import VideoProductionAgent
from agents.media.voiceover_agent import VoiceoverAgent

def add_media_routes(app):
    """Add media generation routes to Flask app."""
    
    # Initialize media agents
    media_orchestrator = MediaOrchestratorAgent()
    video_agent = VideoProductionAgent()
    voiceover_agent = VoiceoverAgent()
    
    @app.route("/api/create-onboarding-video", methods=["POST"])
    def create_onboarding_video():
        """Create complete onboarding video with voiceover."""
        try:
            data = request.get_json()
            
            # Extract user data for personalization
            user_data = {
                "company": data.get("company", "Your Business"),
                "industry": data.get("industry", "business"),
                "email": data.get("email", ""),
                "plan": data.get("plan", "standard")
            }
            
            # Create complete onboarding media
            result = media_orchestrator.create_complete_onboarding(user_data)
            
            return jsonify({
                "success": True,
                "workflow_id": result["workflow_id"],
                "status": result["status"],
                "deliverables": result.get("deliverables", {}),
                "message": f"Onboarding video created for {user_data['company']}"
            })
            
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @app.route("/api/media-status/<workflow_id>")
    def get_media_status(workflow_id):
        """Get status of media workflow."""
        try:
            status = media_orchestrator.get_workflow_status(workflow_id)
            
            if "error" in status:
                return jsonify({
                    "success": False,
                    "error": status["error"]
                }), 404
            
            return jsonify({
                "success": True,
                "workflow": status
            })
            
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @app.route("/api/list-media")
    def list_media():
        """List all generated media."""
        try:
            workflows = media_orchestrator.list_workflows()
            videos = video_agent.list_onboarding_videos()
            voiceovers = voiceover_agent.list_voiceovers()
            
            return jsonify({
                "success": True,
                "workflows": workflows,
                "videos": videos,
                "voiceovers": voiceovers
            })
            
        except Exception as e:
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @app.route("/media-studio")
    def media_studio():
        """Media studio dashboard for managing video creation."""
        return render_template_string(MEDIA_STUDIO_TEMPLATE)
    
    @app.route("/onboarding-preview/<workflow_id>")
    def onboarding_preview(workflow_id):
        """Preview generated onboarding content."""
        try:
            workflow = media_orchestrator.get_workflow_status(workflow_id)
            
            if "error" in workflow:
                return f"<h1>Workflow not found: {workflow_id}</h1>", 404
            
            return render_template_string(ONBOARDING_PREVIEW_TEMPLATE, 
                                        workflow=workflow)
            
        except Exception as e:
            return f"<h1>Error: {str(e)}</h1>", 500
    
    @app.route("/download-media/<workflow_id>/<media_type>")
    def download_media(workflow_id, media_type):
        """Download generated media files."""
        try:
            workflow = media_orchestrator.get_workflow_status(workflow_id)
            
            if "error" in workflow:
                return jsonify({"error": "Workflow not found"}), 404
            
            if media_type == "video":
                file_path = workflow.get("video", {}).get("video_path")
            elif media_type == "audio":
                file_path = workflow.get("voiceover", {}).get("audio_path")
            elif media_type == "deliverables":
                file_path = workflow.get("deliverables", {}).get("package_path")
            else:
                return jsonify({"error": "Invalid media type"}), 400
            
            if file_path and Path(file_path).exists():
                return send_file(file_path, as_attachment=True)
            else:
                return jsonify({"error": "File not found"}), 404
                
        except Exception as e:
            return jsonify({"error": str(e)}), 500


# HTML Templates for media studio interfaces
MEDIA_STUDIO_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SINCOR Media Studio</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <div class="flex items-center mb-8">
            <h1 class="text-4xl font-bold text-green-400">📹 SINCOR Media Studio</h1>
            <div class="ml-4 bg-blue-600 px-3 py-1 rounded text-sm">AI-Powered Video Creation</div>
        </div>

        <!-- Quick Actions -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="bg-green-800 p-6 rounded-lg">
                <h3 class="text-xl font-bold mb-4">🎬 Create Onboarding Video</h3>
                <p class="text-gray-300 mb-4">Generate personalized onboarding video with AI voiceover</p>
                <button onclick="createOnboardingVideo()" 
                        class="bg-green-600 hover:bg-green-500 px-4 py-2 rounded font-semibold">
                    Start Creation
                </button>
            </div>

            <div class="bg-purple-800 p-6 rounded-lg">
                <h3 class="text-xl font-bold mb-4">🎙️ Generate Voiceover</h3>
                <p class="text-gray-300 mb-4">Create AI voiceovers for any content</p>
                <button onclick="showVoiceoverForm()" 
                        class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded font-semibold">
                    Generate Audio
                </button>
            </div>

            <div class="bg-blue-800 p-6 rounded-lg">
                <h3 class="text-xl font-bold mb-4">📊 Media Library</h3>
                <p class="text-gray-300 mb-4">Browse and manage generated content</p>
                <button onclick="loadMediaLibrary()" 
                        class="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded font-semibold">
                    Browse Library
                </button>
            </div>
        </div>

        <!-- Creation Form -->
        <div id="creationForm" class="hidden bg-gray-800 p-6 rounded-lg mb-6">
            <h3 class="text-2xl font-bold mb-4">Create Onboarding Video</h3>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                <div>
                    <label class="block text-sm font-semibold mb-2">Company Name*</label>
                    <input type="text" id="companyName" 
                           class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
                           placeholder="Enter company name">
                </div>
                
                <div>
                    <label class="block text-sm font-semibold mb-2">Industry</label>
                    <select id="industry" 
                            class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2">
                        <option value="business">General Business</option>
                        <option value="technology">Technology</option>
                        <option value="healthcare">Healthcare</option>
                        <option value="finance">Finance</option>
                        <option value="retail">Retail</option>
                        <option value="manufacturing">Manufacturing</option>
                        <option value="consulting">Consulting</option>
                        <option value="real_estate">Real Estate</option>
                    </select>
                </div>
                
                <div>
                    <label class="block text-sm font-semibold mb-2">Email</label>
                    <input type="email" id="email" 
                           class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2"
                           placeholder="contact@company.com">
                </div>
                
                <div>
                    <label class="block text-sm font-semibold mb-2">Plan</label>
                    <select id="plan" 
                            class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2">
                        <option value="starter">Starter Plan</option>
                        <option value="professional">Professional Plan</option>
                        <option value="enterprise">Enterprise Plan</option>
                    </select>
                </div>
            </div>
            
            <div class="flex space-x-4">
                <button onclick="generateVideo()" 
                        class="bg-green-600 hover:bg-green-500 px-6 py-2 rounded font-semibold">
                    🎬 Generate Video
                </button>
                <button onclick="hideCreationForm()" 
                        class="bg-gray-600 hover:bg-gray-500 px-6 py-2 rounded">
                    Cancel
                </button>
            </div>
        </div>

        <!-- Status Display -->
        <div id="statusDisplay" class="hidden bg-gray-800 p-6 rounded-lg mb-6">
            <div class="flex items-center mb-4">
                <div id="statusSpinner" class="animate-spin rounded-full h-6 w-6 border-b-2 border-green-400 mr-3"></div>
                <h3 class="text-xl font-bold">Creating Your Video...</h3>
            </div>
            <div id="statusText" class="text-gray-300"></div>
            <div id="statusProgress" class="w-full bg-gray-700 rounded-full h-2 mt-4">
                <div id="progressBar" class="bg-green-600 h-2 rounded-full transition-all duration-500" style="width: 0%"></div>
            </div>
        </div>

        <!-- Media Library -->
        <div id="mediaLibrary" class="bg-gray-800 p-6 rounded-lg">
            <h3 class="text-2xl font-bold mb-4">📚 Media Library</h3>
            <div id="libraryContent">
                <p class="text-gray-400">Click "Browse Library" to load media files...</p>
            </div>
        </div>
    </div>

    <script>
        let currentWorkflowId = null;

        function createOnboardingVideo() {
            document.getElementById('creationForm').classList.remove('hidden');
        }

        function hideCreationForm() {
            document.getElementById('creationForm').classList.add('hidden');
        }

        function showVoiceoverForm() {
            alert('Voiceover generation coming soon!');
        }

        async function generateVideo() {
            const company = document.getElementById('companyName').value;
            const industry = document.getElementById('industry').value;
            const email = document.getElementById('email').value;
            const plan = document.getElementById('plan').value;

            if (!company) {
                alert('Company name is required!');
                return;
            }

            // Show status
            document.getElementById('creationForm').classList.add('hidden');
            document.getElementById('statusDisplay').classList.remove('hidden');
            
            const statusText = document.getElementById('statusText');
            const progressBar = document.getElementById('progressBar');

            try {
                statusText.textContent = 'Initializing video generation...';
                progressBar.style.width = '10%';

                const response = await fetch('/api/create-onboarding-video', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ company, industry, email, plan })
                });

                const result = await response.json();

                if (result.success) {
                    currentWorkflowId = result.workflow_id;
                    
                    statusText.textContent = 'Video generation started successfully!';
                    progressBar.style.width = '100%';
                    
                    setTimeout(() => {
                        document.getElementById('statusDisplay').classList.add('hidden');
                        showWorkflowResult(result);
                        loadMediaLibrary();
                    }, 2000);
                } else {
                    statusText.textContent = `Error: ${result.error}`;
                    progressBar.style.width = '0%';
                }

            } catch (error) {
                statusText.textContent = `Error: ${error.message}`;
                progressBar.style.width = '0%';
            }
        }

        function showWorkflowResult(result) {
            alert(`Video creation initiated!\\n\\nWorkflow ID: ${result.workflow_id}\\nStatus: ${result.status}\\n\\nCheck the Media Library for your generated content.`);
        }

        async function loadMediaLibrary() {
            const libraryContent = document.getElementById('libraryContent');
            libraryContent.innerHTML = '<p class="text-gray-400">Loading media library...</p>';

            try {
                const response = await fetch('/api/list-media');
                const data = await response.json();

                if (data.success) {
                    let html = '';

                    if (data.workflows.length > 0) {
                        html += '<h4 class="text-lg font-bold mb-3 text-green-400">📋 Video Workflows</h4>';
                        html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">';
                        
                        data.workflows.forEach(workflow => {
                            const statusColor = workflow.status === 'completed' ? 'text-green-400' : 
                                              workflow.status === 'failed' ? 'text-red-400' : 'text-yellow-400';
                            
                            html += `
                                <div class="bg-gray-700 p-4 rounded border-l-4 ${workflow.status === 'completed' ? 'border-green-400' : 
                                       workflow.status === 'failed' ? 'border-red-400' : 'border-yellow-400'}">
                                    <div class="flex justify-between items-start mb-2">
                                        <h5 class="font-bold">${workflow.company}</h5>
                                        <span class="text-xs ${statusColor}">${workflow.status.toUpperCase()}</span>
                                    </div>
                                    <p class="text-sm text-gray-400 mb-2">Type: ${workflow.type}</p>
                                    <p class="text-xs text-gray-500">Created: ${new Date(workflow.start_time).toLocaleString()}</p>
                                    ${workflow.status === 'completed' ? 
                                        `<a href="/onboarding-preview/${workflow.workflow_id}" target="_blank" 
                                           class="inline-block mt-2 bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded text-xs">
                                            👁️ Preview
                                        </a>` : ''
                                    }
                                </div>
                            `;
                        });
                        html += '</div>';
                    }

                    if (data.videos.length > 0) {
                        html += '<h4 class="text-lg font-bold mb-3 text-purple-400">🎬 Video Files</h4>';
                        html += '<div class="space-y-2">';
                        
                        data.videos.forEach(video => {
                            html += `
                                <div class="bg-gray-700 p-3 rounded flex justify-between items-center">
                                    <div>
                                        <span class="font-mono text-sm">${video.filename}</span>
                                        <span class="text-xs text-gray-400 ml-2">(${(video.size_bytes / 1024).toFixed(1)} KB)</span>
                                    </div>
                                    <div class="text-xs text-gray-500">
                                        ${new Date(video.created_at).toLocaleString()}
                                    </div>
                                </div>
                            `;
                        });
                        html += '</div>';
                    }

                    if (html === '') {
                        html = '<p class="text-gray-400">No media files generated yet. Create your first onboarding video!</p>';
                    }

                    libraryContent.innerHTML = html;
                } else {
                    libraryContent.innerHTML = `<p class="text-red-400">Error loading library: ${data.error}</p>`;
                }

            } catch (error) {
                libraryContent.innerHTML = `<p class="text-red-400">Error: ${error.message}</p>`;
            }
        }

        // Auto-load media library on page load
        window.addEventListener('load', () => {
            loadMediaLibrary();
        });
    </script>
</body>
</html>
"""

ONBOARDING_PREVIEW_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Onboarding Preview - {{ workflow.user_data.company }}</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <div class="mb-8">
            <h1 class="text-3xl font-bold text-green-400 mb-2">
                🎬 Onboarding Preview: {{ workflow.user_data.company }}
            </h1>
            <p class="text-gray-300">Generated: {{ workflow.start_time }}</p>
            <div class="inline-block bg-{{ 'green' if workflow.status == 'completed' else 'yellow' }}-600 px-3 py-1 rounded text-sm mt-2">
                Status: {{ workflow.status.upper() }}
            </div>
        </div>

        {% if workflow.script %}
        <div class="bg-gray-800 p-6 rounded-lg mb-6">
            <h2 class="text-2xl font-bold mb-4 text-blue-400">📝 Video Script</h2>
            <div class="space-y-4">
                {% for scene in workflow.script.scenes %}
                <div class="bg-gray-700 p-4 rounded border-l-4 border-blue-400">
                    <div class="flex justify-between items-start mb-2">
                        <h3 class="font-bold text-lg">Scene {{ scene.id }}: {{ scene.title }}</h3>
                        <span class="text-sm text-gray-400">{{ scene.duration }}s</span>
                    </div>
                    <p class="text-gray-200 mb-2">{{ scene.text }}</p>
                    {% if scene.visuals %}
                    <p class="text-sm text-gray-400">🎨 Visuals: {{ scene.visuals }}</p>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        {% if workflow.synchronized_media %}
        <div class="bg-gray-800 p-6 rounded-lg mb-6">
            <h2 class="text-2xl font-bold mb-4 text-purple-400">🔄 Synchronization Map</h2>
            <div class="mb-4">
                <p><strong>Total Duration:</strong> {{ workflow.synchronized_media.total_duration }} seconds</p>
                <p><strong>Resolution:</strong> {{ workflow.synchronized_media.resolution }}</p>
                <p><strong>Audio Format:</strong> {{ workflow.synchronized_media.audio_format }}</p>
            </div>
            
            <div class="space-y-2">
                {% for sync in workflow.synchronized_media.synchronization_map %}
                <div class="bg-gray-700 p-3 rounded flex justify-between items-center">
                    <div>
                        <span class="font-bold">Scene {{ sync.scene_id }}</span>
                        <span class="text-gray-300 ml-2">{{ sync.text[:50] }}...</span>
                    </div>
                    <div class="text-sm text-gray-400">
                        Video: {{ sync.video_start }}s-{{ sync.video_end }}s | 
                        Audio: {{ sync.audio_start }}s-{{ sync.audio_end }}s
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        {% if workflow.deliverables %}
        <div class="bg-gray-800 p-6 rounded-lg mb-6">
            <h2 class="text-2xl font-bold mb-4 text-green-400">📦 Deliverables</h2>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                {% for file_type, file_info in workflow.deliverables.files.items() %}
                <div class="bg-gray-700 p-4 rounded">
                    <h3 class="font-bold text-lg mb-2">{{ file_type.title() }} File</h3>
                    <p class="text-gray-300 mb-2">{{ file_info.description }}</p>
                    <p class="text-sm text-gray-400 mb-3">Format: {{ file_info.format }}</p>
                    {% if file_info.path %}
                    <a href="/download-media/{{ workflow.workflow_id }}/{{ file_type }}" 
                       class="bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded text-sm">
                        📥 Download
                    </a>
                    {% endif %}
                </div>
                {% endfor %}
            </div>

            <div class="bg-blue-900 p-4 rounded">
                <h3 class="font-bold mb-2">📋 Usage Instructions:</h3>
                <ul class="list-disc list-inside space-y-1 text-sm">
                    {% for instruction in workflow.deliverables.usage_instructions %}
                    <li>{{ instruction }}</li>
                    {% endfor %}
                </ul>
            </div>

            <div class="bg-green-900 p-4 rounded mt-4">
                <h3 class="font-bold mb-2">🚀 Next Steps:</h3>
                <ol class="list-decimal list-inside space-y-1 text-sm">
                    {% for step in workflow.deliverables.next_steps %}
                    <li>{{ step }}</li>
                    {% endfor %}
                </ol>
            </div>
        </div>
        {% endif %}

        <div class="text-center">
            <a href="/media-studio" class="bg-gray-600 hover:bg-gray-500 px-6 py-3 rounded-lg font-semibold">
                ← Back to Media Studio
            </a>
        </div>
    </div>
</body>
</html>
"""