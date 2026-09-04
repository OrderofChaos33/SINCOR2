// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {MockERC20} from "./mocks/MockERC20.sol";
import {MockAeroExecutionAdapter} from "./mocks/MockAeroExecutionAdapter.sol";
import {MockChainlinkFeed} from "./mocks/MockChainlinkFeed.sol";

import {SincRiskModule} from "../src/sincor/SincRiskModule.sol";
import {SincFeeFlywheel} from "../src/sincor/SincFeeFlywheel.sol";
import {ISincRiskModule} from "../src/interfaces/ISincVaultSystem.sol";

contract SincFlywheelTest is Test {
    MockERC20 internal usdc;
    MockERC20 internal aero;
    MockAeroExecutionAdapter internal adapter;
    MockChainlinkFeed internal usdcFeed;
    MockChainlinkFeed internal aeroFeed;

    SincRiskModule internal risk;
    SincFeeFlywheel internal flywheel;

    address internal treasury = address(0xBEEF);
    address internal gauge = address(0xCAFE);

    function setUp() external {
        usdc = new MockERC20("USDC", "USDC", 6);
        aero = new MockERC20("AERO", "AERO", 18);
        adapter = new MockAeroExecutionAdapter(address(aero));
        adapter.setRate(1e12, 1);

        usdcFeed = new MockChainlinkFeed(8, "USDC/USD", 1e8);
        aeroFeed = new MockChainlinkFeed(8, "AERO/USD", 1e8);

        risk = new SincRiskModule(address(this), 1_000);
        risk.setOracle(address(usdc), address(usdcFeed), 1 days);
        risk.setOracle(address(aero), address(aeroFeed), 1 days);

        flywheel = new SincFeeFlywheel(address(this), address(aero), address(adapter), address(risk), treasury, 1_000, 365 days);
        flywheel.setGaugeWhitelist(gauge, true);
        flywheel.setGaugeWeight(gauge, 10_000);

        usdc.mint(address(this), 1_000_000e6);
        usdc.approve(address(flywheel), type(uint256).max);
    }

    function test_processFees_splitsAndLocks() external {
        uint256 feeAmount = 1_000e6;
        flywheel.processFees(address(usdc), feeAmount, gauge);

        assertEq(usdc.balanceOf(treasury), 300e6);
        assertEq(adapter.totalLocked(), 700e18);
        assertEq(adapter.lastGauge(), gauge);
        assertEq(adapter.lastWeight(), 10_000);
    }

    function test_validateSwap_revertsAboveMaxSlippage() external {
        ISincRiskModule.SwapCheckParams memory p = ISincRiskModule.SwapCheckParams({
            tokenIn: address(usdc),
            tokenOut: address(aero),
            amountIn: 1e6,
            expectedOutOracle: 1e18,
            maxSlippageBps: 51,
            maxOracleDeviationBps: 500
        });

        vm.expectRevert(SincRiskModule.SlippageTooHigh.selector);
        risk.validateSwap(p);
    }
}
