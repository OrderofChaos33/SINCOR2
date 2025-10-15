#!/usr/bin/env python3
"""
SINCOR KITT Interface Demo - Shows natural language interaction with 42-agent system
"""

import time
import random
from sincor_kitt_interface import SINCORInterface

def run_demo():
    """Run a demonstration of the KITT interface with simulated user inputs."""
    
    # Initialize SINCOR
    sincor = SINCORInterface()
    
    # Demo conversations
    demo_inputs = [
        "Hey SINCOR, get me 100 leads for my auto detailing business",
        "How's my business performing this month?",
        "Launch a new marketing campaign for my HVAC service",
        "help",
        "I need to launch a new cleaning service in Miami"
    ]
    
    print("\n" + "="*60)
    print("          SINCOR KITT INTERFACE DEMONSTRATION")
    print("="*60)
    print("\nWatch as SINCOR coordinates your 42-agent ecosystem through")
    print("natural language conversation, just like KITT from Knight Rider!\n")
    
    for i, user_input in enumerate(demo_inputs, 1):
        print(f"\n[DEMO {i}/5] USER: {user_input}")
        print("-" * 50)
        
        # Simulate SINCOR processing
        processing_messages = [
            "Analyzing your request...",
            "Coordinating agents...", 
            "Processing...",
            "Consulting business intelligence...",
            "Mobilizing specialist teams..."
        ]
        
        print(f"\nSINCOR: {random.choice(processing_messages)}")
        time.sleep(1.5)
        
        # Parse and respond
        parsed = sincor.process_natural_language(user_input)
        response = sincor.generate_kitt_response(parsed["intent"], parsed["entities"], user_input)
        
        print(f"\nSINCOR: {response}")
        
        # Show agent coordination status
        if parsed["intent"] != "general_help":
            coordination = sincor.simulate_agent_coordination(parsed["intent"], parsed["entities"])
            time.sleep(1)
            
            status = random.choice(sincor.status_updates)
            print(f"\nSINCOR: {status}")
        
        print("\n" + "="*60)
        
        if i < len(demo_inputs):
            print("Press Enter to continue to next demo...")
            input()
    
    print("\n[STAR] DEMO COMPLETE!")
    print("\nThis is how your 42-agent SINCOR ecosystem responds to natural")
    print("language commands. Each request coordinates multiple specialized")
    print("agents working together to deliver Fortune 500-level automation!")
    print("\nYour AI business companion is ready to transform your operations!")

if __name__ == "__main__":
    run_demo()