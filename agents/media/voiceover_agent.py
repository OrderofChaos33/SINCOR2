"""
SINCOR Voiceover Agent
Generates AI voiceovers for videos and presentations
"""

import os
import json
import requests
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..base_agent import BaseAgent

class VoiceoverAgent(BaseAgent):
    """Agent for creating AI-generated voiceovers."""
    
    def __init__(self, config: Dict = None):
        super().__init__("VoiceoverAgent", "logs/voiceover_agent.log", config)
        self.output_dir = Path("outputs/audio")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Voice synthesis APIs (using ElevenLabs-style API)
        self.tts_api_url = os.getenv("TTS_API_URL", "https://api.elevenlabs.io/v1/text-to-speech")
        self.tts_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        
        # Default voice settings
        self.default_voice_id = "EXAVITQu4vr4xnSDxMaL"  # Bella (friendly female voice)
        self.professional_voice_id = "pNInz6obpgDQGcFmaJgB"  # Adam (professional male voice)
        
        self._log("Voiceover Agent initialized")
    
    def create_onboarding_voiceover(self, script: Dict, user_data: Dict = None) -> Dict[str, Any]:
        """Create voiceover for onboarding video script."""
        try:
            company = user_data.get('company', 'your business') if user_data else 'your business'
            self._log(f"Creating voiceover for: {company}")
            
            # Prepare the full narration text
            narration = self._prepare_narration_text(script, user_data)
            
            # Generate voiceover audio
            audio_path = self._generate_voiceover_audio(narration, user_data)
            
            # Create timing information for video sync
            timing_data = self._create_timing_data(script, narration)
            
            result = {
                "success": True,
                "audio_path": str(audio_path),
                "narration_text": narration,
                "timing_data": timing_data,
                "voice_settings": self._get_voice_settings(),
                "duration_seconds": timing_data["total_duration"],
                "created_at": datetime.now().isoformat()
            }
            
            self._log(f"Voiceover created: {audio_path}")
            return result
            
        except Exception as e:
            self._log(f"Error creating voiceover: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _prepare_narration_text(self, script: Dict, user_data: Dict = None) -> str:
        """Prepare the full narration text from script scenes."""
        company = user_data.get('company', 'your business') if user_data else 'your business'
        industry = user_data.get('industry', 'business') if user_data else 'business'
        
        # Personalize the script text
        narration_parts = []
        
        for scene in script.get("scenes", []):
            text = scene["text"]
            
            # Add natural pauses and emphasis
            if scene["id"] == 1:  # Welcome scene
                text = f"Welcome to SINCOR, {company}! [PAUSE] I'm CORTEX, your AI business automation assistant."
            elif scene["id"] == 2:  # 42-Agent network
                text = f"You now have access to forty-two specialized AI agents... [PAUSE] designed to transform {industry} operations."
            elif scene["id"] == 3:  # Admin dashboard
                text = "Your command center... [PAUSE] where you control all agents and monitor performance in real-time."
            elif scene["id"] == 4:  # Business intelligence
                text = "Automated market research, competitor analysis, [PAUSE] and growth opportunities specific to your market."
            elif scene["id"] == 5:  # Lead generation
                text = f"Automated lead discovery and qualification... [PAUSE] for {industry} prospects in your target market."
            elif scene["id"] == 6:  # Next steps
                text = "Ready to begin? [PAUSE] Access your admin panel to start configuring your agents for maximum impact."
            
            narration_parts.append(text)
        
        # Join with longer pauses between scenes
        full_narration = " [LONG_PAUSE] ".join(narration_parts)
        
        return full_narration
    
    def _generate_voiceover_audio(self, narration_text: str, user_data: Dict = None) -> Path:
        """Generate audio file from narration text."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        company_name = user_data.get('company', 'user').replace(' ', '_').lower() if user_data else 'user'
        audio_filename = f"voiceover_{company_name}_{timestamp}.wav"
        audio_path = self.output_dir / audio_filename
        
        # Clean the text for TTS (remove pause markers for now)
        clean_text = narration_text.replace("[PAUSE]", "").replace("[LONG_PAUSE]", "")
        
        if self.tts_api_key and self.tts_api_url:
            try:
                # Use ElevenLabs or similar TTS API
                audio_content = self._call_tts_api(clean_text)
                
                with open(audio_path, 'wb') as f:
                    f.write(audio_content)
                    
            except Exception as e:
                self._log(f"TTS API failed, creating text file: {e}")
                self._create_text_audio_file(clean_text, audio_path)
        else:
            # Fallback: create text file for manual voiceover
            self._create_text_audio_file(clean_text, audio_path)
        
        return audio_path
    
    def _call_tts_api(self, text: str) -> bytes:
        """Call text-to-speech API to generate audio."""
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.tts_api_key
        }
        
        voice_settings = {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.5,
            "use_speaker_boost": True
        }
        
        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": voice_settings
        }
        
        response = requests.post(
            f"{self.tts_api_url}/{self.default_voice_id}",
            json=data,
            headers=headers
        )
        
        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"TTS API error: {response.status_code} - {response.text}")
    
    def _create_text_audio_file(self, text: str, audio_path: Path):
        """Create text file as fallback when TTS API is not available."""
        # Create a text file with the narration script
        text_path = audio_path.with_suffix('.txt')
        
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write("# SINCOR Onboarding Voiceover Script\n")
            f.write(f"# Generated: {datetime.now()}\n")
            f.write(f"# Audio file: {audio_path.name}\n\n")
            f.write("NARRATION TEXT:\n")
            f.write("=" * 50 + "\n\n")
            f.write(text)
            f.write("\n\n" + "=" * 50)
            f.write("\n\nINSTRUCTIONS:")
            f.write("\n- Use professional, friendly tone")
            f.write("\n- Speak at moderate pace (150-160 words per minute)")
            f.write("\n- Add natural pauses at [PAUSE] markers")
            f.write("\n- Emphasize key terms like 'SINCOR', 'CORTEX', '42 agents'")
        
        # Create placeholder audio file
        with open(audio_path, 'w') as f:
            f.write("# Audio placeholder - TTS API not configured\n")
            f.write(f"# Script available at: {text_path}\n")
    
    def _create_timing_data(self, script: Dict, narration: str) -> Dict:
        """Create timing data for video synchronization."""
        scenes = script.get("scenes", [])
        
        # Estimate timing based on text length and natural speech
        words_per_minute = 150
        timing_data = {
            "scenes": [],
            "total_duration": 0
        }
        
        current_time = 0
        
        for scene in scenes:
            # Estimate duration based on text length
            text = scene["text"]
            word_count = len(text.split())
            estimated_duration = (word_count / words_per_minute) * 60
            
            # Add buffer time for pauses
            scene_duration = max(estimated_duration + 2, scene.get("duration", 30))
            
            scene_timing = {
                "scene_id": scene["id"],
                "start_time": current_time,
                "end_time": current_time + scene_duration,
                "duration": scene_duration,
                "text": text,
                "word_count": word_count
            }
            
            timing_data["scenes"].append(scene_timing)
            current_time += scene_duration
        
        timing_data["total_duration"] = current_time
        
        return timing_data
    
    def _get_voice_settings(self) -> Dict:
        """Get current voice configuration."""
        return {
            "voice_id": self.default_voice_id,
            "voice_type": "friendly_female",
            "language": "en-US",
            "accent": "American",
            "speed": "normal",
            "pitch": "natural",
            "emotion": "professional_friendly"
        }
    
    def create_custom_voiceover(self, text: str, voice_settings: Dict = None) -> Dict[str, Any]:
        """Create custom voiceover for any text."""
        try:
            self._log(f"Creating custom voiceover for text: {text[:50]}...")
            
            # Use provided settings or defaults
            settings = voice_settings or self._get_voice_settings()
            
            # Generate audio
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            audio_filename = f"custom_voiceover_{timestamp}.wav"
            audio_path = self.output_dir / audio_filename
            
            if self.tts_api_key:
                try:
                    audio_content = self._call_tts_api(text)
                    with open(audio_path, 'wb') as f:
                        f.write(audio_content)
                except Exception as e:
                    self._log(f"TTS API failed: {e}")
                    self._create_text_audio_file(text, audio_path)
            else:
                self._create_text_audio_file(text, audio_path)
            
            result = {
                "success": True,
                "audio_path": str(audio_path),
                "text": text,
                "voice_settings": settings,
                "created_at": datetime.now().isoformat()
            }
            
            return result
            
        except Exception as e:
            self._log(f"Error creating custom voiceover: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def list_voiceovers(self) -> List[Dict]:
        """List all generated voiceovers."""
        voiceovers = []
        
        for audio_file in self.output_dir.glob("*.wav"):
            voiceover_info = {
                "filename": audio_file.name,
                "path": str(audio_file),
                "size_bytes": audio_file.stat().st_size,
                "created_at": datetime.fromtimestamp(audio_file.stat().st_mtime).isoformat()
            }
            
            # Check for accompanying text script
            text_file = audio_file.with_suffix('.txt')
            if text_file.exists():
                voiceover_info["script_available"] = True
                voiceover_info["script_path"] = str(text_file)
            else:
                voiceover_info["script_available"] = False
            
            voiceovers.append(voiceover_info)
        
        return sorted(voiceovers, key=lambda x: x["created_at"], reverse=True)
    
    def _run_custom_diagnostics(self) -> Dict[str, Any]:
        """Run custom diagnostics for voiceover agent."""
        return {
            "output_directory": str(self.output_dir),
            "directory_exists": self.output_dir.exists(),
            "audio_files_count": len(list(self.output_dir.glob("*.wav"))),
            "tts_api_configured": bool(self.tts_api_key),
            "voice_id": self.default_voice_id
        }