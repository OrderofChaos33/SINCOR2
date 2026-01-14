#!/usr/bin/env python3
"""
Ask CORTEX to create our promotional video using its own agent network
"""

from sincor_kitt_interface import SINCORInterface

def ask_cortex_to_make_video():
    """Let's ask CORTEX to coordinate video creation through its agents."""
    
    print("\n" + "="*60)
    print("    ASKING CORTEX TO CREATE ITS OWN PROMOTIONAL VIDEO")
    print("="*60)
    
    cortex = SINCORInterface()
    
    # Ask CORTEX to make its own promotional video
    video_request = "Hey CORTEX, I need you to create a 3-minute promotional video that shows how revolutionary you are. Use your Content Generation Agent, Marketing Agent, and Creative Teams to produce 'The Opportunity of a Lifetime' video that will make every entrepreneur want to use your 42-agent system immediately."
    
    print(f"\n[FILM] USER: {video_request}")
    print("\n" + "-"*60)
    
    # Let CORTEX coordinate its own video production
    parsed = cortex.process_natural_language(video_request)
    response = cortex.generate_kitt_response(parsed["intent"], parsed["entities"], video_request)
    
    # Replace branding
    response = response.replace("SINCOR", "CORTEX")
    
    print(f"\n[ROBOT] CORTEX: {response}")
    
    # Show the coordination happening
    coordination = cortex.simulate_agent_coordination(parsed["intent"], parsed["entities"])
    print(f"\n[VIDEO] CORTEX: Video production team activated! {len(coordination['agents_activated'])} agents now working on your promotional video...")
    print("\n[ROCKET] CORTEX: Content Gen Agent creating script, Marketing Agent developing strategy, Visual Agents preparing assets...")
    
    print("\n" + "="*60)
    print("[SPARKLE] MIND = BLOWN! Your 42-agent system just coordinated its own video production!")
    print("="*60)

if __name__ == "__main__":
    ask_cortex_to_make_video()