// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SincPriceOracle} from "../src/SincPriceOracle.sol";
import {IChainlinkAggregatorV3} from "../src/interfaces/ISincLoopInfra.sol";

contract MockCurvePrice {
    uint256 public priceWei;

    function set(uint256 p) external {
        priceWei = p;
    }

    function currentPriceWei() external view returns (uint256) {
        return priceWei;
    }
}

contract MockAggregatorV3 {
    int256 public answer;
    uint256 public updatedAt;

    function set(int256 a, uint256 u) external {
        answer = a;
        updatedAt = u;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (1, answer, updatedAt, updatedAt, 1);
    }
}

contract SincPriceOracleTest is Test {
    SincPriceOracle oracle;
    MockCurvePrice curve;
    MockAggregatorV3 feed;

    address admin = makeAddr("admin");
    address rando = makeAddr("rando");

    function setUp() public {
        vm.warp(1_750_000_000); // realistic timestamp (default 1 breaks staleness math)
        curve = new MockCurvePrice();
        feed = new MockAggregatorV3();
        oracle = new SincPriceOracle(admin, address(curve), address(feed), 1 hours);
        // Live Base values, 2026-07-20: 3.0095e-8 ETH per whole SINC, ETH = $1,859.69
        curve.set(30_095_132_558); // wei per whole SINC
        feed.set(185_969_000_000, block.timestamp); // 8 dp
    }

    // ------------------------- happy path -------------------------

    function test_curveMode_matchesHandComputedPrice() public view {
        // 3.0095132558e10 * 1.85969e11 / 1e20 = 55.97 -> 55 (floor)
        assertEq(oracle.sincPriceUSDC(), 55);
    }

    function test_priceScalesLinearly() public {
        curve.set(30_095_132_558 * 100);
        uint256 p = oracle.sincPriceUSDC();
        assertTrue(p == 5596 || p == 5597); // floor-rounding boundary
    }

    // ------------------------- failure modes -------------------------

    function test_revertsOnStaleChainlink() public {
        feed.set(185_969_000_000, block.timestamp - 2 hours);
        vm.expectRevert(SincPriceOracle.StalePrice.selector);
        oracle.sincPriceUSDC();
    }

    function test_revertsOnNegativeAnswer() public {
        feed.set(-1, block.timestamp);
        vm.expectRevert(SincPriceOracle.InvalidPrice.selector);
        oracle.sincPriceUSDC();
    }

    function test_revertsOnZeroAnswer() public {
        feed.set(0, block.timestamp);
        vm.expectRevert(SincPriceOracle.InvalidPrice.selector);
        oracle.sincPriceUSDC();
    }

    function test_revertsOutOfBoundsHigh() public {
        curve.set(1e18); // $1.86e9 per SINC — absurd
        vm.expectRevert(abi.encodeWithSelector(SincPriceOracle.OutOfBounds.selector, 1_859_690_000));
        oracle.sincPriceUSDC();
    }

    function test_revertsOutOfBoundsLow() public {
        curve.set(1); // far below minPrice
        vm.expectRevert(abi.encodeWithSelector(SincPriceOracle.OutOfBounds.selector, 0));
        oracle.sincPriceUSDC();
    }

    // ------------------------- manual mode -------------------------

    function test_manualMode() public {
        vm.startPrank(admin);
        oracle.setManualPrice(1_500_000); // $1.50 floor price
        oracle.setSource(SincPriceOracle.Source.MANUAL);
        vm.stopPrank();
        assertEq(oracle.sincPriceUSDC(), 1_500_000);
    }

    function test_manualModeStaleness() public {
        vm.startPrank(admin);
        oracle.setManualPrice(1_500_000);
        oracle.setSource(SincPriceOracle.Source.MANUAL);
        vm.stopPrank();
        vm.warp(block.timestamp + 25 hours); // > manualHeartbeat (24h)
        vm.expectRevert(SincPriceOracle.StalePrice.selector);
        oracle.sincPriceUSDC();
    }

    function test_manualModeIgnoresCurveAndFeed() public {
        vm.startPrank(admin);
        oracle.setManualPrice(1_500_000);
        oracle.setSource(SincPriceOracle.Source.MANUAL);
        vm.stopPrank();
        feed.set(0, 0); // would revert in CURVE mode
        assertEq(oracle.sincPriceUSDC(), 1_500_000);
    }

    // ------------------------- access control -------------------------

    function test_randoCannotAdmin() public {
        vm.startPrank(rando);
        vm.expectRevert();
        oracle.setManualPrice(1);
        vm.expectRevert();
        oracle.setSource(SincPriceOracle.Source.MANUAL);
        vm.expectRevert();
        oracle.setCurve(address(1));
        vm.expectRevert();
        oracle.setEthUsdFeed(address(1), 0);
        vm.expectRevert();
        oracle.setBounds(1, 2);
        vm.stopPrank();
    }

    function test_adminCanRotateCurveAndFeed() public {
        MockCurvePrice curve2 = new MockCurvePrice();
        curve2.set(60_190_265_116); // 2x
        MockAggregatorV3 feed2 = new MockAggregatorV3();
        feed2.set(185_969_000_000, block.timestamp);
        vm.startPrank(admin);
        oracle.setCurve(address(curve2));
        oracle.setEthUsdFeed(address(feed2), 2 hours);
        vm.stopPrank();
        assertEq(oracle.sincPriceUSDC(), 111);
    }

    function test_badBoundsRevert() public {
        vm.prank(admin);
        vm.expectRevert(SincPriceOracle.BadBounds.selector);
        oracle.setBounds(0, 10);
        vm.prank(admin);
        vm.expectRevert(SincPriceOracle.BadBounds.selector);
        oracle.setBounds(10, 10);
    }

    function test_constructorRejectsZeroAddresses() public {
        vm.expectRevert(SincPriceOracle.ZeroAddress.selector);
        new SincPriceOracle(address(0), address(curve), address(feed), 0);
        vm.expectRevert(SincPriceOracle.ZeroAddress.selector);
        new SincPriceOracle(admin, address(0), address(feed), 0);
        vm.expectRevert(SincPriceOracle.ZeroAddress.selector);
        new SincPriceOracle(admin, address(curve), address(0), 0);
    }
}
