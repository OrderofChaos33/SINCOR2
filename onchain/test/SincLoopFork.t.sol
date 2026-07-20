// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {SincPriceOracle} from "../src/SincPriceOracle.sol";
import {SincSwapRouter} from "../src/SincSwapRouter.sol";

/// @title SincLoopFork — live Base-mainnet fork verification of the production oracle + router.
/// @notice Runs automatically when BASE_RPC_URL is set (CI sets it); skips cleanly otherwise.
///         All addresses below are the canonical Base deployments (CANONICAL_ADDRESSES.md).
contract SincLoopForkTest is Test {
    // --- canonical Base addresses (verified onchain) ---
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7; // 8 dp
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913; // 6 dp
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant CURVE = 0x75dE341a2BC81806198364F125d4Cde36527619C; // live sale curve
    address constant CL_ETH_USD = 0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70; // Chainlink
    address constant AERO_ROUTER = 0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43;
    address constant AERO_FACTORY = 0x420DD381b31aEf6683db6B902084cB0FFECe40Da;

    SincPriceOracle oracle;
    SincSwapRouter router;
    address user = makeAddr("user");
    bool forked;

    function setUp() public {
        string memory rpc = vm.envOr("BASE_RPC_URL", string(""));
        if (bytes(rpc).length == 0) return;
        vm.createSelectFork(rpc);
        forked = true;
        oracle = new SincPriceOracle(address(this), CURVE, CL_ETH_USD, 2 hours);
        router = new SincSwapRouter(SINC, USDC, WETH, CURVE, AERO_ROUTER, AERO_FACTORY, oracle, address(this));
    }

    modifier onlyFork() {
        if (!forked) return;
        _;
    }

    // ------------------------------ oracle ------------------------------

    function testFork_oracleReturnsSaneLivePrice() public onlyFork {
        uint256 p = oracle.sincPriceUSDC();
        emit log_named_uint("sincPriceUSDC (6dp per whole SINC)", p);
        // Curve early-stage: expect somewhere in $0.000001 .. $0.01 per SINC.
        assertGt(p, 0);
        assertLt(p, 10_000);
    }

    function testFork_oracleStaleAfterWarp() public onlyFork {
        vm.warp(block.timestamp + 3 days);
        vm.expectRevert(SincPriceOracle.StalePrice.selector);
        oracle.sincPriceUSDC();
    }

    // ------------------------------ router ------------------------------

    function testFork_swapUSDCForSINC() public onlyFork {
        uint256 usdcIn = 1e6; // 1 USDC
        deal(USDC, user, usdcIn);
        vm.startPrank(user);
        IERC20(USDC).approve(address(router), usdcIn);
        uint256 sincOut = router.swapUSDCForSINC(usdcIn);
        vm.stopPrank();

        uint256 p = oracle.sincPriceUSDC();
        uint256 expected = (usdcIn * router.sincUnit()) / p; // at oracle price
        emit log_named_uint("USDC in", usdcIn);
        emit log_named_uint("SINC out", sincOut);
        emit log_named_uint("expected at oracle", expected);
        assertGt(sincOut, 0);
        // Curve takes a 3% referral cut + price impact; allow 10% band below oracle.
        assertGe(sincOut, (expected * 90) / 100);
        assertLe(sincOut, (expected * 110) / 100); // pool/Chainlink ETH price can differ slightly
        assertEq(IERC20(SINC).balanceOf(user), sincOut);
    }

    function testFork_roundTrip() public onlyFork {
        uint256 usdcIn = 1e6; // 1 USDC
        deal(USDC, user, usdcIn);
        vm.startPrank(user);
        IERC20(USDC).approve(address(router), usdcIn);
        uint256 sincOut = router.swapUSDCForSINC(usdcIn);

        // Sell half back (curve reserve is thin — keep the leg small).
        uint256 sellAmt = sincOut / 2;
        IERC20(SINC).approve(address(router), sellAmt);
        uint256 usdcOut = router.swapSINCForUSDC(sellAmt);
        vm.stopPrank();

        emit log_named_uint("round-trip USDC back (of 0.5 USDC leg)", usdcOut);
        assertGt(usdcOut, 0);
        assertLt(usdcOut, usdcIn); // round trip always loses value (3% cut + impact)
        assertGe(usdcOut, usdcIn / 4); // but not catastrophically for a half-leg
    }

    function testFork_slippageProtectionReverts() public onlyFork {
        // Zero slippage allowance: the curve's 3% referral cut alone must trip it.
        router.setSlippage(0, 0);
        uint256 usdcIn = 1e6;
        deal(USDC, user, usdcIn);
        vm.startPrank(user);
        IERC20(USDC).approve(address(router), usdcIn);
        vm.expectRevert(); // Slippage(minSinc, sincOut)
        router.swapUSDCForSINC(usdcIn);
        vm.stopPrank();
    }

    function testFork_rescueOnlyOwner() public onlyFork {
        address rando = makeAddr("rando");
        vm.prank(rando);
        vm.expectRevert();
        router.rescue(USDC, rando, 1);
        vm.prank(rando);
        vm.expectRevert();
        router.setSlippage(0, 0);
    }

    function testFork_zeroInputReverts() public onlyFork {
        vm.expectRevert(SincSwapRouter.ZeroInput.selector);
        router.swapUSDCForSINC(0);
        vm.expectRevert(SincSwapRouter.ZeroInput.selector);
        router.swapSINCForUSDC(0);
    }
}
