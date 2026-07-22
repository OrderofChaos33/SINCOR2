// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SharedLiquidityVault} from "../../src/SharedLiquidityVault.sol";
import {MockERC20} from "../mocks/MockERC20.sol";

/// @notice Stateful handler driving random multi-LP, multi-strategy sequences.
///         All calls pre-bounded so nothing reverts (foundry.toml: fail_on_revert).
contract VaultHandler is Test {
    SharedLiquidityVault public vault;
    MockERC20 public sinc;
    MockERC20 public usdc;

    address[] public lps;
    address[] public hooks;
    uint256[] public sids;

    // ghost: token => total drawn across all LPs/strategies
    mapping(address => uint256) public ghostOutstanding;

    constructor(SharedLiquidityVault _vault, MockERC20 _sinc, MockERC20 _usdc, address _guardian) {
        vault = _vault;
        sinc = _sinc;
        usdc = _usdc;

        for (uint256 i = 0; i < 3; i++) {
            address lp = address(uint160(0x1000 + i));
            lps.push(lp);
            sinc.mint(lp, 1_000_000e18);
            usdc.mint(lp, 1_000_000e6);
            vm.startPrank(lp);
            sinc.approve(address(vault), type(uint256).max);
            usdc.approve(address(vault), type(uint256).max);
            vm.stopPrank();
        }
        for (uint256 i = 0; i < 2; i++) {
            address h = address(uint160(0x2000 + i));
            hooks.push(h);
            vm.prank(_guardian);
            sids.push(vault.registerStrategy(h, address(0x3000), 0, 0));
            sinc.mint(h, 1_000_000e18);
            usdc.mint(h, 1_000_000e6);
            vm.startPrank(h);
            sinc.approve(address(vault), type(uint256).max);
            usdc.approve(address(vault), type(uint256).max);
            vm.stopPrank();
        }
    }

    function _token(uint256 seed) internal view returns (MockERC20) {
        return seed % 2 == 0 ? sinc : usdc;
    }

    function deposit(uint256 seed, uint256 amt) external {
        MockERC20 t = _token(seed);
        address lp = lps[seed % lps.length];
        uint256 unit = address(t) == address(usdc) ? 1e6 : 1e18;
        amt = bound(amt, 1, 100_000 * unit);
        vm.prank(lp);
        vault.deposit(address(t), amt);
    }

    function withdraw(uint256 seed, uint256 amt) external {
        MockERC20 t = _token(seed);
        address lp = lps[seed % lps.length];
        uint256 free = vault.freeBalance(lp, address(t));
        if (free == 0) return;
        amt = bound(amt, 1, free);
        vm.prank(lp);
        vault.withdraw(address(t), amt);
    }

    function allocate(uint256 seed, uint256 amt) external {
        MockERC20 t = _token(seed);
        address lp = lps[seed % lps.length];
        uint256 sid = sids[seed % sids.length];
        uint256 real = vault.realBalance(lp, address(t));
        if (real == 0) return;
        amt = bound(amt, 1, real);
        vm.prank(lp);
        vault.allocateVirtual(sid, address(t), amt);
    }

    function deallocate(uint256 seed, uint256 amt) external {
        MockERC20 t = _token(seed);
        address lp = lps[seed % lps.length];
        uint256 sid = sids[seed % sids.length];
        uint256 alloc = vault.virtualAlloc(lp, sid, address(t));
        uint256 drawn = vault.outstanding(lp, sid, address(t));
        if (alloc <= drawn) return;
        amt = bound(amt, 1, alloc - drawn);
        vm.prank(lp);
        vault.deallocateVirtual(sid, address(t), amt);
    }

    function draw(uint256 seed, uint256 amt) external {
        MockERC20 t = _token(seed);
        uint256 i = seed % sids.length;
        address lp = lps[seed % lps.length];
        uint256 avail = vault.availableDraw(sids[i], lp, address(t));
        if (avail == 0) return;
        amt = bound(amt, 1, avail);
        vm.prank(hooks[i]);
        vault.drawDown(sids[i], lp, address(t), amt);
        ghostOutstanding[address(t)] += amt;
    }

    function settle(uint256 seed, uint256 feeSeed) external {
        MockERC20 t = _token(seed);
        uint256 i = seed % sids.length;
        address lp = lps[seed % lps.length];
        uint256 drawn = vault.outstanding(lp, sids[i], address(t));
        if (drawn == 0) return;
        uint256 unit = address(t) == address(usdc) ? 1e6 : 1e18;
        uint256 fee = bound(feeSeed, 0, 10_000 * unit);
        vm.prank(hooks[i]);
        vault.settleUp(sids[i], lp, address(t), drawn, fee, 500);
        ghostOutstanding[address(t)] -= drawn;
    }
}

/// @notice Invariant: vault solvency + accounting integrity across arbitrary sequences.
contract SharedLiquidityVaultInvariantTest is Test {
    SharedLiquidityVault vault;
    MockERC20 sinc;
    MockERC20 usdc;
    VaultHandler handler;

    function setUp() public {
        sinc = new MockERC20("SINC", "SINC", 18);
        usdc = new MockERC20("USD Coin", "USDC", 6);
        vault = new SharedLiquidityVault(sinc, usdc, address(0x4000), address(0x5000));
        handler = new VaultHandler(vault, sinc, usdc, address(0x4000));
        targetContract(address(handler));
    }

    /// Assets on-hand + lent out always cover deposits + accrued fee claims.
    function invariant_solvent() public view {
        assertTrue(vault.checkInvariant());
    }

    /// Internal outstanding ledger always matches the ghost sum of draw/settle calls.
    function invariant_outstandingMatchesGhost() public view {
        assertEq(vault.outstandingTotal(address(usdc)), handler.ghostOutstanding(address(usdc)));
        assertEq(vault.outstandingTotal(address(sinc)), handler.ghostOutstanding(address(sinc)));
    }

    /// Drawn capital is always held by the strategy hooks (nothing vanishes).
    function invariant_drawnCapitalHeldByHooks() public view {
        address h0 = handler.hooks(0);
        address h1 = handler.hooks(1);
        assertEq(
            usdc.balanceOf(h0) + usdc.balanceOf(h1) + usdc.balanceOf(address(vault)) + usdc.balanceOf(address(0x5000)),
            3_000_000e6 - usdc.balanceOf(handler.lps(0)) - usdc.balanceOf(handler.lps(1)) - usdc.balanceOf(handler.lps(2))
            + usdc.balanceOf(handler.lps(0)) + usdc.balanceOf(handler.lps(1)) + usdc.balanceOf(handler.lps(2))
        );
    }
}
