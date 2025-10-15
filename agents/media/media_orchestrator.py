"""
SINCOR Media Orchestrator Agent
Coordinates video production and voiceover agents for complete media creation
"""

import json
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

from ..base_agent import BaseAgent
from .video_production_agent import VideoProductionAgent
from .voiceover_agent import VoiceoverAgent

class MediaOrchestratorAgent(BaseAgent):
    """Orchestrates media production workflow for onboarding and marketing."""
    
    def __init__(self, config: Dict = None):
        super().__init__("MediaOrchestratorAgent", "logs/media_orchestrator.log", config)
        
        # Initialize sub-agents
        self.video_agent = VideoProductionAgent(config)
        self.voiceover_agent = VoiceoverAgent(config)
        
        # Media workflow tracking
        self.workflow_dir = Path("outputs/media_workflows")
        self.workflow_dir.mkdir(parents=True, exist_ok=True)
        
        self._log("Media Orchestrator Agent initialized")
    
    def create_complete_onboarding(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create complete onboarding video with synchronized voiceover."""
        try:
            company = user_data.get('company', 'User')
            self._log(f"Starting complete onboarding creation for: {company}")
            
            # Create workflow tracking
            workflow_id = f"onboarding_{company.replace(' ', '_').lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            workflow = self._initialize_workflow(workflow_id, user_data)
            
            # Step 1: Generate video with script
            self._log("Step 1: Generating onboarding video...")
            workflow["status"] = "generating_video"
            self._save_workflow(workflow)
            
            video_result = self.video_agent.create_onboarding_video(user_data)
            if not video_result["success"]:
                workflow["error"] = f"Video generation failed: {video_result['error']}"
                workflow["status"] = "failed"
                self._save_workflow(workflow)
                return workflow
            
            workflow["video"] = video_result
            workflow["script"] = video_result["script"]
            
            # Step 2: Generate synchronized voiceover
            self._log("Step 2: Generating synchronized voiceover...")
            workflow["status"] = "generating_voiceover"
            self._save_workflow(workflow)
            
            voiceover_result = self.voiceover_agent.create_onboarding_voiceover(
                video_result["script"], 
                user_data
            )
            if not voiceover_result["success"]:
                workflow["error"] = f"Voiceover generation failed: {voiceover_result['error']}"
                workflow["status"] = "failed"
                self._save_workflow(workflow)
                return workflow
            
            workflow["voiceover"] = voiceover_result
            
            # Step 3: Synchronize and finalize
            self._log("Step 3: Synchronizing media components...")
            workflow["status"] = "synchronizing"
            self._save_workflow(workflow)
            
            sync_result = self._synchronize_media(workflow)
            workflow["synchronized_media"] = sync_result
            
            # Step 4: Complete workflow
            workflow["status"] = "completed"
            workflow["completion_time"] = datetime.now().isoformat()
            workflow["total_duration_minutes"] = (
                datetime.fromisoformat(workflow["completion_time"]) - 
                datetime.fromisoformat(workflow["start_time"])
            ).total_seconds() / 60
            
            self._save_workflow(workflow)
            
            # Create final deliverables package
            deliverables = self._create_deliverables_package(workflow)
            workflow["deliverables"] = deliverables
            
            self._log(f"Complete onboarding created successfully: {workflow_id}")
            return workflow
            
        except Exception as e:
            self._log(f"Error in complete onboarding creation: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "workflow_id": workflow_id if 'workflow_id' in locals() else "unknown"
            }
    
    def _initialize_workflow(self, workflow_id: str, user_data: Dict) -> Dict:
        """Initialize workflow tracking."""
        return {
            "workflow_id": workflow_id,
            "type": "onboarding_complete",
            "user_data": user_data,
            "status": "initialized",
            "start_time": datetime.now().isoformat(),
            "steps": [
                "generate_video",
                "generate_voiceover", 
                "synchronize_media",
                "create_deliverables"
            ],
            "current_step": 0,
            "success": True
        }
    
    def _synchronize_media(self, workflow: Dict) -> Dict:
        """Synchronize video and voiceover timing."""
        try:
            video_data = workflow["video"]
            voiceover_data = workflow["voiceover"]
            
            # Get timing information
            video_scenes = video_data["script"]["scenes"]
            voiceover_timing = voiceover_data["timing_data"]
            
            # Create synchronization map
            sync_map = []
            
            for i, scene in enumerate(video_scenes):
                if i < len(voiceover_timing["scenes"]):
                    vo_scene = voiceover_timing["scenes"][i]
                    sync_entry = {
                        "scene_id": scene["id"],
                        "video_start": i * 30,  # 30 seconds per scene
                        "video_end": (i + 1) * 30,
                        "audio_start": vo_scene["start_time"],
                        "audio_end": vo_scene["end_time"],
                        "text": scene["text"],
                        "visuals": scene["visuals"] if "visuals" in scene else "default"
                    }
                    sync_map.append(sync_entry)
            
            # Create final media specifications
            final_specs = {
                "total_duration": max(
                    len(video_scenes) * 30,
                    voiceover_timing["total_duration"]
                ),
                "resolution": "1920x1080",
                "framerate": "30fps",
                "audio_format": "WAV 44.1kHz",
                "synchronization_map": sync_map,
                "created_at": datetime.now().isoformat()
            }
            
            return final_specs
            
        except Exception as e:
            self._log(f"Error synchronizing media: {str(e)}")
            return {"error": str(e)}
    
    def _create_deliverables_package(self, workflow: Dict) -> Dict:
        """Create final deliverables package."""
        try:
            workflow_id = workflow["workflow_id"]
            company = workflow["user_data"].get("company", "User")
            
            deliverables = {
                "package_id": workflow_id,
                "company": company,
                "created_at": datetime.now().isoformat(),
                "files": {
                    "video": {
                        "path": workflow["video"]["video_path"],
                        "format": "MP4",
                        "description": "Complete onboarding video with scenes"
                    },
                    "audio": {
                        "path": workflow["voiceover"]["audio_path"],
                        "format": "WAV",
                        "description": "Synchronized voiceover narration"
                    },
                    "script": {
                        "content": workflow["script"],
                        "description": "Complete video script and timing"
                    },
                    "sync_specs": {
                        "content": workflow["synchronized_media"],
                        "description": "Video-audio synchronization specifications"
                    }
                },
                "usage_instructions": [
                    "Video file contains visual scenes and placeholders",
                    "Audio file contains professional voiceover",
                    "Use sync_specs for precise timing alignment",
                    "Recommended video editor: DaVinci Resolve, Premiere Pro, or Final Cut"
                ],
                "next_steps": [
                    "Import video and audio files into video editor",
                    "Align audio to video using synchronization map",
                    "Add company branding and custom visuals as needed",
                    "Export final video in desired format",
                    "Deploy to onboarding workflow"
                ]
            }
            
            # Save deliverables package
            package_path = self.workflow_dir / f"{workflow_id}_deliverables.json"
            with open(package_path, 'w') as f:
                json.dump(deliverables, f, indent=2)
            
            deliverables["package_path"] = str(package_path)
            
            return deliverables
            
        except Exception as e:
            self._log(f"Error creating deliverables package: {str(e)}")
            return {"error": str(e)}
    
    def _save_workflow(self, workflow: Dict):
        """Save workflow state to disk."""
        workflow_path = self.workflow_dir / f"{workflow['workflow_id']}.json"
        with open(workflow_path, 'w') as f:
            json.dump(workflow, f, indent=2)
    
    def get_workflow_status(self, workflow_id: str) -> Dict:
        """Get status of a media workflow."""
        try:
            workflow_path = self.workflow_dir / f"{workflow_id}.json"
            if workflow_path.exists():
                with open(workflow_path, 'r') as f:
                    return json.load(f)
            else:
                return {"error": "Workflow not found"}
        except Exception as e:
            return {"error": str(e)}
    
    def list_workflows(self, workflow_type: str = None) -> List[Dict]:
        """List all media workflows."""
        workflows = []
        
        for workflow_file in self.workflow_dir.glob("*.json"):
            if workflow_file.name.endswith("_deliverables.json"):
                continue  # Skip deliverables files
                
            try:
                with open(workflow_file, 'r') as f:
                    workflow = json.load(f)
                
                if workflow_type and workflow.get("type") != workflow_type:
                    continue
                
                summary = {
                    "workflow_id": workflow["workflow_id"],
                    "type": workflow["type"],
                    "company": workflow["user_data"].get("company", "Unknown"),
                    "status": workflow["status"],
                    "start_time": workflow["start_time"],
                    "completion_time": workflow.get("completion_time"),
                    "success": workflow.get("success", False)
                }
                
                workflows.append(summary)
                
            except Exception as e:
                self._log(f"Error reading workflow {workflow_file}: {e}")
        
        return sorted(workflows, key=lambda x: x["start_time"], reverse=True)
    
    def create_custom_media(self, media_request: Dict[str, Any]) -> Dict[str, Any]:
        """Create custom media based on specific requirements."""
        try:
            media_type = media_request.get("type", "video")  # "video", "audio", or "complete"
            content = media_request.get("content", {})
            
            workflow_id = f"custom_{media_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            if media_type == "video":
                result = self.video_agent.create_onboarding_video(content)
            elif media_type == "audio":
                text = content.get("text", "")
                result = self.voiceover_agent.create_custom_voiceover(text)
            elif media_type == "complete":
                result = self.create_complete_onboarding(content)
            else:
                return {"success": False, "error": "Unknown media type"}
            
            return result
            
        except Exception as e:
            self._log(f"Error creating custom media: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _run_custom_diagnostics(self) -> Dict[str, Any]:
        """Run custom diagnostics for media orchestrator agent."""
        workflow_files = list(self.workflow_dir.glob("*.json"))
        return {
            "workflow_directory": str(self.workflow_dir),
            "directory_exists": self.workflow_dir.exists(),
            "workflow_count": len([f for f in workflow_files if not f.name.endswith("_deliverables.json")]),
            "deliverables_count": len([f for f in workflow_files if f.name.endswith("_deliverables.json")]),
            "video_agent_status": "initialized",
            "voiceover_agent_status": "initialized"
        }