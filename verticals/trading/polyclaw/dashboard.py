#!/usr/bin/env python3
"""
Polyclaw Observer Dashboard

Desktop GUI that stays open and shows live observation + current stats
from the autonomous trading system.

Run this separately from the scheduler. It can read state from the simulation
or be extended to watch your real agents.

Requirements: pip install customtkinter
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError:
    print("Please install customtkinter: pip install customtkinter")
    exit(1)

# Try to import the autonomous system
try:
    from verticals.trading.polyclaw.simulation_engine import SimulationEngine
    from verticals.trading.polyclaw.observer_improver import ObserverImproverAgent
    AUTONOMOUS_AVAILABLE = True
except ImportError:
    AUTONOMOUS_AVAILABLE = False


class PolyclawDashboard:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("Polyclaw Observer Dashboard")
        self.root.geometry("900x650")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.sim = None
        self.observer = None
        self.running = True

        if AUTONOMOUS_AVAILABLE:
            self.sim = SimulationEngine(initial_bankroll=25000.0)
            self.observer = ObserverImproverAgent()

        self._build_ui()
        self._start_update_loop()

    def _build_ui(self):
        # Header
        header = ctk.CTkLabel(self.root, text="POLYCLAW OBSERVER DASHBOARD", 
                              font=ctk.CTkFont(size=22, weight="bold"))
        header.pack(pady=15)

        # Metrics Frame
        metrics_frame = ctk.CTkFrame(self.root)
        metrics_frame.pack(fill="x", padx=20, pady=10)

        self.bankroll_label = self._create_metric(metrics_frame, "Bankroll", "$25,000.00", 0, 0)
        self.pnl_label = self._create_metric(metrics_frame, "Realized PnL", "$0.00", 0, 1)
        self.drawdown_label = self._create_metric(metrics_frame, "Max Drawdown", "0.00%", 0, 2)
        self.trades_label = self._create_metric(metrics_frame, "Total Trades", "0", 1, 0)
        self.winrate_label = self._create_metric(metrics_frame, "Recent Win Rate", "--", 1, 1)
        self.regime_label = self._create_metric(metrics_frame, "Current Regime", "normal", 1, 2)

        # Observer Section
        observer_frame = ctk.CTkFrame(self.root)
        observer_frame.pack(fill="both", expand=True, padx=20, pady=10)

        obs_title = ctk.CTkLabel(observer_frame, text="Observer Health & Suggestions", 
                                   font=ctk.CTkFont(size=16, weight="bold"))
        obs_title.pack(anchor="w", padx=15, pady=10)

        self.health_text = ctk.CTkTextbox(observer_frame, height=120, font=("Consolas", 12))
        self.health_text.pack(fill="x", padx=15, pady=5)

        self.suggestions_text = ctk.CTkTextbox(observer_frame, height=100, font=("Consolas", 12))
        self.suggestions_text.pack(fill="x", padx=15, pady=5)

        # Status bar
        self.status_label = ctk.CTkLabel(self.root, text="Last update: --", 
                                         font=ctk.CTkFont(size=11))
        self.status_label.pack(pady=10)

        # Control buttons
        btn_frame = ctk.CTkFrame(self.root)
        btn_frame.pack(fill="x", padx=20, pady=10)

        refresh_btn = ctk.CTkButton(btn_frame, text="Force Refresh", command=self._force_refresh)
        refresh_btn.pack(side="left", padx=10)

        quit_btn = ctk.CTkButton(btn_frame, text="Quit", command=self._quit, fg_color="red")
        quit_btn.pack(side="right", padx=10)

    def _create_metric(self, parent, title, value, row, col):
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=col, padx=10, pady=8, sticky="nsew")

        title_label = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=12))
        title_label.pack(pady=(8, 2))

        value_label = ctk.CTkLabel(frame, text=value, font=ctk.CTkFont(size=18, weight="bold"))
        value_label.pack(pady=(0, 8))

        parent.grid_columnconfigure(col, weight=1)
        return value_label

    def _update_metrics(self):
        if not self.sim:
            return

        metrics = self.sim.get_metrics()

        self.bankroll_label.configure(text=f"${metrics['bankroll']:,.2f}")
        self.pnl_label.configure(text=f"${metrics['realized_pnl']:,.2f}")
        self.drawdown_label.configure(text=f"{metrics['max_drawdown']*100:.2f}%")
        self.trades_label.configure(text=str(metrics['trade_count']))

        # Update win rate and regime from observer if available
        if self.observer:
            health = self.observer.get_health_report()
            self.winrate_label.configure(text=f"{health['recent_win_rate']*100:.1f}%")
            self.regime_label.configure(text=health['regime'])

            # Observer health box
            self.health_text.delete("0.0", "end")
            self.health_text.insert("0.0", json.dumps(health, indent=2))

            suggestions = self.observer.suggest_improvements()
            self.suggestions_text.delete("0.0", "end")
            if suggestions:
                self.suggestions_text.insert("0.0", json.dumps(suggestions, indent=2))
            else:
                self.suggestions_text.insert("0.0", "No major issues detected. System operating normally.")

        self.status_label.configure(text=f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    def _start_update_loop(self):
        def loop():
            while self.running:
                try:
                    self.root.after(0, self._update_metrics)
                except:
                    pass
                time.sleep(4)  # Refresh every 4 seconds

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()

    def _force_refresh(self):
        self._update_metrics()

    def _quit(self):
        self.running = False
        self.root.destroy()

    def run(self):
        # Seed some initial activity so the dashboard isn't empty on start
        if self.sim and AUTONOMOUS_AVAILABLE:
            # Run a few quick cycles so there is data to display
            from verticals.trading.polyclaw.core_agent import PolyclawCoreAgent
            core = PolyclawCoreAgent()

            test_markets = [
                {"id": "m1", "probability": 0.63, "true_probability": 0.71},
                {"id": "m2", "probability": 0.39, "true_probability": 0.42},
                {"id": "m3", "probability": 0.58, "true_probability": 0.65},
            ]
            for m in test_markets:
                decision = core.evaluate_market(m, m["true_probability"])
                if decision:
                    self.sim.submit_order(m, decision.side, decision.size * 25000)
                    outcome = m["true_probability"] > 0.5
                    self.sim.resolve_market(decision.market_id, outcome)
                    core.update_from_outcome((decision.side == "buy_yes" and outcome) or 
                                           (decision.side == "buy_no" and not outcome))

            if self.observer:
                self.observer.observe_cycle(self.sim.get_metrics(), self.sim.state.trades)

        self._update_metrics()
        self.root.mainloop()


if __name__ == "__main__":
    print("Starting Polyclaw Observer Dashboard...")
    dashboard = PolyclawDashboard()
    dashboard.run()
