#!/usr/bin/env python3
"""
SINCOR Media Pack Activator
Integrates storyboard generation, video rendering, and business intelligence
Clean, cohesive activation system for revenue generation
"""

import os
import subprocess
import json
import sqlite3
from datetime import datetime
from pathlib import Path

class MediaPackActivator:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.storyboard_dir = self.base_dir / "storyboards"
        self.shots_dir = self.base_dir / "shots"
        self.output_dir = self.base_dir / "out"
        self.db_path = self.base_dir / "data_detailing_fts.db"
        
        # Ensure directories exist
        self.storyboard_dir.mkdir(exist_ok=True)
        self.shots_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
    
    def check_system_ready(self):
        """Verify all components are ready"""
        checks = {
            "Training Data": (self.base_dir / "training" / "ad_frameworks.yaml").exists(),
            "FTS Database": self.db_path.exists(),
            "Assets Images": (self.base_dir / "assets" / "images").exists(),
            "Assets Video": (self.base_dir / "assets" / "video").exists(),
            "Assets Music": (self.base_dir / "assets" / "music").exists(),
        }
        
        print("SINCOR Media Pack System Status:")
        print("=" * 40)
        
        all_ready = True
        for component, status in checks.items():
            status_icon = "OK" if status else "MISSING"
            print(f"{status_icon} {component}")
            if not status:
                all_ready = False
        
        return all_ready
    
    def generate_clinton_media_pack(self):
        """Generate complete media pack for Clinton Auto Detailing"""
        print("\\nActivating Clinton Auto Detailing Media Pack Generation...")
        
        # Business personas for Clinton Auto Detailing
        personas = {
            "business_owner": "fleet cleaning quick turnaround",
            "busy_parent": "family car interior pet hair stains", 
            "senior": "gentle pickup service reliable"
        }
        
        generated_content = []
        
        for persona, query in personas.items():
            print(f"\\nGenerating content for {persona}...")
            
            try:
                # Use existing storyboard generation from sincor-clean
                storyboard_script = self.base_dir.parent / "sincor-clean" / "scripts_make_storyboard.py"
                
                if storyboard_script.exists():
                    output_file = self.storyboard_dir / f"clinton_{persona}_storyboard.txt"
                    
                    cmd = [
                        "python", str(storyboard_script),
                        "--db", str(self.db_path),
                        "--q", query,
                        "--framework", "HBPC",
                        "--persona", persona,
                        "--out", str(output_file)
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    
                    if result.returncode == 0:
                        print(f"Generated storyboard: {output_file}")
                        generated_content.append({
                            "persona": persona,
                            "storyboard": str(output_file),
                            "query": query
                        })
                    else:
                        print(f"Error generating {persona}: {result.stderr}")
                else:
                    print(f"Storyboard script not found: {storyboard_script}")
                    
            except Exception as e:
                print(f"Exception generating {persona}: {e}")
        
        return generated_content
    
    def create_video_from_storyboard(self, storyboard_file, persona):
        """Convert storyboard to video using render system"""
        try:
            # Convert storyboard to CSV shots
            csv_file = self.shots_dir / f"clinton_{persona}.csv"
            
            storyboard_to_csv_script = self.base_dir.parent / "sincor-clean" / "scripts_storyboard_to_csv.py"
            
            if storyboard_to_csv_script.exists():
                cmd = ["python", str(storyboard_to_csv_script), str(storyboard_file), str(csv_file)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"Created shots CSV: {csv_file}")
                    
                    # Render video
                    video_file = self.output_dir / f"clinton_{persona}.mp4"
                    render_script = self.base_dir.parent / "sincor-clean" / "scripts_render_video.py"
                    
                    if render_script.exists():
                        cmd = [
                            "python", str(render_script),
                            "--shots", str(csv_file),
                            "--out", str(video_file),
                            "--url", "https://clintondetailing.com/booking",
                            "--phone", "1-815-718-8936",
                            "--service_line", "Clinton Auto Detailing • Interior • Exterior • Mobile Service"
                        ]
                        
                        result = subprocess.run(cmd, capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            print(f"Rendered video: {video_file}")
                            return str(video_file)
                        else:
                            print(f"Video render error: {result.stderr}")
                    
        except Exception as e:
            print(f"Video creation error: {e}")
        
        return None
    
    def activate_full_media_pack_trial(self):
        """Activate complete media pack creation and delivery system"""
        print("SINCOR Media Pack Activation - Clinton Auto Detailing Trial")
        print("=" * 60)
        
        # System readiness check
        if not self.check_system_ready():
            print("\\nSystem not ready. Please ensure all components are available.")
            return False
        
        print("\\nSystem ready. Beginning media pack generation...")
        
        # Generate storyboards
        generated_content = self.generate_clinton_media_pack()
        
        if not generated_content:
            print("\\nNo content generated. Check system configuration.")
            return False
        
        # Create videos
        videos_created = []
        for content in generated_content:
            video_path = self.create_video_from_storyboard(
                content["storyboard"], 
                content["persona"]
            )
            if video_path:
                videos_created.append(video_path)
        
        # Summary
        print("\\n" + "=" * 60)
        print("SINCOR Media Pack Trial Results:")
        print(f"Storyboards generated: {len(generated_content)}")
        print(f"Videos created: {len(videos_created)}")
        
        if videos_created:
            print("\\nGenerated Videos:")
            for video in videos_created:
                print(f"   • {video}")
            
            print("\\nMEDIA PACK TRIAL SUCCESSFUL!")
            print("Revenue generation system is ACTIVE and ready.")
            return True
        else:
            print("\\nMedia pack trial partially successful - storyboards created but video rendering needs attention.")
            return False

def main():
    """Main activation function"""
    activator = MediaPackActivator()
    success = activator.activate_full_media_pack_trial()
    
    if success:
        print("\\nNext step: Present media packs to Clinton Auto Detailing owner")
        print("Revenue stream activated!")
    else:
        print("\\nSystem needs configuration. Check dependencies and try again.")

if __name__ == "__main__":
    main()