"""W-104 (NEW, VERIFIED): the Polyclaw kill switch auto-clears on restart.

``Bankroll.__init__`` runs ``_auto_clear_stuck_dry_runs()``. When open
exposure exceeds the leverage ceiling and every open trade is simulated,
it closes the dry runs AND clears a manually-tripped kill switch::

    closed = self.clear_open_dry_runs()
    if self.kill_switch_active():
        self.clear_kill_switch()      # <-- silent disarm on restart

The documented contract (execution_adapter.py) is that a tripped switch
"blocks every order until manually cleared". A process restart
(Railway redeploy, crash loop) silently undoes the operator's manual
halt whenever the (narrow) all-simulated/over-cap condition holds.
The capital recovery (closing stuck dry-runs) is legitimate; the
switch disarm is not — resumption of trading must stay a human
decision.

Severity: Low. The auto-clear refuses to run when any live trade is
open, so no real money is at risk in the cleared state by
construction; the hazard is silent disarm of a human safety action.

Fix (this branch): remove the auto-clear of the kill switch from
``_auto_clear_stuck_dry_runs`` — stuck dry-run exposure is still
closed automatically, but the switch stays tripped until the operator
clears it via the monitoring endpoint.

PoC/regression below (hermetic tmp DB):
  1. Over-cap all-simulated exposure + manually tripped kill switch.
  2. Re-instantiate Bankroll on the same DB (simulates a restart).
  3. Assert the switch is STILL tripped (fails before the fix) and the
     stuck dry-run exposure was still recovered (passes before/after).
"""
from __future__ import annotations

from sincor2.bankroll import Bankroll


def _overcap_bankroll(tmp_path) -> Bankroll:
    br = Bankroll(db_path=tmp_path / "bankroll.db")
    # Push simulated exposure over the leverage ceiling:
    # max_exposure = equity($50 default) * max_leverage(3.0) = $150.
    br.reserve_exposure(200.0)
    assert br.open_exposure() > br.max_exposure()
    assert all(t["simulated"] for t in br.open_trades())
    return br


class TestKillSwitchSurvivesRestart:
    def test_restart_does_not_clear_manually_tripped_switch(self, tmp_path):
        br = _overcap_bankroll(tmp_path)
        br.trip_kill_switch("operator investigation")
        assert br.kill_switch_active()

        # Simulate a process restart: fresh Bankroll on the same DB.
        br2 = Bankroll(db_path=tmp_path / "bankroll.db")

        # The operator's halt must survive the restart.
        assert br2.kill_switch_active(), (
            "W-104: restart silently cleared a manually-tripped kill switch")

    def test_restart_still_recovers_stuck_dry_run_exposure(self, tmp_path):
        br = _overcap_bankroll(tmp_path)
        br.trip_kill_switch("operator investigation")

        br2 = Bankroll(db_path=tmp_path / "bankroll.db")

        # Capital recovery is preserved: stuck dry-runs are closed so
        # available capital is not permanently $0.
        assert br2.open_exposure() == 0.0
        assert br2.available_capital() > 0.0
