// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {MockERC20} from "./mocks/MockERC20.sol";
import {SincSovereignRevenueRouter} from "../src/sincor/SincSovereignRevenueRouter.sol";
import {SincConstants} from "../src/sincor/SincConstants.sol";

contract SovereignRevenueRouterTest is Test {
    MockERC20 internal usdc;
    SincSovereignRevenueRouter internal router;

    address internal nodeOps = address(0xA001);
    address internal pol = address(0xA002);
    address internal source = address(0xA003);

    function setUp() external {
        usdc = new MockERC20("USDC", "USDC", 6);
        router = new SincSovereignRevenueRouter(address(this), nodeOps, pol, 4_000, 3_000, 3_000);
        router.grantRole(router.REVENUE_ROUTER_ROLE(), source);

        usdc.mint(source, 1_000_000e6);
        vm.prank(source);
        usdc.approve(address(router), type(uint256).max);
    }

    function test_routeRevenue_splitsNodeOpsPolTreasury() external {
        vm.prank(source);
        (uint256 nodeOpsAmount, uint256 polAmount, uint256 treasuryAmount) =
            router.routeRevenue(address(usdc), 100_000e6, keccak256("auction_fee"));

        assertEq(nodeOpsAmount, 40_000e6);
        assertEq(polAmount, 30_000e6);
        assertEq(treasuryAmount, 30_000e6);

        assertEq(usdc.balanceOf(nodeOps), 40_000e6);
        assertEq(usdc.balanceOf(pol), 30_000e6);
        assertEq(usdc.balanceOf(SincConstants.TREASURY_SEED_ADDRESS), 30_000e6);
    }

    function test_routeRevenue_triggersInternalTaskFundingAtThreshold() external {
        router.setTreasuryThreshold(address(usdc), 20_000e6, 5_000e6);

        vm.prank(source);
        router.routeRevenue(address(usdc), 100_000e6, keccak256("spread"));

        // 30k treasury inflow - 5k self-funded task budget
        assertEq(router.treasuryBalanceByToken(address(usdc)), 25_000e6);
    }
}
